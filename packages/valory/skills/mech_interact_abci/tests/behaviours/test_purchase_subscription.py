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

"""Tests for the purchase_subscription behaviour module."""

from unittest.mock import MagicMock

import pytest

from packages.valory.skills.mech_interact_abci.behaviours.purchase_subcription import (
    MechPurchaseSubscriptionBehaviour,
    OWNER_PATH,
    dig,
)


class TestDig:
    """Tests for the dig utility function."""

    def test_simple_key(self) -> None:
        """Test digging a simple key."""
        data = {"a": 1}
        assert dig(data, ["a"]) == 1

    def test_nested_keys(self) -> None:
        """Test digging nested keys."""
        data = {"a": {"b": {"c": 42}}}
        assert dig(data, ["a", "b", "c"]) == 42

    def test_integer_index(self) -> None:
        """Test digging with integer index."""
        data = {"items": [10, 20, 30]}
        assert dig(data, ["items", 1]) == 20

    def test_missing_key_returns_default(self) -> None:
        """Test that missing key returns default."""
        data = {"a": 1}
        assert dig(data, ["b"]) is None

    def test_missing_key_custom_default(self) -> None:
        """Test that missing key returns custom default."""
        data = {"a": 1}
        assert dig(data, ["b"], default="fallback") == "fallback"

    def test_empty_path(self) -> None:
        """Test empty path returns the data itself."""
        data = {"a": 1}
        assert dig(data, []) == {"a": 1}

    def test_index_error(self) -> None:
        """Test that IndexError returns default."""
        data = [1, 2]
        assert dig(data, [5]) is None

    def test_type_error(self) -> None:
        """Test that TypeError returns default."""
        data = {"a": 42}  # int not subscriptable
        assert dig(data, ["a", "b"]) is None

    def test_owner_path_example(self) -> None:
        """Test digging using the OWNER_PATH constant."""
        data = {"proof": {"creator": "0xowner"}}
        assert dig(data, OWNER_PATH) == "0xowner"


def _make_purchase_behaviour() -> MechPurchaseSubscriptionBehaviour:
    """Create a MechPurchaseSubscriptionBehaviour with mocked internals."""
    behaviour = MechPurchaseSubscriptionBehaviour.__new__(
        MechPurchaseSubscriptionBehaviour
    )
    behaviour.multisend_batches = []
    behaviour.multisend_data = b""
    behaviour._safe_tx_hash = ""
    behaviour._context = MagicMock()
    # Init purchase-specific attributes
    behaviour._agreement_id = None
    behaviour._agreement_id_seed = None
    behaviour._ddo_register = None
    behaviour._ddo_values = None
    behaviour._receivers = None
    behaviour._lock_hash = None
    behaviour._lock_id = None
    behaviour._transfer_hash = None
    behaviour._transfer_id = None
    behaviour._escrow_hash = None
    behaviour._escrow_id = None
    behaviour._agreement_tx_data = None
    behaviour._subscription_token_approval_tx_data = None
    behaviour._fulfill_tx_data = None
    return behaviour


class TestMechPurchaseSubscriptionProperties:
    """Tests for MechPurchaseSubscriptionBehaviour property methods."""

    def test_ddo_register_none_logs_error(self) -> None:
        """Test ddo_register logs error when not fetched."""
        behaviour = _make_purchase_behaviour()
        result = behaviour.ddo_register
        assert result is None
        behaviour.context.logger.error.assert_called_once()

    def test_ddo_register_with_value(self) -> None:
        """Test ddo_register returns value when set."""
        behaviour = _make_purchase_behaviour()
        behaviour._ddo_register = ["a", "b", "http://endpoint"]
        assert behaviour.ddo_register == ["a", "b", "http://endpoint"]

    def test_ddo_endpoint_valid(self) -> None:
        """Test ddo_endpoint extracts correct index."""
        behaviour = _make_purchase_behaviour()
        behaviour._ddo_register = ["a", "b", "http://endpoint"]
        assert behaviour.ddo_endpoint == "http://endpoint"

    def test_ddo_endpoint_index_error(self) -> None:
        """Test ddo_endpoint returns None on IndexError."""
        behaviour = _make_purchase_behaviour()
        behaviour._ddo_register = ["a"]
        result = behaviour.ddo_endpoint
        assert result is None

    def test_ddo_values_none_logs_error(self) -> None:
        """Test ddo_values logs error when not fetched."""
        behaviour = _make_purchase_behaviour()
        result = behaviour.ddo_values
        assert result is None
        behaviour.context.logger.error.assert_called_once()

    def test_ddo_values_setter(self) -> None:
        """Test ddo_values setter."""
        behaviour = _make_purchase_behaviour()
        behaviour.ddo_values = {"key": "value"}
        assert behaviour.ddo_values == {"key": "value"}

    def test_receivers_none_logs_error(self) -> None:
        """Test receivers logs error when not fetched."""
        behaviour = _make_purchase_behaviour()
        result = behaviour.receivers
        assert result is None

    def test_receivers_setter(self) -> None:
        """Test receivers setter."""
        behaviour = _make_purchase_behaviour()
        behaviour.receivers = ["0xaddr1", "0xaddr2"]
        assert behaviour.receivers == ["0xaddr1", "0xaddr2"]

    def test_agreement_id_seed_none_logs_error(self) -> None:
        """Test agreement_id_seed logs error when not generated."""
        behaviour = _make_purchase_behaviour()
        result = behaviour.agreement_id_seed
        assert result is None

    def test_agreement_id_seed_setter(self) -> None:
        """Test agreement_id_seed setter."""
        behaviour = _make_purchase_behaviour()
        behaviour.agreement_id_seed = "seed123"
        assert behaviour.agreement_id_seed == "seed123"

    def test_agreement_id_none_logs_error(self) -> None:
        """Test agreement_id logs error when not fetched."""
        behaviour = _make_purchase_behaviour()
        result = behaviour.agreement_id
        assert result is None

    def test_lock_hash_none_logs_error(self) -> None:
        """Test lock_hash logs error when not fetched."""
        behaviour = _make_purchase_behaviour()
        result = behaviour.lock_hash
        assert result is None

    def test_lock_id_none_logs_error(self) -> None:
        """Test lock_id logs error when not fetched."""
        behaviour = _make_purchase_behaviour()
        result = behaviour.lock_id
        assert result is None

    def test_transfer_hash_none_logs_error(self) -> None:
        """Test transfer_hash logs error when not fetched."""
        behaviour = _make_purchase_behaviour()
        result = behaviour.transfer_hash
        assert result is None

    def test_transfer_id_none_logs_error(self) -> None:
        """Test transfer_id logs error when not fetched."""
        behaviour = _make_purchase_behaviour()
        result = behaviour.transfer_id
        assert result is None

    def test_escrow_hash_none_logs_error(self) -> None:
        """Test escrow_hash logs error when not fetched."""
        behaviour = _make_purchase_behaviour()
        result = behaviour.escrow_hash
        assert result is None

    def test_escrow_id_none_logs_error(self) -> None:
        """Test escrow_id logs error when not fetched."""
        behaviour = _make_purchase_behaviour()
        result = behaviour.escrow_id
        assert result is None

    def test_from_address_no_ddo_values(self) -> None:
        """Test from_address returns None when ddo_values is None."""
        behaviour = _make_purchase_behaviour()
        result = behaviour.from_address
        assert result is None

    def test_from_address_valid(self) -> None:
        """Test from_address extracts owner from ddo_values."""
        behaviour = _make_purchase_behaviour()
        behaviour._ddo_values = {"proof": {"creator": "0xowner"}}
        assert behaviour.from_address == "0xowner"

    def test_from_address_missing_owner(self) -> None:
        """Test from_address returns None when owner path is missing."""
        behaviour = _make_purchase_behaviour()
        behaviour._ddo_values = {"proof": {}}
        result = behaviour.from_address
        assert result is None

    def test_agreement_tx_data_none_raises(self) -> None:
        """Test agreement_tx_data raises TypeError when None (HexBytes cannot convert None)."""
        behaviour = _make_purchase_behaviour()
        with pytest.raises(TypeError):
            _ = behaviour.agreement_tx_data

    def test_agreement_tx_data_with_value(self) -> None:
        """Test agreement_tx_data returns HexBytes when set."""
        behaviour = _make_purchase_behaviour()
        behaviour._agreement_tx_data = "0xabcd"
        result = behaviour.agreement_tx_data
        assert result is not None

    def test_subscription_token_approval_tx_data_none_raises(self) -> None:
        """Test subscription_token_approval_tx_data raises TypeError when None."""
        behaviour = _make_purchase_behaviour()
        with pytest.raises(TypeError):
            _ = behaviour.subscription_token_approval_tx_data

    def test_subscription_token_approval_tx_data_with_value(self) -> None:
        """Test subscription_token_approval_tx_data returns HexBytes when set."""
        behaviour = _make_purchase_behaviour()
        behaviour._subscription_token_approval_tx_data = "0xef01"
        result = behaviour.subscription_token_approval_tx_data
        assert result is not None

    def test_fulfill_tx_data_none_raises(self) -> None:
        """Test fulfill_tx_data raises TypeError when None."""
        behaviour = _make_purchase_behaviour()
        with pytest.raises(TypeError):
            _ = behaviour.fulfill_tx_data

    def test_fulfill_tx_data_with_value(self) -> None:
        """Test fulfill_tx_data returns HexBytes when set."""
        behaviour = _make_purchase_behaviour()
        behaviour._fulfill_tx_data = "0x2345"
        result = behaviour.fulfill_tx_data
        assert result is not None


class TestGenerateAgreementIdSeed:
    """Tests for the _generate_agreement_id_seed static method."""

    def test_generates_hex_string(self) -> None:
        """Test that the seed is a valid hex string prefixed with 0x."""
        seed = MechPurchaseSubscriptionBehaviour._generate_agreement_id_seed()
        assert seed.startswith("0x")
        # 32 bytes = 64 hex chars + "0x" prefix = 66 chars total
        assert len(seed) == 66
        # Verify it's valid hex
        bytes.fromhex(seed[2:])

    def test_generates_unique_seeds(self) -> None:
        """Test that consecutive calls produce different seeds."""
        seed1 = MechPurchaseSubscriptionBehaviour._generate_agreement_id_seed()
        seed2 = MechPurchaseSubscriptionBehaviour._generate_agreement_id_seed()
        assert seed1 != seed2
