# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2025-2026 Valory AG
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

"""Tests for the response behaviour module."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from packages.valory.skills.mech_interact_abci.behaviours.response import (
    MechResponseBehaviour,
)
from packages.valory.skills.mech_interact_abci.states.base import (
    MechInteractionResponse,
)


def _make_response_behaviour(**overrides) -> MechResponseBehaviour:
    """Create a MechResponseBehaviour with mocked dependencies."""
    behaviour = MechResponseBehaviour.__new__(MechResponseBehaviour)
    mock_context = MagicMock()
    behaviour._context = mock_context

    # defaults
    behaviour._response_hex = ""
    behaviour._request_info = []
    behaviour._mech_responses = []

    # apply overrides
    for key, value in overrides.items():
        setattr(behaviour, key, value)

    return behaviour


class TestResponseHexSetter:
    """Tests for the response_hex setter."""

    def test_bytes_input(self) -> None:
        """Test setting response_hex with bytes converts to hex string."""
        behaviour = _make_response_behaviour()
        behaviour.response_hex = b"\xde\xad\xbe\xef"
        assert behaviour._response_hex == "deadbeef"

    def test_string_input(self) -> None:
        """Test setting response_hex with string stores directly."""
        behaviour = _make_response_behaviour()
        behaviour.response_hex = "abcdef01"
        assert behaviour._response_hex == "abcdef01"

    def test_invalid_type_logs_error_and_sets_empty(self) -> None:
        """Test setting response_hex with invalid type logs error and sets empty string."""
        behaviour = _make_response_behaviour()
        behaviour.response_hex = 12345
        assert behaviour._response_hex == ""
        behaviour.context.logger.error.assert_called_once()
        log_msg = behaviour.context.logger.error.call_args[0][0]
        assert "not valid hex bytes or string" in log_msg


class TestDeliveryMech:
    """Tests for the delivery_mech property."""

    def test_returns_mech_at_index(self) -> None:
        """Test delivery_mech returns the element at DELIVERY_MECH_INDEX."""
        behaviour = _make_response_behaviour()
        behaviour._request_info = ["requester", "0xdeliverymech", "extra"]
        assert behaviour.delivery_mech == "0xdeliverymech"

    def test_empty_request_info_returns_last_called_mech(self) -> None:
        """Test delivery_mech returns last_called_mech when request_info is empty."""
        behaviour = _make_response_behaviour()
        behaviour._request_info = []
        behaviour._context.state.last_called_mech = "0xlastcalled"
        result = behaviour.delivery_mech
        assert result == "0xlastcalled"

    def test_none_request_info_returns_fallback(self) -> None:
        """Test delivery_mech handles None request_info gracefully."""
        behaviour = _make_response_behaviour()
        behaviour._request_info = None
        behaviour._context.state.last_called_mech = None
        from web3.constants import ADDRESS_ZERO

        result = behaviour.delivery_mech
        assert result == ADDRESS_ZERO


class TestProcessResponseWithArtifacts:
    """Tests for _process_response_with_artifacts."""

    def test_non_json_returns_original(self) -> None:
        """Test that non-JSON strings are returned unchanged."""
        behaviour = _make_response_behaviour()
        assert behaviour._process_response_with_artifacts("plain text") == "plain text"

    def test_non_dict_json_returns_original(self) -> None:
        """Test that JSON arrays are returned unchanged."""
        raw = json.dumps([1, 2, 3])
        behaviour = _make_response_behaviour()
        assert behaviour._process_response_with_artifacts(raw) == raw

    def test_no_artifacts_returns_original(self) -> None:
        """Test that JSON without artifacts key is returned unchanged."""
        raw = json.dumps({"result": "answer"})
        behaviour = _make_response_behaviour()
        assert behaviour._process_response_with_artifacts(raw) == raw

    def test_empty_artifacts_returns_original(self) -> None:
        """Test that empty artifacts list returns original."""
        raw = json.dumps({"result": "answer", "artifacts": []})
        behaviour = _make_response_behaviour()
        assert behaviour._process_response_with_artifacts(raw) == raw

    def test_with_artifacts_returns_summary(self) -> None:
        """Test that response with artifacts returns summary with count and size."""
        artifacts = [
            {"base64": "a" * 100},
            {"base64": "b" * 200},
        ]
        raw = json.dumps({"result": "answer", "artifacts": artifacts})
        behaviour = _make_response_behaviour()
        behaviour._context.mech_response.url = "http://ipfs/test"

        result = behaviour._process_response_with_artifacts(raw)
        parsed = json.loads(result)

        assert "2 image artifacts" in parsed["summary"]
        assert "300 bytes" in parsed["summary"]
        assert parsed["ipfs_link"] == "http://ipfs/test"

    def test_artifacts_without_base64_key(self) -> None:
        """Test artifacts that lack base64 key count as 0 size."""
        artifacts = [{"other": "data"}, {"base64": "abc"}]
        raw = json.dumps({"artifacts": artifacts})
        behaviour = _make_response_behaviour()
        behaviour._context.mech_response.url = "http://ipfs/test"

        result = behaviour._process_response_with_artifacts(raw)
        parsed = json.loads(result)
        assert "3 bytes" in parsed["summary"]


class TestHandleResponse:
    """Tests for _handle_response."""

    def test_none_response_increments_retries(self) -> None:
        """Test that None response increments retries and returns None."""
        behaviour = _make_response_behaviour()
        behaviour._context.mech_response.api_id = "test_api"

        result = behaviour._handle_response(None)

        assert result is None
        behaviour._context.mech_response.increment_retries.assert_called_once()

    def test_valid_response_resets_retries(self) -> None:
        """Test that valid response resets retries and returns result."""
        behaviour = _make_response_behaviour()

        result = behaviour._handle_response("some response data")

        assert result == "some response data"
        behaviour._context.mech_response.reset_retries.assert_called_once()

    def test_long_response_is_truncated_in_log(self) -> None:
        """Test that long responses are truncated in log messages."""
        behaviour = _make_response_behaviour()

        long_response = "x" * 1000
        result = behaviour._handle_response(long_response)

        assert result == long_response  # return value is NOT truncated
        log_call = behaviour.context.logger.info.call_args[0][0]
        assert "..." in log_call


class TestIsLegacyMatch:
    """Tests for _is_legacy_match."""

    def test_matching_data_updates_request_id(self) -> None:
        """Test that on match, requestId is copied from request to pending response."""
        behaviour = _make_response_behaviour()
        pending = MechInteractionResponse(nonce="n1", data="aabbcc")

        request = MagicMock()
        request.data = MagicMock()
        request.data.hex.return_value = "aabbcc"
        request.requestId = 99

        result = behaviour._is_legacy_match(pending, request)

        assert result is True
        assert pending.requestId == 99

    def test_non_matching_data(self) -> None:
        """Test legacy match fails when data doesn't match."""
        behaviour = _make_response_behaviour()
        pending = MechInteractionResponse(nonce="n1", data="aabbcc")

        request = MagicMock()
        request.data = MagicMock()
        request.data.hex.return_value = "ddeeff"
        request.requestId = 99

        result = behaviour._is_legacy_match(pending, request)

        assert result is False
        # requestId should NOT be updated on non-match
        assert pending.requestId == 0


class TestIsMarketplaceMatch:
    """Tests for _is_marketplace_match."""

    def test_first_pending_with_request_ids_matches(self) -> None:
        """Test marketplace match succeeds for first pending response with requestIds."""
        behaviour = _make_response_behaviour()
        pending = MechInteractionResponse(nonce="n1")
        request = MagicMock()
        request.requestIds = ["0x1", "0x2"]

        result = behaviour._is_marketplace_match(
            pending, request, is_first_pending=True
        )

        assert result is True
        assert pending.requestIds == ["0x1", "0x2"]

    def test_not_first_pending_does_not_match(self) -> None:
        """Test marketplace match always fails for non-first pending responses."""
        behaviour = _make_response_behaviour()
        pending = MechInteractionResponse(nonce="n2")
        request = MagicMock()
        request.requestIds = ["0x1"]

        result = behaviour._is_marketplace_match(
            pending, request, is_first_pending=False
        )

        assert result is False

    def test_first_pending_without_request_ids(self) -> None:
        """Test marketplace match fails when request has no requestIds."""
        behaviour = _make_response_behaviour()
        pending = MechInteractionResponse(nonce="n1")
        request = MagicMock()
        request.requestIds = []

        result = behaviour._is_marketplace_match(
            pending, request, is_first_pending=True
        )

        assert result is False

    def test_first_pending_missing_request_ids_attr(self) -> None:
        """Test marketplace match fails when request lacks requestIds attribute."""
        behaviour = _make_response_behaviour()
        pending = MechInteractionResponse(nonce="n1")
        request = SimpleNamespace(data="abc")  # no requestIds attr

        result = behaviour._is_marketplace_match(
            pending, request, is_first_pending=True
        )

        assert result is False


class TestCheckMatch:
    """Tests for _check_match dispatch logic."""

    @patch.object(MechResponseBehaviour, "should_use_marketplace_v2", return_value=True)
    def test_dispatches_to_marketplace_when_enabled_and_v2(self, _mock) -> None:
        """Test _check_match delegates to marketplace match when marketplace v2 is active."""
        behaviour = _make_response_behaviour()
        behaviour._context.params.use_mech_marketplace = True

        pending = MechInteractionResponse(nonce="n1")
        request = MagicMock()
        request.requestIds = ["0xabc"]

        result = behaviour._check_match(pending, request, is_first_pending=True)
        assert result is True

    def test_dispatches_to_legacy_when_marketplace_disabled(self) -> None:
        """Test _check_match delegates to legacy match when marketplace is disabled."""
        behaviour = _make_response_behaviour()
        behaviour._context.params.use_mech_marketplace = False

        pending = MechInteractionResponse(nonce="n1", data="aabb")
        request = MagicMock()
        request.data = MagicMock()
        request.data.hex.return_value = "aabb"
        request.requestId = 5

        result = behaviour._check_match(pending, request, is_first_pending=True)
        assert result is True
        assert pending.requestId == 5

    @patch.object(
        MechResponseBehaviour, "should_use_marketplace_v2", return_value=False
    )
    def test_dispatches_to_legacy_when_v1(self, _mock) -> None:
        """Test _check_match delegates to legacy match when marketplace is v1."""
        behaviour = _make_response_behaviour()
        behaviour._context.params.use_mech_marketplace = True

        pending = MechInteractionResponse(nonce="n1", data="ccdd")
        request = MagicMock()
        request.data = MagicMock()
        request.data.hex.return_value = "ccdd"
        request.requestId = 10

        result = behaviour._check_match(pending, request, is_first_pending=True)
        assert result is True
