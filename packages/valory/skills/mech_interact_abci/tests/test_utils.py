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

"""Tests for the utils module."""

import json
import time

import pytest

from packages.valory.skills.mech_interact_abci.states.base import (
    MechInteractionResponse,
    MechRequest,
)
from packages.valory.skills.mech_interact_abci.utils import (
    DataclassEncoder,
    MAX_JSON_DEPTH,
    json_depth_exceeds,
    loads_bounded,
)

AT_LIMIT = "[" * MAX_JSON_DEPTH + "]" * MAX_JSON_DEPTH
OVER_LIMIT = "[" * (MAX_JSON_DEPTH + 1) + "]" * (MAX_JSON_DEPTH + 1)


class TestDataclassEncoder:
    """Tests for DataclassEncoder."""

    def test_encode_mech_request(self) -> None:
        """Test encoding a MechRequest dataclass."""
        request = MechRequest(data="0xabc", requestId=123)
        result = json.dumps(request, cls=DataclassEncoder)
        parsed = json.loads(result)
        assert parsed["data"] == "0xabc"
        assert parsed["requestId"] == 123

    def test_encode_mech_interaction_response_with_request_id(self) -> None:
        """Test encoding MechInteractionResponse with non-zero requestId."""
        response = MechInteractionResponse(
            nonce="nonce1", requestId=42, result="some result"
        )
        result = json.dumps(response, cls=DataclassEncoder)
        parsed = json.loads(result)
        assert parsed["requestId"] == 42
        assert parsed["result"] == "some result"
        assert parsed["nonce"] == "nonce1"

    def test_encode_mech_interaction_response_default_request_id(self) -> None:
        """Test encoding MechInteractionResponse with default (0) requestId."""
        response = MechInteractionResponse(nonce="nonce2")
        result = json.dumps(response, cls=DataclassEncoder)
        parsed = json.loads(result)
        assert parsed["nonce"] == "nonce2"

    def test_encode_non_dataclass_fallback(self) -> None:
        """Test that non-dataclass objects raise TypeError."""
        encoder = DataclassEncoder()
        with pytest.raises(TypeError):
            encoder.default(object())

    def test_encode_list_of_dataclasses(self) -> None:
        """Test encoding a list of dataclasses."""
        responses = [
            MechInteractionResponse(nonce="n1", requestId=1, result="r1"),
            MechInteractionResponse(nonce="n2", requestId=2, result="r2"),
        ]
        result = json.dumps(responses, cls=DataclassEncoder)
        parsed = json.loads(result)
        assert len(parsed) == 2
        assert parsed[0]["nonce"] == "n1"
        assert parsed[1]["nonce"] == "n2"


class TestJsonDepthExceeds:
    """Tests for json_depth_exceeds."""

    @pytest.mark.parametrize(
        "text, exceeds",
        [
            (AT_LIMIT, False),
            (OVER_LIMIT, True),
            (OVER_LIMIT.encode(), True),
            ("{" * (MAX_JSON_DEPTH + 1), True),
            ('{"a": "' + "[" * 1_000 + '"}', False),
            ('["\\"' + "[" * 1_000 + '"]', False),
            ("[" * 50 + "]" * 50 + "[" * 50 + "]" * 50, False),
            ("", False),
            ("plain text", False),
            ("]]]]", False),
            ('["' + "[" * 1_000, False),
        ],
        ids=[
            "at_limit",
            "over_limit",
            "over_limit_bytes",
            "unclosed_objects",
            "brackets_in_string",
            "brackets_after_escaped_quote",
            "siblings_not_nested",
            "empty",
            "no_brackets",
            "only_closing",
            "brackets_in_unclosed_string",
        ],
    )
    def test_counts_only_nesting_outside_strings(
        self, text: str, exceeds: bool
    ) -> None:
        """Depth is the deepest open bracket, ignoring brackets in strings."""
        assert json_depth_exceeds(text) is exceeds


def test_an_unclosed_string_is_read_in_linear_time() -> None:
    """Escaped quotes in a string that never closes are scanned once, not per quote."""
    text = '"' + '\\"' * 200_000
    started = time.monotonic()
    assert json_depth_exceeds(text) is False
    assert time.monotonic() - started < 1.0


class TestLoadsBounded:
    """Tests for loads_bounded."""

    def test_parses_a_document_within_the_limit(self) -> None:
        """A document at the limit parses as json.loads would."""
        assert loads_bounded(AT_LIMIT) == json.loads(AT_LIMIT)

    @pytest.mark.parametrize(
        "text", [OVER_LIMIT, "[" * 100_000 + "]" * 100_000], ids=["over", "deep"]
    )
    def test_refuses_a_document_over_the_limit(self, text: str) -> None:
        """A document nested past the limit raises ValueError before parsing."""
        with pytest.raises(ValueError, match="nested deeper"):
            loads_bounded(text)


class TestMultiByteEncodings:
    """Bytes in any encoding json.loads accepts are measured as it reads them."""

    @pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16", "utf-32"])
    def test_nesting_is_measured_after_decoding(self, encoding: str) -> None:
        """A document over the limit is refused whatever encoding it arrives in."""
        body = ('["\u2200", ' + OVER_LIMIT + "]").encode(encoding)
        assert json_depth_exceeds(body) is True
        with pytest.raises(ValueError, match="nested deeper"):
            loads_bounded(body)

    @pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le", "utf-32"])
    def test_a_document_within_the_limit_parses(self, encoding: str) -> None:
        """A document at the limit parses in every encoding json.loads accepts."""
        inner = "[" * (MAX_JSON_DEPTH - 1) + "]" * (MAX_JSON_DEPTH - 1)
        body = ('["\u2200", ' + inner + "]").encode(encoding)
        assert loads_bounded(body) == json.loads(body)

    def test_bytes_that_cannot_be_decoded_are_refused(self) -> None:
        """Bytes invalid in the detected encoding are refused, not scanned."""
        body = b"[\xff\xfe"
        assert json_depth_exceeds(body) is True
        with pytest.raises(ValueError):
            loads_bounded(body)
