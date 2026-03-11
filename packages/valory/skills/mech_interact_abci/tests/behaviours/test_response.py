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

import pytest

from packages.valory.skills.mech_interact_abci.behaviours.response import (
    MechResponseBehaviour,
)
from packages.valory.skills.mech_interact_abci.states.base import (
    MECH_RESPONSE,
    MechInteractionResponse,
    MechRequest,
)


def _make_response_behaviour() -> MechResponseBehaviour:
    """Create a MechResponseBehaviour with mocked internals."""
    behaviour = MechResponseBehaviour.__new__(MechResponseBehaviour)
    behaviour.multisend_batches = []
    behaviour.multisend_data = b""
    behaviour._safe_tx_hash = ""
    behaviour._context = MagicMock()
    behaviour._context.shared_state = {}
    # Init response-specific attributes
    behaviour._from_block = 0
    behaviour._requests = []
    behaviour._response_hex = ""
    behaviour._request_info = []
    behaviour._is_valid_acn_sender = False
    # Set initial mech response
    behaviour.context.shared_state[MECH_RESPONSE] = MechInteractionResponse(
        error="The mech's response has not been set!"
    )
    return behaviour


class TestMechResponseBehaviourProperties:
    """Tests for MechResponseBehaviour property methods."""

    def test_current_mech_response_getter(self) -> None:
        """Test current_mech_response getter."""
        behaviour = _make_response_behaviour()
        response = behaviour.current_mech_response
        assert isinstance(response, MechInteractionResponse)

    def test_current_mech_response_setter(self) -> None:
        """Test current_mech_response setter."""
        behaviour = _make_response_behaviour()
        new_response = MechInteractionResponse(nonce="test")
        behaviour.current_mech_response = new_response
        assert behaviour.context.shared_state[MECH_RESPONSE] is new_response

    def test_from_block_getter_setter(self) -> None:
        """Test from_block getter and setter."""
        behaviour = _make_response_behaviour()
        assert behaviour.from_block == 0
        behaviour.from_block = 12345
        assert behaviour.from_block == 12345

    def test_requests_getter(self) -> None:
        """Test requests getter returns empty list."""
        behaviour = _make_response_behaviour()
        assert behaviour.requests == []

    def test_requests_setter(self) -> None:
        """Test requests setter converts dicts to MechRequest."""
        behaviour = _make_response_behaviour()
        behaviour.requests = [{"data": "0x1", "requestId": 1}]
        assert len(behaviour.requests) == 1
        assert isinstance(behaviour.requests[0], MechRequest)

    def test_request_info_getter_setter(self) -> None:
        """Test request_info getter and setter."""
        behaviour = _make_response_behaviour()
        assert behaviour.request_info == []
        behaviour.request_info = [1, "0xmech", 3]
        assert behaviour.request_info == [1, "0xmech", 3]

    def test_delivery_mech_valid(self) -> None:
        """Test delivery_mech returns correct index."""
        behaviour = _make_response_behaviour()
        behaviour._request_info = ["some_data", "0xdelivery_mech", "more_data"]
        assert behaviour.delivery_mech == "0xdelivery_mech"

    def test_delivery_mech_index_error(self) -> None:
        """Test delivery_mech falls back on IndexError."""
        behaviour = _make_response_behaviour()
        behaviour._request_info = []
        behaviour._context.state = MagicMock()
        behaviour._context.state.last_called_mech = "0xlast"
        result = behaviour.delivery_mech
        assert result == "0xlast"

    def test_response_hex_getter(self) -> None:
        """Test response_hex getter."""
        behaviour = _make_response_behaviour()
        assert behaviour.response_hex == ""

    def test_response_hex_setter_bytes(self) -> None:
        """Test response_hex setter with bytes."""
        behaviour = _make_response_behaviour()
        behaviour.response_hex = b"\xab\xcd"
        assert behaviour.response_hex == "abcd"

    def test_response_hex_setter_string(self) -> None:
        """Test response_hex setter with string."""
        behaviour = _make_response_behaviour()
        behaviour.response_hex = "0xabcd"
        assert behaviour.response_hex == "0xabcd"

    def test_response_hex_setter_invalid(self) -> None:
        """Test response_hex setter with invalid type raises TypeError."""
        behaviour = _make_response_behaviour()
        with pytest.raises(TypeError, match="not valid hex bytes or string"):
            behaviour.response_hex = 12345  # type: ignore

    def test_is_valid_acn_sender_getter_setter(self) -> None:
        """Test is_valid_acn_sender getter and setter."""
        behaviour = _make_response_behaviour()
        assert behaviour.is_valid_acn_sender is False
        behaviour.is_valid_acn_sender = True
        assert behaviour.is_valid_acn_sender is True
