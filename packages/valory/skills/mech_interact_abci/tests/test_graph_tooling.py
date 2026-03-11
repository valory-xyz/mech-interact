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

"""Tests for the graph_tooling module."""

import json

from packages.valory.skills.mech_interact_abci.graph_tooling.requests import (
    FetchStatus,
    to_content,
)


class TestToContent:
    """Tests for the to_content function."""

    def test_wraps_query_in_json(self) -> None:
        """Test converting a query string to JSON-encoded bytes."""
        result = to_content("{ mechs { id } }")
        parsed = json.loads(result)
        assert parsed == {"query": "{ mechs { id } }"}

    def test_empty_query(self) -> None:
        """Test converting an empty query string."""
        parsed = json.loads(to_content(""))
        assert parsed == {"query": ""}

    def test_returns_utf8_bytes(self) -> None:
        """Test that the result is valid UTF-8 bytes."""
        query = '{ mechs(where: {name: "test"}) { id } }'
        result = to_content(query)
        assert isinstance(result, bytes)
        result.decode("utf-8")  # should not raise


class TestFetchStatus:
    """Tests for the FetchStatus enum."""

    def test_has_exactly_four_members(self) -> None:
        """Test that FetchStatus has exactly 4 unique members."""
        assert len(FetchStatus) == 4

    def test_values_are_unique(self) -> None:
        """Test that enum values are unique."""
        values = [s.value for s in FetchStatus]
        assert len(values) == len(set(values))
