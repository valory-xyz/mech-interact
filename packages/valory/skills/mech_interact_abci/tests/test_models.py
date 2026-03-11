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

"""Test the models.py module of the MechInteract."""

from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from hexbytes import HexBytes

from packages.valory.contracts.multisend.contract import MultiSendOperation
from packages.valory.skills.abstract_round_abci.test_tools.base import DummyContext
from packages.valory.skills.mech_interact_abci.models import (
    CHAIN_TO_NVM_CONFIG,
    CHAIN_TO_PRICE_TOKEN,
    MechMarketplaceConfig,
    MultisendBatch,
    NVMConfig,
    Ox,
    SharedState,
)


class TestSharedState:
    """Test SharedState of MechInteract."""

    def test_initialization(self) -> None:
        """Test initialization."""
        SharedState(name="", skill_context=DummyContext())


class TestNVMConfig:
    """Tests for NVMConfig dataclass."""

    def _make_config(self, **overrides: object) -> NVMConfig:
        """Create an NVMConfig with sensible defaults."""
        defaults = dict(
            balance_tracker_address="0xabc",
            did_registry_address="0xdef",
            agreement_store_manager_address="0x123",
            lock_payment_condition_address="0x456",
            transfer_nft_condition_address="0x789",
            escrow_payment_condition_address="0xaaa",
            plan_fee_nvm=100,
            plan_price_mech=900,
            subscription_nft_address="0xbbb",
            nft_sales_address="0xccc",
            subscription_token_address="0xddd",
            subscription_provider_address="0xeee",
            plan_did="did:nv:abc123",
        )
        defaults.update(overrides)
        return NVMConfig(**defaults)

    def test_did_property_strips_prefix(self) -> None:
        """Test that the did property replaces the prefix with 0x."""
        config = self._make_config(plan_did="did:nv:abc123def456")
        assert config.did == "0xabc123def456"

    def test_did_property_no_prefix(self) -> None:
        """Test did when the plan_did has no prefix to replace."""
        config = self._make_config(plan_did="0xalready")
        assert config.did == "0xalready"

    def test_defaults(self) -> None:
        """Test default field values."""
        config = self._make_config()
        assert config.subscription_credits == int(1e6)
        assert config.subscription_cost == 0
        assert config.agreement_cost == 0

    def test_gnosis_config_exists(self) -> None:
        """Test that gnosis chain config is defined."""
        from autonomy.chain.config import ChainType

        assert ChainType.GNOSIS in CHAIN_TO_NVM_CONFIG

    def test_base_config_exists(self) -> None:
        """Test that base chain config is defined."""
        from autonomy.chain.config import ChainType

        assert ChainType.BASE in CHAIN_TO_NVM_CONFIG


class TestMechMarketplaceConfig:
    """Tests for MechMarketplaceConfig dataclass."""

    def test_valid_config(self) -> None:
        """Test creating a valid config."""
        config = MechMarketplaceConfig(
            mech_marketplace_address="0xmarket",
            response_timeout=30,
        )
        assert config.mech_marketplace_address == "0xmarket"
        assert config.response_timeout == 30
        assert config.use_dynamic_mech_selection is True
        assert config.priority_mech_address is None

    def test_invalid_response_timeout_zero(self) -> None:
        """Test that zero response_timeout raises ValueError."""
        with pytest.raises(ValueError, match="response_timeout must be positive"):
            MechMarketplaceConfig(
                mech_marketplace_address="0xmarket",
                response_timeout=0,
            )

    def test_invalid_response_timeout_negative(self) -> None:
        """Test that negative response_timeout raises ValueError."""
        with pytest.raises(ValueError, match="response_timeout must be positive"):
            MechMarketplaceConfig(
                mech_marketplace_address="0xmarket",
                response_timeout=-1,
            )

    def test_frozen(self) -> None:
        """Test that MechMarketplaceConfig is frozen (immutable)."""
        config = MechMarketplaceConfig(
            mech_marketplace_address="0xmarket",
            response_timeout=30,
        )
        with pytest.raises(AttributeError):
            config.mech_marketplace_address = "0xother"  # type: ignore

    def test_with_priority_mech(self) -> None:
        """Test config with priority mech address set."""
        config = MechMarketplaceConfig(
            mech_marketplace_address="0xmarket",
            response_timeout=30,
            priority_mech_address="0xpriority",
            use_dynamic_mech_selection=False,
        )
        assert config.priority_mech_address == "0xpriority"
        assert config.use_dynamic_mech_selection is False


class TestMultisendBatch:
    """Tests for MultisendBatch dataclass."""

    def test_valid_batch(self) -> None:
        """Test creating a valid batch."""
        batch = MultisendBatch(to="0xaddr", data=HexBytes(b"\x01\x02"))
        assert batch.to == "0xaddr"
        assert batch.data == HexBytes(b"\x01\x02")
        assert batch.value == 0
        assert batch.operation == MultiSendOperation.CALL

    def test_with_value(self) -> None:
        """Test batch with a non-zero value."""
        batch = MultisendBatch(to="0xaddr", data=HexBytes(b""), value=100)
        assert batch.value == 100

    def test_with_delegate_call(self) -> None:
        """Test batch with DELEGATECALL operation."""
        batch = MultisendBatch(
            to="0xaddr",
            data=HexBytes(b""),
            operation=MultiSendOperation.DELEGATE_CALL,
        )
        assert batch.operation == MultiSendOperation.DELEGATE_CALL

    def test_empty_to_raises(self) -> None:
        """Test that empty 'to' address raises ValueError."""
        with pytest.raises(ValueError, match="Target address"):
            MultisendBatch(to="", data=HexBytes(b"\x01"))

    def test_negative_value_raises(self) -> None:
        """Test that negative value raises ValueError."""
        with pytest.raises(ValueError, match="Value must be non-negative"):
            MultisendBatch(to="0xaddr", data=HexBytes(b""), value=-1)

    def test_non_hexbytes_data_raises(self) -> None:
        """Test that non-HexBytes data raises ValueError."""
        with pytest.raises(ValueError, match="Data must be HexBytes"):
            MultisendBatch(to="0xaddr", data=b"\x01")  # type: ignore


class TestSharedStatePenalization:
    """Tests for SharedState penalization methods."""

    def _make_shared_state(self) -> SharedState:
        """Create a SharedState for testing."""
        return SharedState(name="", skill_context=DummyContext())

    def test_penalized_mechs_empty(self) -> None:
        """Test that penalized_mechs starts empty."""
        state = self._make_shared_state()
        # Mock synced_timestamp and params
        with patch.object(
            type(state),
            "synced_timestamp",
            new_callable=PropertyMock,
            return_value=1000,
        ), patch.object(
            type(state), "params", new_callable=PropertyMock
        ) as mock_params:
            mock_params.return_value = MagicMock(penalize_mech_time_window=300)
            assert state.penalized_mechs == {}

    def test_penalize_mech(self) -> None:
        """Test penalizing a mech adds it to the dict."""
        state = self._make_shared_state()
        with patch.object(
            type(state),
            "synced_timestamp",
            new_callable=PropertyMock,
            return_value=1000,
        ), patch.object(
            type(state), "params", new_callable=PropertyMock
        ) as mock_params:
            mock_params.return_value = MagicMock(penalize_mech_time_window=300)
            state.penalize_mech("0xmech1")
            assert "0xmech1" in state.penalized_mechs

    def test_penalize_mech_expires(self) -> None:
        """Test that penalized mechs expire after the time window."""
        state = self._make_shared_state()
        # Penalize at timestamp 1000
        with patch.object(
            type(state),
            "synced_timestamp",
            new_callable=PropertyMock,
            return_value=1000,
        ):
            state.penalize_mech("0xmech1")

        # Check at timestamp 1400, window is 300 -> expired
        with patch.object(
            type(state),
            "synced_timestamp",
            new_callable=PropertyMock,
            return_value=1400,
        ), patch.object(
            type(state), "params", new_callable=PropertyMock
        ) as mock_params:
            mock_params.return_value = MagicMock(penalize_mech_time_window=300)
            assert state.penalized_mechs == {}

    def test_penalized_mechs_sorted_by_time(self) -> None:
        """Test that penalized mechs are sorted by penalization time."""
        state = self._make_shared_state()
        # Penalize mech1 at 1000, mech2 at 1010
        with patch.object(
            type(state),
            "synced_timestamp",
            new_callable=PropertyMock,
            return_value=1000,
        ):
            state.penalize_mech("0xmech1")
        with patch.object(
            type(state),
            "synced_timestamp",
            new_callable=PropertyMock,
            return_value=1010,
        ):
            state.penalize_mech("0xmech2")

        # Check at 1015, window 300
        with patch.object(
            type(state),
            "synced_timestamp",
            new_callable=PropertyMock,
            return_value=1015,
        ), patch.object(
            type(state), "params", new_callable=PropertyMock
        ) as mock_params:
            mock_params.return_value = MagicMock(penalize_mech_time_window=300)
            result = state.penalized_mechs
            keys = list(result.keys())
            assert keys == ["0xmech1", "0xmech2"]

    def test_penalize_last_called_mech(self) -> None:
        """Test penalizing the last called mech."""
        state = self._make_shared_state()
        state.last_called_mech = "0xlast"
        with patch.object(
            type(state),
            "synced_timestamp",
            new_callable=PropertyMock,
            return_value=1000,
        ), patch.object(
            type(state), "params", new_callable=PropertyMock
        ) as mock_params:
            mock_params.return_value = MagicMock(penalize_mech_time_window=300)
            state.penalize_last_called_mech()
            assert "0xlast" in state.penalized_mechs

    def test_penalize_last_called_mech_none_logs_warning(self) -> None:
        """Test that penalizing when no last_called_mech logs a warning."""
        state = self._make_shared_state()
        assert state.last_called_mech is None
        state.penalize_last_called_mech()
        state.context.logger.warning.assert_called_once()


class TestChainMappings:
    """Tests for chain-to-config mappings."""

    def test_chain_to_price_token_has_entries(self) -> None:
        """Test that the price token mapping has entries."""
        assert len(CHAIN_TO_PRICE_TOKEN) > 0

    def test_chain_to_nvm_config_has_entries(self) -> None:
        """Test that the NVM config mapping has entries."""
        assert len(CHAIN_TO_NVM_CONFIG) > 0

    def test_all_nvm_configs_have_valid_did(self) -> None:
        """Test that all NVM configs have a valid did property."""
        for chain, config in CHAIN_TO_NVM_CONFIG.items():
            did = config.did
            assert did.startswith(Ox), f"Config for {chain} has invalid did: {did}"
