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

"""Tests for the request behaviour module properties."""

from unittest.mock import MagicMock

import pytest

from packages.valory.skills.mech_interact_abci.behaviours.request import (
    DECIMALS_6,
    DECIMALS_18,
    METADATA_FILENAME,
    MechRequestBehaviour,
    NVM_PAYMENT_TYPES,
    PaymentType,
    TOKEN_PAYMENT_TYPES,
)


def _make_request_behaviour() -> MechRequestBehaviour:
    """Create a MechRequestBehaviour with mocked internals."""
    behaviour = MechRequestBehaviour.__new__(MechRequestBehaviour)
    behaviour.multisend_batches = []
    behaviour.multisend_data = b""
    behaviour._safe_tx_hash = ""
    behaviour._context = MagicMock()
    # Init request-specific attributes
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


class TestPaymentType:
    """Tests for PaymentType enum."""

    def test_native_value(self) -> None:
        """Test NATIVE payment type value."""
        assert PaymentType.NATIVE.value.startswith("0x")

    def test_token_payment_types(self) -> None:
        """Test TOKEN_PAYMENT_TYPES contains expected types."""
        assert PaymentType.TOKEN_USDC in TOKEN_PAYMENT_TYPES
        assert PaymentType.TOKEN_OLAS in TOKEN_PAYMENT_TYPES

    def test_nvm_payment_types(self) -> None:
        """Test NVM_PAYMENT_TYPES contains expected types."""
        assert PaymentType.NATIVE_NVM in NVM_PAYMENT_TYPES
        assert PaymentType.TOKEN_NVM_USDC in NVM_PAYMENT_TYPES


class TestMechRequestBehaviourProperties:
    """Tests for MechRequestBehaviour property methods."""

    def test_request_data_getter_setter(self) -> None:
        """Test request_data getter and setter."""
        behaviour = _make_request_behaviour()
        assert behaviour.request_data == b""
        behaviour.request_data = b"new_data"
        assert behaviour.request_data == b"new_data"

    def test_price_getter_setter(self) -> None:
        """Test price getter and setter."""
        behaviour = _make_request_behaviour()
        assert behaviour.price == 0
        behaviour.price = 42
        assert behaviour.price == 42

    def test_mech_payment_type_setter_valid(self) -> None:
        """Test mech_payment_type setter with valid payment type."""
        behaviour = _make_request_behaviour()
        behaviour.mech_payment_type = PaymentType.TOKEN_OLAS.value
        assert behaviour.mech_payment_type == PaymentType.TOKEN_OLAS

    def test_mech_payment_type_setter_invalid(self) -> None:
        """Test mech_payment_type setter with invalid payment type."""
        behaviour = _make_request_behaviour()
        behaviour.mech_payment_type = "0xinvalid"
        # Should log warning but not crash
        behaviour.context.logger.warning.assert_called_once()
        # Payment type should remain as the default
        assert behaviour.mech_payment_type == PaymentType.NATIVE

    def test_using_native(self) -> None:
        """Test using_native property."""
        behaviour = _make_request_behaviour()
        assert behaviour.using_native is True

    def test_using_native_false(self) -> None:
        """Test using_native returns False for token."""
        behaviour = _make_request_behaviour()
        behaviour._mech_payment_type = PaymentType.TOKEN_OLAS
        assert behaviour.using_native is False

    def test_using_token(self) -> None:
        """Test using_token property."""
        behaviour = _make_request_behaviour()
        behaviour._mech_payment_type = PaymentType.TOKEN_USDC
        assert behaviour.using_token is True

    def test_using_token_false(self) -> None:
        """Test using_token returns False for native."""
        behaviour = _make_request_behaviour()
        assert behaviour.using_token is False

    def test_using_nevermined(self) -> None:
        """Test using_nevermined property."""
        behaviour = _make_request_behaviour()
        behaviour._mech_payment_type = PaymentType.NATIVE_NVM
        assert behaviour.using_nevermined is True

    def test_using_nevermined_false(self) -> None:
        """Test using_nevermined returns False for native."""
        behaviour = _make_request_behaviour()
        assert behaviour.using_nevermined is False

    def test_token_decimals_usdc(self) -> None:
        """Test token_decimals for USDC payment."""
        behaviour = _make_request_behaviour()
        behaviour._mech_payment_type = PaymentType.TOKEN_USDC
        assert behaviour.token_decimals == DECIMALS_6

    def test_token_decimals_nvm_usdc(self) -> None:
        """Test token_decimals for NVM USDC payment."""
        behaviour = _make_request_behaviour()
        behaviour._mech_payment_type = PaymentType.TOKEN_NVM_USDC
        assert behaviour.token_decimals == DECIMALS_6

    def test_token_decimals_native(self) -> None:
        """Test token_decimals for native payment."""
        behaviour = _make_request_behaviour()
        assert behaviour.token_decimals == DECIMALS_18

    def test_token_decimals_olas(self) -> None:
        """Test token_decimals for OLAS payment."""
        behaviour = _make_request_behaviour()
        behaviour._mech_payment_type = PaymentType.TOKEN_OLAS
        assert behaviour.token_decimals == DECIMALS_18

    def test_nvm_balance_tracker_contract_id_native_nvm(self) -> None:
        """Test nvm_balance_tracker_contract_id for NATIVE_NVM."""
        behaviour = _make_request_behaviour()
        behaviour._mech_payment_type = PaymentType.NATIVE_NVM
        result = behaviour.nvm_balance_tracker_contract_id
        assert result is not None

    def test_nvm_balance_tracker_contract_id_token_nvm(self) -> None:
        """Test nvm_balance_tracker_contract_id for TOKEN_NVM_USDC."""
        behaviour = _make_request_behaviour()
        behaviour._mech_payment_type = PaymentType.TOKEN_NVM_USDC
        result = behaviour.nvm_balance_tracker_contract_id
        assert result is not None

    def test_nvm_balance_tracker_contract_id_invalid(self) -> None:
        """Test nvm_balance_tracker_contract_id raises for non-NVM type."""
        behaviour = _make_request_behaviour()
        behaviour._mech_payment_type = PaymentType.NATIVE
        with pytest.raises(ValueError, match="Unknown"):
            _ = behaviour.nvm_balance_tracker_contract_id

    def test_subscription_balance_none_logs_error(self) -> None:
        """Test subscription_balance logs error when not fetched."""
        behaviour = _make_request_behaviour()
        result = behaviour.subscription_balance
        assert result is None
        behaviour.context.logger.error.assert_called_once()

    def test_subscription_balance_with_value(self) -> None:
        """Test subscription_balance returns value when set."""
        behaviour = _make_request_behaviour()
        behaviour._subscription_balance = 100
        assert behaviour.subscription_balance == 100

    def test_nvm_balance_none_logs_error(self) -> None:
        """Test nvm_balance logs error when not fetched."""
        behaviour = _make_request_behaviour()
        result = behaviour.nvm_balance
        assert result is None
        behaviour.context.logger.error.assert_called_once()

    def test_nvm_balance_with_value(self) -> None:
        """Test nvm_balance returns value when set."""
        behaviour = _make_request_behaviour()
        behaviour._nvm_balance = 200
        assert behaviour.nvm_balance == 200

    def test_total_nvm_balance_both_set(self) -> None:
        """Test total_nvm_balance when both balances are set."""
        behaviour = _make_request_behaviour()
        behaviour._subscription_balance = 100
        behaviour._nvm_balance = 200
        assert behaviour.total_nvm_balance == 300

    def test_total_nvm_balance_none(self) -> None:
        """Test total_nvm_balance returns None when one is not set."""
        behaviour = _make_request_behaviour()
        behaviour._subscription_balance = 100
        # nvm_balance is None
        assert behaviour.total_nvm_balance is None

    def test_subscription_address_none_logs_error(self) -> None:
        """Test subscription_address logs error when not fetched."""
        behaviour = _make_request_behaviour()
        result = behaviour.subscription_address
        assert result is None
        behaviour.context.logger.error.assert_called_once()

    def test_subscription_id_none_logs_error(self) -> None:
        """Test subscription_id logs error when not fetched."""
        behaviour = _make_request_behaviour()
        result = behaviour.subscription_id
        assert result is None
        behaviour.context.logger.error.assert_called_once()

    def test_mech_max_delivery_rate_none_logs_error(self) -> None:
        """Test mech_max_delivery_rate logs error when not fetched."""
        behaviour = _make_request_behaviour()
        result = behaviour.mech_max_delivery_rate
        assert result is None
        behaviour.context.logger.error.assert_called_once()

    def test_balance_tracker_none_logs_warning(self) -> None:
        """Test balance_tracker logs warning when not fetched."""
        behaviour = _make_request_behaviour()
        result = behaviour.balance_tracker
        assert result is None
        behaviour.context.logger.warning.assert_called_once()

    def test_approval_data_none_logs_warning(self) -> None:
        """Test approval_data logs warning when not built."""
        behaviour = _make_request_behaviour()
        result = behaviour.approval_data
        assert result is None
        behaviour.context.logger.warning.assert_called_once()

    def test_wei_to_unit_default_decimals(self) -> None:
        """Test wei_to_unit with default decimals."""
        behaviour = _make_request_behaviour()
        # Default is NATIVE (18 decimals)
        result = behaviour.wei_to_unit(10**18)
        assert result == 1.0

    def test_wei_to_unit_custom_decimals(self) -> None:
        """Test wei_to_unit with custom decimals."""
        behaviour = _make_request_behaviour()
        result = behaviour.wei_to_unit(10**6, decimals=6)
        assert result == 1.0

    def test_metadata_filepath(self) -> None:
        """Test metadata_filepath creates a temp path."""
        behaviour = _make_request_behaviour()
        filepath = behaviour.metadata_filepath
        assert filepath.endswith(METADATA_FILENAME)

    def test_decode_hex_to_bytes_with_0x_prefix(self) -> None:
        """Test _decode_hex_to_bytes strips 0x prefix."""
        behaviour = _make_request_behaviour()
        result = behaviour._decode_hex_to_bytes("0xabcd", "test")
        assert result == b"\xab\xcd"

    def test_decode_hex_to_bytes_without_prefix(self) -> None:
        """Test _decode_hex_to_bytes works without 0x prefix."""
        behaviour = _make_request_behaviour()
        result = behaviour._decode_hex_to_bytes("abcd", "test")
        assert result == b"\xab\xcd"

    def test_decode_hex_to_bytes_invalid(self) -> None:
        """Test _decode_hex_to_bytes returns None on invalid hex."""
        behaviour = _make_request_behaviour()
        result = behaviour._decode_hex_to_bytes("0xZZZZ", "test")
        assert result is None
        behaviour.context.logger.error.assert_called_once()
