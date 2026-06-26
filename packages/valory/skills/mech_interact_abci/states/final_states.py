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

"""This module contains the final states of the mech interact abci app."""

from packages.valory.skills.abstract_round_abci.base import DegenerateRound


class FinishedMarketplaceLegacyDetectedRound(DegenerateRound):
    """A round which indicates that the round detection finished by identifying that the legacy marketplace is used."""


class FinishedMechLegacyDetectedRound(DegenerateRound):
    """A round which indicates that the round detection finished by identifying that the legacy mech is used."""


class FinishedMechInformationRound(DegenerateRound):
    """FinishedMechInformationRound"""


class FailedMechInformationRound(DegenerateRound):
    """FailedMechInformationRound"""


class FinishedMechRequestRound(DegenerateRound):
    """FinishedMechRequestRound"""


class FinishedMechRequestSkipRound(DegenerateRound):
    """FinishedMechRequestSkipRound"""


class FinishedMechPurchaseSubscriptionRound(DegenerateRound):
    """FinishedMechPurchaseSubscriptionRound"""


class FinishedMechResponseRound(DegenerateRound):
    """FinishedMechResponseRound"""


class FinishedMechResponseTimeoutRound(DegenerateRound):
    """FinishedMechResponseTimeoutRound"""


class FinishedOffchainMechRequestRound(DegenerateRound):
    """Offchain happy path: request POST got 200; ready to poll.

    The response is ready to be polled via the offchain HTTP poller. The
    consumer routes this to ``MechResponseRound``.
    """


class FinishedOffchainMechDepositNeededRound(DegenerateRound):
    """Offchain 402 path: structured 402, deposit multisend pending settlement.

    Carries ``tx_submitter``+``most_voted_tx_hash`` for the consumer's
    transaction-settlement skill; on ``PostTxSettlement`` the multiplexer
    routes back to ``MechRequestRound`` to retry the original request_id
    at the same nonce after the deposit lands.
    """


class FailedOffchainMechRequestRound(DegenerateRound):
    """Offchain failure path: the cycle exhausted its failover budget.

    Triggered by all timeouts, all 503s, an over-cap 402, or a Nevermined
    402 that cannot be auto-resolved. The reason is in
    ``synchronized_data.offchain_last_failure_reason``. The consumer
    surfaces this to its decision-receive flow as a clean failure.
    """
