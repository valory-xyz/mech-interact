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

"""Tests for the base behaviour module."""

from typing import Generator
from unittest.mock import MagicMock

import pytest
from hexbytes import HexBytes

from packages.valory.skills.mech_interact_abci.behaviours.base import (
    MechInteractBaseBehaviour,
    SAFE_GAS,
)
from packages.valory.skills.mech_interact_abci.models import MultisendBatch
from packages.valory.skills.transaction_settlement_abci.rounds import TX_HASH_LENGTH


class ConcreteBehaviour(MechInteractBaseBehaviour):
    """Concrete subclass for testing the abstract base."""

    matching_round = MagicMock()

    def async_act(self) -> Generator:
        """Dummy async_act."""
        yield


def _make_behaviour() -> ConcreteBehaviour:
    """Create a ConcreteBehaviour with mocked internals."""
    behaviour = ConcreteBehaviour.__new__(ConcreteBehaviour)
    behaviour.multisend_batches = []
    behaviour.multisend_data = b""
    behaviour._safe_tx_hash = ""
    behaviour._context = MagicMock()
    return behaviour


class TestMechInteractBaseBehaviourProperties:
    """Tests for MechInteractBaseBehaviour properties that can be tested without FSM."""

    def test_safe_gas_constant(self) -> None:
        """Test that SAFE_GAS is 0."""
        assert SAFE_GAS == 0

    def test_safe_tx_hash_setter_valid(self) -> None:
        """Test setting a valid safe tx hash."""
        behaviour = _make_behaviour()
        valid_hash = "0x" + "a" * (TX_HASH_LENGTH - 2)
        behaviour.safe_tx_hash = valid_hash
        assert behaviour._safe_tx_hash == "a" * (TX_HASH_LENGTH - 2)

    def test_safe_tx_hash_setter_invalid_length(self) -> None:
        """Test setting an invalid length safe tx hash raises ValueError."""
        behaviour = _make_behaviour()
        with pytest.raises(ValueError, match="Incorrect length"):
            behaviour.safe_tx_hash = "0xshort"

    def test_safe_tx_hash_getter(self) -> None:
        """Test getting safe tx hash."""
        behaviour = _make_behaviour()
        behaviour._safe_tx_hash = "abc123"
        assert behaviour.safe_tx_hash == "abc123"

    def test_multi_send_txs(self) -> None:
        """Test multi_send_txs returns list of dicts."""
        behaviour = _make_behaviour()
        batch = MultisendBatch(to="0xaddr", data=HexBytes(b"\x01"), value=100)
        behaviour.multisend_batches = [batch]
        result = behaviour.multi_send_txs
        assert len(result) == 1
        assert result[0]["to"] == "0xaddr"
        assert result[0]["value"] == 100

    def test_txs_value_empty(self) -> None:
        """Test txs_value with no batches."""
        behaviour = _make_behaviour()
        assert behaviour.txs_value == 0

    def test_txs_value_with_batches(self) -> None:
        """Test txs_value sums all batch values."""
        behaviour = _make_behaviour()
        behaviour.multisend_batches = [
            MultisendBatch(to="0xa", data=HexBytes(b""), value=100),
            MultisendBatch(to="0xb", data=HexBytes(b""), value=200),
        ]
        assert behaviour.txs_value == 300

    def test_tx_hex_raises_without_hash(self) -> None:
        """Test tx_hex raises ValueError when safe_tx_hash is empty."""
        behaviour = _make_behaviour()
        with pytest.raises(ValueError, match="Cannot prepare a multisend"):
            _ = behaviour.tx_hex

    def test_default_error(self) -> None:
        """Test default_error logs error message."""
        behaviour = _make_behaviour()
        response = MagicMock()
        behaviour.default_error("contract_id", "callable", response)
        behaviour.context.logger.error.assert_called_once()

    def test_contract_interaction_error_info(self) -> None:
        """Test contract_interaction_error with info level message."""
        behaviour = _make_behaviour()
        response = MagicMock()
        response.raw_transaction.body = {"info": "some info message"}
        behaviour.contract_interaction_error("contract_id", "callable", response)
        behaviour.context.logger.info.assert_called_once_with("some info message")

    def test_contract_interaction_error_warning(self) -> None:
        """Test contract_interaction_error with warning level message."""
        behaviour = _make_behaviour()
        response = MagicMock()
        response.raw_transaction.body = {"warning": "some warning"}
        behaviour.contract_interaction_error("contract_id", "callable", response)
        behaviour.context.logger.warning.assert_called_once_with("some warning")

    def test_contract_interaction_error_error(self) -> None:
        """Test contract_interaction_error with error level message."""
        behaviour = _make_behaviour()
        response = MagicMock()
        response.raw_transaction.body = {"error": "some error"}
        behaviour.contract_interaction_error("contract_id", "callable", response)
        behaviour.context.logger.error.assert_called_once_with("some error")

    def test_contract_interaction_error_fallback(self) -> None:
        """Test contract_interaction_error falls back to default_error."""
        behaviour = _make_behaviour()
        response = MagicMock()
        response.raw_transaction.body = {}
        behaviour.contract_interaction_error("contract_id", "callable", response)
        behaviour.context.logger.error.assert_called_once()

    def test_should_use_marketplace_v2_disabled(self) -> None:
        """Test should_use_marketplace_v2 when marketplace is disabled."""
        behaviour = _make_behaviour()
        behaviour.context.params.use_mech_marketplace = False
        assert behaviour.should_use_marketplace_v2() is False

    def test_should_use_marketplace_v2_no_check(self) -> None:
        """Test should_use_marketplace_v2 raises when check not performed."""
        behaviour = _make_behaviour()
        behaviour.context.params.use_mech_marketplace = True
        # Mock synchronized_data
        synced = MagicMock()
        synced.versioning_check_performed = False
        type(behaviour).synchronized_data = property(lambda self: synced)
        with pytest.raises(ValueError, match="Compatibility check"):
            behaviour.should_use_marketplace_v2()

    def test_should_use_marketplace_v2_true(self) -> None:
        """Test should_use_marketplace_v2 returns True when v2."""
        behaviour = _make_behaviour()
        behaviour.context.params.use_mech_marketplace = True
        synced = MagicMock()
        synced.versioning_check_performed = True
        synced.is_marketplace_v2 = True
        type(behaviour).synchronized_data = property(lambda self: synced)
        assert behaviour.should_use_marketplace_v2() is True
