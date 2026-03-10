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

"""This package contains round behaviours of MechInteractAbciApp."""

from pathlib import Path
from typing import Any, Dict, Optional, Type

from packages.valory.skills.abstract_round_abci.base import AbciAppDB
from packages.valory.skills.abstract_round_abci.behaviours import BaseBehaviour
from packages.valory.skills.abstract_round_abci.test_tools.base import (
    FSMBehaviourBaseCase,
)
from packages.valory.skills.mech_interact_abci.behaviours.base import (
    MechInteractBaseBehaviour,
)
from packages.valory.skills.mech_interact_abci.behaviours.mech_info import (
    MechInformationBehaviour,
)
from packages.valory.skills.mech_interact_abci.behaviours.mech_version import (
    MechVersionDetectionBehaviour,
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
from packages.valory.skills.mech_interact_abci.behaviours.round_behaviour import (
    MechInteractRoundBehaviour,
)
from packages.valory.skills.mech_interact_abci.states.base import (
    Event,
    SynchronizedData,
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


class BaseMechInteractTest(FSMBehaviourBaseCase):
    """Base test case."""

    path_to_skill = Path(__file__).parent.parent

    behaviour: MechInteractRoundBehaviour
    behaviour_class: Type[MechInteractBaseBehaviour]
    next_behaviour_class: Optional[Type[MechInteractBaseBehaviour]] = None
    synchronized_data: SynchronizedData
    done_event = Event.DONE

    @property
    def current_behaviour_id(self) -> str:
        """Current RoundBehaviour's behaviour id"""

        return self.behaviour.current_behaviour.behaviour_id

    def fast_forward(self, data: Optional[Dict[str, Any]] = None) -> None:
        """Fast-forward on initialization"""

        data = data if data is not None else {}
        self.fast_forward_to_behaviour(
            self.behaviour,
            self.behaviour_class.behaviour_id,
            SynchronizedData(AbciAppDB(setup_data=AbciAppDB.data_to_lists(data))),
        )
        assert self.current_behaviour_id == self.behaviour_class.behaviour_id

    def complete(self, event: Event) -> None:
        """Complete test"""

        self.behaviour.act_wrapper()
        self.mock_a2a_transaction()
        self._test_done_flag_set()
        self.end_round(done_event=event)
        if self.next_behaviour_class is not None:
            assert self.current_behaviour_id == self.next_behaviour_class.behaviour_id


class TestMechVersionDetectionBehaviour(BaseMechInteractTest):
    """Tests MechVersionDetectionBehaviour"""

    behaviour_class: Type[BaseBehaviour] = MechVersionDetectionBehaviour
    next_behaviour_class: Type[BaseBehaviour] = MechInformationBehaviour

    def test_matching_round(self):
        """Test that the behaviour is matched to the correct round."""
        assert MechVersionDetectionBehaviour.matching_round == MechVersionDetectionRound


class TestMechInformationBehaviour(BaseMechInteractTest):
    """Tests MechInformationBehaviour"""

    behaviour_class: Type[BaseBehaviour] = MechInformationBehaviour

    def test_matching_round(self):
        """Test that the behaviour is matched to the correct round."""
        assert MechInformationBehaviour.matching_round == MechInformationRound


class TestMechRequestBehaviour(BaseMechInteractTest):
    """Tests MechRequestBehaviour"""

    behaviour_class: Type[BaseBehaviour] = MechRequestBehaviour

    def test_matching_round(self):
        """Test that the behaviour is matched to the correct round."""
        assert MechRequestBehaviour.matching_round == MechRequestRound


class TestMechPurchaseSubscriptionBehaviour(BaseMechInteractTest):
    """Tests MechPurchaseSubscriptionBehaviour"""

    behaviour_class: Type[BaseBehaviour] = MechPurchaseSubscriptionBehaviour

    def test_matching_round(self):
        """Test that the behaviour is matched to the correct round."""
        assert (
            MechPurchaseSubscriptionBehaviour.matching_round
            == MechPurchaseSubscriptionRound
        )


class TestMechResponseBehaviour(BaseMechInteractTest):
    """Tests MechResponseBehaviour"""

    behaviour_class: Type[BaseBehaviour] = MechResponseBehaviour

    def test_matching_round(self):
        """Test that the behaviour is matched to the correct round."""
        assert MechResponseBehaviour.matching_round == MechResponseRound


class TestMechInteractRoundBehaviour(BaseMechInteractTest):
    """Tests for the MechInteractRoundBehaviour."""

    behaviour_class: Type[BaseBehaviour] = MechRequestBehaviour

    def test_all_behaviours_registered(self):
        """Test that all behaviours are registered."""
        expected = {
            MechVersionDetectionBehaviour,
            MechInformationBehaviour,
            MechRequestBehaviour,
            MechPurchaseSubscriptionBehaviour,
            MechResponseBehaviour,
        }
        assert MechInteractRoundBehaviour.behaviours == expected

    def test_initial_behaviour(self):
        """Test that the initial behaviour is correct."""
        assert MechInteractRoundBehaviour.initial_behaviour_cls == MechRequestBehaviour
