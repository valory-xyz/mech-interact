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

import pytest

from packages.valory.contracts.erc20.contract import ERC20TokenContract
from packages.valory.protocols.contract_api import ContractApiMessage
from packages.valory.skills.mech_interact_abci.behaviours.request import (
    DECIMALS_6,
    DECIMALS_18,
    METADATA_FILENAME,
    NVM_PAYMENT_TYPES,
    PaymentType,
    TOKEN_PAYMENT_TYPES,
)
from packages.valory.skills.mech_interact_abci.tests.behaviours.conftest import (
    assert_unset_property_logs,
)


def _drive_generator(gen):
    """Drive a generator until it returns, ignoring yielded values."""
    try:
        while True:
            next(gen)
    except StopIteration as e:
        return e.value


class TestPaymentType:
    """Tests for PaymentType enum."""

    def test_native_value_is_hex(self) -> None:
        """Test NATIVE payment type value is hex-prefixed."""
        assert PaymentType.NATIVE.value.startswith("0x")

    def test_token_payment_types_membership(self) -> None:
        """Test TOKEN_PAYMENT_TYPES contains expected types."""
        assert PaymentType.TOKEN_USDC in TOKEN_PAYMENT_TYPES
        assert PaymentType.TOKEN_OLAS in TOKEN_PAYMENT_TYPES

    def test_nvm_payment_types_membership(self) -> None:
        """Test NVM_PAYMENT_TYPES contains expected types."""
        assert PaymentType.NATIVE_NVM in NVM_PAYMENT_TYPES
        assert PaymentType.TOKEN_NVM_USDC in NVM_PAYMENT_TYPES


class TestRequestDataAndPrice:
    """Tests for request_data and price getters/setters."""

    def test_request_data_roundtrip(self, request_behaviour) -> None:
        """Test request_data getter and setter."""
        assert request_behaviour.request_data == b""
        request_behaviour.request_data = b"new_data"
        assert request_behaviour.request_data == b"new_data"

    def test_price_roundtrip(self, request_behaviour) -> None:
        """Test price getter and setter."""
        assert request_behaviour.price == 0
        request_behaviour.price = 42
        assert request_behaviour.price == 42


class TestMechPaymentTypeSetter:
    """Tests for mech_payment_type setter."""

    def test_valid_payment_type(self, request_behaviour) -> None:
        """Test setting a valid payment type."""
        request_behaviour.mech_payment_type = PaymentType.TOKEN_OLAS.value
        assert request_behaviour.mech_payment_type == PaymentType.TOKEN_OLAS

    def test_invalid_payment_type_warns(self, request_behaviour) -> None:
        """Test setting an invalid payment type logs warning and keeps default."""
        request_behaviour.mech_payment_type = "0xinvalid"
        request_behaviour.context.logger.warning.assert_called_once()
        assert request_behaviour.mech_payment_type == PaymentType.NATIVE


class TestPaymentTypeProperties:
    """Tests for using_native, using_token, using_nevermined, token_decimals."""

    @pytest.mark.parametrize(
        "payment_type,prop,expected",
        [
            (PaymentType.NATIVE, "using_native", True),
            (PaymentType.TOKEN_OLAS, "using_native", False),
            (PaymentType.TOKEN_USDC, "using_token", True),
            (PaymentType.NATIVE, "using_token", False),
            (PaymentType.NATIVE_NVM, "using_nevermined", True),
            (PaymentType.NATIVE, "using_nevermined", False),
        ],
    )
    def test_payment_type_boolean_properties(
        self, request_behaviour, payment_type, prop, expected
    ) -> None:
        """Test boolean payment type properties for various types."""
        request_behaviour._mech_payment_type = payment_type
        assert getattr(request_behaviour, prop) is expected

    @pytest.mark.parametrize(
        "payment_type,expected_decimals",
        [
            (PaymentType.TOKEN_USDC, DECIMALS_6),
            (PaymentType.TOKEN_NVM_USDC, DECIMALS_6),
            (PaymentType.NATIVE, DECIMALS_18),
            (PaymentType.TOKEN_OLAS, DECIMALS_18),
        ],
    )
    def test_token_decimals(
        self, request_behaviour, payment_type, expected_decimals
    ) -> None:
        """Test token_decimals returns correct value per payment type."""
        request_behaviour._mech_payment_type = payment_type
        assert request_behaviour.token_decimals == expected_decimals


class TestNvmBalanceTrackerContractId:
    """Tests for nvm_balance_tracker_contract_id."""

    @pytest.mark.parametrize(
        "payment_type",
        [PaymentType.NATIVE_NVM, PaymentType.TOKEN_NVM_USDC],
    )
    def test_valid_nvm_types(self, request_behaviour, payment_type) -> None:
        """Test nvm_balance_tracker_contract_id returns a value for NVM types."""
        request_behaviour._mech_payment_type = payment_type
        assert request_behaviour.nvm_balance_tracker_contract_id is not None

    def test_non_nvm_type_raises(self, request_behaviour) -> None:
        """Test nvm_balance_tracker_contract_id raises for non-NVM type."""
        request_behaviour._mech_payment_type = PaymentType.NATIVE
        with pytest.raises(ValueError, match="Unknown"):
            _ = request_behaviour.nvm_balance_tracker_contract_id


class TestNonePropertyLogging:
    """Tests for properties that log errors/warnings when accessed before being set."""

    @pytest.mark.parametrize(
        "prop,log_method",
        [
            ("subscription_balance", "error"),
            ("nvm_balance", "error"),
            ("subscription_address", "error"),
            ("subscription_id", "error"),
            ("mech_max_delivery_rate", "error"),
            ("balance_tracker", "warning"),
            ("approval_data", "warning"),
        ],
    )
    def test_none_logs_message(self, request_behaviour, prop, log_method) -> None:
        """Test that accessing unset properties logs the appropriate message."""
        assert_unset_property_logs(request_behaviour, prop, log_method)

    def test_subscription_balance_returns_value_when_set(
        self, request_behaviour
    ) -> None:
        """Test subscription_balance returns value when set."""
        request_behaviour._subscription_balance = 100
        assert request_behaviour.subscription_balance == 100

    def test_nvm_balance_returns_value_when_set(self, request_behaviour) -> None:
        """Test nvm_balance returns value when set."""
        request_behaviour._nvm_balance = 200
        assert request_behaviour.nvm_balance == 200


class TestTotalNvmBalance:
    """Tests for total_nvm_balance."""

    def test_both_set(self, request_behaviour) -> None:
        """Test total_nvm_balance when both balances are set."""
        request_behaviour._subscription_balance = 100
        request_behaviour._nvm_balance = 200
        assert request_behaviour.total_nvm_balance == 300

    def test_returns_none_when_partial(self, request_behaviour) -> None:
        """Test total_nvm_balance returns None when one balance is missing."""
        request_behaviour._subscription_balance = 100
        assert request_behaviour.total_nvm_balance is None


class TestWeiToUnit:
    """Tests for wei_to_unit."""

    @pytest.mark.parametrize(
        "wei,decimals,expected",
        [
            (10**18, 18, 1.0),
            (10**6, 6, 1.0),
            (5 * 10**17, 18, 0.5),
        ],
    )
    def test_conversion(self, request_behaviour, wei, decimals, expected) -> None:
        """Test wei_to_unit converts correctly."""
        assert request_behaviour.wei_to_unit(wei, decimals=decimals) == expected

    def test_default_decimals_uses_token_decimals(self, request_behaviour) -> None:
        """Test wei_to_unit uses token_decimals when decimals is not provided."""
        # Default payment type is NATIVE (18 decimals)
        assert request_behaviour.wei_to_unit(10**18) == 1.0


class TestMetadataFilepath:
    """Tests for metadata_filepath."""

    def test_ends_with_filename(self, request_behaviour) -> None:
        """Test metadata_filepath creates a path ending with METADATA_FILENAME."""
        assert request_behaviour.metadata_filepath.endswith(METADATA_FILENAME)


class TestDecodeHexToBytes:
    """Tests for _decode_hex_to_bytes."""

    def test_with_0x_prefix(self, request_behaviour) -> None:
        """Test strips 0x prefix and decodes."""
        assert request_behaviour._decode_hex_to_bytes("0xabcd", "t") == b"\xab\xcd"

    def test_without_prefix(self, request_behaviour) -> None:
        """Test decodes without 0x prefix."""
        assert request_behaviour._decode_hex_to_bytes("abcd", "t") == b"\xab\xcd"

    def test_invalid_hex_returns_none(self, request_behaviour) -> None:
        """Test returns None and logs error on invalid hex."""
        result = request_behaviour._decode_hex_to_bytes("0xZZZZ", "t")
        assert result is None
        request_behaviour.context.logger.error.assert_called_once()


def _gen_returning(value):
    """Build a generator-function that yields once and returns ``value``."""

    def _g(*_args, **_kwargs):
        yield
        return value

    return _g


class TestApproveBalanceTracker:
    """Tests for _approve_balance_tracker (thin wrapper around contract_interact)."""

    def _setup(self, request_behaviour, *, return_value=True):
        captured = {}

        def mock_contract_interact(**kwargs):
            captured.update(kwargs)
            yield
            return return_value

        request_behaviour.contract_interact = mock_contract_interact
        request_behaviour._balance_tracker = "0xtracker"
        request_behaviour._mech_max_delivery_rate = 1234
        request_behaviour.context.params.price_token = "0xtoken"  # nosec B105
        request_behaviour.context.params.mech_chain_id = 100
        return captured

    def test_calls_contract_interact_with_correct_kwargs(
        self, request_behaviour
    ) -> None:
        """Verify every kwarg passed to contract_interact matches the ERC20 approve call."""
        captured = self._setup(request_behaviour)

        result = _drive_generator(request_behaviour._approve_balance_tracker())

        assert result is True
        assert (
            captured["performative"]
            == ContractApiMessage.Performative.GET_RAW_TRANSACTION
        )
        assert captured["contract_address"] == "0xtoken"
        assert captured["contract_public_id"] == ERC20TokenContract.contract_id
        assert captured["contract_callable"] == "build_approval_tx"
        assert captured["data_key"] == "data"
        assert captured["placeholder"] == "_approval_data"
        assert captured["spender"] == "0xtracker"
        assert captured["amount"] == 1234
        assert captured["chain_id"] == 100

    def test_propagates_failure_from_contract_interact(self, request_behaviour) -> None:
        """A False from contract_interact propagates back unchanged."""
        self._setup(request_behaviour, return_value=False)
        result = _drive_generator(request_behaviour._approve_balance_tracker())
        assert result is False

    def test_logs_info_on_invocation(self, request_behaviour) -> None:
        """The behaviour announces that it is building the approval."""
        self._setup(request_behaviour)
        _drive_generator(request_behaviour._approve_balance_tracker())
        request_behaviour.context.logger.info.assert_called_once_with(
            "Building approval for token payment."
        )


class TestBuildTokenApproval:
    """Tests for _build_token_approval orchestration and batch construction."""

    def test_appends_multisend_batch_with_approval_bytes(
        self, request_behaviour
    ) -> None:
        """Approval bytes are passed through to MultisendBatch.data unchanged.

        Regression guard: the previous implementation called
        bytes.fromhex(self.approval_data.removeprefix("0x")) — which raises
        TypeError because approval_data is bytes, not str. Asserting the
        batch is built with the exact bytes catches that re-introduction.
        """
        request_behaviour._balance_tracker = "0xtracker"
        request_behaviour._approval_data = b"\xab\xcd\xef"
        request_behaviour.context.params.price_token = "0xtoken"  # nosec B105
        request_behaviour._approve_balance_tracker = _gen_returning(True)

        result = _drive_generator(request_behaviour._build_token_approval())

        assert result is True
        assert len(request_behaviour.multisend_batches) == 1
        batch = request_behaviour.multisend_batches[0]
        assert batch.to == "0xtoken"
        assert batch.data == b"\xab\xcd\xef"
        assert isinstance(batch.data, bytes)

    def test_skips_get_balance_tracker_when_already_set(
        self, request_behaviour
    ) -> None:
        """If _balance_tracker is already populated, _get_balance_tracker is skipped."""
        request_behaviour._balance_tracker = "0xtracker"
        request_behaviour._approval_data = b"\x01"
        request_behaviour.context.params.price_token = "0xtoken"  # nosec B105
        get_called = False

        def get_tracker():
            nonlocal get_called
            get_called = True
            yield
            return True

        request_behaviour._get_balance_tracker = get_tracker
        request_behaviour._approve_balance_tracker = _gen_returning(True)

        result = _drive_generator(request_behaviour._build_token_approval())

        assert result is True
        assert get_called is False

    def test_calls_get_balance_tracker_when_unset(self, request_behaviour) -> None:
        """If _balance_tracker is unset, _get_balance_tracker runs first."""
        request_behaviour._balance_tracker = None
        request_behaviour._approval_data = b"\x01"
        request_behaviour.context.params.price_token = "0xtoken"  # nosec B105
        request_behaviour._get_balance_tracker = _gen_returning(True)
        request_behaviour._approve_balance_tracker = _gen_returning(True)

        result = _drive_generator(request_behaviour._build_token_approval())

        assert result is True
        assert len(request_behaviour.multisend_batches) == 1

    def test_returns_false_and_warns_when_get_balance_tracker_fails(
        self, request_behaviour
    ) -> None:
        """A failure to fetch the balance tracker short-circuits with a warning."""
        request_behaviour._balance_tracker = None
        request_behaviour._get_balance_tracker = _gen_returning(False)
        approve_called = False

        def approve():
            nonlocal approve_called
            approve_called = True
            yield
            return True

        request_behaviour._approve_balance_tracker = approve

        result = _drive_generator(request_behaviour._build_token_approval())

        assert result is False
        assert approve_called is False
        assert request_behaviour.multisend_batches == []
        request_behaviour.context.logger.warning.assert_called_once_with(
            "Failed to get balance tracker."
        )

    def test_returns_false_and_errors_when_approve_fails(
        self, request_behaviour
    ) -> None:
        """A failure to build the approval short-circuits with an error log."""
        request_behaviour._balance_tracker = "0xtracker"
        request_behaviour._approve_balance_tracker = _gen_returning(False)

        result = _drive_generator(request_behaviour._build_token_approval())

        assert result is False
        assert request_behaviour.multisend_batches == []
        request_behaviour.context.logger.error.assert_called_once_with(
            "Failed to build approval data."
        )
