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

"""This package contains payload tests for the MechInteractAbciApp."""

import json
from typing import Optional, cast

import pytest

from packages.valory.skills.mech_interact_abci.payloads import (
    JSONPayload,
    MechRequestPayload,
    PrepareTxPayload,
    VotingPayload,
)

SAMPLE_SENDER = "test_sender"
SAMPLE_TX_HASH = "0xabc123"
SAMPLE_TX_SUBMITTER = "MechRequestBehaviour"


class TestPrepareTxPayload:
    """Tests for PrepareTxPayload."""

    def test_json_roundtrip(self) -> None:
        """Test that a payload survives JSON serialization and deserialization."""
        payload = PrepareTxPayload(
            sender=SAMPLE_SENDER,
            tx_submitter=SAMPLE_TX_SUBMITTER,
            tx_hash=SAMPLE_TX_HASH,
        )
        restored = PrepareTxPayload.from_json(payload.json)
        assert restored == payload

    def test_none_fields_roundtrip(self) -> None:
        """Test roundtrip with None fields."""
        payload = PrepareTxPayload(
            sender=SAMPLE_SENDER, tx_submitter=None, tx_hash=None
        )
        restored = cast(PrepareTxPayload, PrepareTxPayload.from_json(payload.json))
        assert restored == payload
        assert restored.tx_submitter is None
        assert restored.tx_hash is None


class TestMechRequestPayload:
    """Tests for MechRequestPayload."""

    def test_json_roundtrip_with_all_fields(self) -> None:
        """Test roundtrip with all fields populated."""
        payload = MechRequestPayload(
            sender=SAMPLE_SENDER,
            tx_submitter=SAMPLE_TX_SUBMITTER,
            tx_hash=SAMPLE_TX_HASH,
            price=100,
            chain_id="1",
            safe_contract_address="0xsafe",
            mech_requests=json.dumps([{"prompt": "test"}]),
            mech_responses=json.dumps([]),
        )
        restored = MechRequestPayload.from_json(payload.json)
        assert restored == payload

    def test_json_roundtrip_with_none_fields(self) -> None:
        """Test roundtrip when all optional fields are None."""
        payload = MechRequestPayload(
            sender=SAMPLE_SENDER,
            tx_submitter=None,
            tx_hash=None,
            price=None,
            chain_id=None,
            safe_contract_address=None,
            mech_requests=None,
            mech_responses=None,
        )
        restored = MechRequestPayload.from_json(payload.json)
        assert restored == payload


class TestJSONPayload:
    """Tests for JSONPayload."""

    def test_json_roundtrip(self) -> None:
        """Test roundtrip with information."""
        info = json.dumps({"key": "value"})
        payload = JSONPayload(sender=SAMPLE_SENDER, information=info)
        restored = JSONPayload.from_json(payload.json)
        assert restored == payload

    def test_none_information_roundtrip(self) -> None:
        """Test roundtrip with None information."""
        payload = JSONPayload(sender=SAMPLE_SENDER, information=None)
        restored = cast(JSONPayload, JSONPayload.from_json(payload.json))
        assert restored == payload
        assert restored.information is None


class TestVotingPayload:
    """Tests for VotingPayload."""

    @pytest.mark.parametrize("vote", [True, False, None])
    def test_json_roundtrip(self, vote: Optional[bool]) -> None:
        """Test roundtrip for all valid vote values."""
        payload = VotingPayload(sender=SAMPLE_SENDER, vote=vote)
        restored = cast(VotingPayload, VotingPayload.from_json(payload.json))
        assert restored == payload
        assert restored.vote is vote
