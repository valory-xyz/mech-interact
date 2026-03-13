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
from typing import List, Type
from unittest.mock import MagicMock

from packages.valory.skills.abstract_round_abci.base import AbstractRound, BaseTxPayload
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
SAMPLE_TX_SUBMITTER = "MechRequestBehaviour"
SAMPLE_TX_HASH = "0xabc"
SAMPLE_PRICE = 100
SAMPLE_CHAIN_ID = "1"
SAMPLE_SAFE_ADDRESS = "0xsafe"
SAMPLE_SUBSCRIPTION_SUBMITTER = "MechPurchaseSubscriptionBehaviour"
SAMPLE_SUBSCRIPTION_TX_HASH = "0xdef"


class BaseMechInteractRoundTest(BaseRoundTestClass):
    """Base test class for MechInteract rounds."""

    round_cls: Type[AbstractRound]
    synchronized_data: SynchronizedData
    _synchronized_data_class = SynchronizedData
    _event_class = Event

    def _create_round(self) -> AbstractRound:
        """Create a fresh round instance."""
        return self.round_cls(
            synchronized_data=self.synchronized_data,
            context=MagicMock(),
        )

    def _process_payloads(
        self, round_instance: AbstractRound, payloads: List[BaseTxPayload]
    ) -> None:
        """Process all payloads in a round."""
        for payload in payloads:
            round_instance.process_payload(payload)


class TestMechRequestRound(BaseMechInteractRoundTest):
    """Tests for MechRequestRound."""

    round_cls = MechRequestRound

    def _make_request_payloads(self, **kwargs):
        """Create identical MechRequestPayload for all participants."""
        defaults = dict(
            tx_submitter=SAMPLE_TX_SUBMITTER,
            tx_hash=SAMPLE_TX_HASH,
            price=SAMPLE_PRICE,
            chain_id=SAMPLE_CHAIN_ID,
            safe_contract_address=SAMPLE_SAFE_ADDRESS,
            mech_requests="[]",
            mech_responses="[]",
        )
        defaults.update(kwargs)
        return [
            MechRequestPayload(sender=agent, **defaults)
            for agent in sorted(self.participants)
        ]

    def test_done_event_when_requests_non_empty(self):
        """Test DONE event when mech requests are non-empty."""
        test_round = self._create_round()
        mech_requests = json.dumps([{"prompt": "test", "tool": "tool", "nonce": "1"}])
        self._process_payloads(
            test_round, self._make_request_payloads(mech_requests=mech_requests)
        )

        result = test_round.end_block()
        assert result is not None
        _, event = result
        assert event == Event.DONE

    def test_skip_request_when_no_requests_or_responses(self):
        """Test SKIP_REQUEST when requests and responses are empty."""
        test_round = self._create_round()
        self._process_payloads(
            test_round,
            self._make_request_payloads(mech_requests="[]", mech_responses="[]"),
        )

        result = test_round.end_block()
        assert result is not None
        _, event = result
        assert event == Event.SKIP_REQUEST

    def test_buy_subscription_when_all_none(self):
        """Test BUY_SUBSCRIPTION when all payload values are None."""
        test_round = self._create_round()
        self._process_payloads(
            test_round,
            self._make_request_payloads(
                tx_submitter=None,
                tx_hash=None,
                price=None,
                chain_id=None,
                safe_contract_address=None,
                mech_requests=None,
                mech_responses=None,
            ),
        )

        result = test_round.end_block()
        assert result is not None
        _, event = result
        assert event == Event.BUY_SUBSCRIPTION

    def test_no_majority_event(self):
        """Test NO_MAJORITY event."""
        test_round = self._create_round()
        self._test_no_majority_event(test_round)


class TestMechResponseRound(BaseMechInteractRoundTest):
    """Tests for MechResponseRound."""

    round_cls = MechResponseRound

    def test_done_event_with_responses(self):
        """Test DONE event with non-empty response information."""
        test_round = self._create_round()
        responses = json.dumps(
            [{"nonce": "1", "result": "answer", "data": "", "requestId": 0}]
        )
        payloads = [
            JSONPayload(sender=agent, information=responses)
            for agent in sorted(self.participants)
        ]
        self._process_payloads(test_round, payloads)

        result = test_round.end_block()
        assert result is not None
        _, event = result
        assert event == Event.DONE

    def test_no_majority_event(self):
        """Test NO_MAJORITY event."""
        test_round = self._create_round()
        self._test_no_majority_event(test_round)


class TestMechVersionDetectionRound(BaseMechInteractRoundTest):
    """Tests for MechVersionDetectionRound."""

    round_cls = MechVersionDetectionRound

    def _make_voting_payloads(self, vote):
        """Create VotingPayload for all participants with the given vote."""
        return [
            VotingPayload(sender=agent, vote=vote)
            for agent in sorted(self.participants)
        ]

    def test_v2_event_with_positive_votes(self):
        """Test V2 event with positive votes."""
        test_round = self._create_round()
        self._process_payloads(test_round, self._make_voting_payloads(vote=True))

        result = test_round.end_block()
        assert result is not None
        synced_data, event = result
        assert event == Event.V2
        assert synced_data.is_marketplace_v2 is True

    def test_v1_event_with_negative_votes(self):
        """Test V1 event with negative votes."""
        test_round = self._create_round()
        self._process_payloads(test_round, self._make_voting_payloads(vote=False))

        result = test_round.end_block()
        assert result is not None
        synced_data, event = result
        assert event == Event.V1
        assert synced_data.is_marketplace_v2 is False

    def test_no_marketplace_event_with_none_votes(self):
        """Test NO_MARKETPLACE event with None votes."""
        test_round = self._create_round()
        self._process_payloads(test_round, self._make_voting_payloads(vote=None))

        result = test_round.end_block()
        assert result is not None
        synced_data, event = result
        assert event == Event.NO_MARKETPLACE
        assert synced_data.is_marketplace_v2 is None

    def test_no_majority_event(self):
        """Test NO_MAJORITY event."""
        test_round = self._create_round()
        self._test_no_majority_event(test_round)


class TestMechInformationRound(BaseMechInteractRoundTest):
    """Tests for MechInformationRound."""

    round_cls = MechInformationRound

    def _make_json_payloads(self, information):
        """Create JSONPayload for all participants."""
        return [
            JSONPayload(sender=agent, information=information)
            for agent in sorted(self.participants)
        ]

    def test_done_event_with_information(self):
        """Test DONE event with mech information."""
        test_round = self._create_round()
        info = json.dumps([{"id": "1", "address": "0xmech"}])
        self._process_payloads(test_round, self._make_json_payloads(info))

        result = test_round.end_block()
        assert result is not None
        _, event = result
        assert event == Event.DONE

    def test_none_event_when_information_is_none(self):
        """Test NONE event when information is None."""
        test_round = self._create_round()
        self._process_payloads(test_round, self._make_json_payloads(None))

        result = test_round.end_block()
        assert result is not None
        _, event = result
        assert event == Event.NONE

    def test_no_majority_event(self):
        """Test NO_MAJORITY event."""
        test_round = self._create_round()
        self._test_no_majority_event(test_round)


class TestMechPurchaseSubscriptionRound(BaseMechInteractRoundTest):
    """Tests for MechPurchaseSubscriptionRound."""

    round_cls = MechPurchaseSubscriptionRound

    def _make_prepare_tx_payloads(self, tx_submitter, tx_hash):
        """Create PrepareTxPayload for all participants."""
        return [
            PrepareTxPayload(sender=agent, tx_submitter=tx_submitter, tx_hash=tx_hash)
            for agent in sorted(self.participants)
        ]

    def test_done_event_with_prepared_tx(self):
        """Test DONE event with a prepared transaction."""
        test_round = self._create_round()
        self._process_payloads(
            test_round,
            self._make_prepare_tx_payloads(
                SAMPLE_SUBSCRIPTION_SUBMITTER, SAMPLE_SUBSCRIPTION_TX_HASH
            ),
        )

        result = test_round.end_block()
        assert result is not None
        _, event = result
        assert event == Event.DONE

    def test_none_event_when_all_none(self):
        """Test NONE event when all payload values are None."""
        test_round = self._create_round()
        self._process_payloads(test_round, self._make_prepare_tx_payloads(None, None))

        result = test_round.end_block()
        assert result is not None
        _, event = result
        assert event == Event.NONE

    def test_no_majority_event(self):
        """Test NO_MAJORITY event."""
        test_round = self._create_round()
        self._test_no_majority_event(test_round)
