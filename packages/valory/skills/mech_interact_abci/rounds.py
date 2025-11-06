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

"""This package contains the rounds of MechInteractAbciApp."""

from typing import Dict, Set

from packages.valory.skills.abstract_round_abci.base import (
    AbciApp,
    AbciAppTransitionFunction,
    AppState,
    EventToTimeout,
    get_name,
)
from packages.valory.skills.mech_interact_abci.states.base import (
    Event,
    SynchronizedData,
)
from packages.valory.skills.mech_interact_abci.states.final_states import (
    FailedMechInformationRound,
    FinishedMarketplaceLegacyDetectedRound,
    FinishedMechInformationRound,
    FinishedMechLegacyDetectedRound,
    FinishedMechPurchaseSubscriptionRound,
    FinishedMechRequestRound,
    FinishedMechRequestSkipRound,
    FinishedMechResponseRound,
    FinishedMechResponseTimeoutRound,
)
from packages.valory.skills.mech_interact_abci.states.mech_info import (
    MechInformationRound,
)
from packages.valory.skills.mech_interact_abci.states.mech_version import (
    MechVersionDetectionRound,
)
from packages.valory.skills.mech_interact_abci.states.purchase_subscription import (
    MechPurchaseSubscriptionRound,
)
from packages.valory.skills.mech_interact_abci.states.request import MechRequestRound
from packages.valory.skills.mech_interact_abci.states.response import MechResponseRound


class MechInteractAbciApp(AbciApp[Event]):
    """MechInteractAbciApp

    Initial round: MechVersionDetectionRound

    Initial states: {MechRequestRound, MechResponseRound, MechVersionDetectionRound}

    Transition states:
        0. MechVersionDetectionRound
            - v2: 1.
            - v1: 5.
            - no marketplace: 6.
            - no majority: 0.
            - round timeout: 0.
        1. MechInformationRound
            - done: 7.
            - none: 8.
            - no majority: 1.
            - round timeout: 1.
        2. MechRequestRound
            - done: 9.
            - skip request: 12.
            - buy subscription: 3.
            - no majority: 2.
            - round timeout: 2.
        3. MechPurchaseSubscriptionRound
            - done: 13.
            - none: 3.
            - no majority: 3.
            - round timeout: 3.
        4. MechResponseRound
            - done: 10.
            - no majority: 4.
            - round timeout: 11.
        5. FinishedMarketplaceLegacyDetectedRound
        6. FinishedMechLegacyDetectedRound
        7. FinishedMechInformationRound
        8. FailedMechInformationRound
        9. FinishedMechRequestRound
        10. FinishedMechResponseRound
        11. FinishedMechResponseTimeoutRound
        12. FinishedMechRequestSkipRound
        13. FinishedMechPurchaseSubscriptionRound

    Final states: {FailedMechInformationRound, FinishedMarketplaceLegacyDetectedRound, FinishedMechInformationRound, FinishedMechLegacyDetectedRound, FinishedMechPurchaseSubscriptionRound, FinishedMechRequestRound, FinishedMechRequestSkipRound, FinishedMechResponseRound, FinishedMechResponseTimeoutRound}

    Timeouts:
        round timeout: 30.0
    """

    initial_round_cls: AppState = MechVersionDetectionRound
    initial_states: Set[AppState] = {
        MechVersionDetectionRound,
        MechRequestRound,
        MechResponseRound,
    }
    transition_function: AbciAppTransitionFunction = {
        MechVersionDetectionRound: {
            Event.V2: MechInformationRound,
            Event.V1: FinishedMarketplaceLegacyDetectedRound,
            Event.NO_MARKETPLACE: FinishedMechLegacyDetectedRound,
            Event.NO_MAJORITY: MechVersionDetectionRound,
            Event.ROUND_TIMEOUT: MechVersionDetectionRound,
        },
        MechInformationRound: {
            Event.DONE: FinishedMechInformationRound,
            Event.NONE: FailedMechInformationRound,
            Event.NO_MAJORITY: MechInformationRound,
            Event.ROUND_TIMEOUT: MechInformationRound,
        },
        MechRequestRound: {
            Event.DONE: FinishedMechRequestRound,
            Event.SKIP_REQUEST: FinishedMechRequestSkipRound,
            Event.BUY_SUBSCRIPTION: MechPurchaseSubscriptionRound,
            Event.NO_MAJORITY: MechRequestRound,
            Event.ROUND_TIMEOUT: MechRequestRound,
        },
        MechPurchaseSubscriptionRound: {
            Event.DONE: FinishedMechPurchaseSubscriptionRound,
            Event.NONE: MechPurchaseSubscriptionRound,
            Event.NO_MAJORITY: MechPurchaseSubscriptionRound,
            Event.ROUND_TIMEOUT: MechPurchaseSubscriptionRound,
        },
        MechResponseRound: {
            Event.DONE: FinishedMechResponseRound,
            Event.NO_MAJORITY: MechResponseRound,
            Event.ROUND_TIMEOUT: FinishedMechResponseTimeoutRound,
        },
        FinishedMarketplaceLegacyDetectedRound: {},
        FinishedMechLegacyDetectedRound: {},
        FinishedMechInformationRound: {},
        FailedMechInformationRound: {},
        FinishedMechRequestRound: {},
        FinishedMechResponseRound: {},
        FinishedMechResponseTimeoutRound: {},
        FinishedMechRequestSkipRound: {},
        FinishedMechPurchaseSubscriptionRound: {},
    }
    final_states: Set[AppState] = {
        FinishedMarketplaceLegacyDetectedRound,
        FinishedMechLegacyDetectedRound,
        FinishedMechInformationRound,
        FailedMechInformationRound,
        FinishedMechRequestRound,
        FinishedMechResponseRound,
        FinishedMechResponseTimeoutRound,
        FinishedMechRequestSkipRound,
        FinishedMechPurchaseSubscriptionRound,
    }
    event_to_timeout: EventToTimeout = {
        Event.ROUND_TIMEOUT: 30.0,
    }
    cross_period_persisted_keys: Set[str] = {get_name(SynchronizedData.mech_responses)}
    db_pre_conditions: Dict[AppState, Set[str]] = {
        MechVersionDetectionRound: set(),
        # using `set(get_name(SynchronizedData.mech_requests))`
        # makes the checks complain that "db pre and post conditions intersect"
        MechRequestRound: set(),
        # we should be able to include `SynchronizedData.final_tx_hash` in the set below,
        # however, we can't, because the checks incorrectly report that "db pre and post conditions intersect"
        MechResponseRound: set(),
    }
    db_post_conditions: Dict[AppState, Set[str]] = {
        FinishedMarketplaceLegacyDetectedRound: {
            get_name(SynchronizedData.is_marketplace_v2),
        },
        FinishedMechLegacyDetectedRound: {
            get_name(SynchronizedData.is_marketplace_v2),
        },
        FinishedMechInformationRound: {
            get_name(SynchronizedData.is_marketplace_v2),
            get_name(SynchronizedData.mechs_info),
            get_name(SynchronizedData.relevant_mechs_info),
            get_name(SynchronizedData.mech_tools),
            get_name(SynchronizedData.priority_mech_address),
        },
        FailedMechInformationRound: set(),
        FinishedMechRequestRound: {
            get_name(SynchronizedData.tx_submitter),
            get_name(SynchronizedData.most_voted_tx_hash),
            get_name(SynchronizedData.mech_price),
        },
        FinishedMechRequestSkipRound: set(),
        FinishedMechPurchaseSubscriptionRound: {
            get_name(SynchronizedData.tx_submitter),
            get_name(SynchronizedData.most_voted_tx_hash),
        },
        FinishedMechResponseRound: set(get_name(SynchronizedData.mech_responses)),
        FinishedMechResponseTimeoutRound: set(),
    }
