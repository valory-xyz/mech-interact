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

"""Tests for the Mech contract module."""

from unittest.mock import MagicMock, patch

from packages.valory.contracts.mech.contract import Mech

CONTRACT_ADDRESS = "0x1234567890abcdef1234567890abcdef12345678"
SENDER_ADDRESS = "0xaabbccddee1234567890abcdef1234567890abcd"
TX_HASH = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


class TestMechExecuteWithTimeout:
    """Tests for Mech.execute_with_timeout."""

    def test_execute_with_timeout_success(self):
        """Test successful execution within timeout."""
        result, err = Mech.execute_with_timeout(lambda: {"data": "ok"}, timeout=5.0)
        assert result == {"data": "ok"}
        assert err is None

    def test_execute_with_timeout_returns_string_error(self):
        """Test that string return values are treated as errors."""
        result, err = Mech.execute_with_timeout(
            lambda: "something went wrong", timeout=5.0
        )
        assert result is None
        assert err == "something went wrong"

    def test_execute_with_timeout_timeout(self):
        """Test timeout handling."""
        import time

        def slow_func():
            time.sleep(10)
            return {"data": "ok"}

        result, err = Mech.execute_with_timeout(slow_func, timeout=0.1)
        assert result is None
        assert "didn't respond" in err


class TestMechGetPrice:
    """Tests for Mech.get_price."""

    @patch.object(Mech, "get_instance")
    def test_get_price(self, mock_get_instance, ledger_api):
        """Test get_price returns the price."""
        mock_instance = MagicMock()
        mock_get_instance.return_value = mock_instance
        ledger_api.contract_method_call.return_value = 1000

        result = Mech.get_price(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
        )

        ledger_api.contract_method_call.assert_called_once_with(mock_instance, "price")
        assert result == {"price": 1000}


class TestMechGetRequestData:
    """Tests for Mech.get_request_data."""

    @patch.object(Mech, "get_instance")
    def test_get_request_data(self, mock_get_instance, ledger_api):
        """Test get_request_data encodes arguments correctly."""
        mock_instance = MagicMock()
        mock_instance.encode_abi.return_value = "0xaabbccdd"
        mock_get_instance.return_value = mock_instance

        request_data = b"test request"

        result = Mech.get_request_data(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            request_data=request_data,
        )

        mock_instance.encode_abi.assert_called_once_with(
            "request", args=(request_data,)
        )
        assert result == {"data": bytes.fromhex("aabbccdd")}


class TestMechProcessEvent:
    """Tests for Mech._process_event."""

    def test_process_event_success(self, ledger_api):
        """Test _process_event with matching logs."""
        mock_contract = MagicMock()
        mock_event = MagicMock()
        mock_event.return_value.process_receipt.return_value = [
            {"args": {"requestId": 1, "data": b"response"}}
        ]
        mock_contract.events.Request = mock_event

        result = Mech._process_event(
            ledger_api,
            mock_contract,
            TX_HASH,
            1,
            "Request",
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
        mock_contract.events.Request = mock_event

        result = Mech._process_event(
            ledger_api,
            mock_contract,
            TX_HASH,
            1,
            "Request",
            "requestId",
        )

        assert "error" in result
        assert "1 'Request' events were expected" in result["error"]

    def test_process_event_missing_args_key(self, ledger_api):
        """Test _process_event when expected keys are missing from args."""
        mock_contract = MagicMock()
        mock_event = MagicMock()
        mock_event.return_value.process_receipt.return_value = [
            {"args": {"someOtherKey": 42}}
        ]
        mock_contract.events.Request = mock_event

        result = Mech._process_event(
            ledger_api,
            mock_contract,
            TX_HASH,
            1,
            "Request",
            "requestId",
        )

        assert "error" in result
        assert "do not match the expected format" in result["error"]

    def test_process_event_none_args(self, ledger_api):
        """Test _process_event when args is None."""
        mock_contract = MagicMock()
        mock_event = MagicMock()
        mock_event.return_value.process_receipt.return_value = [{}]
        mock_contract.events.Request = mock_event

        result = Mech._process_event(
            ledger_api,
            mock_contract,
            TX_HASH,
            1,
            "Request",
            "requestId",
        )

        assert "error" in result

    def test_process_event_multiple_logs(self, ledger_api):
        """Test _process_event with multiple logs."""
        mock_contract = MagicMock()
        mock_event = MagicMock()
        mock_event.return_value.process_receipt.return_value = [
            {"args": {"requestId": 1, "data": b"a"}},
            {"args": {"requestId": 2, "data": b"b"}},
        ]
        mock_contract.events.Request = mock_event

        result = Mech._process_event(
            ledger_api,
            mock_contract,
            TX_HASH,
            2,
            "Request",
            "requestId",
            "data",
        )

        assert "results" in result
        assert len(result["results"]) == 2


class TestMechProcessRequestEvent:
    """Tests for Mech.process_request_event."""

    @patch.object(Mech, "_process_event")
    def test_process_request_event_first_abi_succeeds(
        self, mock_process_event, ledger_api
    ):
        """Test process_request_event succeeds with first ABI."""
        mock_process_event.return_value = {
            "results": [{"requestId": 1, "data": b"test"}]
        }

        result = Mech.process_request_event(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            tx_hash=TX_HASH,
        )

        assert "results" in result
        # Should have been called once since first ABI succeeded
        assert mock_process_event.call_count == 1

    @patch.object(Mech, "_process_event")
    def test_process_request_event_falls_back_to_second_abi(
        self, mock_process_event, ledger_api
    ):
        """Test process_request_event falls back to second ABI on error."""
        mock_process_event.side_effect = [
            {"error": "wrong abi"},
            {"results": [{"requestId": 1, "data": b"test"}]},
        ]

        result = Mech.process_request_event(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            tx_hash=TX_HASH,
        )

        assert "results" in result
        assert mock_process_event.call_count == 2

    @patch.object(Mech, "_process_event")
    def test_process_request_event_all_abis_fail(self, mock_process_event, ledger_api):
        """Test process_request_event returns error when all ABIs fail."""
        mock_process_event.return_value = {"error": "no matching events"}

        result = Mech.process_request_event(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            tx_hash=TX_HASH,
        )

        assert "error" in result


class TestMechProcessDeliverEvent:
    """Tests for Mech.process_deliver_event."""

    @patch.object(Mech, "_process_event")
    def test_process_deliver_event_success(self, mock_process_event, ledger_api):
        """Test process_deliver_event succeeds."""
        mock_process_event.return_value = {
            "results": [{"requestId": 1, "data": b"delivered"}]
        }

        result = Mech.process_deliver_event(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            tx_hash=TX_HASH,
        )

        assert "results" in result

    @patch.object(Mech, "_process_event")
    def test_process_deliver_event_falls_back(self, mock_process_event, ledger_api):
        """Test process_deliver_event falls back to second ABI."""
        mock_process_event.side_effect = [
            {"error": "wrong abi"},
            {"results": [{"requestId": 1, "data": b"ok"}]},
        ]

        result = Mech.process_deliver_event(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            tx_hash=TX_HASH,
        )

        assert "results" in result


class TestMechGetBlockNumber:
    """Tests for Mech.get_block_number."""

    def test_get_block_number(self, ledger_api):
        """Test get_block_number returns the block number."""
        result = Mech.get_block_number(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            tx_hash=TX_HASH,
        )

        assert result == {"number": 100}
        ledger_api.api.eth.get_transaction_receipt.assert_called_once_with(TX_HASH)


class TestMechGetMechId:
    """Tests for Mech.get_mech_id."""

    @patch.object(Mech, "get_instance")
    def test_get_mech_id(self, mock_get_instance, ledger_api):
        """Test get_mech_id returns the token ID."""
        mock_instance = MagicMock()
        mock_get_instance.return_value = mock_instance
        ledger_api.contract_method_call.return_value = 42

        result = Mech.get_mech_id(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
        )

        ledger_api.contract_method_call.assert_called_once_with(
            mock_instance, "tokenId"
        )
        assert result == {"id": 42}


class TestMechGetRequestsCount:
    """Tests for Mech.get_requests_count."""

    @patch.object(Mech, "get_instance")
    def test_get_requests_count(self, mock_get_instance, ledger_api):
        """Test get_requests_count returns the count."""
        mock_instance = MagicMock()
        mock_instance.functions.getRequestsCount.return_value.call.return_value = 10
        mock_get_instance.return_value = mock_instance

        result = Mech.get_requests_count(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            address=SENDER_ADDRESS,
        )

        assert result == {"requests_count": 10}


class TestMechGetPendingRequests:
    """Tests for Mech.get_pending_requests."""

    @patch.object(Mech, "get_instance")
    def test_get_pending_requests(self, mock_get_instance, ledger_api):
        """Test get_pending_requests returns pending count."""
        mock_instance = MagicMock()
        mock_instance.functions.mapUndeliveredRequestsCounts.return_value.call.return_value = (
            3
        )
        mock_get_instance.return_value = mock_instance

        result = Mech.get_pending_requests(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            sender_address=SENDER_ADDRESS,
        )

        assert result == {"pending_requests": 3}


class TestMechProcessAbiForResponse:
    """Tests for Mech._process_abi_for_response."""

    def test_no_deliver_event_in_abi(self, ledger_api):
        """Test _process_abi_for_response when no Deliver event in ABI."""
        abi = [{"name": "Request", "type": "event"}]

        result, is_final = Mech._process_abi_for_response(
            abi_index=0,
            abi=abi,
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            request_id=1,
            from_block="earliest",
            to_block="latest",
        )

        assert "error" in result
        assert "No Deliver event found" in result["error"]
        assert is_final is False

    @patch("packages.valory.contracts.mech.contract.get_event_data")
    @patch("packages.valory.contracts.mech.contract.event_abi_to_log_topic")
    def test_no_matching_response(self, mock_topic, mock_get_event_data, ledger_api):
        """Test _process_abi_for_response when no matching response found."""
        mock_topic.return_value = b"\x00" * 32
        ledger_api.api.eth.get_logs.return_value = []

        abi = [
            {
                "name": "Deliver",
                "type": "event",
                "inputs": [
                    {"name": "requestId", "type": "uint256", "indexed": False},
                    {"name": "data", "type": "bytes", "indexed": False},
                ],
                "anonymous": False,
            }
        ]

        result, is_final = Mech._process_abi_for_response(
            abi_index=0,
            abi=abi,
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            request_id=1,
            from_block="earliest",
            to_block="latest",
        )

        assert "info" in result
        assert "has not delivered" in result["info"]
        assert is_final is False

    @patch("packages.valory.contracts.mech.contract.get_event_data")
    @patch("packages.valory.contracts.mech.contract.event_abi_to_log_topic")
    def test_single_matching_response(
        self, mock_topic, mock_get_event_data, ledger_api
    ):
        """Test _process_abi_for_response with exactly one matching response."""
        mock_topic.return_value = b"\x00" * 32
        mock_log = MagicMock()
        ledger_api.api.eth.get_logs.return_value = [mock_log]
        mock_get_event_data.return_value = {
            "args": {"requestId": 1, "data": b"response data"}
        }

        abi = [
            {
                "name": "Deliver",
                "type": "event",
                "inputs": [
                    {"name": "requestId", "type": "uint256", "indexed": False},
                    {"name": "data", "type": "bytes", "indexed": False},
                ],
                "anonymous": False,
            }
        ]

        result, is_final = Mech._process_abi_for_response(
            abi_index=0,
            abi=abi,
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            request_id=1,
            from_block="earliest",
            to_block="latest",
        )

        assert result == {"data": b"response data"}
        assert is_final is True

    @patch("packages.valory.contracts.mech.contract.get_event_data")
    @patch("packages.valory.contracts.mech.contract.event_abi_to_log_topic")
    def test_multiple_matching_responses(
        self, mock_topic, mock_get_event_data, ledger_api
    ):
        """Test _process_abi_for_response with multiple matching responses."""
        mock_topic.return_value = b"\x00" * 32
        ledger_api.api.eth.get_logs.return_value = [MagicMock(), MagicMock()]
        mock_get_event_data.return_value = {
            "args": {"requestId": 1, "data": b"response"}
        }

        abi = [
            {
                "name": "Deliver",
                "type": "event",
                "inputs": [
                    {"name": "requestId", "type": "uint256", "indexed": False},
                    {"name": "data", "type": "bytes", "indexed": False},
                ],
                "anonymous": False,
            }
        ]

        result, is_final = Mech._process_abi_for_response(
            abi_index=0,
            abi=abi,
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            request_id=1,
            from_block="earliest",
            to_block="latest",
        )

        assert "error" in result
        assert "A single response was expected" in result["error"]
        assert is_final is False

    @patch("packages.valory.contracts.mech.contract.get_event_data")
    @patch("packages.valory.contracts.mech.contract.event_abi_to_log_topic")
    def test_matching_response_missing_data(
        self, mock_topic, mock_get_event_data, ledger_api
    ):
        """Test _process_abi_for_response when response has no data field."""
        mock_topic.return_value = b"\x00" * 32
        ledger_api.api.eth.get_logs.return_value = [MagicMock()]
        mock_get_event_data.return_value = {"args": {"requestId": 1}}

        abi = [
            {
                "name": "Deliver",
                "type": "event",
                "inputs": [
                    {"name": "requestId", "type": "uint256", "indexed": False},
                    {"name": "data", "type": "bytes", "indexed": False},
                ],
                "anonymous": False,
            }
        ]

        result, is_final = Mech._process_abi_for_response(
            abi_index=0,
            abi=abi,
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            request_id=1,
            from_block="earliest",
            to_block="latest",
        )

        assert "error" in result
        assert "does not match the expected format" in result["error"]
        assert is_final is False


class TestMechGetResponse:
    """Tests for Mech.get_response."""

    @patch.object(Mech, "_process_abi_for_response")
    def test_get_response_success(self, mock_process, ledger_api):
        """Test get_response returns data on success."""
        mock_process.return_value = ({"data": b"response"}, True)

        result = Mech.get_response(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            request_id=1,
            timeout=5.0,
        )

        assert result == {"data": b"response"}

    @patch.object(Mech, "_process_abi_for_response")
    def test_get_response_no_delivery(self, mock_process, ledger_api):
        """Test get_response when no delivery found."""
        mock_process.return_value = (
            {"info": "not delivered yet"},
            False,
        )

        result = Mech.get_response(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            request_id=1,
            timeout=5.0,
        )

        assert "info" in result

    @patch.object(Mech, "execute_with_timeout")
    def test_get_response_timeout(self, mock_exec, ledger_api):
        """Test get_response handles timeout error."""
        mock_exec.return_value = (None, "The RPC didn't respond in 5.0.")

        result = Mech.get_response(
            ledger_api=ledger_api,
            contract_address=CONTRACT_ADDRESS,
            request_id=1,
            timeout=5.0,
        )

        assert result == {"error": "The RPC didn't respond in 5.0."}
