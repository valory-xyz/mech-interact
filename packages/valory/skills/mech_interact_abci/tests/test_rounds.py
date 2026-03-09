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

"""This package contains the tests for rounds of MechInteract."""

import json
from typing import Type
from unittest.mock import MagicMock

from packages.valory.skills.abstract_round_abci.base import AbstractRound
from packages.valory.skills.abstract_round_abci.test_tools.rounds import (
    BaseRoundTestClass,
)
from packages.valory.skills.mech_interact_abci.payloads import (
    JSONPayload,
    MechRequestPayload,
    PrepareTxPayload,
    VotingPayload,
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

MAX_PARTICIPANTS: int = 4


class BaseMechInteractRoundTest(BaseRoundTestClass):
    """Base test class for MechInteract rounds."""

    round_cls: Type[AbstractRound]
    synchronized_data: SynchronizedData
    _synchronized_data_class = SynchronizedData
    _event_class = Event


class TestMechRequestRound(BaseMechInteractRoundTest):
    """Tests for MechRequestRound."""

    round_cls = MechRequestRound

    def _make_request_payloads(self, **kwargs):
        """Create identical MechRequestPayload for all participants."""
        defaults = dict(
            tx_submitter="MechRequestBehaviour",
            tx_hash="0xabc",
            price=100,
            chain_id="1",
            safe_contract_address="0xsafe",
            mech_requests="[]",
            mech_responses="[]",
        )
        defaults.update(kwargs)
        return [
            MechRequestPayload(sender=agent, **defaults)
            for agent in sorted(self.participants)
        ]

    def test_done_event(self):
        """Test DONE event when mech requests are non-empty."""
        test_round = self.round_cls(
            synchronized_data=self.synchronized_data,
            context=MagicMock(),
        )
        mech_requests = json.dumps([{"prompt": "test", "tool": "tool", "nonce": "1"}])
        for payload in self._make_request_payloads(
            mech_requests=mech_requests,
        ):
            test_round.process_payload(payload)

        result = test_round.end_block()
        assert result is not None
        _, event = result
        assert event == Event.DONE

    def test_skip_request_event(self):
        """Test SKIP_REQUEST when requests and responses are empty."""
        test_round = self.round_cls(
            synchronized_data=self.synchronized_data,
            context=MagicMock(),
        )
        for payload in self._make_request_payloads(
            mech_requests="[]",
            mech_responses="[]",
        ):
            test_round.process_payload(payload)

        result = test_round.end_block()
        assert result is not None
        _, event = result
        assert event == Event.SKIP_REQUEST

    def test_buy_subscription_event(self):
        """Test BUY_SUBSCRIPTION when all payload values are None."""
        test_round = self.round_cls(
            synchronized_data=self.synchronized_data,
            context=MagicMock(),
        )
        for payload in self._make_request_payloads(
            tx_submitter=None,
            tx_hash=None,
            price=None,
            chain_id=None,
            safe_contract_address=None,
            mech_requests=None,
            mech_responses=None,
        ):
            test_round.process_payload(payload)

        result = test_round.end_block()
        assert result is not None
        _, event = result
        assert event == Event.BUY_SUBSCRIPTION

    def test_no_majority_event(self):
        """Test NO_MAJORITY event."""
        test_round = self.round_cls(
            synchronized_data=self.synchronized_data,
            context=MagicMock(),
        )
        self._test_no_majority_event(test_round)


class TestMechResponseRound(BaseMechInteractRoundTest):
    """Tests for MechResponseRound."""

    round_cls = MechResponseRound

    def test_done_event(self):
        """Test DONE event with non-empty response information."""
        test_round = self.round_cls(
            synchronized_data=self.synchronized_data,
            context=MagicMock(),
        )
        responses = json.dumps(
            [{"nonce": "1", "result": "answer", "data": "", "requestId": 0}]
        )
        for agent in sorted(self.participants):
            payload = JSONPayload(sender=agent, information=responses)
            test_round.process_payload(payload)

        result = test_round.end_block()
        assert result is not None
        _, event = result
        assert event == Event.DONE

    def test_no_majority_event(self):
        """Test NO_MAJORITY event."""
        test_round = self.round_cls(
            synchronized_data=self.synchronized_data,
            context=MagicMock(),
        )
        self._test_no_majority_event(test_round)


class TestMechVersionDetectionRound(BaseMechInteractRoundTest):
    """Tests for MechVersionDetectionRound."""

    round_cls = MechVersionDetectionRound

    def test_v2_event(self):
        """Test V2 event with positive votes."""
        test_round = self.round_cls(
            synchronized_data=self.synchronized_data,
            context=MagicMock(),
        )
        for agent in sorted(self.participants):
            payload = VotingPayload(sender=agent, vote=True)
            test_round.process_payload(payload)

        result = test_round.end_block()
        assert result is not None
        synced_data, event = result
        assert event == Event.V2
        assert synced_data.is_marketplace_v2 is True

    def test_v1_event(self):
        """Test V1 event with negative votes."""
        test_round = self.round_cls(
            synchronized_data=self.synchronized_data,
            context=MagicMock(),
        )
        for agent in sorted(self.participants):
            payload = VotingPayload(sender=agent, vote=False)
            test_round.process_payload(payload)

        result = test_round.end_block()
        assert result is not None
        synced_data, event = result
        assert event == Event.V1
        assert synced_data.is_marketplace_v2 is False

    def test_no_marketplace_event(self):
        """Test NO_MARKETPLACE event with None votes."""
        test_round = self.round_cls(
            synchronized_data=self.synchronized_data,
            context=MagicMock(),
        )
        for agent in sorted(self.participants):
            payload = VotingPayload(sender=agent, vote=None)
            test_round.process_payload(payload)

        result = test_round.end_block()
        assert result is not None
        synced_data, event = result
        assert event == Event.NO_MARKETPLACE
        assert synced_data.is_marketplace_v2 is None

    def test_no_majority_event(self):
        """Test NO_MAJORITY event."""
        test_round = self.round_cls(
            synchronized_data=self.synchronized_data,
            context=MagicMock(),
        )
        self._test_no_majority_event(test_round)


class TestMechInformationRound(BaseMechInteractRoundTest):
    """Tests for MechInformationRound."""

    round_cls = MechInformationRound

    def test_done_event(self):
        """Test DONE event with mech information."""
        test_round = self.round_cls(
            synchronized_data=self.synchronized_data,
            context=MagicMock(),
        )
        info = json.dumps([{"id": "1", "address": "0xmech"}])
        for agent in sorted(self.participants):
            payload = JSONPayload(sender=agent, information=info)
            test_round.process_payload(payload)

        result = test_round.end_block()
        assert result is not None
        _, event = result
        assert event == Event.DONE

    def test_none_event(self):
        """Test NONE event when information is None."""
        test_round = self.round_cls(
            synchronized_data=self.synchronized_data,
            context=MagicMock(),
        )
        for agent in sorted(self.participants):
            payload = JSONPayload(sender=agent, information=None)
            test_round.process_payload(payload)

        result = test_round.end_block()
        assert result is not None
        _, event = result
        assert event == Event.NONE

    def test_no_majority_event(self):
        """Test NO_MAJORITY event."""
        test_round = self.round_cls(
            synchronized_data=self.synchronized_data,
            context=MagicMock(),
        )
        self._test_no_majority_event(test_round)


class TestMechPurchaseSubscriptionRound(BaseMechInteractRoundTest):
    """Tests for MechPurchaseSubscriptionRound."""

    round_cls = MechPurchaseSubscriptionRound

    def test_done_event(self):
        """Test DONE event with a prepared transaction."""
        test_round = self.round_cls(
            synchronized_data=self.synchronized_data,
            context=MagicMock(),
        )
        for agent in sorted(self.participants):
            payload = PrepareTxPayload(
                sender=agent,
                tx_submitter="MechPurchaseSubscriptionBehaviour",
                tx_hash="0xdef",
            )
            test_round.process_payload(payload)

        result = test_round.end_block()
        assert result is not None
        _, event = result
        assert event == Event.DONE

    def test_none_event(self):
        """Test NONE event when all payload values are None."""
        test_round = self.round_cls(
            synchronized_data=self.synchronized_data,
            context=MagicMock(),
        )
        for agent in sorted(self.participants):
            payload = PrepareTxPayload(
                sender=agent,
                tx_submitter=None,
                tx_hash=None,
            )
            test_round.process_payload(payload)

        result = test_round.end_block()
        assert result is not None
        _, event = result
        assert event == Event.NONE

    def test_no_majority_event(self):
        """Test NO_MAJORITY event."""
        test_round = self.round_cls(
            synchronized_data=self.synchronized_data,
            context=MagicMock(),
        )
        self._test_no_majority_event(test_round)
