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

"""This module contains the transaction payloads of the MechInteractAbciApp."""

from dataclasses import dataclass
from typing import Optional

from packages.valory.skills.abstract_round_abci.base import BaseTxPayload


@dataclass(frozen=True)
class PrepareTxPayload(BaseTxPayload):
    """Represent a transaction payload for a prepared transaction."""

    tx_submitter: Optional[str]
    tx_hash: Optional[str]


@dataclass(frozen=True)
class MechRequestPayload(PrepareTxPayload):
    """Represent a transaction payload for the MechRequestRound."""

    price: Optional[int]
    chain_id: Optional[str]
    safe_contract_address: Optional[str]
    mech_requests: Optional[str]
    mech_responses: Optional[str]
    # Offchain dispatch fields. None for the on-chain path (today's behaviour,
    # unchanged). One of ``offchain_done|offchain_deposit_needed|
    # offchain_all_failed`` when ``MechRequestBehaviour`` took the offchain
    # HTTP path. ``MechRequestRound.end_block`` reads ``offchain_result`` to
    # dispatch to the right ``Event`` variant; the other three carry the
    # state needed to either retry after a settled deposit, continue failover
    # across the ranked mech list, or surface a clean failure to the
    # consumer.
    offchain_result: Optional[str] = None
    offchain_attempted_mechs: Optional[str] = None
    offchain_pending_request: Optional[str] = None
    offchain_last_failure_reason: Optional[str] = None


@dataclass(frozen=True)
class JSONPayload(BaseTxPayload):
    """Represent a transaction payload for information serialized as a JSON string."""

    information: Optional[str]


@dataclass(frozen=True)
class VotingPayload(BaseTxPayload):
    """Represent a transaction payload for voting."""

    vote: Optional[bool]
