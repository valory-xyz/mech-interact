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
        assert dig({"a": 1}, ["a"]) == 1

    def test_nested_keys(self) -> None:
        """Test digging nested keys."""
        assert dig({"a": {"b": {"c": 42}}}, ["a", "b", "c"]) == 42

    def test_integer_index(self) -> None:
        """Test digging with integer index into a list."""
        assert dig({"items": [10, 20, 30]}, ["items", 1]) == 20

    @pytest.mark.parametrize(
        "data,path,default,expected",
        [
            ({"a": 1}, ["b"], None, None),
            ({"a": 1}, ["b"], "fallback", "fallback"),
            ({"a": 1}, [], None, {"a": 1}),
            ([1, 2], [5], None, None),
            ({"a": 42}, ["a", "b"], None, None),
        ],
        ids=[
            "missing_key",
            "custom_default",
            "empty_path",
            "index_out_of_range",
            "type_error_not_subscriptable",
        ],
    )
    def test_edge_cases(self, data, path, default, expected) -> None:
        """Test dig edge cases: missing keys, defaults, empty path, errors."""
        assert dig(data, path, default=default) == expected

    def test_owner_path_constant(self) -> None:
        """Test digging using the OWNER_PATH constant."""
        data = {"proof": {"creator": "0xowner"}}
        assert dig(data, OWNER_PATH) == "0xowner"


class TestNonePropertyLogging:
    """Tests for properties that log errors when accessed before being set."""

    @pytest.mark.parametrize(
        "prop",
        [
            "ddo_register",
            "ddo_values",
            "receivers",
            "agreement_id_seed",
            "agreement_id",
            "lock_hash",
            "lock_id",
            "transfer_hash",
            "transfer_id",
            "escrow_hash",
            "escrow_id",
        ],
    )
    def test_unset_property_returns_none_and_logs_error(
        self, purchase_behaviour, prop
    ) -> None:
        """Test that accessing an unset property returns None and logs error."""
        result = getattr(purchase_behaviour, prop)
        assert result is None
        purchase_behaviour.context.logger.error.assert_called_once()


class TestDdoEndpoint:
    """Tests for ddo_endpoint property."""

    def test_extracts_correct_index(self, purchase_behaviour) -> None:
        """Test ddo_endpoint extracts the endpoint from ddo_register."""
        purchase_behaviour._ddo_register = ["a", "b", "http://endpoint"]
        assert purchase_behaviour.ddo_endpoint == "http://endpoint"

    def test_returns_none_on_index_error(self, purchase_behaviour) -> None:
        """Test ddo_endpoint returns None when list is too short."""
        purchase_behaviour._ddo_register = ["a"]
        assert purchase_behaviour.ddo_endpoint is None


class TestPropertySetters:
    """Tests for property setters."""

    def test_ddo_values_roundtrip(self, purchase_behaviour) -> None:
        """Test ddo_values setter and getter."""
        purchase_behaviour.ddo_values = {"key": "value"}
        assert purchase_behaviour.ddo_values == {"key": "value"}

    def test_receivers_roundtrip(self, purchase_behaviour) -> None:
        """Test receivers setter and getter."""
        purchase_behaviour.receivers = ["0xaddr1", "0xaddr2"]
        assert purchase_behaviour.receivers == ["0xaddr1", "0xaddr2"]

    def test_agreement_id_seed_roundtrip(self, purchase_behaviour) -> None:
        """Test agreement_id_seed setter and getter."""
        purchase_behaviour.agreement_id_seed = "seed123"
        assert purchase_behaviour.agreement_id_seed == "seed123"


class TestFromAddress:
    """Tests for from_address property."""

    def test_returns_none_when_no_ddo_values(self, purchase_behaviour) -> None:
        """Test returns None when ddo_values is unset."""
        assert purchase_behaviour.from_address is None

    def test_extracts_owner(self, purchase_behaviour) -> None:
        """Test extracts owner from ddo_values using OWNER_PATH."""
        purchase_behaviour._ddo_values = {"proof": {"creator": "0xowner"}}
        assert purchase_behaviour.from_address == "0xowner"

    def test_returns_none_when_owner_missing(self, purchase_behaviour) -> None:
        """Test returns None when owner path is absent in ddo_values."""
        purchase_behaviour._ddo_values = {"proof": {}}
        assert purchase_behaviour.from_address is None


class TestTxDataProperties:
    """Tests for agreement_tx_data, subscription_token_approval_tx_data, fulfill_tx_data."""

    @pytest.mark.parametrize(
        "prop",
        [
            "agreement_tx_data",
            "subscription_token_approval_tx_data",
            "fulfill_tx_data",
        ],
    )
    def test_returns_none_when_unset(self, purchase_behaviour, prop) -> None:
        """Test returns None and logs error when backing field is None."""
        result = getattr(purchase_behaviour, prop)
        assert result is None
        purchase_behaviour.context.logger.error.assert_called_once()

    @pytest.mark.parametrize(
        "attr,prop",
        [
            ("_agreement_tx_data", "agreement_tx_data"),
            (
                "_subscription_token_approval_tx_data",
                "subscription_token_approval_tx_data",
            ),
            ("_fulfill_tx_data", "fulfill_tx_data"),
        ],
    )
    def test_returns_hexbytes_when_set(self, purchase_behaviour, attr, prop) -> None:
        """Test returns HexBytes when backing field is set."""
        setattr(purchase_behaviour, attr, "0xabcd")
        result = getattr(purchase_behaviour, prop)
        assert result is not None
        assert bytes(result) == b"\xab\xcd"


class TestGenerateAgreementIdSeed:
    """Tests for the _generate_agreement_id_seed static method."""

    def test_generates_valid_hex(self) -> None:
        """Test that the seed is a valid 32-byte hex string prefixed with 0x."""
        seed = MechPurchaseSubscriptionBehaviour._generate_agreement_id_seed()
        assert seed.startswith("0x")
        assert len(seed) == 66  # 0x + 64 hex chars
        bytes.fromhex(seed[2:])  # validates hex

    def test_generates_unique_seeds(self) -> None:
        """Test that consecutive calls produce different seeds."""
        seeds = {
            MechPurchaseSubscriptionBehaviour._generate_agreement_id_seed()
            for _ in range(10)
        }
        assert len(seeds) == 10
