# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2023-2025 Valory AG
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
from dataclasses import InitVar, asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Set, Type, cast

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
METADATA_PREFIX_SIZE = 2


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


@dataclass
class MechMetadata:
    """A Mech's metadata."""

    prompt: str
    tool: str
    nonce: str


@dataclass
class MechRequest:
    """A Mech's request."""

    data: str = ""
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

    metadata: List[Dict[str, str]]

    @property
    def metadata_str(self) -> Optional[str]:
        """Return un-nested metadata string."""
        metadata = self.metadata[0] if self.metadata else None
        if metadata is None:
            return None
        return metadata.get(METADATA_FIELD, None)[METADATA_PREFIX_SIZE:]


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

    def __post_init__(
        self,
        receivedRequests: int,
        selfDeliveredFromReceived: int,
        maxDeliveryRate: int,
    ) -> None:
        """Handle camelCase fields and serialize service if passed as a dict."""
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

    def __lt__(self, other: "MechInfo") -> bool:
        """Compare two `MechInfo` objects."""
        if self.max_delivery_rate != other.max_delivery_rate:
            return self.max_delivery_rate > other.max_delivery_rate

        delivered_ratio = self.delivered_ratio
        other_delivered_ratio = other.delivered_ratio
        if delivered_ratio != other_delivered_ratio:
            return delivered_ratio < other_delivered_ratio

        return self.karma < other.karma

    @property
    def empty_metadata(self) -> bool:
        """Return whether the metadata is empty."""
        return self.service.metadata_str is None

    @property
    def delivered_ratio(self) -> float:
        """Return the ratio of the self delivered requests to the total received requests."""
        return self.self_delivered / self.received_requests


MechsInfo = List[MechInfo]


class SynchronizedData(TxSynchronizedData):
    """
    Class to represent the synchronized data.

    This data is replicated by the tendermint application.
    """

    @property
    def mechs_info(self) -> MechsInfo:
        """Get the mechs' information."""
        mech_info = self.db.get("mech_info", SERIALIZED_EMPTY_LIST)
        if isinstance(mech_info, str):
            mech_info = json.loads(mech_info)
        return [MechInfo(**item) for item in mech_info]

    @property
    def relevant_mechs_info(self) -> MechsInfo:
        """Get the relevant mechs' information."""
        return [info for info in self.mechs_info if info.relevant_tools]

    @property
    def mech_tools(self) -> Set[str]:
        """Get the mechs' tools."""
        return {
            tool for mech_info in self.mechs_info for tool in mech_info.relevant_tools
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
        if self.priority_mech:
            return self.priority_mech.address
        return None

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
        return [MechMetadata(**metadata_item) for metadata_item in requests]

    @property
    def mech_responses(self) -> List[MechInteractionResponse]:
        """Get the mech responses."""
        responses = self.db.get("mech_responses", SERIALIZED_EMPTY_LIST)
        if isinstance(responses, str):
            responses = json.loads(responses)
        return [MechInteractionResponse(**response_item) for response_item in responses]

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
    def final_tx_hash(self) -> Optional[str]:
        """Get the verified tx hash."""
        return cast(str, self.db.get("final_tx_hash", None))

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


class MechInteractionRound(CollectSameUntilThresholdRound):
    """A base round for the mech interactions."""

    payload_class: Type[BaseTxPayload] = BaseTxPayload
    synchronized_data_class = SynchronizedData
    done_event = Event.DONE
    no_majority_event = Event.NO_MAJORITY
    extended_requirements = ()
