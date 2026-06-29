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

"""This module contains the request state of the mech interaction abci app."""

from enum import Enum
from typing import Optional, Tuple, cast

from packages.valory.skills.abstract_round_abci.base import (
    BaseSynchronizedData,
    get_name,
)
from packages.valory.skills.mech_interact_abci.payloads import MechRequestPayload
from packages.valory.skills.mech_interact_abci.states.base import (
    Event,
    MechInteractionRound,
    SynchronizedData,
)

# Sentinel ``tx_submitter`` the off-chain deposit retry path stamps onto
# ``OffchainCycleResult.tx_submitter``. Consumer multiplexers (e.g.
# ``liquidity_trader_abci.PostTxSettlementRound``) key off this value to
# route the settled deposit tx back into ``MechRequestRound`` — where
# ``_retry_pending`` runs the cached POST — instead of treating it like a
# normal mech request tx (which dispatches forward to ``MechResponseRound``).
# The value is intentionally distinct from ``MechRequestRound.auto_round_id()``
# (``"mech_request_round"``) so the multiplexer can tell the two cases apart.
OFFCHAIN_DEPOSIT_TX_SUBMITTER = "mech_request_round_offchain_deposit"

# Key the dispatch off the enum's own ``value`` rather than free strings:
# a future rename of the event symbol fails type-checking instead of silently
# falling through to the on-chain ``DONE`` / ``SKIP_REQUEST`` branch.
_OFFCHAIN_RESULT_TO_EVENT = {
    Event.OFFCHAIN_DONE.value: Event.OFFCHAIN_DONE,
    Event.OFFCHAIN_DEPOSIT_NEEDED.value: Event.OFFCHAIN_DEPOSIT_NEEDED,
    Event.OFFCHAIN_ALL_FAILED.value: Event.OFFCHAIN_ALL_FAILED,
}


class MechRequestRound(MechInteractionRound):
    """A round for performing requests to a Mech."""

    payload_class = MechRequestPayload

    # Static anchors for ``autonomy analyse fsm-specs``; ``end_block``
    # dispatches the three off-chain events through ``_OFFCHAIN_RESULT_TO_EVENT``,
    # which the static analyser cannot resolve.
    offchain_done_event: Event = Event.OFFCHAIN_DONE
    offchain_deposit_needed_event: Event = Event.OFFCHAIN_DEPOSIT_NEEDED
    offchain_all_failed_event: Event = Event.OFFCHAIN_ALL_FAILED

    selection_key = (
        get_name(SynchronizedData.tx_submitter),
        get_name(SynchronizedData.most_voted_tx_hash),
        get_name(SynchronizedData.mech_price),
        get_name(SynchronizedData.chain_id),
        get_name(SynchronizedData.safe_contract_address),
        get_name(SynchronizedData.mech_requests),
        get_name(SynchronizedData.mech_responses),
        get_name(SynchronizedData.offchain_result),
        get_name(SynchronizedData.offchain_pending_request),
        get_name(SynchronizedData.offchain_last_failure_reason),
    )
    collection_key = get_name(SynchronizedData.participant_to_requests)
    none_event = Event.BUY_SUBSCRIPTION

    def end_block(self) -> Optional[Tuple[BaseSynchronizedData, Enum]]:
        """Process the end of the block."""
        res = super().end_block()

        if res is None:
            return None  # pragma: no cover

        synced_data, event = cast(Tuple[SynchronizedData, Enum], res)

        if event != Event.DONE:
            return res

        # Offchain dispatch. ``MechRequestBehaviour`` sets ``offchain_result``
        # on the payload when ``use_offchain=True``; the value drives which
        # ``OFFCHAIN_*`` event leaves the round so the FSM routes to the
        # right final state. ``None`` means the on-chain path ran today's
        # behaviour and the existing ``DONE`` / ``SKIP_REQUEST`` rules apply.
        offchain_event = _OFFCHAIN_RESULT_TO_EVENT.get(
            synced_data.offchain_result or ""
        )
        if offchain_event is not None:
            return synced_data, offchain_event

        if not (synced_data.mech_requests or synced_data.mech_responses):
            return synced_data, Event.SKIP_REQUEST

        return res
