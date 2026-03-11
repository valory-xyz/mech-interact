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
from dataclasses import dataclass

import pytest

from packages.valory.skills.mech_interact_abci.states.base import (
    MechInteractionResponse,
    MechRequest,
)
from packages.valory.skills.mech_interact_abci.utils import DataclassEncoder


class TestDataclassEncoder:
    """Tests for DataclassEncoder."""

    def test_encode_simple_dataclass(self) -> None:
        """Test encoding a simple dataclass."""

        @dataclass
        class SimpleData:
            name: str
            value: int

        obj = SimpleData(name="test", value=42)
        result = json.dumps(obj, cls=DataclassEncoder)
        parsed = json.loads(result)
        assert parsed == {"name": "test", "value": 42}

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

    def test_encode_dataclass_type_not_instance(self) -> None:
        """Test that a dataclass type (not instance) falls through to default."""
        encoder = DataclassEncoder()
        with pytest.raises(TypeError):
            encoder.default(MechRequest)
