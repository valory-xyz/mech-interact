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

"""Tests for the MechMM contract module."""

from unittest.mock import MagicMock, patch

from packages.valory.contracts.mech_mm.contract import MechMM

CONTRACT_ADDRESS = "0x1234567890abcdef1234567890abcdef12345678"
TX_HASH = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


class TestMechMMToPrefixedHex:
    """Tests for MechMM._to_prefixed_hex."""

    def test_to_prefixed_hex(self):
        """Test _to_prefixed_hex converts bytes to 0x-prefixed hex string."""
        result = MechMM._to_prefixed_hex(b"\xde\xad\xbe\xef")
        assert result == "0xdeadbeef"


class TestMechMMGetRequestData:
    """Tests for MechMM.get_request_data."""

    @patch.object(MechMM, "get_instance")
    def test_get_request_data(self, mock_get_instance, ledger_api):
        """Test get_request_data encodes arguments correctly."""
        mock_instance = MagicMock()
        mock_instance.encode_abi.return_value = "0xaabbccdd"
        mock_get_instance.return_value = mock_instance

        request_data = b"test request"
        result = MechMM.get_request_data(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            request_data=request_data,
        )

        mock_instance.encode_abi.assert_called_once_with(
            "request", args=(request_data,)
        )
        assert result == {"data": bytes.fromhex("aabbccdd")}


class TestMechMMProcessEvent:
    """Tests for MechMM._process_event."""

    def test_process_event_success(self, ledger_api):
        """Test _process_event with matching logs."""
        mock_contract = MagicMock()
        mock_event = MagicMock()
        mock_event.return_value.process_receipt.return_value = [
            {"args": {"requestId": 1, "data": b"response"}}
        ]
        mock_contract.events.Deliver = mock_event

        result = MechMM._process_event(
            ledger_api,
            mock_contract,
            TX_HASH,
            1,
            "Deliver",
            "requestId",
            "data",
        )

        assert "results" in result
        assert len(result["results"]) == 1
        assert result["results"][0] == {"requestId": 1, "data": b"response"}

    def test_process_event_wrong_log_count(self, ledger_api):
        """Test _process_event with mismatched log count."""
        mock_contract = MagicMock()
        mock_event = MagicMock()
        mock_event.return_value.process_receipt.return_value = []
        mock_contract.events.Deliver = mock_event

        result = MechMM._process_event(
            ledger_api,
            mock_contract,
            TX_HASH,
            1,
            "Deliver",
            "requestId",
        )

        assert "error" in result
        assert "1 'Deliver' events were expected" in result["error"]

    def test_process_event_missing_expected_key(self, ledger_api):
        """Test _process_event when expected key is missing."""
        mock_contract = MagicMock()
        mock_event = MagicMock()
        mock_event.return_value.process_receipt.return_value = [
            {"args": {"otherField": 42}}
        ]
        mock_contract.events.Deliver = mock_event

        result = MechMM._process_event(
            ledger_api,
            mock_contract,
            TX_HASH,
            1,
            "Deliver",
            "requestId",
        )

        assert "error" in result
        assert "do not match the expected format" in result["error"]

    def test_process_event_none_args(self, ledger_api):
        """Test _process_event when args is None."""
        mock_contract = MagicMock()
        mock_event = MagicMock()
        mock_event.return_value.process_receipt.return_value = [{}]
        mock_contract.events.Deliver = mock_event

        result = MechMM._process_event(
            ledger_api,
            mock_contract,
            TX_HASH,
            1,
            "Deliver",
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
        mock_contract.events.Deliver = mock_event

        result = MechMM._process_event(
            ledger_api,
            mock_contract,
            TX_HASH,
            2,
            "Deliver",
            "requestId",
            "data",
        )

        assert "results" in result
        assert len(result["results"]) == 2


class TestMechMMGetResponse:
    """Tests for MechMM.get_response."""

    @patch("packages.valory.contracts.mech_mm.contract.rpc_call_with_timeout")
    @patch.object(MechMM, "get_instance")
    def test_get_response_success(self, mock_get_instance, mock_rpc_call, ledger_api):
        """Test get_response returns data on success."""
        mock_rpc_call.return_value = ({"data": b"response"}, None)

        result = MechMM.get_response(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            request_id=b"\x01" * 32,
            timeout=5.0,
        )

        assert result == {"data": b"response"}

    @patch("packages.valory.contracts.mech_mm.contract.rpc_call_with_timeout")
    @patch.object(MechMM, "get_instance")
    def test_get_response_timeout(self, mock_get_instance, mock_rpc_call, ledger_api):
        """Test get_response handles timeout error."""
        mock_rpc_call.return_value = (None, "The RPC didn't respond in 5.")

        result = MechMM.get_response(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            request_id=b"\x01" * 32,
            timeout=5.0,
        )

        assert result == {"error": "The RPC didn't respond in 5."}

    @patch("packages.valory.contracts.mech_mm.contract.rpc_call_with_timeout")
    @patch.object(MechMM, "get_instance")
    def test_get_response_info_not_delivered(
        self, mock_get_instance, mock_rpc_call, ledger_api
    ):
        """Test get_response when not yet delivered."""
        mock_rpc_call.return_value = (
            {"info": "not delivered yet"},
            None,
        )

        result = MechMM.get_response(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            request_id=b"\x01" * 32,
            timeout=5.0,
        )

        assert "info" in result


class TestMechMMGetPaymentType:
    """Tests for MechMM.get_payment_type."""

    @patch.object(MechMM, "get_instance")
    def test_get_payment_type(self, mock_get_instance, ledger_api):
        """Test get_payment_type returns the payment type hex string."""
        mock_instance = MagicMock()
        mock_get_instance.return_value = mock_instance
        ledger_api.contract_method_call.return_value = b"\x00" * 31 + b"\x01"

        result = MechMM.get_payment_type(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
        )

        ledger_api.contract_method_call.assert_called_once_with(
            mock_instance, "paymentType"
        )
        assert "payment_type" in result
        assert result["payment_type"].startswith("0x")


class TestMechMMGetMaxDeliveryRate:
    """Tests for MechMM.get_max_delivery_rate."""

    @patch.object(MechMM, "get_instance")
    def test_get_max_delivery_rate(self, mock_get_instance, ledger_api):
        """Test get_max_delivery_rate returns the rate."""
        mock_instance = MagicMock()
        mock_get_instance.return_value = mock_instance
        ledger_api.contract_method_call.return_value = 500

        result = MechMM.get_max_delivery_rate(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
        )

        ledger_api.contract_method_call.assert_called_once_with(
            mock_instance, "maxDeliveryRate"
        )
        assert result == {"max_delivery_rate": 500}


class TestMechMMGetServiceId:
    """Tests for MechMM.get_service_id."""

    @patch.object(MechMM, "get_instance")
    def test_get_service_id(self, mock_get_instance, ledger_api):
        """Test get_service_id returns the service id."""
        mock_instance = MagicMock()
        mock_get_instance.return_value = mock_instance
        ledger_api.contract_method_call.return_value = 7

        result = MechMM.get_service_id(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
        )

        ledger_api.contract_method_call.assert_called_once_with(
            mock_instance, "serviceId"
        )
        assert result == {"service_id": 7}
