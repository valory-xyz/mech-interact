# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2023-2026 Valory AG
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

"""This module contains the base functionality for the rounds of the mech interact abci app."""

import json
import math
import time
from dataclasses import InitVar, asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Set, Type, Union, cast

from packages.valory.skills.abstract_round_abci.base import (
    BaseTxPayload,
    CollectSameUntilThresholdRound,
    CollectionRound,
)
from packages.valory.skills.mech_interact_abci.payloads import (
    JSONPayload,
    MechRequestPayload,
    PrepareTxPayload,
)
from packages.valory.skills.transaction_settlement_abci.rounds import (
    SynchronizedData as TxSynchronizedData,
)

SERIALIZED_EMPTY_LIST = "[]"
METADATA_FIELD = "metadata"
BLOCK_TIMESTAMP_FIELD = "blockTimestamp"
METADATA_PREFIX_SIZE = 2
MACHINE_EPS = 1e-9
HALF_LIFE_SECONDS = 60 * 60
DELIVERY_RATE_METRIC_WEIGHT = 0.1
LIVENESS_METRIC_WEIGHT = 0.45
DELIVERED_RATIO_METRIC_WEIGHT = 0.45
LAPLACE_SMOOTHING_ALPHA = 8
LAPLACE_SMOOTHING_BETA = 1
COLD_START_LIVENESS = LAPLACE_SMOOTHING_ALPHA / (
    LAPLACE_SMOOTHING_ALPHA + LAPLACE_SMOOTHING_BETA
)

# Off-chain dispatch `last_failure_reason` values, surfaced so operators and
# downstream skills can branch on a stable label when the off-chain path
# exhausts its options (see the off-chain request/response behaviours).
OFFCHAIN_ALL_FAILED = "offchain_all_failed"
OFFCHAIN_402_INSUFFICIENT = "offchain_402_insufficient"
OFFCHAIN_503_ALL_MECHS = "offchain_503_all_mechs"
OFFCHAIN_TIMEOUT_ALL_MECHS = "offchain_timeout_all_mechs"
OFFCHAIN_BAD_RESPONSE = "offchain_bad_response"

NestedSubgraphItemType = List[Dict[str, Any]]


class Event(Enum):
    """MechInteractAbciApp Events"""

    DONE = "done"
    NONE = "none"
    V1 = "v1"
    V2 = "v2"
    NO_MARKETPLACE = "no_marketplace"
    NO_MAJORITY = "no_majority"
    ROUND_TIMEOUT = "round_timeout"
    SKIP_REQUEST = "skip_request"
    BUY_SUBSCRIPTION = "buy_subscription"
    # Offchain dispatch events. Emitted by ``MechRequestRound.end_block`` when
    # the request behaviour took the offchain HTTP path (use_offchain=true) and
    # produced one of the three offchain outcomes.
    OFFCHAIN_DONE = "offchain_done"
    OFFCHAIN_DEPOSIT_NEEDED = "offchain_deposit_needed"
    OFFCHAIN_ALL_FAILED = "offchain_all_failed"


@dataclass
class MechMetadata:
    """A Mech's metadata."""

    prompt: str
    tool: str
    nonce: str
    schema_version: str = "2.0"
    request_context: Optional[Dict[str, Any]] = None
    # Extra tool parameters, merged into the request payload top-level (next to
    # prompt/tool/nonce) so the tool receives them as run() kwargs. Mirrors the
    # mech-client `extra_attributes` channel. Defaults to None for back-compat.
    extra_attributes: Optional[Dict[str, Any]] = None


@dataclass
class MechRequest:
    """A Mech's request.

    ``data`` is ``bytes`` when populated from a parsed contract event and
    ``str`` (hex-encoded) when carried through synchronized data across
    rounds; both forms are handled by the consumers.
    """

    data: Union[str, bytes] = ""
    requestId: int = 0
    requestIds: List[int] = field(default_factory=list)
    numRequests: int = 0


MECH_RESPONSE = "mech_response"


@dataclass
class MechInteractionResponse(MechRequest):
    """A structure for the response of a mech interaction task."""

    nonce: str = ""
    result: Optional[str] = None
    error: str = "Unknown"
    response_data: Optional[bytes] = None
    sender_address: Optional[str] = None

    def retries_exceeded(self) -> None:
        """Set an incorrect format response."""
        self.error = "Retries were exceeded while trying to get the mech's response."

    def incorrect_format(self, res: Any) -> None:
        """Set an incorrect format response."""
        self.error = f"The response's format was unexpected: {res}"


@dataclass
class Service:
    """Structure for a Service."""

    metadata: NestedSubgraphItemType
    deliveries: NestedSubgraphItemType

    @staticmethod
    def _get_nested_item(nested: NestedSubgraphItemType, access_field: str) -> Any:
        """Get a nested subgraph item."""
        item = nested[0] if nested else None
        if item is None:
            return None
        return item.get(access_field, None)

    @property
    def metadata_str(self) -> Optional[str]:
        """Return un-nested metadata string."""
        metadata_hex = self._get_nested_item(self.metadata, METADATA_FIELD)
        if metadata_hex is None:
            return None
        return metadata_hex[METADATA_PREFIX_SIZE:]

    @property
    def last_delivered(self) -> Optional[int]:
        """Return the last delivered block timestamp."""
        timestamp = self._get_nested_item(self.deliveries, BLOCK_TIMESTAMP_FIELD)
        if timestamp is None:
            return None

        try:
            return int(timestamp)
        except (ValueError, TypeError):
            return None

    @property
    def liveness(self) -> float:
        """Return the liveness of the service."""
        if not self.last_delivered:
            return 0

        # using exponential decay to make day-scale differences meaningful.
        now = int(time.time())
        age = max(0, now - self.last_delivered)
        # taf is a time constant that depends on half-life (time for score to halve)
        # half-life can be tuned so that 1 day, 1 week, etc. map to desirable scores.
        taf = HALF_LIFE_SECONDS / math.log(2)
        return math.exp(-age / taf)


@dataclass
class MechInfo:
    """Structure for the Mech information."""

    id: str
    address: str
    service: Service
    karma: int
    receivedRequests: InitVar[int] = 0
    selfDeliveredFromReceived: InitVar[int] = 0
    maxDeliveryRate: InitVar[int] = 0
    received_requests: int = 0
    self_delivered: int = 0
    max_delivery_rate: int = 0
    relevant_tools: Set[str] = field(default_factory=set)
    # Offchain HTTP URL published by the mech operator in the IPFS metadata
    # manifest under the ``url`` key (see mech-deployments ``make
    # update-metadata`` flow). None for mechs whose manifest predates the
    # offchain rollout; consumers fall back to the static ``offchain_url``
    # config when None.
    http_url: Optional[str] = None

    def __post_init__(
        self,
        receivedRequests: int,
        selfDeliveredFromReceived: int,
        maxDeliveryRate: int,
    ) -> None:
        """Handle camelCase fields, serialize service if passed as a dict and ensure int values."""
        if isinstance(self.service, dict):
            self.service = Service(**self.service)

        if isinstance(self.relevant_tools, (list, tuple)):
            self.relevant_tools = set(self.relevant_tools)

        case_convertion_mapping = {
            "received_requests": receivedRequests,
            "self_delivered": selfDeliveredFromReceived,
            "max_delivery_rate": maxDeliveryRate,
        }
        for snake_name, value in case_convertion_mapping.items():
            # if already given in snake case, ignore camel case input
            if getattr(self, snake_name) != 0:
                return

            try:
                setattr(self, snake_name, int(value))
            except (ValueError, TypeError):
                raise ValueError(
                    f"Unexpected non-int {value=} received as {snake_name!r} for mech with id {self.id}."
                )

        try:
            self.karma = int(self.karma)
        except (ValueError, TypeError):
            raise ValueError(
                f"Unexpected non-int {self.karma=} received for mech with id {self.id}."
            )

    def __lt__(self, other: "MechInfo") -> bool:
        """Compare two `MechInfo` objects."""

        def score(instance: "MechInfo") -> float:
            """Score a mech's state."""
            filters = (
                DELIVERY_RATE_METRIC_WEIGHT * instance.delivery_rate_metric,
                LIVENESS_METRIC_WEIGHT * instance.liveness,
                DELIVERED_RATIO_METRIC_WEIGHT * instance.delivered_ratio_smoothed,
            )
            return sum(filters)

        s1 = score(self)
        s2 = score(other)

        # floating-point equality is unreliable, therefore, using the abs of the diff and comparing with a tiny value
        if abs(s1 - s2) < MACHINE_EPS:
            return self.karma < other.karma

        return s1 < s2

    @property
    def delivery_rate_metric(self) -> float:
        """Return the delivery rate metric."""
        return 1 / (1 + math.log(self.max_delivery_rate))

    @property
    def empty_metadata(self) -> bool:
        """Return whether the metadata is empty."""
        return self.service.metadata_str is None

    @property
    def liveness(self) -> float:
        """The liveness of the mech."""
        if self.received_requests == 0:
            return COLD_START_LIVENESS
        return self.service.liveness

    @property
    def delivered_ratio(self) -> float:
        """Ratio of the self-delivered requests to the received requests."""
        if self.received_requests == 0:
            return 0.0
        return self.self_delivered / self.received_requests

    @property
    def delivered_ratio_smoothed(self) -> float:
        """Ratio of the self-delivered requests to the received requests, Laplace smoothed to improve cold start."""
        return (self.self_delivered + LAPLACE_SMOOTHING_ALPHA) / (
            self.received_requests + LAPLACE_SMOOTHING_ALPHA + LAPLACE_SMOOTHING_BETA
        )


MechsInfo = List[MechInfo]


class MechInfoEncoder(json.JSONEncoder):
    """A custom JSON encoder for the MechInfo."""

    def default(self, obj: Any) -> Any:
        """The default JSON encoder."""
        if is_dataclass(obj) and not isinstance(obj, type):
            return asdict(obj)

        # convert relevant_tools set to list as JSON doesn't support sets
        if isinstance(obj, set):
            return list(obj)

        return super().default(obj)


class SynchronizedData(TxSynchronizedData):
    """
    Class to represent the synchronized data.

    This data is replicated by the tendermint application.
    """

    @property
    def mechs_info(self) -> MechsInfo:
        """Get the mechs' information."""
        mechs_info = self.db.get("mechs_info", SERIALIZED_EMPTY_LIST)
        if isinstance(mechs_info, str):
            mechs_info = json.loads(mechs_info)
        return [MechInfo(**item) for item in (mechs_info or [])]

    @property
    def mech_tool(self) -> str:
        """Get the selected mech tool."""
        return str(self.db.get_strict("mech_tool"))

    @property
    def selected_mechs(self) -> List[str]:
        """Get the consumer-pinned mech addresses (lowercase). Empty means no pin.

        A malformed value in the db key (wrong shape or invalid JSON) returns
        an empty list rather than raising on every subsequent round until
        the key is cleared.

        :return: lowercase mech addresses.
        """
        raw = self.db.get("selected_mechs", SERIALIZED_EMPTY_LIST)
        try:
            if isinstance(raw, str):
                raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(raw, list):
            return []
        return [str(addr).lower() for addr in raw]

    @property
    def relevant_mechs_info(self) -> MechsInfo:
        """Get the relevant mechs' information.

        :return: mechs that serve the selected tool. If a consumer has pinned
            a subset of mechs via `selected_mechs`, the result is further
            restricted to that subset.
        """
        pinned = self.selected_mechs
        return [
            info
            for info in self.mechs_info
            if self.mech_tool in info.relevant_tools
            and (not pinned or info.address.lower() in pinned)
        ]

    @property
    def mech_tools(self) -> Set[str]:
        """Get the mechs' tools.

        Mirrors `relevant_mechs_info`: when `selected_mechs` is non-empty,
        only tools served by pinned mechs are exposed. Otherwise the
        consumer could pick a tool that no eligible mech serves and the
        round would dead-end at request prep.

        :return: tool names served by at least one eligible mech.
        """
        pinned = self.selected_mechs
        return {
            tool
            for mech_info in self.mechs_info
            if not pinned or mech_info.address.lower() in pinned
            for tool in mech_info.relevant_tools
        }

    @property
    def priority_mech(
        self,
    ) -> Optional[MechInfo]:
        """Get the priority mech."""
        if self.relevant_mechs_info:
            return max(self.relevant_mechs_info)
        return None

    @property
    def priority_mech_address(
        self,
    ) -> Optional[str]:
        """Get the priority mech's address."""
        priority_mech = self.priority_mech
        if priority_mech:
            return priority_mech.address
        return None

    @property
    def ranked_mechs(
        self,
    ) -> MechsInfo:
        """Get the mechs ranked from the best to the worse."""
        relevant_mechs_info = self.relevant_mechs_info
        if relevant_mechs_info:
            return sorted(relevant_mechs_info, reverse=True)
        return []

    @property
    def ranked_mechs_addresses(
        self,
    ) -> List[str]:
        """Get the priority mech's address."""
        ranked_mechs = self.ranked_mechs
        if ranked_mechs:
            return [mech.address for mech in ranked_mechs]
        return []

    @property
    def mech_price(self) -> int:
        """Get the mech's request price."""
        return int(self.db.get_strict("mech_price"))

    @property
    def mech_requests(self) -> List[MechMetadata]:
        """Get the mech requests."""
        requests = self.db.get("mech_requests", SERIALIZED_EMPTY_LIST)
        if isinstance(requests, str):
            requests = json.loads(requests)
        return [MechMetadata(**metadata_item) for metadata_item in (requests or [])]

    @property
    def mech_responses(self) -> List[MechInteractionResponse]:
        """Get the mech responses."""
        responses = self.db.get("mech_responses", SERIALIZED_EMPTY_LIST)
        if isinstance(responses, str):
            responses = json.loads(responses)
        return [
            MechInteractionResponse(**response_item)
            for response_item in (responses or [])
        ]

    @property
    def participant_to_info(self) -> Mapping[str, JSONPayload]:
        """Get the `participant_to_info`."""
        serialized = self.db.get_strict("participant_to_info")
        deserialized = CollectionRound.deserialize_collection(serialized)
        return cast(Mapping[str, JSONPayload], deserialized)

    @property
    def participant_to_requests(self) -> Mapping[str, MechRequestPayload]:
        """Get the `participant_to_requests`."""
        serialized = self.db.get_strict("participant_to_requests")
        deserialized = CollectionRound.deserialize_collection(serialized)
        return cast(Mapping[str, MechRequestPayload], deserialized)

    @property
    def participant_to_responses(self) -> Mapping[str, JSONPayload]:
        """Get the `participant_to_responses`."""
        serialized = self.db.get_strict("participant_to_responses")
        deserialized = CollectionRound.deserialize_collection(serialized)
        return cast(Mapping[str, JSONPayload], deserialized)

    @property
    def participant_to_purchase(self) -> Mapping[str, PrepareTxPayload]:
        """Get the `participant_to_purchase`."""
        serialized = self.db.get_strict("participant_to_purchase")
        deserialized = CollectionRound.deserialize_collection(serialized)
        return cast(Mapping[str, PrepareTxPayload], deserialized)

    @property
    def final_tx_hash(self) -> Optional[str]:  # type: ignore[override]
        """Get the verified tx hash."""
        return cast(Optional[str], self.db.get("final_tx_hash", None))

    @property
    def chain_id(self) -> Optional[str]:
        """Get the chain name where to send the transactions."""
        return cast(str, self.db.get("chain_id", None))

    @property
    def tx_submitter(self) -> str:
        """Get the round that submitted a tx to transaction_settlement_abci."""
        return str(self.db.get_strict("tx_submitter"))

    @property
    def versioning_check_performed(self) -> bool:
        """Whether the marketplace versioning check has been performed."""
        return bool(self.db.get("is_marketplace_v2", None) is not None)

    @property
    def is_marketplace_v2(self) -> Optional[bool]:
        """Whether a marketplace V2 is used. True if v2, False if v1, None if no marketplace is used."""
        return self.db.get_strict("is_marketplace_v2")

    @property
    def offchain_result(self) -> Optional[str]:
        """Outcome label emitted by the offchain path of the request behaviour.

        One of ``offchain_done``, ``offchain_deposit_needed``,
        ``offchain_all_failed``, or ``None`` when the on-chain path ran.
        ``MechRequestRound.end_block`` reads this to dispatch to the right
        ``Event`` variant. Persisted as a string so the value flows through
        the standard payload/selection-key path.
        """
        return cast(Optional[str], self.db.get("offchain_result", None))

    @property
    def offchain_pending_request(self) -> Optional[Dict[str, Any]]:
        """Serialized state of the in-flight offchain request awaiting deposit.

        Populated when the previous attempt returned a structured 402 and the
        FSM is now settling the deposit multisend. On re-entry the behaviour
        reads this to reuse the same ``request_id``, signature, nonce, and
        target mech for the retry POST, so the original signed binding stays
        valid against the contract's monotonic ``mapNonces``.
        """
        raw = self.db.get("offchain_pending_request", None)
        if raw is None:
            return None
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return None
            if isinstance(parsed, dict):
                return parsed
            return None
        if isinstance(raw, dict):
            return raw
        return None

    @property
    def offchain_last_failure_reason(self) -> Optional[str]:
        """Label set when an offchain cycle exhausted its retries.

        Matches one of the module-level ``OFFCHAIN_*`` constants
        (``OFFCHAIN_ALL_FAILED``, ``OFFCHAIN_402_INSUFFICIENT``,
        ``OFFCHAIN_503_ALL_MECHS``, ``OFFCHAIN_TIMEOUT_ALL_MECHS``). Used by
        downstream rounds and consumer agents to surface why the FSM is
        leaving the offchain branch.
        """
        return cast(Optional[str], self.db.get("offchain_last_failure_reason", None))


class MechInteractionRound(CollectSameUntilThresholdRound):
    """A base round for the mech interactions."""

    payload_class: Type[BaseTxPayload] = BaseTxPayload
    synchronized_data_class = SynchronizedData
    done_event = Event.DONE
    no_majority_event = Event.NO_MAJORITY
    extended_requirements = ()
