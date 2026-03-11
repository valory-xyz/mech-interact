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

"""Tests for the mech_version behaviour module."""

import logging

from packages.valory.skills.mech_interact_abci.behaviours.mech_version import (
    V1,
    V2,
    get_version_name,
    suppress_logs,
)


class TestSuppressLogs:
    """Tests for the suppress_logs context manager."""

    def test_suppresses_logs(self) -> None:
        """Test that logs are suppressed inside the context."""
        logging.getLogger("test_suppress")
        with suppress_logs():
            # Inside the context, logging should be disabled at CRITICAL level
            assert logging.root.manager.disable >= logging.CRITICAL

        # After the context, logging should be restored
        assert logging.root.manager.disable < logging.CRITICAL

    def test_restores_previous_level(self) -> None:
        """Test that the previous logging level is restored."""
        previous = logging.root.manager.disable
        with suppress_logs():
            pass
        assert logging.root.manager.disable == previous

    def test_restores_on_exception(self) -> None:
        """Test that logging is restored even if an exception occurs."""
        previous = logging.root.manager.disable
        try:
            with suppress_logs():
                raise ValueError("test error")
        except ValueError:
            pass
        assert logging.root.manager.disable == previous

    def test_custom_level(self) -> None:
        """Test suppressing with a custom level."""
        with suppress_logs(level=logging.WARNING):
            assert logging.root.manager.disable >= logging.WARNING


class TestGetVersionName:
    """Tests for the get_version_name function."""

    def test_v2_true(self) -> None:
        """Test that True returns V2."""
        assert get_version_name(True) == V2

    def test_v1_false(self) -> None:
        """Test that False returns V1."""
        assert get_version_name(False) == V1
