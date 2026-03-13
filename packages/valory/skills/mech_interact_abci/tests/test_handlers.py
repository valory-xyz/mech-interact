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

"""Tests for the handlers module."""

from unittest.mock import MagicMock

import pytest

import packages.valory.skills.mech_interact_abci.handlers  # noqa: F401
from packages.valory.skills.mech_interact_abci.handlers import AcnHandler
from packages.valory.skills.mech_interact_abci.states.base import (
    MECH_RESPONSE,
    MechInteractionResponse,
)


@pytest.fixture
def acn_handler() -> AcnHandler:
    """Create an AcnHandler with mocked context."""
    handler = AcnHandler.__new__(AcnHandler)
    mock_context = MagicMock()
    mock_context.shared_state = {}
    handler._context = mock_context
    return handler


class TestAcnHandler:
    """Tests for AcnHandler."""

    def test_handle_data_no_current_response(self, acn_handler) -> None:
        """Test handling data when no mech response is expected logs error and stores nothing."""
        message = MagicMock()
        message.performative.value = "data"
        message.content = b"should not be stored"
        message.sender = "should_not_be_stored"
        acn_handler.handle(message)
        acn_handler.context.logger.error.assert_called_once()
        # Verify no response object was created or mutated
        assert acn_handler.current_mech_response is None

    def test_handle_data_mismatched_request_id(self, acn_handler) -> None:
        """Test handling data with wrong request ID logs error and does not store data."""
        response = MechInteractionResponse(nonce="n1", requestId=42)
        acn_handler.context.shared_state[MECH_RESPONSE] = response
        message = MagicMock()
        message.performative.value = "data"
        message.request_id = 999
        message.content = b"wrong request data"
        message.sender = "wrong_sender"
        acn_handler.handle(message)
        acn_handler.context.logger.error.assert_called_once()
        # Verify the response was NOT mutated with wrong data
        assert response.response_data is None
        assert response.sender_address is None

    def test_handle_data_matching_request_id(self, acn_handler) -> None:
        """Test handling data with matching request ID stores response data."""
        response = MechInteractionResponse(nonce="n1", requestId=42)
        acn_handler.context.shared_state[MECH_RESPONSE] = response
        message = MagicMock()
        message.performative.value = "data"
        message.request_id = 42
        message.content = b"response data"
        message.sender = "agent_sender"
        acn_handler.handle(message)
        assert response.response_data == b"response data"
        assert response.sender_address == "agent_sender"

    def test_handle_data_request_id_string_coercion(self, acn_handler) -> None:
        """Test that request ID comparison uses string coercion (int vs str match)."""
        response = MechInteractionResponse(nonce="n1", requestId=42)
        acn_handler.context.shared_state[MECH_RESPONSE] = response
        message = MagicMock()
        message.performative.value = "data"
        message.request_id = "42"  # string instead of int
        message.content = b"coerced match"
        message.sender = "coerced_sender"
        acn_handler.handle(message)
        assert response.response_data == b"coerced match"
        assert response.sender_address == "coerced_sender"

    def test_handle_unrecognized_performative(self, acn_handler) -> None:
        """Test handling an unrecognized performative logs error."""
        message = MagicMock()
        message.performative.value = "unknown_action"
        acn_handler.handle(message)
        acn_handler.context.logger.error.assert_called_once()
