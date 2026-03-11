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

    def test_basic_query(self) -> None:
        """Test converting a basic query string to content bytes."""
        query = "{ mechs { id } }"
        result = to_content(query)
        assert isinstance(result, bytes)
        parsed = json.loads(result)
        assert parsed == {"query": "{ mechs { id } }"}

    def test_empty_query(self) -> None:
        """Test converting an empty query string."""
        result = to_content("")
        parsed = json.loads(result)
        assert parsed == {"query": ""}

    def test_sorted_keys(self) -> None:
        """Test that the JSON output has sorted keys."""
        result = to_content("test")
        decoded = result.decode("utf-8")
        assert '"query"' in decoded

    def test_utf8_encoding(self) -> None:
        """Test that the result is UTF-8 encoded."""
        query = '{ mechs(where: {name: "test"}) { id } }'
        result = to_content(query)
        assert result.decode("utf-8")  # Should not raise


class TestFetchStatus:
    """Tests for the FetchStatus enum."""

    def test_all_statuses(self) -> None:
        """Test that all expected statuses exist."""
        assert FetchStatus.SUCCESS is not None
        assert FetchStatus.IN_PROGRESS is not None
        assert FetchStatus.FAIL is not None
        assert FetchStatus.NONE is not None

    def test_enum_values_are_unique(self) -> None:
        """Test that enum values are unique."""
        values = [s.value for s in FetchStatus]
        assert len(values) == len(set(values))
