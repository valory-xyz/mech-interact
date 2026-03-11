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

import packages.valory.skills.mech_interact_abci.handlers  # noqa
from packages.valory.skills.mech_interact_abci.handlers import AcnHandler
from packages.valory.skills.mech_interact_abci.states.base import (
    MECH_RESPONSE,
    MechInteractionResponse,
)


def test_import() -> None:
    """Test that the 'handlers.py' of the MechInteract can be imported."""


def _make_acn_handler() -> AcnHandler:
    """Create an AcnHandler with mocked context."""
    handler = AcnHandler.__new__(AcnHandler)
    mock_context = MagicMock()
    mock_context.shared_state = {}
    handler._context = mock_context
    return handler


class TestAcnHandler:
    """Tests for AcnHandler."""

    def test_setup(self) -> None:
        """Test setup does nothing."""
        handler = _make_acn_handler()
        handler.setup()

    def test_teardown(self) -> None:
        """Test teardown does nothing."""
        handler = _make_acn_handler()
        handler.teardown()

    def test_current_mech_response_none(self) -> None:
        """Test current_mech_response when no response is set."""
        handler = _make_acn_handler()
        assert handler.current_mech_response is None

    def test_current_mech_response_exists(self) -> None:
        """Test current_mech_response when response exists."""
        handler = _make_acn_handler()
        response = MechInteractionResponse(nonce="n1", requestId=42)
        handler.context.shared_state[MECH_RESPONSE] = response
        assert handler.current_mech_response is response

    def test_handle_data_no_current_response(self) -> None:
        """Test handling data when no mech response is expected."""
        handler = _make_acn_handler()
        message = MagicMock()
        message.performative.value = "data"
        handler.handle(message)
        handler.context.logger.error.assert_called_once()

    def test_handle_data_mismatched_request_id(self) -> None:
        """Test handling data with wrong request ID."""
        handler = _make_acn_handler()
        response = MechInteractionResponse(nonce="n1", requestId=42)
        handler.context.shared_state[MECH_RESPONSE] = response

        message = MagicMock()
        message.performative.value = "data"
        message.request_id = 999
        handler.handle(message)
        handler.context.logger.error.assert_called_once()

    def test_handle_data_matching_request_id(self) -> None:
        """Test handling data with matching request ID."""
        handler = _make_acn_handler()
        response = MechInteractionResponse(nonce="n1", requestId=42)
        handler.context.shared_state[MECH_RESPONSE] = response

        message = MagicMock()
        message.performative.value = "data"
        message.request_id = 42
        message.content = b"response data"
        message.sender = "agent_sender"
        handler.handle(message)
        assert response.response_data == b"response data"
        assert response.sender_address == "agent_sender"

    def test_handle_unrecognized_performative(self) -> None:
        """Test handling an unrecognized performative."""
        handler = _make_acn_handler()
        message = MagicMock()
        message.performative.value = "unknown_action"
        handler.handle(message)
        handler.context.logger.error.assert_called_once()
