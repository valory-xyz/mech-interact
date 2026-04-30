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
from unittest.mock import MagicMock, patch

import pytest

from packages.valory.skills.abstract_round_abci.behaviour_utils import BaseBehaviour
from packages.valory.skills.mech_interact_abci.behaviours.base import (
    MechInteractBaseBehaviour,
)
from packages.valory.skills.mech_interact_abci.behaviours.purchase_subcription import (
    MechPurchaseSubscriptionBehaviour,
)
from packages.valory.skills.mech_interact_abci.behaviours.request import (
    MechRequestBehaviour,
)
from packages.valory.skills.mech_interact_abci.behaviours.response import (
    MechResponseBehaviour,
)


class ConcreteBehaviour(MechInteractBaseBehaviour):
    """Concrete subclass for testing the abstract base."""

    matching_round = MagicMock()

    def async_act(self) -> Generator:
        """Dummy async_act."""
        yield


def _noop_base_init(self, **kwargs):  # type: ignore
    """No-op replacement for the framework-level BaseBehaviour.__init__."""


def assert_unset_property_logs(
    behaviour: MechInteractBaseBehaviour, prop: str, log_method: str = "error"
) -> None:
    """Assert that accessing an unset property returns None and logs."""
    result = getattr(behaviour, prop)
    assert result is None
    logger = getattr(behaviour.context.logger, log_method)
    logger.assert_called_once()


@pytest.fixture
def base_behaviour() -> ConcreteBehaviour:
    """Create a ConcreteBehaviour via its real __init__."""
    with patch.object(BaseBehaviour, "__init__", _noop_base_init):
        behaviour = ConcreteBehaviour()
    behaviour._context = MagicMock()
    return behaviour


@pytest.fixture
def request_behaviour() -> MechRequestBehaviour:
    """Create a MechRequestBehaviour via its real __init__."""
    with patch.object(BaseBehaviour, "__init__", _noop_base_init):
        behaviour = MechRequestBehaviour()
    behaviour._context = MagicMock()
    return behaviour


@pytest.fixture
def response_behaviour() -> MechResponseBehaviour:
    """Create a MechResponseBehaviour via its real __init__.

    Note: _context must be set before __init__ because the
    current_mech_response setter accesses self.context.shared_state
    during initialization.
    """
    with patch.object(BaseBehaviour, "__init__", _noop_base_init):
        behaviour = MechResponseBehaviour.__new__(MechResponseBehaviour)
        behaviour._context = MagicMock()
        behaviour._context.shared_state = {}
        MechResponseBehaviour.__init__(behaviour)
    return behaviour


@pytest.fixture
def purchase_behaviour() -> MechPurchaseSubscriptionBehaviour:
    """Create a MechPurchaseSubscriptionBehaviour via its real __init__."""
    with patch.object(BaseBehaviour, "__init__", _noop_base_init):
        behaviour = MechPurchaseSubscriptionBehaviour()
    behaviour._context = MagicMock()
    return behaviour
