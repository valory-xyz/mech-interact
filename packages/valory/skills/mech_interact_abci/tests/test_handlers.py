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

    def test_setup_and_teardown_are_noop(self, acn_handler) -> None:
        """Test setup and teardown run without error (they are no-ops)."""
        acn_handler.setup()
        acn_handler.teardown()

    def test_current_mech_response_none_when_empty(self, acn_handler) -> None:
        """Test current_mech_response returns None when not set."""
        assert acn_handler.current_mech_response is None

    def test_current_mech_response_returns_response(self, acn_handler) -> None:
        """Test current_mech_response returns the stored response."""
        response = MechInteractionResponse(nonce="n1", requestId=42)
        acn_handler.context.shared_state[MECH_RESPONSE] = response
        assert acn_handler.current_mech_response is response

    def test_handle_data_no_current_response(self, acn_handler) -> None:
        """Test handling data when no mech response is expected logs error."""
        message = MagicMock()
        message.performative.value = "data"
        acn_handler.handle(message)
        acn_handler.context.logger.error.assert_called_once()

    def test_handle_data_mismatched_request_id(self, acn_handler) -> None:
        """Test handling data with wrong request ID logs error."""
        response = MechInteractionResponse(nonce="n1", requestId=42)
        acn_handler.context.shared_state[MECH_RESPONSE] = response
        message = MagicMock()
        message.performative.value = "data"
        message.request_id = 999
        acn_handler.handle(message)
        acn_handler.context.logger.error.assert_called_once()

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

    def test_handle_unrecognized_performative(self, acn_handler) -> None:
        """Test handling an unrecognized performative logs error."""
        message = MagicMock()
        message.performative.value = "unknown_action"
        acn_handler.handle(message)
        acn_handler.context.logger.error.assert_called_once()
