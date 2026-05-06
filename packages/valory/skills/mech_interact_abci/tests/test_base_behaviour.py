# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2025-2026 Valory AG
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

from typing import Any, Generator
from unittest.mock import MagicMock

import pytest

from packages.valory.protocols.contract_api import ContractApiMessage
from packages.valory.skills.mech_interact_abci.behaviours.base import (
    MechInteractBaseBehaviour,
)
from packages.valory.skills.mech_interact_abci.models import MultisendBatch
from packages.valory.skills.transaction_settlement_abci.rounds import TX_HASH_LENGTH


class _ConcreteBehaviour(MechInteractBaseBehaviour):
    """Concrete subclass for testing abstract MechInteractBaseBehaviour."""

    matching_round = MagicMock()

    def async_act(self) -> Generator[None, None, None]:
        """No-op async_act."""
        yield


def _make_base_behaviour(**overrides: Any) -> MechInteractBaseBehaviour:
    """Create a MechInteractBaseBehaviour with mocked dependencies."""
    behaviour = _ConcreteBehaviour.__new__(_ConcreteBehaviour)
    mock_context = MagicMock()
    behaviour._context = mock_context
    behaviour.multisend_batches = []
    behaviour.multisend_data = b""
    behaviour._safe_tx_hash = ""

    for key, value in overrides.items():
        setattr(behaviour, key, value)

    return behaviour


def _drive_generator(gen: Generator[Any, Any, Any]) -> Any:
    """Drive a generator that yields once and returns a value."""
    try:
        next(gen)
        gen.send(None)
    except StopIteration as e:
        return e.value
    raise AssertionError("Generator did not stop")


class TestSafeTxHashProperty:
    """Tests for the safe_tx_hash getter."""

    def test_getter_returns_internal_value(self) -> None:
        """Test that the getter returns the stored value."""
        behaviour = _make_base_behaviour()
        behaviour._safe_tx_hash = "abcdef"
        assert behaviour.safe_tx_hash == "abcdef"


class TestSafeTxHashSetter:
    """Tests for the safe_tx_hash setter validation."""

    def test_valid_hash_strips_prefix(self) -> None:
        """Test that a valid hash is stored with 0x prefix stripped."""
        behaviour = _make_base_behaviour()
        valid_hash = "0x" + "ab" * ((TX_HASH_LENGTH - 2) // 2)
        assert len(valid_hash) == TX_HASH_LENGTH
        behaviour.safe_tx_hash = valid_hash
        assert behaviour._safe_tx_hash == valid_hash[2:]

    def test_invalid_length_raises(self) -> None:
        """Test that a hash with incorrect length raises ValueError."""
        behaviour = _make_base_behaviour()
        with pytest.raises(ValueError, match="Incorrect length"):
            behaviour.safe_tx_hash = "0xtooshort"


class TestTxsValue:
    """Tests for the txs_value property."""

    def test_empty_batches(self) -> None:
        """Test txs_value is 0 with no batches."""
        behaviour = _make_base_behaviour()
        assert behaviour.txs_value == 0

    def test_sums_batch_values(self) -> None:
        """Test txs_value sums all batch values."""
        behaviour = _make_base_behaviour()
        behaviour.multisend_batches = [
            MultisendBatch(to="0x1", data=b"", value=100),
            MultisendBatch(to="0x2", data=b"", value=250),
        ]
        assert behaviour.txs_value == 350


class TestMultiSendTxs:
    """Tests for the multi_send_txs property."""

    def test_converts_batches_to_dicts(self) -> None:
        """Test multi_send_txs converts MultisendBatch dataclasses to dicts."""
        behaviour = _make_base_behaviour()
        behaviour.multisend_batches = [
            MultisendBatch(to="0xaddr", data=b"\x01\x02", value=50),
        ]
        result = behaviour.multi_send_txs
        assert len(result) == 1
        assert result[0]["to"] == "0xaddr"
        assert result[0]["value"] == 50
        assert result[0]["data"] == b"\x01\x02"


class TestTxHex:
    """Tests for the tx_hex property."""

    def test_raises_without_safe_tx_hash(self) -> None:
        """Test tx_hex raises ValueError when safe_tx_hash is empty."""
        behaviour = _make_base_behaviour()
        with pytest.raises(ValueError, match="without a safe transaction hash"):
            _ = behaviour.tx_hex


class TestDefaultError:
    """Tests for the default_error method."""

    def test_logs_error_with_contract_info(self) -> None:
        """Test default_error logs an error containing contract ID and callable."""
        behaviour = _make_base_behaviour()
        response_msg = MagicMock()
        behaviour.default_error("my_contract", "my_callable", response_msg)
        log_call = behaviour.context.logger.error.call_args[0][0]
        assert "my_contract" in log_call
        assert "my_callable" in log_call


class TestContractInteractionError:
    """Tests for contract_interaction_error routing logic."""

    @pytest.mark.parametrize("level", ["info", "warning", "error"])
    def test_routes_to_correct_log_level(self, level: MagicMock) -> None:
        """Test that the method uses the log level from the response body."""
        behaviour = _make_base_behaviour()
        response_msg = MagicMock()
        response_msg.raw_transaction.body = {level: f"msg at {level}"}

        behaviour.contract_interaction_error("contract_id", "callable", response_msg)

        logger_method = getattr(behaviour.context.logger, level)
        logger_method.assert_called_once_with(f"msg at {level}")

    def test_info_takes_priority_over_warning(self) -> None:
        """Test that info level is checked first."""
        behaviour = _make_base_behaviour()
        response_msg = MagicMock()
        response_msg.raw_transaction.body = {
            "info": "info message",
            "warning": "warning message",
        }

        behaviour.contract_interaction_error("contract_id", "callable", response_msg)

        behaviour.context.logger.info.assert_called_once_with("info message")
        behaviour.context.logger.warning.assert_not_called()

    def test_falls_through_to_default_error(self) -> None:
        """Test that when no level key is found, default_error is called."""
        behaviour = _make_base_behaviour()
        response_msg = MagicMock()
        response_msg.raw_transaction.body = {}

        behaviour.contract_interaction_error("contract_id", "callable", response_msg)

        behaviour.context.logger.error.assert_called_once()
        log_call = behaviour.context.logger.error.call_args[0][0]
        assert "contract_id" in log_call


class TestContractInteract:
    """Tests for the contract_interact generator method."""

    def test_success_sets_attribute(self) -> None:
        """Test that successful contract interaction sets the placeholder attribute."""
        behaviour = _make_base_behaviour()

        response_msg = MagicMock()
        response_msg.performative = ContractApiMessage.Performative.RAW_TRANSACTION
        response_msg.raw_transaction.body = {"data_key": "result_value"}

        def mock_get_contract_api_response(
            *args: Any, **kwargs: Any
        ) -> Generator[None, None, MagicMock]:
            yield
            return response_msg

        behaviour.get_contract_api_response = mock_get_contract_api_response  # type: ignore[method-assign]

        gen = behaviour.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore[arg-type]
            contract_address="0xaddr",
            contract_public_id=MagicMock(),
            contract_callable="do_thing",
            data_key="data_key",
            placeholder="_test_result",
        )

        result = _drive_generator(gen)
        assert result is True
        assert behaviour._test_result == "result_value"

    def test_wrong_performative_returns_false(self) -> None:
        """Test that wrong performative in response returns False."""
        behaviour = _make_base_behaviour()

        response_msg = MagicMock()
        response_msg.performative = ContractApiMessage.Performative.ERROR

        def mock_get_contract_api_response(
            *args: Any, **kwargs: Any
        ) -> Generator[None, None, MagicMock]:
            yield
            return response_msg

        behaviour.get_contract_api_response = mock_get_contract_api_response  # type: ignore[method-assign]

        gen = behaviour.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore[arg-type]
            contract_address="0xaddr",
            contract_public_id=MagicMock(),
            contract_callable="do_thing",
            data_key="data_key",
            placeholder="_test_result",
        )

        result = _drive_generator(gen)
        assert result is False
        behaviour.context.logger.error.assert_called()

    def test_missing_data_key_returns_false(self) -> None:
        """Test that missing data_key in response body returns False."""
        behaviour = _make_base_behaviour()

        response_msg = MagicMock()
        response_msg.performative = ContractApiMessage.Performative.RAW_TRANSACTION
        response_msg.raw_transaction.body = {"other_key": "value"}

        def mock_get_contract_api_response(
            *args: Any, **kwargs: Any
        ) -> Generator[None, None, MagicMock]:
            yield
            return response_msg

        behaviour.get_contract_api_response = mock_get_contract_api_response  # type: ignore[method-assign]

        gen = behaviour.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore[arg-type]
            contract_address="0xaddr",
            contract_public_id=MagicMock(),
            contract_callable="do_thing",
            data_key="data_key",
            placeholder="_test_result",
        )

        result = _drive_generator(gen)
        assert result is False


class TestBuildMultisendData:
    """Tests for _build_multisend_data generator method."""

    def test_success_sets_multisend_data(self) -> None:
        """Test successful multisend data build sets the data bytes."""
        behaviour = _make_base_behaviour()
        behaviour._context.params.multisend_address = "0xmultisend"
        behaviour._context.params.mech_chain_id = "100"

        response_msg = MagicMock()
        response_msg.performative = ContractApiMessage.Performative.RAW_TRANSACTION
        response_msg.raw_transaction.body = {"data": "0xaabbccdd"}

        def mock_get_contract_api_response(
            *args: Any, **kwargs: Any
        ) -> Generator[None, None, MagicMock]:
            yield
            return response_msg

        behaviour.get_contract_api_response = mock_get_contract_api_response  # type: ignore[method-assign]

        gen = behaviour._build_multisend_data()
        result = _drive_generator(gen)

        assert result is True
        assert behaviour.multisend_data == bytes.fromhex("aabbccdd")

    def test_wrong_performative_returns_false(self) -> None:
        """Test wrong performative returns False."""
        behaviour = _make_base_behaviour()
        behaviour._context.params.multisend_address = "0xmultisend"
        behaviour._context.params.mech_chain_id = "100"

        response_msg = MagicMock()
        response_msg.performative = ContractApiMessage.Performative.ERROR

        def mock_get_contract_api_response(
            *args: Any, **kwargs: Any
        ) -> Generator[None, None, MagicMock]:
            yield
            return response_msg

        behaviour.get_contract_api_response = mock_get_contract_api_response  # type: ignore[method-assign]

        gen = behaviour._build_multisend_data()
        result = _drive_generator(gen)

        assert result is False

    def test_missing_data_key_returns_false(self) -> None:
        """Test missing 'data' key in response returns False."""
        behaviour = _make_base_behaviour()
        behaviour._context.params.multisend_address = "0xmultisend"
        behaviour._context.params.mech_chain_id = "100"

        response_msg = MagicMock()
        response_msg.performative = ContractApiMessage.Performative.RAW_TRANSACTION
        response_msg.raw_transaction.body = {"other": "value"}

        def mock_get_contract_api_response(
            *args: Any, **kwargs: Any
        ) -> Generator[None, None, MagicMock]:
            yield
            return response_msg

        behaviour.get_contract_api_response = mock_get_contract_api_response  # type: ignore[method-assign]

        gen = behaviour._build_multisend_data()
        result = _drive_generator(gen)

        assert result is False
