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

"""Shared fixtures for behaviour tests."""

from typing import Generator
from unittest.mock import MagicMock

import pytest

from packages.valory.skills.mech_interact_abci.behaviours.base import (
    MechInteractBaseBehaviour,
)
from packages.valory.skills.mech_interact_abci.behaviours.purchase_subcription import (
    MechPurchaseSubscriptionBehaviour,
)
from packages.valory.skills.mech_interact_abci.behaviours.request import (
    MechRequestBehaviour,
    PaymentType,
)
from packages.valory.skills.mech_interact_abci.behaviours.response import (
    MechResponseBehaviour,
)
from packages.valory.skills.mech_interact_abci.states.base import (
    MECH_RESPONSE,
    MechInteractionResponse,
)


class ConcreteBehaviour(MechInteractBaseBehaviour):
    """Concrete subclass for testing the abstract base."""

    matching_round = MagicMock()

    def async_act(self) -> Generator:
        """Dummy async_act."""
        yield


def _init_base_attrs(behaviour: MechInteractBaseBehaviour) -> None:
    """Initialize the common base behaviour attributes."""
    behaviour.multisend_batches = []
    behaviour.multisend_data = b""
    behaviour._safe_tx_hash = ""
    behaviour._context = MagicMock()


@pytest.fixture
def base_behaviour() -> ConcreteBehaviour:
    """Create a ConcreteBehaviour with mocked internals."""
    behaviour = ConcreteBehaviour.__new__(ConcreteBehaviour)
    _init_base_attrs(behaviour)
    return behaviour


@pytest.fixture
def request_behaviour() -> MechRequestBehaviour:
    """Create a MechRequestBehaviour with mocked internals."""
    behaviour = MechRequestBehaviour.__new__(MechRequestBehaviour)
    _init_base_attrs(behaviour)
    behaviour._v1_hex_truncated = ""
    behaviour._request_data = b""
    behaviour._price = 0
    behaviour._mech_requests = []
    behaviour._pending_responses = []
    behaviour.priority_mech_address = ""
    behaviour.token_balance = 0
    behaviour.wallet_balance = 0
    behaviour._mech_payment_type = PaymentType.NATIVE
    behaviour._mech_max_delivery_rate = None
    behaviour._subscription_balance = None
    behaviour._nvm_balance = None
    behaviour._subscription_address = None
    behaviour._subscription_id = None
    behaviour._balance_tracker = None
    behaviour._approval_data = None
    return behaviour


@pytest.fixture
def response_behaviour() -> MechResponseBehaviour:
    """Create a MechResponseBehaviour with mocked internals."""
    behaviour = MechResponseBehaviour.__new__(MechResponseBehaviour)
    _init_base_attrs(behaviour)
    behaviour._context.shared_state = {}
    behaviour._from_block = 0
    behaviour._requests = []
    behaviour._response_hex = ""
    behaviour._request_info = []
    behaviour._is_valid_acn_sender = False
    behaviour.context.shared_state[MECH_RESPONSE] = MechInteractionResponse(
        error="The mech's response has not been set!"
    )
    return behaviour


@pytest.fixture
def purchase_behaviour() -> MechPurchaseSubscriptionBehaviour:
    """Create a MechPurchaseSubscriptionBehaviour with mocked internals."""
    behaviour = MechPurchaseSubscriptionBehaviour.__new__(
        MechPurchaseSubscriptionBehaviour
    )
    _init_base_attrs(behaviour)
    behaviour._agreement_id = None
    behaviour._agreement_id_seed = None
    behaviour._ddo_register = None
    behaviour._ddo_values = None
    behaviour._receivers = None
    behaviour._lock_hash = None
    behaviour._lock_id = None
    behaviour._transfer_hash = None
    behaviour._transfer_id = None
    behaviour._escrow_hash = None
    behaviour._escrow_id = None
    behaviour._agreement_tx_data = None
    behaviour._subscription_token_approval_tx_data = None
    behaviour._fulfill_tx_data = None
    return behaviour
