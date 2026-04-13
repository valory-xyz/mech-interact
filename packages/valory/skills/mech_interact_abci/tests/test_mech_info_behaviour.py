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

"""Tests for the mech_info behaviour module."""

from unittest.mock import MagicMock

from packages.valory.skills.mech_interact_abci.behaviours.mech_info import (
    CID_PREFIX,
    MechInformationBehaviour,
)
from packages.valory.skills.mech_interact_abci.graph_tooling.requests import FetchStatus
from packages.valory.skills.mech_interact_abci.states.base import MechInfo, Service


def _make_mech_info_behaviour(**overrides) -> MechInformationBehaviour:
    """Create a MechInformationBehaviour with mocked dependencies."""
    behaviour = MechInformationBehaviour.__new__(MechInformationBehaviour)
    mock_context = MagicMock()
    behaviour._context = mock_context
    behaviour._fetch_status = FetchStatus.NONE
    behaviour._failed_mechs = set()

    for key, value in overrides.items():
        setattr(behaviour, key, value)

    return behaviour


def _make_mech_info(
    address: str = "0xmech1",
    metadata_str: str = "abc123",
    relevant_tools: set = None,
) -> MechInfo:
    """Create a MechInfo with test data."""
    service = Service(
        metadata=[{"metadata": "0x" + metadata_str}],
        deliveries=[],
    )
    return MechInfo(
        id="1",
        address=address,
        service=service,
        karma=10,
        receivedRequests=5,
        selfDeliveredFromReceived=3,
        maxDeliveryRate=100,
        relevant_tools=relevant_tools or set(),
    )


class TestSetMechAgentSpecs:
    """Tests for set_mech_agent_specs."""

    def test_constructs_ipfs_link(self) -> None:
        """Test that set_mech_agent_specs builds correct IPFS link."""
        behaviour = _make_mech_info_behaviour()
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        behaviour._context.mech_tools = mock_api

        behaviour.set_mech_agent_specs("abc123")

        expected_url = "https://ipfs.io/" + CID_PREFIX + "abc123"
        assert mock_api.url == expected_url


class TestPopulateTools:
    """Tests for the populate_tools generator method."""

    def test_skips_mechs_with_existing_tools(self) -> None:
        """Test that mechs with already-populated tools are skipped."""
        behaviour = _make_mech_info_behaviour()
        mech = _make_mech_info(relevant_tools={"tool1", "tool2"})

        gen = behaviour.populate_tools([mech])
        try:
            gen.send(None)  # start generator
        except StopIteration as e:
            result = e.value

        assert result is True
        assert mech.relevant_tools == {"tool1", "tool2"}

    def test_populates_tools_from_http_response(self) -> None:
        """Test that tools are fetched via HTTP and populated on mech info."""
        behaviour = _make_mech_info_behaviour()
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"
        behaviour._context.params.irrelevant_tools = {"irrelevant_tool"}

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        mock_api.get_spec.return_value = {"url": "http://test", "method": "GET"}
        mock_api.process_response.return_value = ["tool_a", "tool_b", "irrelevant_tool"]
        behaviour._context.mech_tools = mock_api

        mech = _make_mech_info(relevant_tools=set())

        # Mock get_http_response as a generator
        http_response = MagicMock()

        def mock_get_http_response(**kwargs):
            yield
            return http_response

        behaviour.get_http_response = mock_get_http_response

        gen = behaviour.populate_tools([mech])
        try:
            next(gen)  # yield from get_http_response
            gen.send(None)
        except StopIteration as e:
            result = e.value

        assert result is True
        assert mech.relevant_tools == {"tool_a", "tool_b"}
        mock_api.reset_retries.assert_called_once()

    def test_returns_false_on_failed_http(self) -> None:
        """Test that failed HTTP response returns False and increments retries."""
        behaviour = _make_mech_info_behaviour()
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        mock_api.get_spec.return_value = {"url": "http://test", "method": "GET"}
        mock_api.process_response.return_value = None  # failed
        mock_api.is_retries_exceeded.return_value = False
        mock_api.url = "http://test/hash"
        behaviour._context.mech_tools = mock_api

        mech = _make_mech_info(relevant_tools=set())

        def mock_get_http_response(**kwargs):
            yield
            return MagicMock()

        behaviour.get_http_response = mock_get_http_response

        gen = behaviour.populate_tools([mech])
        try:
            next(gen)
            gen.send(None)
        except StopIteration as e:
            result = e.value

        assert result is False
        mock_api.increment_retries.assert_called_once()
        behaviour.context.logger.warning.assert_called()

    def test_warns_on_empty_tools(self) -> None:
        """Test that empty tools list logs a warning but still succeeds."""
        behaviour = _make_mech_info_behaviour()
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"
        behaviour._context.params.irrelevant_tools = set()

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        mock_api.get_spec.return_value = {"url": "http://test", "method": "GET"}
        mock_api.process_response.return_value = []  # empty
        behaviour._context.mech_tools = mock_api

        mech = _make_mech_info(relevant_tools=set())

        def mock_get_http_response(**kwargs):
            yield
            return MagicMock()

        behaviour.get_http_response = mock_get_http_response

        gen = behaviour.populate_tools([mech])
        try:
            next(gen)
            gen.send(None)
        except StopIteration as e:
            result = e.value

        assert result is True
        # Check the empty tools warning was logged
        warning_calls = behaviour.context.logger.warning.call_args_list
        assert any("empty" in str(call) for call in warning_calls)

    def test_multiple_mechs_processes_all(self) -> None:
        """Test that populate_tools processes all mechs without existing tools."""
        behaviour = _make_mech_info_behaviour()
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"
        behaviour._context.params.irrelevant_tools = set()

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        mock_api.get_spec.return_value = {"url": "http://test", "method": "GET"}
        # Return different tools for each call
        mock_api.process_response.side_effect = [["tool_1"], ["tool_2"]]
        behaviour._context.mech_tools = mock_api

        mech1 = _make_mech_info(address="0xmech1", relevant_tools=set())
        mech2 = _make_mech_info(address="0xmech2", relevant_tools=set())

        def mock_get_http_response(**kwargs):
            yield
            return MagicMock()

        behaviour.get_http_response = mock_get_http_response

        gen = behaviour.populate_tools([mech1, mech2])
        try:
            next(gen)  # first mech: yield from get_http_response
            gen.send(
                None
            )  # first mech completes, second mech: yield from get_http_response
            gen.send(None)  # second mech completes, generator returns True
        except StopIteration as e:
            result = e.value

        assert result is True
        assert mech1.relevant_tools == {"tool_1"}
        assert mech2.relevant_tools == {"tool_2"}


class TestGetMechsInfo:
    """Tests for the get_mechs_info generator method."""

    def test_returns_none_on_fetch_failure(self) -> None:
        """Test get_mechs_info returns None when fetch_mechs_info fails."""
        behaviour = _make_mech_info_behaviour()
        behaviour._fetch_status = FetchStatus.IN_PROGRESS  # not SUCCESS

        def mock_fetch_mechs_info():
            yield
            return []

        behaviour.fetch_mechs_info = mock_fetch_mechs_info

        gen = behaviour.get_mechs_info()
        try:
            next(gen)
            gen.send(None)
        except StopIteration as e:
            result = e.value

        assert result is None

    def test_returns_none_on_empty_info(self) -> None:
        """Test get_mechs_info returns None when mech info is empty."""
        behaviour = _make_mech_info_behaviour()
        behaviour._fetch_status = FetchStatus.SUCCESS

        def mock_fetch_mechs_info():
            behaviour._fetch_status = FetchStatus.SUCCESS
            yield
            return []

        behaviour.fetch_mechs_info = mock_fetch_mechs_info

        gen = behaviour.get_mechs_info()
        try:
            next(gen)
            gen.send(None)
        except StopIteration as e:
            result = e.value

        assert result is None


class TestQuarantine:
    """Tests for per-mech quarantine on IPFS fetch failure."""

    def test_populate_tools_quarantines_mech_after_retries_exhausted(self) -> None:
        """A mech whose fetch fails with retries exhausted is added to _failed_mechs."""
        behaviour = _make_mech_info_behaviour()
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        mock_api.get_spec.return_value = {"url": "http://test", "method": "GET"}
        mock_api.process_response.return_value = None
        mock_api.is_retries_exceeded.return_value = True
        mock_api.url = "http://test/broken-cid"
        behaviour._context.mech_tools = mock_api

        mech = _make_mech_info(address="0xbroken", relevant_tools=set())

        def mock_get_http_response(**kwargs):
            yield
            return MagicMock()

        behaviour.get_http_response = mock_get_http_response

        gen = behaviour.populate_tools([mech])
        try:
            next(gen)
            gen.send(None)
        except StopIteration as e:
            result = e.value

        assert result is False
        assert "0xbroken" in behaviour._failed_mechs
        mock_api.reset_retries.assert_called_once()
        behaviour.context.logger.error.assert_called()

    def test_populate_tools_skips_quarantined_mechs(self) -> None:
        """A mech already in _failed_mechs is not fetched again."""
        behaviour = _make_mech_info_behaviour()
        behaviour._failed_mechs = {"0xbroken"}

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        behaviour._context.mech_tools = mock_api

        called = {"count": 0}

        def mock_get_http_response(**kwargs):
            called["count"] += 1
            yield
            return MagicMock()

        behaviour.get_http_response = mock_get_http_response

        mech = _make_mech_info(address="0xbroken", relevant_tools=set())

        gen = behaviour.populate_tools([mech])
        try:
            gen.send(None)
        except StopIteration as e:
            result = e.value

        assert result is True
        assert called["count"] == 0

    def test_get_mechs_info_partial_success_returns_serialized_json(self) -> None:
        """One good mech + one broken mech returns JSON with both; broken has empty tools."""
        behaviour = _make_mech_info_behaviour()
        behaviour._fetch_status = FetchStatus.SUCCESS
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"
        behaviour._context.params.irrelevant_tools = set()

        good = _make_mech_info(address="0xgood", relevant_tools=set())
        broken = _make_mech_info(address="0xbroken", relevant_tools=set())

        def mock_fetch_mechs_info():
            behaviour._fetch_status = FetchStatus.SUCCESS
            yield
            return [good, broken]

        behaviour.fetch_mechs_info = mock_fetch_mechs_info

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        mock_api.get_spec.return_value = {"url": "http://test", "method": "GET"}
        # good -> tools list; broken -> None repeatedly until retries exceeded.
        mock_api.process_response.side_effect = [["tool_good"], None]
        mock_api.is_retries_exceeded.return_value = True
        mock_api.url = "http://test/broken"
        behaviour._context.mech_tools = mock_api

        def mock_get_http_response(**kwargs):
            yield
            return MagicMock()

        behaviour.get_http_response = mock_get_http_response

        gen = behaviour.get_mechs_info()
        result = None
        try:
            while True:
                next(gen)
                gen.send(None)
        except StopIteration as e:
            result = e.value

        assert result is not None
        assert "0xgood" in result
        assert "0xbroken" in result
        assert good.relevant_tools == {"tool_good"}
        assert broken.relevant_tools == set()
        assert "0xbroken" in behaviour._failed_mechs

    def test_get_mechs_info_all_failed_returns_none(self) -> None:
        """If every mech fails, result is None so the round can retry."""
        behaviour = _make_mech_info_behaviour()
        behaviour._fetch_status = FetchStatus.SUCCESS
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"

        mech = _make_mech_info(address="0xbroken", relevant_tools=set())

        def mock_fetch_mechs_info():
            behaviour._fetch_status = FetchStatus.SUCCESS
            yield
            return [mech]

        behaviour.fetch_mechs_info = mock_fetch_mechs_info

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        mock_api.get_spec.return_value = {"url": "http://test", "method": "GET"}
        mock_api.process_response.return_value = None
        mock_api.is_retries_exceeded.return_value = True
        mock_api.url = "http://test/broken"
        behaviour._context.mech_tools = mock_api

        def mock_get_http_response(**kwargs):
            yield
            return MagicMock()

        behaviour.get_http_response = mock_get_http_response

        gen = behaviour.get_mechs_info()
        result = "unset"
        try:
            while True:
                next(gen)
                gen.send(None)
        except StopIteration as e:
            result = e.value

        assert result is None
        assert "0xbroken" in behaviour._failed_mechs

    def test_clean_up_clears_failed_mechs(self) -> None:
        """clean_up resets the per-round quarantine set."""
        behaviour = _make_mech_info_behaviour()
        behaviour._failed_mechs = {"0xbroken"}
        mock_api = MagicMock()
        behaviour._context.mech_tools = mock_api

        behaviour.clean_up()

        assert behaviour._failed_mechs == set()
        mock_api.reset_retries.assert_called_once()
