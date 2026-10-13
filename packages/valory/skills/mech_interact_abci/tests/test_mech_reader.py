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

"""Tests for reading one mech's published facts from a handler."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from packages.valory.protocols.contract_api import ContractApiMessage
from packages.valory.skills.abstract_round_abci.behaviour_utils import (
    HTTP_CLIENT_PUBLIC_ID,
    LEDGER_API_ADDRESS,
)
from packages.valory.skills.mech_interact_abci.mech_reader import (
    MAX_TOOLS,
    MechFacts,
    RecordStatus,
    agent_card_url,
    chain_id_of,
    clean_text,
    read_live_rate,
    read_mech,
    url_host,
)
from packages.valory.skills.mech_interact_abci.models import (
    DEFAULT_MARKETPLACE_API_URL,
    priced_mechs_with_metadata,
)

MECH = "0x" + "e4" * 20
SUBGRAPH = "https://subgraph.example/marketplace-gnosis"
MARKETPLACE_API = "https://marketplace.example/api/erc8004"
NATIVE = "0xba699a34be8fe0e7725e93dcbce1701b0211a8ca61330aaeb8a05bf2ec7abed1"
RATE = 10**16
CREATED = 1786492800  # 2026-08-12T00:00:00Z
CARD_URL = f"{MARKETPLACE_API}/gnosis/ai-agents/3786/agent-card.json"


def _record(**overrides: Any) -> Dict[str, Any]:
    """A subgraph answer for the mech, as the marketplace subgraph returns it."""
    mech: Dict[str, Any] = {
        "address": MECH,
        "maxDeliveryRate": str(RATE),
        "paymentType": NATIVE.upper().replace("0X", "0x"),
        "receivedRequests": "644",
        "selfDeliveredFromReceived": "635",
        "deliveredByOthersFromReceived": "0",
        "service": {"id": "3786"},
    }
    mech.update(overrides)
    return {
        "data": {
            "meches": [mech],
            "createMeches": [{"blockTimestamp": str(CREATED)}],
        }
    }


def _card(**overrides: Any) -> Dict[str, Any]:
    """An agent card for the mech, as the marketplace serves it."""
    card: Dict[str, Any] = {
        "name": "Bob's mech",
        "skills": [{"name": "superforcaster"}, {"name": "factual_research"}],
        "provider": None,
        "metadata": {"mechAddress": f"eip155:100:{MECH}"},
    }
    card.update(overrides)
    return card


class _Context:
    """A skill context that records outgoing messages and lets a test answer them."""

    def __init__(self) -> None:
        self.params = SimpleNamespace(
            mech_chain_id="gnosis", marketplace_api_url=MARKETPLACE_API
        )
        self.mechs_subgraph = SimpleNamespace(url=SUBGRAPH)
        self.default_ledger_id = "ethereum"
        self.requests = SimpleNamespace(request_id_to_callback={})
        self.logger = MagicMock()
        self.outbox = MagicMock()
        self.sent: List[Dict[str, Any]] = []
        self.http_dialogues = SimpleNamespace(create=self._create)
        self.contract_api_dialogues = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        nonce = f"nonce-{len(self.sent)}"
        self.sent.append(kwargs)
        dialogue = SimpleNamespace(
            dialogue_label=SimpleNamespace(dialogue_reference=(nonce, ""))
        )
        return SimpleNamespace(**kwargs), dialogue

    def answer(self, body: Any, status: int = 200) -> None:
        """Reply to the oldest unanswered request with an HTTP response."""
        self.reply(SimpleNamespace(status_code=status, body=json.dumps(body).encode()))

    def reply(self, message: Any) -> None:
        """Hand a reply to the callback of the oldest unanswered request."""
        nonce = next(iter(self.requests.request_id_to_callback))
        callback = self.requests.request_id_to_callback.pop(nonce)
        callback(message, None)

    def query(self, index: int) -> str:
        """The GraphQL query sent in the request at `index`."""
        return json.loads(self.sent[index]["body"])["query"]


def _read(context: _Context, address: str = MECH) -> List[MechFacts]:
    """Start a read and return the list the callback appends to."""
    results: List[MechFacts] = []
    read_mech(context, address, results.append)
    return results


def _state(body: Dict[str, Any], performative: Any = None) -> Any:
    """A contract API reply."""
    return SimpleNamespace(
        performative=performative or ContractApiMessage.Performative.STATE,
        state=SimpleNamespace(body=body),
    )


def test_a_mech_is_read_from_its_record_and_then_its_card() -> None:
    """Two reads, in order, and every fact is taken from them."""
    context = _Context()
    results = _read(context, MECH.upper().replace("0X", "0x"))
    assert results == []
    context.answer(_record())
    context.answer(_card())

    assert [request["url"] for request in context.sent] == [SUBGRAPH, CARD_URL]
    assert context.sent[0]["counterparty"] == str(HTTP_CLIENT_PUBLIC_ID)
    assert context.sent[0]["method"] == "POST"
    assert f'address: "{MECH}"' in context.query(0)
    assert context.sent[1]["method"] == "GET"
    assert context.outbox.put_message.call_count == 2
    assert results == [
        MechFacts(
            address=MECH,
            chain_id=100,
            record=RecordStatus.FOUND,
            card_read=True,
            payment_type=NATIVE,
            max_delivery_rate=RATE,
            service_id=3786,
            received_requests=644,
            self_delivered=635,
            delivered_by_others=0,
            created_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            name="Bob's mech",
            tools=("superforcaster", "factual_research"),
        )
    ]


def test_a_card_with_a_provider_names_a_verified_operator() -> None:
    """The marketplace sets `provider` only when the operator's domain proof holds."""
    context = _Context()
    results = _read(context)
    context.answer(_record())
    context.answer(
        _card(provider={"organization": "Bob Labs", "url": "https://Bob.example/x"})
    )
    facts = results[0]
    assert facts.operator_verified is True
    assert (facts.operator_name, facts.operator_domain) == ("Bob Labs", "bob.example")


@pytest.mark.parametrize(
    "body, status",
    [
        ({"error": "No metadata found"}, 404),
        ({"error": "Failed to fetch metadata from IPFS"}, 502),
        (["not", "an", "object"], 200),
        (_card(metadata={"mechAddress": "eip155:100:0x" + "ab" * 20}), 200),
        (_card(metadata=None), 200),
    ],
)
def test_a_card_that_is_missing_or_about_another_mech_is_not_read(
    body: Any, status: int
) -> None:
    """The record's facts are kept and the card is marked unread."""
    context = _Context()
    results = _read(context)
    context.answer(_record())
    context.answer(body, status=status)
    facts = results[0]
    assert facts.record is RecordStatus.FOUND
    assert facts.card_read is False
    assert (facts.name, facts.tools, facts.operator_verified) == (None, (), False)
    assert facts.max_delivery_rate == RATE


@pytest.mark.parametrize(
    "body, status, record",
    [
        ({"data": {"meches": [], "createMeches": []}}, 200, RecordStatus.NOT_INDEXED),
        ({"errors": [{"message": "boom"}]}, 200, RecordStatus.UNAVAILABLE),
        ({"data": None}, 200, RecordStatus.UNAVAILABLE),
        ({}, 503, RecordStatus.UNAVAILABLE),
    ],
)
def test_without_a_record_no_card_is_read(
    body: Any, status: int, record: RecordStatus
) -> None:
    """Not indexed and unreadable are told apart, and the card is never requested."""
    context = _Context()
    results = _read(context)
    context.answer(body, status=status)
    assert results == [MechFacts(MECH, 100, record)]
    assert len(context.sent) == 1


def test_a_record_without_a_service_is_returned_without_a_card() -> None:
    """No service id, no card URL."""
    context = _Context()
    results = _read(context)
    context.answer(_record(service=None))
    assert results[0].record is RecordStatus.FOUND
    assert results[0].card_read is False
    assert len(context.sent) == 1


def test_a_null_price_and_odd_values_read_as_unknown() -> None:
    """A subgraph without a price for the mech, or with junk in a counter, gives `None`."""
    context = _Context()
    results = _read(context)
    context.answer(_record(maxDeliveryRate=None, receivedRequests=True, paymentType=7))
    context.answer(_card())
    facts = results[0]
    assert (facts.max_delivery_rate, facts.received_requests, facts.payment_type) == (
        None,
        None,
        None,
    )


@pytest.mark.parametrize("address", ['0x" } evil', "0xabc", "", "bob.eth"])
def test_an_address_that_is_not_an_address_is_never_sent(address: str) -> None:
    """The address goes into a query, so anything but `0x` + 40 hex stops here."""
    context = _Context()
    results = _read(context, address)
    assert results[0].record is RecordStatus.NOT_INDEXED
    assert context.sent == []


def test_tools_are_cleaned_and_capped() -> None:
    """Card text is untrusted: control characters and blanks are dropped, the list is capped."""
    skills: List[Any] = [{"name": f"tool-{i}"} for i in range(MAX_TOOLS + 5)]
    skills[0] = {"name": "bad\u0000tool"}
    skills[1] = {"name": "   "}
    skills[2] = "not an object"
    context = _Context()
    results = _read(context)
    context.answer(_record())
    context.answer(_card(skills=skills))
    tools = results[0].tools
    assert len(tools) == MAX_TOOLS
    assert "bad\u0000tool" not in tools and "" not in tools


def test_the_live_rate_is_read_through_the_default_ledger_with_the_chain_as_kwarg() -> (
    None
):
    """The ledger connection keys its APIs by ledger type and picks the chain from kwargs."""
    context = _Context()
    results: List[Optional[int]] = []
    read_live_rate(context, MECH, results.append)
    request = context.sent[0]
    assert request["counterparty"] == LEDGER_API_ADDRESS
    assert request["performative"] == ContractApiMessage.Performative.GET_STATE
    assert request["ledger_id"] == "ethereum"
    assert request["kwargs"].body == {"chain_id": "gnosis"}
    assert request["contract_id"] == "valory/mech_mm:0.1.0"
    assert request["contract_address"] == MECH
    assert request["callable"] == "get_max_delivery_rate"
    context.reply(_state({"max_delivery_rate": RATE}))
    assert results == [RATE]
    context.logger.warning.assert_not_called()


@pytest.mark.parametrize(
    "reply",
    [
        _state({}, ContractApiMessage.Performative.ERROR),
        _state({"max_delivery_rate": RATE}, ContractApiMessage.Performative.ERROR),
        _state({}),
        _state({"max_delivery_rate": "many"}),
        _state({"max_delivery_rate": True}),
    ],
)
def test_an_unreadable_live_rate_is_none_and_logged(reply: Any) -> None:
    """Anything but a STATE reply with an integer gives `None`, with the reply logged."""
    context = _Context()
    results: List[Optional[int]] = []
    read_live_rate(context, MECH, results.append)
    context.reply(reply)
    assert results == [None]
    context.logger.warning.assert_called_once()


def test_a_live_rate_for_a_non_address_is_not_requested() -> None:
    """Same address rule as the record read."""
    context = _Context()
    results: List[Optional[int]] = []
    read_live_rate(context, "0x123", results.append)
    assert results == [None]
    assert context.sent == []


@pytest.mark.parametrize(
    "network, chain_id",
    [
        ("gnosis", 100),
        ("Polygon", 137),
        ("base", 8453),
        ("optimism", 10),
        ("moon", None),
    ],
)
def test_chain_ids_come_from_the_chain_name(
    network: str, chain_id: Optional[int]
) -> None:
    """Known chains map to their ids; an unknown name maps to none."""
    assert chain_id_of(network) == chain_id


def test_card_url_is_built_from_the_api_root() -> None:
    """A trailing slash on the configured root does not double up."""
    assert agent_card_url(MARKETPLACE_API + "/", "gnosis", 3786) == CARD_URL
    assert DEFAULT_MARKETPLACE_API_URL == "https://marketplace.olas.network/api/erc8004"


@pytest.mark.parametrize(
    "value, text",
    [(" Bob ", "Bob"), ("", None), ("a\u0007b", None), (7, None), ("x" * 2049, None)],
)
def test_clean_text(value: Any, text: Optional[str]) -> None:
    """Printable, stripped, non-empty and bounded, or nothing."""
    assert clean_text(value) == text


@pytest.mark.parametrize(
    "value, host",
    [
        ("https://Bob.example/a", "bob.example"),
        ("https://[bad", None),
        ("no host", None),
    ],
)
def test_url_host(value: Any, host: Optional[str]) -> None:
    """A malformed URL has no host instead of raising."""
    assert url_host(value) == host


def _subgraph_mech(address: str, rate: Any, metadata: bool = True) -> Dict[str, Any]:
    """A discovery entry from the marketplace subgraph."""
    return {
        "id": address,
        "address": address,
        "maxDeliveryRate": rate,
        "karma": "0",
        "receivedRequests": "1",
        "selfDeliveredFromReceived": "1",
        "service": {
            "metadata": [{"metadata": "0xabc"}] if metadata else [],
            "deliveries": [],
        },
    }


def test_discovery_leaves_out_a_mech_without_a_price_instead_of_failing() -> None:
    """A null subgraph price cannot be bounded, so that mech alone is skipped."""
    priced = "0x" + "aa" * 20
    unpriced = "0x" + "bb" * 20
    no_metadata = "0x" + "cc" * 20
    kept, left_out = priced_mechs_with_metadata(
        [
            _subgraph_mech(priced, str(RATE)),
            _subgraph_mech(unpriced, None),
            _subgraph_mech(no_metadata, str(RATE), metadata=False),
        ]
    )
    assert [mech.address for mech in kept] == [priced]
    assert left_out == [unpriced]
