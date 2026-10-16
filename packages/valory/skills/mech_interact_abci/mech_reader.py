# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2026 Valory AG
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#
# ------------------------------------------------------------------------------

"""Read what is published about one marketplace mech from a handler, as facts only."""

import enum
import json
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from string import Template
from typing import Any, Callable, Dict, Optional, Tuple, cast
from urllib.parse import urlparse

from aea.protocols.base import Message

from autonomy.chain.config import ChainType

from packages.valory.contracts.mech_mm.contract import MechMM
from packages.valory.protocols.contract_api import ContractApiMessage
from packages.valory.protocols.http.message import HttpMessage
from packages.valory.skills.abstract_round_abci.behaviour_utils import (
    HTTP_CLIENT_PUBLIC_ID,
    LEDGER_API_ADDRESS,
)
from packages.valory.skills.abstract_round_abci.dialogues import (
    ContractApiDialogues,
    HttpDialogues,
)
from packages.valory.skills.abstract_round_abci.models import Requests
from packages.valory.skills.mech_interact_abci.states.base import ADDRESS_RE
from packages.valory.skills.mech_interact_abci.utils import loads_bounded

HTTP_OK = 200
JSON_HEADERS = "Content-Type: application/json\r\n"
MAX_TEXT_LENGTH = 2048
MAX_TOOLS = 50
MAX_DELIVERY_RATE_CALLABLE = "get_max_delivery_rate"
MAX_DELIVERY_RATE_KEY = "max_delivery_rate"

MECH_RECORD_QUERY = Template(
    '{ meches(where: {address: "$address"}) { address maxDeliveryRate '
    "paymentType receivedRequests selfDeliveredFromReceived "
    "deliveredByOthersFromReceived service { id } } "
    'createMeches(where: {mech: "$address"}) { blockTimestamp } }'
)


# The marketplace names some networks differently from the chain names; by chain id.
MARKETPLACE_NETWORKS = {10: "op-mainnet", 42161: "arbitrum-one", 34443: "mode-mainnet"}
HTTP_NOT_FOUND = 404


class RecordStatus(enum.Enum):
    """Whether the marketplace subgraph lists the mech."""

    FOUND = "found"
    NOT_INDEXED = "not_indexed"
    UNAVAILABLE = "unavailable"


class CardStatus(enum.Enum):
    """What came of reading the agent card; only `UNAVAILABLE` may change on a retry."""

    READ = "read"
    NOT_PUBLISHED = "not_published"
    UNAVAILABLE = "unavailable"
    NOT_TRIED = "not_tried"


@dataclass(frozen=True)
class MechFacts:  # pylint: disable=too-many-instance-attributes
    """What is published about a mech; operator fields only from a card whose `provider` is set."""

    address: str
    chain_id: Optional[int]
    record: RecordStatus
    card: CardStatus = CardStatus.NOT_TRIED
    payment_type: Optional[str] = None
    max_delivery_rate: Optional[int] = None
    service_id: Optional[int] = None
    received_requests: Optional[int] = None
    self_delivered: Optional[int] = None
    delivered_by_others: Optional[int] = None
    created_at: Optional[datetime] = None
    name: Optional[str] = None
    tools: Tuple[str, ...] = ()
    operator_name: Optional[str] = None
    operator_domain: Optional[str] = None
    operator_verified: bool = False

    @property
    def card_read(self) -> bool:
        """Whether the card was read and is about this mech."""
        return self.card is CardStatus.READ


def clean_text(value: Any) -> Optional[str]:
    """Accept stripped, non-empty, printable text of at most 2048 characters."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_TEXT_LENGTH:
        return None
    if any(unicodedata.category(char).startswith("C") for char in text):
        return None
    return text


def url_host(value: Any) -> Optional[str]:
    """Get the lowercase host of a URL, or `None` when there is none."""
    text = clean_text(value)
    if text is None:
        return None
    try:
        return urlparse(text).hostname or None
    except ValueError:
        return None


def chain_id_of(network: str) -> Optional[int]:
    """Get the chain id of a chain name such as `gnosis`, or `None` for an unknown one."""
    try:
        return ChainType(network.lower()).id
    except ValueError:
        return None


def marketplace_network(network: str) -> str:
    """The marketplace's name for a chain, which differs from the chain name for some chains."""
    return MARKETPLACE_NETWORKS.get(chain_id_of(network) or 0, network.lower())


def agent_card_url(api_url: str, network: str, service_id: int) -> str:
    """The marketplace agent card of the service on the chain."""
    return (
        f"{api_url.rstrip('/')}/{marketplace_network(network)}/ai-agents/"
        f"{service_id}/agent-card.json"
    )


def _as_int(value: Any) -> Optional[int]:
    """Read an integer given as a number or a decimal string, or `None`."""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_body(message: Message) -> Optional[Any]:
    """Decode a successful HTTP response's JSON body, or `None`."""
    http_message = cast(HttpMessage, message)
    if http_message.status_code != HTTP_OK:
        return None
    try:
        return loads_bounded(http_message.body)
    except (ValueError, TypeError, RecursionError):
        return None


def _graph_data(message: Message) -> Optional[Dict[str, Any]]:
    """Get a GraphQL response's `data`, or `None` when the call failed."""
    body = _json_body(message)
    if not isinstance(body, dict) or body.get("errors"):
        return None
    data = body.get("data")
    return data if isinstance(data, dict) else None


def _created_at(created: Any) -> Optional[datetime]:
    """Read the creation time from the subgraph's `createMeches` list, in UTC, or `None`."""
    first = created[0] if isinstance(created, list) and created else None
    timestamp = (
        _as_int(first.get("blockTimestamp")) if isinstance(first, dict) else None
    )
    if timestamp is None or timestamp < 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def card_is_for_service(
    card: Dict[str, Any], service_id: Optional[int], chain_id: Optional[int]
) -> bool:
    """Whether the card describes the mech's service on its chain.

    A card names only its service's first mech, so it is matched by service
    and chain, which also covers every other mech of that service.
    """
    metadata = card.get("metadata")
    if not isinstance(metadata, dict) or service_id is None:
        return False
    if _as_int(metadata.get("serviceId")) != service_id:
        return False
    mech_address = metadata.get("mechAddress")
    return chain_id is None or (
        isinstance(mech_address, str)
        and mech_address.lower().startswith(f"eip155:{chain_id}:")
    )


def read_record(address: str, chain_id: Optional[int], data: Any) -> MechFacts:
    """Read the subgraph's answer about the mech into facts, without the card."""
    if not isinstance(data, dict):
        return MechFacts(address, chain_id, RecordStatus.UNAVAILABLE)
    meches = data.get("meches")
    mech = meches[0] if isinstance(meches, list) and meches else None
    if not isinstance(mech, dict):
        return MechFacts(address, chain_id, RecordStatus.NOT_INDEXED)
    service = mech.get("service")
    payment_type = mech.get("paymentType")
    return MechFacts(
        address,
        chain_id,
        RecordStatus.FOUND,
        payment_type=(payment_type.lower() if isinstance(payment_type, str) else None),
        max_delivery_rate=_as_int(mech.get("maxDeliveryRate")),
        service_id=_as_int(service.get("id")) if isinstance(service, dict) else None,
        received_requests=_as_int(mech.get("receivedRequests")),
        self_delivered=_as_int(mech.get("selfDeliveredFromReceived")),
        delivered_by_others=_as_int(mech.get("deliveredByOthersFromReceived")),
        created_at=_created_at(data.get("createMeches")),
    )


def with_card(facts: MechFacts, status_code: Optional[int], card: Any) -> MechFacts:
    """Add the agent card's facts, or the reason it could not be read.

    :param facts: the facts read from the subgraph record.
    :param status_code: the HTTP status of the card request.
    :param card: the decoded body of a successful card request, else `None`.
    :return: the facts with the card's, and its status.
    """
    if status_code == HTTP_NOT_FOUND:
        return replace(facts, card=CardStatus.NOT_PUBLISHED)
    if not isinstance(card, dict) or not card_is_for_service(
        card, facts.service_id, facts.chain_id
    ):
        return replace(facts, card=CardStatus.UNAVAILABLE)
    skills = card.get("skills")
    names = (
        (skill.get("name") for skill in skills if isinstance(skill, dict))
        if isinstance(skills, list)
        else ()
    )
    tools = tuple(text for text in (clean_text(name) for name in names) if text)
    raw_provider = card.get("provider")
    provider: Dict[str, Any] = raw_provider if isinstance(raw_provider, dict) else {}
    verified = isinstance(raw_provider, dict)
    return replace(
        facts,
        card=CardStatus.READ,
        name=clean_text(card.get("name")),
        tools=tools[:MAX_TOOLS],
        operator_name=clean_text(provider.get("organization")),
        operator_domain=url_host(provider.get("url")),
        operator_verified=verified,
    )


def _register_callback(
    context: Any, nonce: str, callback: Callable[[Message], None]
) -> None:
    """Have the reply to the request with this nonce passed to `callback`."""
    requests = cast(Requests, context.requests)
    requests.request_id_to_callback[nonce] = lambda reply, _behaviour: callback(reply)


def _send_http(
    context: Any,
    url: str,
    callback: Callable[[Message], None],
    query: Optional[str] = None,
) -> None:
    """Send a GET, or a GraphQL POST when a query is given, through the HTTP client."""
    http_dialogues = cast(HttpDialogues, context.http_dialogues)
    message, dialogue = http_dialogues.create(
        counterparty=str(HTTP_CLIENT_PUBLIC_ID),
        performative=HttpMessage.Performative.REQUEST,
        method="POST" if query is not None else "GET",
        url=url,
        headers=JSON_HEADERS if query is not None else "",
        version="",
        body=json.dumps({"query": query}).encode() if query is not None else b"",
    )
    _register_callback(context, dialogue.dialogue_label.dialogue_reference[0], callback)
    context.outbox.put_message(message=message)


def read_mech(
    context: Any, address: str, callback: Callable[[MechFacts], None]
) -> None:
    """Read the subgraph record and then the agent card of one mech; `callback` gets the facts.

    Works from a handler: it needs the skill's `params` (`mech_chain_id`,
    `marketplace_api_url`), `mechs_subgraph`, `http_dialogues`, `requests` and
    `outbox`. An address that is not `0x` + 40 hex characters is never sent.

    :param context: the skill context.
    :param address: the mech address.
    :param callback: called exactly once with what was read, also when a reply
        cannot be parsed, so a handler waiting on it always answers.
    """
    address = address.lower()
    network = str(context.params.mech_chain_id).lower()
    chain_id = chain_id_of(network)
    if ADDRESS_RE.fullmatch(address) is None:
        callback(MechFacts(address, chain_id, RecordStatus.NOT_INDEXED))
        return

    def on_card(facts: MechFacts, reply: Message) -> None:
        status_code = getattr(reply, "status_code", None)
        try:
            with_facts = with_card(facts, status_code, _json_body(reply))
        except Exception as error:  # pylint: disable=broad-except
            context.logger.warning(
                f"Could not read the agent card of {address}: {error}"
            )
            with_facts = replace(facts, card=CardStatus.UNAVAILABLE)
        if with_facts.card is CardStatus.UNAVAILABLE:
            context.logger.warning(
                f"The agent card of {address} could not be read (status {status_code})."
            )
        callback(with_facts)

    def on_record(reply: Message) -> None:
        try:
            facts = read_record(address, chain_id, _graph_data(reply))
        except Exception as error:  # pylint: disable=broad-except
            context.logger.warning(f"Could not read the record of {address}: {error}")
            facts = MechFacts(address, chain_id, RecordStatus.UNAVAILABLE)
        if facts.record is not RecordStatus.FOUND or facts.service_id is None:
            callback(facts)
            return
        _send_http(
            context,
            agent_card_url(
                context.params.marketplace_api_url, network, facts.service_id
            ),
            lambda card_reply: on_card(facts, card_reply),
        )

    _send_http(
        context,
        context.mechs_subgraph.url,
        on_record,
        MECH_RECORD_QUERY.substitute(address=address),
    )


def _state_int(message: Message, key: str) -> Optional[int]:
    """Read an integer from a contract `STATE` reply's body, or `None` for any other reply."""
    reply = cast(ContractApiMessage, message)
    if reply.performative != ContractApiMessage.Performative.STATE:
        return None
    body = reply.state.body or {}
    return _as_int(body.get(key))


def read_live_rate(
    context: Any, address: str, callback: Callable[[Optional[int]], None]
) -> None:
    """Read the mech contract's live `maxDeliveryRate`; `callback` gets it, or `None`.

    The ledger connection keys its APIs by ledger type, so the request names
    the default ledger and passes the chain as `chain_id`.

    :param context: the skill context.
    :param address: the mech address.
    :param callback: called once with the rate in the payment asset's smallest unit.
    """
    if ADDRESS_RE.fullmatch(address.lower()) is None:
        callback(None)
        return
    contract_api_dialogues = cast(ContractApiDialogues, context.contract_api_dialogues)
    message, dialogue = contract_api_dialogues.create(
        counterparty=LEDGER_API_ADDRESS,
        performative=ContractApiMessage.Performative.GET_STATE,
        ledger_id=context.default_ledger_id,
        contract_id=str(MechMM.contract_id),
        contract_address=address,
        callable=MAX_DELIVERY_RATE_CALLABLE,
        kwargs=ContractApiMessage.Kwargs(
            {"chain_id": str(context.params.mech_chain_id)}
        ),
    )

    def on_reply(reply: Message) -> None:
        rate = _state_int(reply, MAX_DELIVERY_RATE_KEY)
        if rate is None:
            context.logger.warning(
                f"Could not read maxDeliveryRate of {address}: {reply}"
            )
        callback(rate)

    _register_callback(context, dialogue.dialogue_label.dialogue_reference[0], on_reply)
    context.outbox.put_message(message=message)
