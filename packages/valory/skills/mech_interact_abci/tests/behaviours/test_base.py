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

from unittest.mock import MagicMock

import pytest

from packages.valory.skills.mech_interact_abci.models import MultisendBatch
from packages.valory.skills.transaction_settlement_abci.rounds import TX_HASH_LENGTH


class TestSafeTxHash:
    """Tests for safe_tx_hash getter/setter."""

    def test_setter_strips_0x_prefix(self, base_behaviour) -> None:
        """Test setting a valid safe tx hash strips the 0x prefix."""
        valid_hash = "0x" + "a" * (TX_HASH_LENGTH - 2)
        base_behaviour.safe_tx_hash = valid_hash
        assert base_behaviour.safe_tx_hash == "a" * (TX_HASH_LENGTH - 2)

    def test_setter_rejects_invalid_length(self, base_behaviour) -> None:
        """Test setting an invalid length safe tx hash raises ValueError."""
        with pytest.raises(ValueError, match="Incorrect length"):
            base_behaviour.safe_tx_hash = "0xshort"


class TestMultisendProperties:
    """Tests for multisend-related properties."""

    def test_multi_send_txs_serializes_batches(self, base_behaviour) -> None:
        """Test multi_send_txs returns list of dicts from batches."""
        batch = MultisendBatch(to="0xaddr", data=b"\x01", value=100)
        base_behaviour.multisend_batches = [batch]
        result = base_behaviour.multi_send_txs
        assert len(result) == 1
        assert result[0]["to"] == "0xaddr"
        assert result[0]["value"] == 100

    def test_txs_value_sums_batch_values(self, base_behaviour) -> None:
        """Test txs_value sums all batch values."""
        base_behaviour.multisend_batches = [
            MultisendBatch(to="0xa", data=b"", value=100),
            MultisendBatch(to="0xb", data=b"", value=200),
        ]
        assert base_behaviour.txs_value == 300

    def test_txs_value_empty_is_zero(self, base_behaviour) -> None:
        """Test txs_value with no batches returns 0."""
        assert base_behaviour.txs_value == 0

    def test_tx_hex_raises_without_hash(self, base_behaviour) -> None:
        """Test tx_hex raises ValueError when safe_tx_hash is empty."""
        with pytest.raises(ValueError, match="Cannot prepare a multisend"):
            _ = base_behaviour.tx_hex


class TestContractInteractionError:
    """Tests for contract interaction error logging."""

    @pytest.mark.parametrize(
        "body,expected_level",
        [
            ({"info": "some info"}, "info"),
            ({"warning": "some warning"}, "warning"),
            ({"error": "some error"}, "error"),
        ],
    )
    def test_logs_at_correct_level(self, base_behaviour, body, expected_level) -> None:
        """Test contract_interaction_error routes to the correct log level."""
        response = MagicMock()
        response.raw_transaction.body = body
        base_behaviour.contract_interaction_error("cid", "call", response)
        logger = getattr(base_behaviour.context.logger, expected_level)
        logger.assert_called_once_with(body[expected_level])

    def test_falls_back_to_default_error(self, base_behaviour) -> None:
        """Test contract_interaction_error falls back when no level key found."""
        response = MagicMock()
        response.raw_transaction.body = {}
        base_behaviour.contract_interaction_error("cid", "call", response)
        base_behaviour.context.logger.error.assert_called_once()

    def test_default_error(self, base_behaviour) -> None:
        """Test default_error logs error message."""
        response = MagicMock()
        base_behaviour.default_error("contract_id", "callable", response)
        base_behaviour.context.logger.error.assert_called_once()


class TestShouldUseMarketplaceV2:
    """Tests for should_use_marketplace_v2."""

    def test_disabled_returns_false(self, base_behaviour) -> None:
        """Test returns False when marketplace is disabled."""
        base_behaviour.context.params.use_mech_marketplace = False
        assert base_behaviour.should_use_marketplace_v2() is False

    def test_raises_when_check_not_performed(self, base_behaviour) -> None:
        """Test raises ValueError when versioning check not done."""
        base_behaviour.context.params.use_mech_marketplace = True
        synced = MagicMock()
        synced.versioning_check_performed = False
        type(base_behaviour).synchronized_data = property(lambda self: synced)
        with pytest.raises(ValueError, match="Compatibility check"):
            base_behaviour.should_use_marketplace_v2()

    def test_returns_true_when_v2(self, base_behaviour) -> None:
        """Test returns True when marketplace is v2."""
        base_behaviour.context.params.use_mech_marketplace = True
        synced = MagicMock()
        synced.versioning_check_performed = True
        synced.is_marketplace_v2 = True
        type(base_behaviour).synchronized_data = property(lambda self: synced)
        assert base_behaviour.should_use_marketplace_v2() is True
