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

"""This module contains the purchase subscription state of the mech interaction abci app."""

from packages.valory.skills.abstract_round_abci.base import get_name
from packages.valory.skills.mech_interact_abci.payloads import PrepareTxPayload
from packages.valory.skills.mech_interact_abci.states.base import (
    Event,
    MechInteractionRound,
    SynchronizedData,
)


class MechPurchaseSubscriptionRound(MechInteractionRound):
    """A round for preparing a tx to purchase an NVM subscription."""

    payload_class = PrepareTxPayload
    selection_key = (
        get_name(SynchronizedData.tx_submitter),
        get_name(SynchronizedData.most_voted_tx_hash),
    )
    collection_key = get_name(SynchronizedData.participant_to_purchase)
    none_event = Event.NONE
