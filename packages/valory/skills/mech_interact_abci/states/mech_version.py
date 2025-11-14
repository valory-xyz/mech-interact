# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2025 Valory AG
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

"""This module contains the mech version detection state of the mech interaction abci app."""

from enum import Enum
from typing import Optional, Tuple, cast

from packages.valory.skills.abstract_round_abci.base import (
    BaseSynchronizedData,
    VotingRound,
    get_name,
)
from packages.valory.skills.mech_interact_abci.payloads import VotingPayload
from packages.valory.skills.mech_interact_abci.states.base import (
    Event,
    SynchronizedData,
)


class MechVersionDetectionRound(VotingRound):
    """A round for voting on the mech marketplace's version."""

    payload_class = VotingPayload
    synchronized_data_class = SynchronizedData
    done_event = Event.V2
    negative_event = Event.V1
    none_event = Event.NO_MARKETPLACE
    no_majority_event = Event.NO_MAJORITY
    collection_key = get_name(SynchronizedData.participant_to_votes)

    def end_block(self) -> Optional[Tuple[BaseSynchronizedData, Enum]]:
        """Process the end of the block."""
        res = super().end_block()
        if res is None:
            return None

        synced_data, event = cast(Tuple[SynchronizedData, Enum], res)

        if event in (Event.V2, Event.V1, Event.NO_MARKETPLACE):
            is_v2 = None if event == Event.NO_MARKETPLACE else event == Event.V2
            synced_data = synced_data.update(
                synchronized_data_class=self.synchronized_data_class,
                **{get_name(SynchronizedData.is_marketplace_v2): is_v2},
            )

        return synced_data, event
