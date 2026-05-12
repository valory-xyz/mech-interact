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

"""Tests for the request behaviour module."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from packages.valory.skills.mech_interact_abci.behaviours.request import (
    DECIMALS_6,
    DECIMALS_18,
    MechRequestBehaviour,
    PaymentType,
)


def _make_request_behaviour(**overrides: Any) -> MechRequestBehaviour:
    """Create a MechRequestBehaviour with mocked dependencies."""
    behaviour = MechRequestBehaviour.__new__(MechRequestBehaviour)
    mock_context = MagicMock()
    behaviour._context = mock_context
    behaviour._mech_payment_type = PaymentType.NATIVE
    behaviour._mech_max_delivery_rate = None
    behaviour._subscription_balance = None
    behaviour._nvm_balance = None
    behaviour._subscription_address = None
    behaviour._subscription_id = None
    behaviour._balance_tracker = None
    behaviour._approval_data = None
    behaviour.token_balance = 0
    behaviour.wallet_balance = 0

    for key, value in overrides.items():
        setattr(behaviour, key, value)

    return behaviour


class TestDecodeHexToBytes:
    """Tests for _decode_hex_to_bytes."""

    def test_valid_hex_without_prefix(self) -> None:
        """Test decoding valid hex string without 0x prefix."""
        behaviour = _make_request_behaviour()
        result = behaviour._decode_hex_to_bytes("aabbccdd", "test_data")
        assert result == b"\xaa\xbb\xcc\xdd"

    def test_valid_hex_with_prefix(self) -> None:
        """Test decoding valid hex string with 0x prefix."""
        behaviour = _make_request_behaviour()
        result = behaviour._decode_hex_to_bytes("0xaabbccdd", "test_data")
        assert result == b"\xaa\xbb\xcc\xdd"

    def test_invalid_hex_returns_none(self) -> None:
        """Test decoding invalid hex string returns None and logs error."""
        behaviour = _make_request_behaviour()
        result = behaviour._decode_hex_to_bytes("not_hex!", "test_data")
        assert result is None
        behaviour.context.logger.error.assert_called_once()

    def test_empty_hex_with_prefix(self) -> None:
        """Test decoding '0x' returns empty bytes."""
        behaviour = _make_request_behaviour()
        result = behaviour._decode_hex_to_bytes("0x", "test_data")
        assert result == b""


class TestMechPaymentTypeSetter:
    """Tests for the mech_payment_type setter."""

    def test_valid_native_payment_type(self) -> None:
        """Test setting a valid NATIVE payment type."""
        behaviour = _make_request_behaviour()
        behaviour.mech_payment_type = PaymentType.NATIVE.value
        assert behaviour._mech_payment_type == PaymentType.NATIVE

    def test_valid_token_olas_payment_type(self) -> None:
        """Test setting a valid TOKEN_OLAS payment type."""
        behaviour = _make_request_behaviour()
        behaviour.mech_payment_type = PaymentType.TOKEN_OLAS.value
        assert behaviour._mech_payment_type == PaymentType.TOKEN_OLAS

    def test_invalid_payment_type_logs_warning(self) -> None:
        """Test setting an invalid payment type logs a warning and keeps previous value."""
        behaviour = _make_request_behaviour()
        behaviour.mech_payment_type = "0xinvalid_type"
        behaviour.context.logger.warning.assert_called_once()
        # Should keep the default NATIVE type
        assert behaviour._mech_payment_type == PaymentType.NATIVE


class TestPaymentTypeProperties:
    """Tests for using_native, using_token, using_nevermined properties."""

    def test_using_native(self) -> None:
        """Test using_native returns True for NATIVE payment type."""
        behaviour = _make_request_behaviour()
        assert behaviour.using_native is True
        assert behaviour.using_token is False
        assert behaviour.using_nevermined is False

    def test_using_token_olas(self) -> None:
        """Test using_token returns True for TOKEN_OLAS payment type."""
        behaviour = _make_request_behaviour(_mech_payment_type=PaymentType.TOKEN_OLAS)
        assert behaviour.using_native is False
        assert behaviour.using_token is True
        assert behaviour.using_nevermined is False

    def test_using_token_usdc(self) -> None:
        """Test using_token returns True for TOKEN_USDC payment type."""
        behaviour = _make_request_behaviour(_mech_payment_type=PaymentType.TOKEN_USDC)
        assert behaviour.using_token is True

    def test_using_nevermined_native(self) -> None:
        """Test using_nevermined returns True for NATIVE_NVM payment type."""
        behaviour = _make_request_behaviour(_mech_payment_type=PaymentType.NATIVE_NVM)
        assert behaviour.using_native is False
        assert behaviour.using_token is False
        assert behaviour.using_nevermined is True


class TestTokenDecimals:
    """Tests for token_decimals property."""

    def test_native_uses_18_decimals(self) -> None:
        """Test NATIVE payment type uses 18 decimals."""
        behaviour = _make_request_behaviour()
        assert behaviour.token_decimals == DECIMALS_18

    def test_usdc_uses_6_decimals(self) -> None:
        """Test TOKEN_USDC payment type uses 6 decimals."""
        behaviour = _make_request_behaviour(_mech_payment_type=PaymentType.TOKEN_USDC)
        assert behaviour.token_decimals == DECIMALS_6

    def test_nvm_usdc_uses_6_decimals(self) -> None:
        """Test TOKEN_NVM_USDC payment type uses 6 decimals."""
        behaviour = _make_request_behaviour(
            _mech_payment_type=PaymentType.TOKEN_NVM_USDC
        )
        assert behaviour.token_decimals == DECIMALS_6

    def test_olas_uses_18_decimals(self) -> None:
        """Test TOKEN_OLAS payment type uses 18 decimals."""
        behaviour = _make_request_behaviour(_mech_payment_type=PaymentType.TOKEN_OLAS)
        assert behaviour.token_decimals == DECIMALS_18


class TestWeiToUnit:
    """Tests for wei_to_unit conversion."""

    def test_18_decimals(self) -> None:
        """Test conversion with 18 decimals (1 ETH)."""
        behaviour = _make_request_behaviour()
        result = behaviour.wei_to_unit(10**18)
        assert result == 1.0

    def test_6_decimals(self) -> None:
        """Test conversion with 6 decimals (1 USDC)."""
        behaviour = _make_request_behaviour(_mech_payment_type=PaymentType.TOKEN_USDC)
        result = behaviour.wei_to_unit(10**6)
        assert result == 1.0

    def test_custom_decimals(self) -> None:
        """Test conversion with custom decimals override."""
        behaviour = _make_request_behaviour()
        result = behaviour.wei_to_unit(1000, decimals=3)
        assert result == 1.0


class TestNvmBalanceTrackerContractId:
    """Tests for nvm_balance_tracker_contract_id property."""

    def test_native_nvm_returns_correct_contract(self) -> None:
        """Test NATIVE_NVM returns the native balance tracker contract."""
        from packages.valory.contracts.nvm_balance_tracker_native.contract import (
            BalanceTrackerNvmSubscriptionNative,
        )

        behaviour = _make_request_behaviour(_mech_payment_type=PaymentType.NATIVE_NVM)
        assert (
            behaviour.nvm_balance_tracker_contract_id
            == BalanceTrackerNvmSubscriptionNative.contract_id
        )

    def test_token_nvm_usdc_returns_correct_contract(self) -> None:
        """Test TOKEN_NVM_USDC returns the token balance tracker contract."""
        from packages.valory.contracts.nvm_balance_tracker_token.contract import (
            BalanceTrackerNvmSubscriptionToken,
        )

        behaviour = _make_request_behaviour(
            _mech_payment_type=PaymentType.TOKEN_NVM_USDC
        )
        assert (
            behaviour.nvm_balance_tracker_contract_id
            == BalanceTrackerNvmSubscriptionToken.contract_id
        )

    def test_non_nvm_type_raises(self) -> None:
        """Test non-NVM payment type raises ValueError."""
        behaviour = _make_request_behaviour(_mech_payment_type=PaymentType.NATIVE)
        with pytest.raises(ValueError, match="Unknown"):
            _ = behaviour.nvm_balance_tracker_contract_id


class TestGetPriorityMechAddress:
    """Tests for get_priority_mech_address dispatch."""

    def test_marketplace_disabled_returns_mech_contract(self) -> None:
        """Test returns mech_contract_address when marketplace is disabled."""
        behaviour = _make_request_behaviour()
        behaviour._context.params.use_mech_marketplace = False
        behaviour._context.params.mech_contract_address = "0xlegacy_mech"

        result = behaviour.get_priority_mech_address()
        assert result == "0xlegacy_mech"

    @patch.object(MechRequestBehaviour, "should_use_marketplace_v2", return_value=False)
    def test_marketplace_v1_returns_config_address(self, _mock: MagicMock) -> None:
        """Test returns priority_mech_address from config when marketplace v1."""
        behaviour = _make_request_behaviour()
        behaviour._context.params.use_mech_marketplace = True
        behaviour._context.params.valid_mechs = frozenset()
        behaviour._context.params.mech_marketplace_config.priority_mech_address = (
            "0xv1_mech"
        )

        result = behaviour.get_priority_mech_address()
        assert result == "0xv1_mech"

    @patch.object(MechRequestBehaviour, "should_use_marketplace_v2", return_value=True)
    def test_marketplace_v2_dynamic_skips_penalized(self, _mock: MagicMock) -> None:
        """Test v2 with dynamic selection skips penalized mechs."""
        behaviour = _make_request_behaviour()
        behaviour._context.params.use_mech_marketplace = True
        behaviour._context.params.mech_marketplace_config.use_dynamic_mech_selection = (
            True
        )

        mock_synced = MagicMock()
        mock_synced.ranked_mechs_addresses = ["0xpenalized", "0xgood", "0xalso_good"]
        mock_synced.priority_mech_address = "0xfallback"

        mock_shared = MagicMock()
        mock_shared.penalized_mechs = {"0xpenalized"}
        behaviour._context.state = mock_shared

        with patch.object(
            type(behaviour),
            "synchronized_data",
            new_callable=lambda: property(lambda self: mock_synced),
        ):
            result = behaviour.get_priority_mech_address()
        assert result == "0xgood"

    @patch.object(MechRequestBehaviour, "should_use_marketplace_v2", return_value=True)
    def test_marketplace_v2_dynamic_all_penalized_returns_none(
        self, _mock: MagicMock
    ) -> None:
        """All-penalized fails fast with `no_non_penalized_valid_mech`."""
        behaviour = _make_request_behaviour()
        behaviour._context.params.use_mech_marketplace = True
        behaviour._context.params.mech_marketplace_config.use_dynamic_mech_selection = (
            True
        )

        mock_synced = MagicMock()
        mock_synced.ranked_mechs_addresses = ["0xpenalized1", "0xpenalized2"]
        mock_synced.selected_mechs = []

        mock_shared = MagicMock()
        mock_shared.penalized_mechs = {"0xpenalized1", "0xpenalized2"}
        behaviour._context.state = mock_shared

        with patch.object(
            type(behaviour),
            "synchronized_data",
            new_callable=lambda: property(lambda self: mock_synced),
        ):
            result = behaviour.get_priority_mech_address()

        assert result is None
        assert mock_shared.last_failure_reason == "no_non_penalized_valid_mech"

    @patch.object(MechRequestBehaviour, "should_use_marketplace_v2", return_value=True)
    def test_v2_dynamic_all_pinned_penalized_prefers_penalized_reason(
        self,
        _mock: MagicMock,
    ) -> None:
        """All-penalized takes precedence over `no_overlap_with_selected_mechs`.

        When a pin narrows the candidate set but every pinned mech is
        penalized, `ranked_mechs_addresses` is still non-empty, so the
        more specific `no_non_penalized_valid_mech` reason wins over
        `no_overlap_with_selected_mechs`.
        """
        behaviour = _make_request_behaviour()
        behaviour._context.params.use_mech_marketplace = True
        behaviour._context.params.mech_marketplace_config.use_dynamic_mech_selection = (
            True
        )

        mock_synced = MagicMock()
        mock_synced.ranked_mechs_addresses = ["0xpinned"]
        mock_synced.selected_mechs = ["0xpinned"]

        mock_shared = MagicMock()
        mock_shared.penalized_mechs = {"0xpinned"}
        behaviour._context.state = mock_shared

        with patch.object(
            type(behaviour),
            "synchronized_data",
            new_callable=lambda: property(lambda self: mock_synced),
        ):
            result = behaviour.get_priority_mech_address()

        assert result is None
        assert mock_shared.last_failure_reason == "no_non_penalized_valid_mech"

    @patch.object(MechRequestBehaviour, "should_use_marketplace_v2", return_value=True)
    def test_v2_dynamic_writes_no_overlap_with_selected_mechs_when_empty(
        self, _mock: MagicMock
    ) -> None:
        """No candidates + non-empty pin writes `no_overlap_with_selected_mechs`."""
        behaviour = _make_request_behaviour()
        behaviour._context.params.use_mech_marketplace = True
        behaviour._context.params.mech_marketplace_config.use_dynamic_mech_selection = (
            True
        )

        mock_synced = MagicMock()
        mock_synced.ranked_mechs_addresses = []
        mock_synced.selected_mechs = ["0xpinned"]

        mock_shared = MagicMock()
        mock_shared.penalized_mechs = set()
        behaviour._context.state = mock_shared

        with patch.object(
            type(behaviour),
            "synchronized_data",
            new_callable=lambda: property(lambda self: mock_synced),
        ):
            result = behaviour.get_priority_mech_address()

        assert result is None
        assert mock_shared.last_failure_reason == "no_overlap_with_selected_mechs"

    @patch.object(MechRequestBehaviour, "should_use_marketplace_v2", return_value=True)
    def test_v2_dynamic_writes_no_overlap_with_selected_tool_when_no_pin(
        self, _mock: MagicMock
    ) -> None:
        """No pin + no mech serves the chosen tool writes `no_overlap_with_selected_tool`."""
        behaviour = _make_request_behaviour()
        behaviour._context.params.use_mech_marketplace = True
        behaviour._context.params.mech_marketplace_config.use_dynamic_mech_selection = (
            True
        )

        mock_synced = MagicMock()
        mock_synced.ranked_mechs_addresses = []
        mock_synced.selected_mechs = []

        mock_shared = MagicMock()
        mock_shared.penalized_mechs = set()
        behaviour._context.state = mock_shared

        with patch.object(
            type(behaviour),
            "synchronized_data",
            new_callable=lambda: property(lambda self: mock_synced),
        ):
            result = behaviour.get_priority_mech_address()

        assert result is None
        assert mock_shared.last_failure_reason == "no_overlap_with_selected_tool"

    @patch.object(MechRequestBehaviour, "should_use_marketplace_v2", return_value=True)
    def test_marketplace_v2_no_dynamic_returns_config(self, _mock: MagicMock) -> None:
        """Test v2 without dynamic selection returns config address."""
        behaviour = _make_request_behaviour()
        behaviour._context.params.use_mech_marketplace = True
        behaviour._context.params.valid_mechs = frozenset()
        behaviour._context.params.mech_marketplace_config.use_dynamic_mech_selection = (
            False
        )
        behaviour._context.params.mech_marketplace_config.priority_mech_address = (
            "0xconfig_mech"
        )

        result = behaviour.get_priority_mech_address()
        assert result == "0xconfig_mech"

    @patch.object(MechRequestBehaviour, "should_use_marketplace_v2", return_value=True)
    def test_static_priority_mech_outside_allowlist_returns_none(
        self, _mock: MagicMock
    ) -> None:
        """A configured priority mech missing from `valid_mechs` is rejected."""
        behaviour = _make_request_behaviour()
        behaviour._context.params.use_mech_marketplace = True
        behaviour._context.params.valid_mechs = frozenset({"0xallowed"})
        behaviour._context.params.mech_marketplace_config.use_dynamic_mech_selection = (
            False
        )
        behaviour._context.params.mech_marketplace_config.priority_mech_address = (
            "0xnot_allowed"
        )

        result = behaviour.get_priority_mech_address()
        assert result is None
        behaviour.context.logger.warning.assert_called()

    @patch.object(MechRequestBehaviour, "should_use_marketplace_v2", return_value=True)
    def test_static_priority_mech_in_allowlist_returns_config(
        self, _mock: MagicMock
    ) -> None:
        """A configured priority mech present in `valid_mechs` is honored."""
        behaviour = _make_request_behaviour()
        behaviour._context.params.use_mech_marketplace = True
        behaviour._context.params.valid_mechs = frozenset({"0xallowed"})
        behaviour._context.params.mech_marketplace_config.use_dynamic_mech_selection = (
            False
        )
        behaviour._context.params.mech_marketplace_config.priority_mech_address = (
            "0xallowed"
        )

        result = behaviour.get_priority_mech_address()
        assert result == "0xallowed"

    def test_async_act_skips_when_priority_mech_empty(self) -> None:
        """An empty `priority_mech_address` must skip `_prepare_safe_tx` cleanly."""
        from packages.valory.skills.mech_interact_abci.states.base import MechMetadata

        behaviour = _make_request_behaviour()
        behaviour._mech_requests = [MechMetadata(prompt="p", tool="t", nonce="n")]
        behaviour.priority_mech_address = ""

        mock_synced = MagicMock()
        mock_synced.safe_contract_address = "0xsafe"

        mock_shared = MagicMock()
        mock_shared.last_failure_reason = "no_non_penalized_valid_mech"
        behaviour._context.state = mock_shared
        behaviour._context.params.mech_chain_id = "gnosis"

        benchmark_ctx = MagicMock()
        behaviour._context.benchmark_tool.measure.return_value = benchmark_ctx
        benchmark_ctx.local.return_value.__enter__ = MagicMock()
        benchmark_ctx.local.return_value.__exit__ = MagicMock(return_value=False)

        prepare_calls = {"count": 0}

        def fake_prepare() -> Any:
            prepare_calls["count"] += 1
            yield
            return False

        captured = {}

        def capture_finish(payload: Any) -> Any:
            captured["payload"] = payload
            yield

        with patch.object(
            type(behaviour),
            "synchronized_data",
            new_callable=lambda: property(lambda self: mock_synced),
        ):
            behaviour._prepare_safe_tx = fake_prepare  # type: ignore[method-assign]
            behaviour.finish_behaviour = capture_finish  # type: ignore[method-assign]

            gen = behaviour.async_act()
            try:
                while True:
                    next(gen)
            except StopIteration:
                pass

        assert prepare_calls["count"] == 0
        assert "payload" in captured

    @patch.object(MechRequestBehaviour, "should_use_marketplace_v2", return_value=True)
    def test_successful_selection_clears_stale_failure_reason(
        self, _mock: MagicMock
    ) -> None:
        """A successful selection must not leak a stale reason from a prior round."""
        behaviour = _make_request_behaviour()
        behaviour._context.params.use_mech_marketplace = True
        behaviour._context.params.mech_marketplace_config.use_dynamic_mech_selection = (
            True
        )

        mock_synced = MagicMock()
        mock_synced.ranked_mechs_addresses = ["0xgood"]
        mock_synced.selected_mechs = []

        mock_shared = MagicMock()
        mock_shared.penalized_mechs = set()
        mock_shared.last_failure_reason = "no_non_penalized_valid_mech"
        behaviour._context.state = mock_shared

        with patch.object(
            type(behaviour),
            "synchronized_data",
            new_callable=lambda: property(lambda self: mock_synced),
        ):
            result = behaviour.get_priority_mech_address()

        assert result == "0xgood"
        assert mock_shared.last_failure_reason is None
