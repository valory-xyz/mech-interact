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

"""Tests for the response behaviour module properties."""

from unittest.mock import MagicMock

from packages.valory.skills.mech_interact_abci.states.base import (
    MECH_RESPONSE,
    MechInteractionResponse,
    MechRequest,
)


class TestCurrentMechResponse:
    """Tests for current_mech_response getter/setter."""

    def test_getter_returns_initial(self, response_behaviour) -> None:
        """Test getter returns the initial MechInteractionResponse."""
        assert isinstance(
            response_behaviour.current_mech_response, MechInteractionResponse
        )

    def test_setter_updates_shared_state(self, response_behaviour) -> None:
        """Test setter writes to shared_state."""
        new_response = MechInteractionResponse(nonce="test")
        response_behaviour.current_mech_response = new_response
        assert response_behaviour.context.shared_state[MECH_RESPONSE] is new_response


class TestSimpleProperties:
    """Tests for from_block, requests, request_info, is_valid_acn_sender."""

    def test_from_block_roundtrip(self, response_behaviour) -> None:
        """Test from_block getter and setter."""
        assert response_behaviour.from_block == 0
        response_behaviour.from_block = 12345
        assert response_behaviour.from_block == 12345

    def test_requests_setter_converts_dicts(self, response_behaviour) -> None:
        """Test requests setter converts dicts to MechRequest."""
        response_behaviour.requests = [{"data": "0x1", "requestId": 1}]
        assert len(response_behaviour.requests) == 1
        assert isinstance(response_behaviour.requests[0], MechRequest)

    def test_request_info_roundtrip(self, response_behaviour) -> None:
        """Test request_info getter and setter."""
        response_behaviour.request_info = [1, "0xmech", 3]
        assert response_behaviour.request_info == [1, "0xmech", 3]

    def test_is_valid_acn_sender_roundtrip(self, response_behaviour) -> None:
        """Test is_valid_acn_sender getter and setter."""
        assert response_behaviour.is_valid_acn_sender is False
        response_behaviour.is_valid_acn_sender = True
        assert response_behaviour.is_valid_acn_sender is True


class TestDeliveryMech:
    """Tests for delivery_mech property."""

    def test_returns_correct_index(self, response_behaviour) -> None:
        """Test delivery_mech returns the mech address at index 1."""
        response_behaviour._request_info = [
            "some_data",
            "0xdelivery_mech",
            "more_data",
        ]
        assert response_behaviour.delivery_mech == "0xdelivery_mech"

    def test_falls_back_to_last_called_mech(self, response_behaviour) -> None:
        """Test delivery_mech falls back to shared_state on IndexError."""
        response_behaviour._request_info = []
        response_behaviour._context.state = MagicMock()
        response_behaviour._context.state.last_called_mech = "0xlast"
        assert response_behaviour.delivery_mech == "0xlast"


class TestResponseHex:
    """Tests for response_hex getter/setter."""

    def test_setter_from_bytes(self, response_behaviour) -> None:
        """Test response_hex setter converts bytes to hex string."""
        response_behaviour.response_hex = b"\xab\xcd"
        assert response_behaviour.response_hex == "abcd"

    def test_setter_from_string(self, response_behaviour) -> None:
        """Test response_hex setter accepts string directly."""
        response_behaviour.response_hex = "0xabcd"
        assert response_behaviour.response_hex == "0xabcd"

    def test_setter_invalid_type_logs_error(self, response_behaviour) -> None:
        """Test response_hex setter logs error and sets empty for invalid input."""
        response_behaviour.response_hex = 12345  # type: ignore
        assert response_behaviour.response_hex == ""
        response_behaviour.context.logger.error.assert_called_once()
