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

"""Tests for the Mech Marketplace Legacy contract module."""

from unittest.mock import MagicMock, patch

from packages.valory.contracts.mech_marketplace_legacy.contract import (
    MechMarketplaceLegacy,
    TOPIC_CHARS,
    pad_address_for_topic,
)

CONTRACT_ADDRESS = "0x1234567890abcdef1234567890abcdef12345678"
SENDER_ADDRESS = "0xaabbccddee1234567890abcdef1234567890abcd"
PRIORITY_MECH = "0x3333333333333333333333333333333333333333"
TX_HASH = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
REQUESTER = "0x5555555555555555555555555555555555555555"


class TestPadAddressForTopic:
    """Tests for the pad_address_for_topic utility function."""

    def test_pad_address_for_topic(self):
        """Test that an address is left-padded to 32 bytes."""
        address = "0xaabbccddee1234567890abcdef1234567890abcd"
        result = pad_address_for_topic(address)
        # Should be 32 bytes (64 hex chars after 0x)
        assert len(result) == 32
        hex_str = result.hex()
        assert len(hex_str) == TOPIC_CHARS
        # The original address (without 0x) should appear at the end
        assert hex_str.endswith("aabbccddee1234567890abcdef1234567890abcd")

    def test_pad_address_zero_padded(self):
        """Test that shorter addresses are zero-padded on the left."""
        address = "0x0000000000000000000000000000000000000001"
        result = pad_address_for_topic(address)
        assert len(result) == 32
        assert result.hex().endswith("0000000000000000000000000000000000000001")


class TestMechMarketplaceLegacyExecuteWithTimeout:
    """Tests for MechMarketplaceLegacy.execute_with_timeout."""

    def test_execute_with_timeout_success(self):
        """Test successful execution within timeout."""
        result, err = MechMarketplaceLegacy.execute_with_timeout(
            lambda: {"data": "ok"}, timeout=5.0
        )
        assert result == {"data": "ok"}
        assert err is None

    def test_execute_with_timeout_string_error(self):
        """Test that string return values are treated as errors."""
        result, err = MechMarketplaceLegacy.execute_with_timeout(
            lambda: "error message", timeout=5.0
        )
        assert result is None
        assert err == "error message"

    def test_execute_with_timeout_timeout(self):
        """Test timeout handling."""
        import time

        def slow_func():
            time.sleep(10)
            return {"data": "ok"}

        result, err = MechMarketplaceLegacy.execute_with_timeout(slow_func, timeout=0.1)
        assert result is None
        assert "didn't respond" in err


class TestMechMarketplaceLegacyGetRequestData:
    """Tests for MechMarketplaceLegacy.get_request_data."""

    @patch.object(MechMarketplaceLegacy, "get_instance")
    def test_get_request_data(self, mock_get_instance, ledger_api):
        """Test get_request_data encodes arguments correctly."""
        mock_instance = MagicMock()
        mock_instance.encode_abi.return_value = "0xaabbccdd"
        mock_get_instance.return_value = mock_instance

        request_data = b"test request"
        staking_instance = "0x4444444444444444444444444444444444444444"

        result = MechMarketplaceLegacy.get_request_data(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            request_data=request_data,
            priority_mech=PRIORITY_MECH,
            priority_mech_staking_instance=staking_instance,
            priority_mech_service_id=1,
            requester_staking_instance=staking_instance,
            requester_service_id=2,
            response_timeout=300,
        )

        mock_instance.encode_abi.assert_called_once_with(
            abi_element_identifier="request",
            args=(
                request_data,
                PRIORITY_MECH,
                staking_instance,
                1,
                staking_instance,
                2,
                300,
            ),
        )
        assert result == {"data": bytes.fromhex("aabbccdd")}


class TestMechMarketplaceLegacyProcessEvent:
    """Tests for MechMarketplaceLegacy._process_event."""

    def test_process_event_success(self, ledger_api):
        """Test _process_event with matching logs."""
        mock_contract = MagicMock()
        mock_event = MagicMock()
        mock_event.return_value.process_receipt.return_value = [
            {"args": {"requestId": 1, "data": b"response"}}
        ]
        mock_contract.events.MarketplaceRequest = mock_event

        result = MechMarketplaceLegacy._process_event(
            ledger_api,
            mock_contract,
            TX_HASH,
            1,
            "MarketplaceRequest",
            "requestId",
            "data",
        )

        assert "results" in result
        assert len(result["results"]) == 1

    def test_process_event_wrong_log_count(self, ledger_api):
        """Test _process_event with mismatched log count."""
        mock_contract = MagicMock()
        mock_event = MagicMock()
        mock_event.return_value.process_receipt.return_value = []
        mock_contract.events.MarketplaceRequest = mock_event

        result = MechMarketplaceLegacy._process_event(
            ledger_api,
            mock_contract,
            TX_HASH,
            1,
            "MarketplaceRequest",
            "requestId",
        )

        assert "error" in result
        assert "1 'MarketplaceRequest' events were expected" in result["error"]

    def test_process_event_missing_expected_key(self, ledger_api):
        """Test _process_event when expected key is missing."""
        mock_contract = MagicMock()
        mock_event = MagicMock()
        mock_event.return_value.process_receipt.return_value = [
            {"args": {"wrongKey": 42}}
        ]
        mock_contract.events.MarketplaceDeliver = mock_event

        result = MechMarketplaceLegacy._process_event(
            ledger_api,
            mock_contract,
            TX_HASH,
            1,
            "MarketplaceDeliver",
            "requestId",
        )

        assert "error" in result
        assert "do not match the expected format" in result["error"]

    def test_process_event_none_args(self, ledger_api):
        """Test _process_event when args is None."""
        mock_contract = MagicMock()
        mock_event = MagicMock()
        mock_event.return_value.process_receipt.return_value = [{}]
        mock_contract.events.MarketplaceDeliver = mock_event

        result = MechMarketplaceLegacy._process_event(
            ledger_api,
            mock_contract,
            TX_HASH,
            1,
            "MarketplaceDeliver",
            "requestId",
        )

        assert "error" in result

    def test_process_event_multiple_logs(self, ledger_api):
        """Test _process_event with multiple matching logs."""
        mock_contract = MagicMock()
        mock_event = MagicMock()
        mock_event.return_value.process_receipt.return_value = [
            {"args": {"requestId": 1, "data": b"a"}},
            {"args": {"requestId": 2, "data": b"b"}},
        ]
        mock_contract.events.MarketplaceRequest = mock_event

        result = MechMarketplaceLegacy._process_event(
            ledger_api,
            mock_contract,
            TX_HASH,
            2,
            "MarketplaceRequest",
            "requestId",
            "data",
        )

        assert "results" in result
        assert len(result["results"]) == 2


class TestMechMarketplaceLegacyProcessRequestEvent:
    """Tests for MechMarketplaceLegacy.process_request_event."""

    @patch.object(MechMarketplaceLegacy, "_process_event")
    @patch.object(MechMarketplaceLegacy, "get_instance")
    def test_process_request_event(
        self, mock_get_instance, mock_process_event, ledger_api
    ):
        """Test process_request_event calls _process_event with correct args."""
        mock_process_event.return_value = {
            "results": [{"requestId": 1, "data": b"req"}]
        }

        result = MechMarketplaceLegacy.process_request_event(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            tx_hash=TX_HASH,
        )

        mock_process_event.assert_called_once()
        call_args = mock_process_event.call_args
        assert call_args[0][4] == "MarketplaceRequest"
        assert "requestId" in call_args[0]
        assert "data" in call_args[0]
        assert "results" in result


class TestMechMarketplaceLegacyProcessDeliverEvent:
    """Tests for MechMarketplaceLegacy.process_deliver_event."""

    @patch.object(MechMarketplaceLegacy, "_process_event")
    @patch.object(MechMarketplaceLegacy, "get_instance")
    def test_process_deliver_event(
        self, mock_get_instance, mock_process_event, ledger_api
    ):
        """Test process_deliver_event calls _process_event with correct args."""
        mock_process_event.return_value = {
            "results": [{"requestId": 1, "data": b"delivered"}]
        }

        result = MechMarketplaceLegacy.process_deliver_event(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            tx_hash=TX_HASH,
        )

        mock_process_event.assert_called_once()
        call_args = mock_process_event.call_args
        assert call_args[0][4] == "MarketplaceDeliver"
        assert "requestId" in call_args[0]
        assert "data" in call_args[0]
        assert "results" in result


class TestMechMarketplaceLegacyGetBlockNumber:
    """Tests for MechMarketplaceLegacy.get_block_number."""

    def test_get_block_number(self, ledger_api):
        """Test get_block_number returns the block number."""
        result = MechMarketplaceLegacy.get_block_number(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            tx_hash=TX_HASH,
        )

        assert result == {"number": 100}
        ledger_api.api.eth.get_transaction_receipt.assert_called_once_with(TX_HASH)
        ledger_api.api.eth.get_block.assert_called_once_with(100)


class TestMechMarketplaceLegacyGetResponse:
    """Tests for MechMarketplaceLegacy.get_response."""

    @patch.object(MechMarketplaceLegacy, "execute_with_timeout")
    def test_get_response_success(self, mock_exec, ledger_api):
        """Test get_response returns data on success."""
        mock_exec.return_value = ({"data": b"response"}, None)

        result = MechMarketplaceLegacy.get_response(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            requester=REQUESTER,
            request_id=1,
            timeout=5.0,
        )

        assert result == {"data": b"response"}

    @patch.object(MechMarketplaceLegacy, "execute_with_timeout")
    def test_get_response_timeout(self, mock_exec, ledger_api):
        """Test get_response handles timeout error."""
        mock_exec.return_value = (None, "The RPC didn't respond in 5.0.")

        result = MechMarketplaceLegacy.get_response(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            requester=REQUESTER,
            request_id=1,
            timeout=5.0,
        )

        assert result == {"error": "The RPC didn't respond in 5.0."}

    @patch.object(MechMarketplaceLegacy, "execute_with_timeout")
    def test_get_response_not_delivered(self, mock_exec, ledger_api):
        """Test get_response when response not yet delivered."""
        mock_exec.return_value = (
            {"info": "not delivered yet"},
            None,
        )

        result = MechMarketplaceLegacy.get_response(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            requester=REQUESTER,
            request_id=1,
            timeout=5.0,
        )

        assert "info" in result

    @patch.object(MechMarketplaceLegacy, "execute_with_timeout")
    def test_get_response_string_error(self, mock_exec, ledger_api):
        """Test get_response when the inner function returns a string (error)."""
        mock_exec.return_value = (None, "unexpected error string")

        result = MechMarketplaceLegacy.get_response(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            requester=REQUESTER,
            request_id=1,
            timeout=5.0,
        )

        assert result == {"error": "unexpected error string"}

    @patch.object(MechMarketplaceLegacy, "get_instance")
    def test_get_response_inner_logic_no_logs(self, mock_get_instance, ledger_api):
        """Test get_response inner function when no logs are found."""
        mock_instance = MagicMock()
        mock_event = MagicMock()
        mock_event.topic = b"\x00" * 32
        mock_instance.events.MarketplaceDeliver.return_value = mock_event
        mock_get_instance.return_value = mock_instance
        ledger_api.api.eth.get_logs.return_value = []

        # Bypass execute_with_timeout to test the inner function directly
        with patch.object(
            MechMarketplaceLegacy,
            "execute_with_timeout",
            side_effect=lambda func, timeout: (func(), None),
        ):
            result = MechMarketplaceLegacy.get_response(
                ledger_api=ledger_api,
                contract_address=CONTRACT_ADDRESS,
                requester=REQUESTER,
                request_id=1,
                timeout=5.0,
            )

        assert "info" in result
        assert "has not delivered" in result["info"]

    @patch.object(MechMarketplaceLegacy, "get_instance")
    def test_get_response_inner_logic_single_match(self, mock_get_instance, ledger_api):
        """Test get_response inner function with a single matching delivery."""
        mock_instance = MagicMock()
        mock_event = MagicMock()
        mock_event.topic = b"\x00" * 32
        mock_event.process_log.return_value = {
            "args": {"requestId": 1, "data": b"delivered data"}
        }
        mock_instance.events.MarketplaceDeliver.return_value = mock_event
        mock_get_instance.return_value = mock_instance
        mock_log = MagicMock()
        ledger_api.api.eth.get_logs.return_value = [mock_log]

        with patch.object(
            MechMarketplaceLegacy,
            "execute_with_timeout",
            side_effect=lambda func, timeout: (func(), None),
        ):
            result = MechMarketplaceLegacy.get_response(
                ledger_api=ledger_api,
                contract_address=CONTRACT_ADDRESS,
                requester=REQUESTER,
                request_id=1,
                timeout=5.0,
            )

        assert result == {"data": b"delivered data"}

    @patch.object(MechMarketplaceLegacy, "get_instance")
    def test_get_response_inner_logic_multiple_matches(
        self, mock_get_instance, ledger_api
    ):
        """Test get_response inner function with multiple matching deliveries (error)."""
        mock_instance = MagicMock()
        mock_event = MagicMock()
        mock_event.topic = b"\x00" * 32
        mock_event.process_log.return_value = {
            "args": {"requestId": 1, "data": b"data"}
        }
        mock_instance.events.MarketplaceDeliver.return_value = mock_event
        mock_get_instance.return_value = mock_instance
        ledger_api.api.eth.get_logs.return_value = [MagicMock(), MagicMock()]

        # When multiple responses match, it returns a string error which
        # execute_with_timeout treats as an error
        with patch.object(
            MechMarketplaceLegacy,
            "execute_with_timeout",
            side_effect=lambda func, timeout: (None, func()),
        ):
            result = MechMarketplaceLegacy.get_response(
                ledger_api=ledger_api,
                contract_address=CONTRACT_ADDRESS,
                requester=REQUESTER,
                request_id=1,
                timeout=5.0,
            )

        assert "error" in result

    @patch.object(MechMarketplaceLegacy, "get_instance")
    def test_get_response_inner_logic_missing_data(self, mock_get_instance, ledger_api):
        """Test get_response inner function when delivery has no data field."""
        mock_instance = MagicMock()
        mock_event = MagicMock()
        mock_event.topic = b"\x00" * 32
        mock_event.process_log.return_value = {"args": {"requestId": 1}}
        mock_instance.events.MarketplaceDeliver.return_value = mock_event
        mock_get_instance.return_value = mock_instance
        ledger_api.api.eth.get_logs.return_value = [MagicMock()]

        # Missing 'data' in args returns string error
        with patch.object(
            MechMarketplaceLegacy,
            "execute_with_timeout",
            side_effect=lambda func, timeout: (None, func()),
        ):
            result = MechMarketplaceLegacy.get_response(
                ledger_api=ledger_api,
                contract_address=CONTRACT_ADDRESS,
                requester=REQUESTER,
                request_id=1,
                timeout=5.0,
            )

        assert "error" in result
