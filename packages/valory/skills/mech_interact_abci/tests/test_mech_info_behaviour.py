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

from typing import Any, Generator, List, Optional, Set
from unittest.mock import MagicMock, patch

from packages.valory.skills.mech_interact_abci.behaviours.mech_info import (
    CID_PREFIX,
    MechInformationBehaviour,
)
from packages.valory.skills.mech_interact_abci.graph_tooling.requests import FetchStatus
from packages.valory.skills.mech_interact_abci.states.base import MechInfo, Service


def _make_mech_info_behaviour(**overrides: Any) -> MechInformationBehaviour:
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
    relevant_tools: Optional[Set[str]] = None,
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


def _drive(gen: Generator[Any, Any, Any]) -> Any:
    """Run a generator to StopIteration, returning its .value."""
    try:
        while True:
            next(gen)
    except StopIteration as e:
        return e.value


def _wire_get_http_response(
    behaviour: MechInformationBehaviour, responses: List[Any]
) -> None:
    """Wire behaviour.get_http_response to return values in order."""
    iter_vals = iter(responses)

    def mock_get_http_response(**kwargs: Any) -> Any:
        yield
        return next(iter_vals)

    behaviour.get_http_response = mock_get_http_response  # type: ignore[method-assign,assignment]


def _setup_api(
    behaviour: MechInformationBehaviour,
    **overrides: Any,
) -> MagicMock:
    """Set up a behaviour with mocked context.params and mech_tools_api.

    Tests that need a non-empty `valid_mechs` set it directly on
    `behaviour._context.params.valid_mechs` after calling this helper.
    """
    behaviour._context.params = MagicMock()
    behaviour._context.params.ipfs_address = "https://ipfs.io/"
    behaviour._context.params.valid_mechs = frozenset()

    api = MagicMock()
    api.__dict__["_frozen"] = True
    api.url = "http://test/hash"
    api.get_spec = lambda: {"url": api.url, "method": "GET"}

    for key, value in overrides.items():
        setattr(api, key, value)

    behaviour._context.mech_tools = api
    return api


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
        """Mechs whose relevant_tools is already populated are not refetched."""
        behaviour = _make_mech_info_behaviour()
        mech = _make_mech_info(relevant_tools={"tool1", "tool2"})

        result = _drive(behaviour.populate_tools([mech]))

        assert result is True
        assert mech.relevant_tools == {"tool1", "tool2"}

    def test_skips_quarantined_mechs(self) -> None:
        """Mechs in _failed_mechs are not fetched."""
        behaviour = _make_mech_info_behaviour()
        behaviour._failed_mechs = {"0xbroken"}

        called = {"count": 0}

        def mock_get_http_response(**kwargs: Any) -> Any:
            called["count"] += 1
            yield
            return MagicMock()

        behaviour.get_http_response = mock_get_http_response  # type: ignore[method-assign,assignment]

        mech = _make_mech_info(address="0xbroken", relevant_tools=set())

        result = _drive(behaviour.populate_tools([mech]))

        assert result is True
        assert called["count"] == 0

    def test_populates_tools_from_http_response(self) -> None:
        """Manifest tools are surfaced verbatim onto each mech's relevant_tools."""
        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        api.process_response.return_value = ["tool_a", "tool_b", "tool_c"]

        mech = _make_mech_info(relevant_tools=set())
        _wire_get_http_response(behaviour, [MagicMock()])

        result = _drive(behaviour.populate_tools([mech]))

        assert result is True
        assert mech.relevant_tools == {"tool_a", "tool_b", "tool_c"}
        api.reset_retries.assert_called_once()

    def test_returns_false_on_transient_error(self) -> None:
        """Transient HTTP failure increments retries and returns False."""
        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        api.process_response.return_value = None
        api.is_permanent_error.return_value = False
        api.is_retries_exceeded.return_value = False

        mech = _make_mech_info(address="0xtransient", relevant_tools=set())
        _wire_get_http_response(behaviour, [MagicMock()])

        result = _drive(behaviour.populate_tools([mech]))

        assert result is False
        assert behaviour._failed_mechs == set()
        api.increment_retries.assert_called_once()
        api.reset_retries.assert_not_called()
        behaviour.context.logger.warning.assert_called()

    def test_quarantines_on_retries_exhausted(self) -> None:
        """Transient mech whose retries are exhausted is added to _failed_mechs."""
        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        api.process_response.return_value = None
        api.is_permanent_error.return_value = False
        api.is_retries_exceeded.return_value = True

        mech = _make_mech_info(address="0xbroken", relevant_tools=set())
        _wire_get_http_response(behaviour, [MagicMock()])

        result = _drive(behaviour.populate_tools([mech]))

        assert result is False
        assert "0xbroken" in behaviour._failed_mechs
        api.reset_retries.assert_called_once()
        behaviour.context.logger.error.assert_called()

    def test_http_url_is_hydrated_from_manifest(self) -> None:
        """Mechs sharing a CID get ``http_url`` set from ``manifest['url']``."""
        import json as _json
        from types import SimpleNamespace

        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        api.process_response.return_value = ["tool_a"]

        http_message = SimpleNamespace(
            body=_json.dumps(
                {"tools": ["tool_a"], "url": "https://mech.example/agent/abc"}
            ).encode("utf-8")
        )
        _wire_get_http_response(behaviour, [http_message])

        mech = _make_mech_info(relevant_tools=set())
        result = _drive(behaviour.populate_tools([mech]))

        assert result is True
        assert mech.http_url == "https://mech.example/agent/abc"

    def test_http_url_stays_none_when_manifest_lacks_key(self) -> None:
        """Older manifests without ``url`` leave ``http_url`` set to ``None``."""
        import json as _json
        from types import SimpleNamespace

        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        api.process_response.return_value = ["tool_a"]

        http_message = SimpleNamespace(
            body=_json.dumps({"tools": ["tool_a"]}).encode("utf-8")
        )
        _wire_get_http_response(behaviour, [http_message])

        mech = _make_mech_info(relevant_tools=set())
        result = _drive(behaviour.populate_tools([mech]))

        assert result is True
        assert mech.http_url is None

    def test_http_url_stays_none_on_non_json_manifest(self) -> None:
        """Malformed manifest body must not crash and leaves ``http_url=None``."""
        from types import SimpleNamespace

        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        api.process_response.return_value = ["tool_a"]

        http_message = SimpleNamespace(body=b"not json at all")
        _wire_get_http_response(behaviour, [http_message])

        mech = _make_mech_info(relevant_tools=set())
        result = _drive(behaviour.populate_tools([mech]))

        assert result is True
        assert mech.http_url is None

    def test_quarantines_on_empty_tools(self) -> None:
        """Empty tools list is deterministic per-CID; mech is quarantined."""
        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        api.process_response.return_value = []

        mech = _make_mech_info(address="0xempty", relevant_tools=set())
        _wire_get_http_response(behaviour, [MagicMock()])

        result = _drive(behaviour.populate_tools([mech]))

        assert result is True
        assert "0xempty" in behaviour._failed_mechs
        assert mech.relevant_tools == set()
        warnings = behaviour.context.logger.warning.call_args_list
        assert any("empty" in str(call) for call in warnings)
        api.reset_retries.assert_called_once()


class TestPermanentErrorClassification:
    """Tests for the permanent-vs-transient error branch in populate_tools."""

    def test_permanent_error_quarantines_without_incrementing_retries(self) -> None:
        """Permanent classification skips the retry counter entirely."""
        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        api.process_response.return_value = None
        api.is_permanent_error.return_value = True

        mech = _make_mech_info(address="0xpermanent", relevant_tools=set())

        http_message = MagicMock()
        http_message.status_code = 500
        _wire_get_http_response(behaviour, [http_message])

        result = _drive(behaviour.populate_tools([mech]))

        assert result is False
        assert "0xpermanent" in behaviour._failed_mechs
        api.increment_retries.assert_not_called()
        api.reset_retries.assert_called_once()
        api.is_permanent_error.assert_called_once_with(http_message)
        errors = behaviour.context.logger.error.call_args_list
        assert any("permanent content error" in str(call) for call in errors)

    def test_transient_then_permanent_resets_retries_on_quarantine(self) -> None:
        """Retry counter bumped on transient call, cleared on permanent quarantine."""
        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        api.process_response.return_value = None
        api.is_retries_exceeded.return_value = False

        mech = _make_mech_info(address="0xflaky", relevant_tools=set())

        # Call 1: transient — increment_retries, no quarantine, no reset.
        api.is_permanent_error.return_value = False
        _wire_get_http_response(behaviour, [MagicMock()])
        _drive(behaviour.populate_tools([mech]))
        assert "0xflaky" not in behaviour._failed_mechs
        api.increment_retries.assert_called_once()
        api.reset_retries.assert_not_called()

        # Call 2: gateway flips to a permanent body on the same mech.
        api.is_permanent_error.return_value = True
        _wire_get_http_response(behaviour, [MagicMock()])
        _drive(behaviour.populate_tools([mech]))
        assert "0xflaky" in behaviour._failed_mechs
        api.increment_retries.assert_called_once()  # still only the first call
        api.reset_retries.assert_called_once()  # the permanent branch resets


class TestCidGrouping:
    """Tests for CID-coalesced fetching in populate_tools.

    Mechs that share a metadata CID resolve to the same IPFS manifest, so
    the populate loop coalesces them into a single fetch and broadcasts the
    result to every mech in the group.
    """

    def test_shared_cid_fetched_once_for_all_mechs(self) -> None:
        """N mechs with identical metadata_str result in a single HTTP fetch."""
        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        api.process_response.return_value = ["tool_x", "tool_y"]

        mechs = [
            _make_mech_info(
                address=f"0x{i}", metadata_str="shared", relevant_tools=set()
            )
            for i in range(4)
        ]

        call_count = {"n": 0}

        def mock_get_http_response(**kwargs: Any) -> Any:
            call_count["n"] += 1
            yield
            return MagicMock()

        behaviour.get_http_response = mock_get_http_response  # type: ignore[method-assign,assignment]

        result = _drive(behaviour.populate_tools(mechs))

        assert result is True
        assert call_count["n"] == 1  # one fetch, not four
        for mech in mechs:
            assert mech.relevant_tools == {"tool_x", "tool_y"}

    def test_distinct_cids_fetched_separately(self) -> None:
        """Each distinct CID gets its own HTTP fetch."""
        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        api.process_response.side_effect = [["tool_a"], ["tool_b"]]

        mech_a = _make_mech_info(
            address="0xa", metadata_str="aaa", relevant_tools=set()
        )
        mech_b = _make_mech_info(
            address="0xb", metadata_str="bbb", relevant_tools=set()
        )

        call_count = {"n": 0}

        def mock_get_http_response(**kwargs: Any) -> Any:
            call_count["n"] += 1
            yield
            return MagicMock()

        behaviour.get_http_response = mock_get_http_response  # type: ignore[method-assign,assignment]

        result = _drive(behaviour.populate_tools([mech_a, mech_b]))

        assert result is True
        assert call_count["n"] == 2
        assert mech_a.relevant_tools == {"tool_a"}
        assert mech_b.relevant_tools == {"tool_b"}

    def test_permanent_error_quarantines_all_mechs_in_group(self) -> None:
        """Permanent error on a CID quarantines every mech sharing that CID."""
        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        api.process_response.return_value = None
        api.is_permanent_error.return_value = True

        mechs = [
            _make_mech_info(address=f"0x{i}", metadata_str="bad", relevant_tools=set())
            for i in range(3)
        ]

        http_message = MagicMock()
        http_message.status_code = 404
        _wire_get_http_response(behaviour, [http_message])

        result = _drive(behaviour.populate_tools(mechs))

        assert result is False
        for mech in mechs:
            assert mech.address in behaviour._failed_mechs
        api.increment_retries.assert_not_called()

    def test_empty_tools_quarantines_all_mechs_in_group(self) -> None:
        """Empty manifest on a shared CID quarantines every mech in the group."""
        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        api.process_response.return_value = []

        mechs = [
            _make_mech_info(
                address=f"0x{i}", metadata_str="empty", relevant_tools=set()
            )
            for i in range(3)
        ]

        _wire_get_http_response(behaviour, [MagicMock()])

        result = _drive(behaviour.populate_tools(mechs))

        assert result is True
        for mech in mechs:
            assert mech.address in behaviour._failed_mechs

    def test_retries_exhausted_quarantines_all_mechs_in_group(self) -> None:
        """Retries-exhausted on a shared CID quarantines every mech in the group."""
        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        api.process_response.return_value = None
        api.is_permanent_error.return_value = False
        api.is_retries_exceeded.return_value = True

        mechs = [
            _make_mech_info(
                address=f"0x{i}", metadata_str="flaky", relevant_tools=set()
            )
            for i in range(3)
        ]

        _wire_get_http_response(behaviour, [MagicMock()])

        result = _drive(behaviour.populate_tools(mechs))

        assert result is False
        for mech in mechs:
            assert mech.address in behaviour._failed_mechs
        api.reset_retries.assert_called_once()


class TestGetMechsInfo:
    """Tests for the get_mechs_info generator method."""

    def test_returns_none_on_fetch_failure(self) -> None:
        """Returns None when fetch_mechs_info doesn't reach SUCCESS state."""
        behaviour = _make_mech_info_behaviour()
        behaviour._fetch_status = FetchStatus.IN_PROGRESS

        def mock_fetch_mechs_info() -> Generator[None, None, List[MechInfo]]:
            yield
            return []

        behaviour.fetch_mechs_info = mock_fetch_mechs_info  # type: ignore[method-assign]

        result = _drive(behaviour.get_mechs_info())

        assert result is None

    def test_returns_none_on_empty_info(self) -> None:
        """Returns None when mech info is empty."""
        behaviour = _make_mech_info_behaviour()

        def mock_fetch_mechs_info() -> Generator[None, None, List[MechInfo]]:
            behaviour._fetch_status = FetchStatus.SUCCESS
            yield
            return []

        behaviour.fetch_mechs_info = mock_fetch_mechs_info  # type: ignore[method-assign]

        result = _drive(behaviour.get_mechs_info())

        assert result is None

    def test_partial_success_returns_serialized_json(self) -> None:
        """One good mech + one broken mech returns JSON; broken has empty tools."""
        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        api.process_response.side_effect = [["tool_good"], None]
        api.is_permanent_error.return_value = True
        api.url = "http://test/broken"

        good = _make_mech_info(
            address="0xgood", metadata_str="good", relevant_tools=set()
        )
        broken = _make_mech_info(
            address="0xbroken", metadata_str="broken", relevant_tools=set()
        )

        def mock_fetch_mechs_info() -> Generator[None, None, List[MechInfo]]:
            behaviour._fetch_status = FetchStatus.SUCCESS
            yield
            return [good, broken]

        behaviour.fetch_mechs_info = mock_fetch_mechs_info  # type: ignore[method-assign]
        _wire_get_http_response(behaviour, [MagicMock(), MagicMock()])

        with patch.object(
            MechInformationBehaviour,
            "synchronized_data",
            new_callable=lambda: property(lambda _self: MagicMock(selected_mechs=[])),
        ):
            result = _drive(behaviour.get_mechs_info())

        assert result is not None
        assert "0xgood" in result
        assert "0xbroken" in result
        assert good.relevant_tools == {"tool_good"}
        assert broken.relevant_tools == set()
        assert "0xbroken" in behaviour._failed_mechs

    def test_all_failed_returns_none(self) -> None:
        """If every mech fails, result is None so the round can retry."""
        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        api.process_response.return_value = None
        api.is_permanent_error.return_value = False
        api.is_retries_exceeded.return_value = True

        mech = _make_mech_info(address="0xbroken", relevant_tools=set())

        def mock_fetch_mechs_info() -> Generator[None, None, List[MechInfo]]:
            behaviour._fetch_status = FetchStatus.SUCCESS
            yield
            return [mech]

        behaviour.fetch_mechs_info = mock_fetch_mechs_info  # type: ignore[method-assign]
        _wire_get_http_response(behaviour, [MagicMock()])

        result = _drive(behaviour.get_mechs_info())

        assert result is None
        assert "0xbroken" in behaviour._failed_mechs


class TestLastFailureReason:
    """Tests for `last_failure_reason` writes from get_mechs_info."""

    def test_writes_subgraph_unavailable_on_fetch_failure(self) -> None:
        """fetch_mechs_info that never reaches SUCCESS writes `subgraph_unavailable`."""
        behaviour = _make_mech_info_behaviour()
        behaviour._fetch_status = FetchStatus.IN_PROGRESS

        def mock_fetch_mechs_info() -> Generator[None, None, List[MechInfo]]:
            yield
            return []

        behaviour.fetch_mechs_info = mock_fetch_mechs_info  # type: ignore[method-assign]

        _drive(behaviour.get_mechs_info())

        assert behaviour._context.state.last_failure_reason == "subgraph_unavailable"

    def test_writes_allowlist_not_configured_when_empty_allowlist(self) -> None:
        """Empty subgraph result + empty `valid_mechs` writes `allowlist_not_configured`."""
        behaviour = _make_mech_info_behaviour()
        behaviour._context.params.valid_mechs = frozenset()

        def mock_fetch_mechs_info() -> Generator[None, None, List[MechInfo]]:
            behaviour._fetch_status = FetchStatus.SUCCESS
            yield
            return []

        behaviour.fetch_mechs_info = mock_fetch_mechs_info  # type: ignore[method-assign]

        _drive(behaviour.get_mechs_info())

        assert (
            behaviour._context.state.last_failure_reason == "allowlist_not_configured"
        )

    def test_writes_valid_mech_list_empty_when_subgraph_filtered_out(self) -> None:
        """Configured allowlist + empty subgraph result writes `valid_mech_list_empty`."""
        behaviour = _make_mech_info_behaviour()
        behaviour._context.params.valid_mechs = frozenset({"0xmech1"})

        def mock_fetch_mechs_info() -> Generator[None, None, List[MechInfo]]:
            behaviour._fetch_status = FetchStatus.SUCCESS
            yield
            return []

        behaviour.fetch_mechs_info = mock_fetch_mechs_info  # type: ignore[method-assign]

        _drive(behaviour.get_mechs_info())

        assert behaviour._context.state.last_failure_reason == "valid_mech_list_empty"

    def test_writes_no_usable_tools_in_mechs_when_all_manifests_empty(self) -> None:
        """All manifests returning an empty tools list writes `no_usable_tools_in_mechs`."""
        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        api.process_response.return_value = []

        mech = _make_mech_info(address="0xmech1", relevant_tools=set())

        def mock_fetch_mechs_info() -> Generator[None, None, List[MechInfo]]:
            behaviour._fetch_status = FetchStatus.SUCCESS
            yield
            return [mech]

        behaviour.fetch_mechs_info = mock_fetch_mechs_info  # type: ignore[method-assign]
        _wire_get_http_response(behaviour, [MagicMock()])

        _drive(behaviour.get_mechs_info())

        assert (
            behaviour._context.state.last_failure_reason == "no_usable_tools_in_mechs"
        )

    def test_clears_failure_reason_on_success(self) -> None:
        """A successful round leaves `last_failure_reason` as None."""
        behaviour = _make_mech_info_behaviour()
        behaviour._context.state.last_failure_reason = "stale_reason"

        api = _setup_api(behaviour)
        api.process_response.return_value = ["tool_a"]

        mech = _make_mech_info(address="0xmech1", relevant_tools=set())

        def mock_fetch_mechs_info() -> Generator[None, None, List[MechInfo]]:
            behaviour._fetch_status = FetchStatus.SUCCESS
            yield
            return [mech]

        behaviour.fetch_mechs_info = mock_fetch_mechs_info  # type: ignore[method-assign]
        _wire_get_http_response(behaviour, [MagicMock()])

        with patch.object(
            MechInformationBehaviour,
            "synchronized_data",
            new_callable=lambda: property(lambda _self: MagicMock(selected_mechs=[])),
        ):
            result = _drive(behaviour.get_mechs_info())

        assert result is not None
        assert behaviour._context.state.last_failure_reason is None

    def test_writes_pinned_mechs_offline_when_pin_not_in_mech_info(self) -> None:
        """Pinned addresses absent from this round's mech_info write `pinned_mechs_offline`."""
        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        api.process_response.return_value = ["tool_a"]

        mech = _make_mech_info(address="0xa", relevant_tools=set())

        def mock_fetch_mechs_info() -> Generator[None, None, List[MechInfo]]:
            behaviour._fetch_status = FetchStatus.SUCCESS
            yield
            return [mech]

        behaviour.fetch_mechs_info = mock_fetch_mechs_info  # type: ignore[method-assign]
        _wire_get_http_response(behaviour, [MagicMock()])

        with patch.object(
            MechInformationBehaviour,
            "synchronized_data",
            new_callable=lambda: property(
                lambda _self: MagicMock(selected_mechs=["0xb"])
            ),
        ):
            result = _drive(behaviour.get_mechs_info())

        assert result is None
        assert behaviour._context.state.last_failure_reason == "pinned_mechs_offline"

    def test_writes_pinned_mechs_no_usable_tools_when_pinned_visible_but_no_tools(
        self,
    ) -> None:
        """Pinned mech visible but with an empty manifest writes `pinned_mechs_no_usable_tools`."""
        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        # First CID ("good") advertises tool_x; second CID ("pinned") has
        # an empty manifest tools list -- only `good` ends up with
        # relevant_tools and the pinned mech is quarantined empty.
        api.process_response.side_effect = [["tool_x"], []]

        good = _make_mech_info(
            address="0xgood", metadata_str="good", relevant_tools=set()
        )
        pinned = _make_mech_info(
            address="0xpinned", metadata_str="pinned", relevant_tools=set()
        )

        def mock_fetch_mechs_info() -> Generator[None, None, List[MechInfo]]:
            behaviour._fetch_status = FetchStatus.SUCCESS
            yield
            return [good, pinned]

        behaviour.fetch_mechs_info = mock_fetch_mechs_info  # type: ignore[method-assign]
        _wire_get_http_response(behaviour, [MagicMock(), MagicMock()])

        with patch.object(
            MechInformationBehaviour,
            "synchronized_data",
            new_callable=lambda: property(
                lambda _self: MagicMock(selected_mechs=["0xpinned"])
            ),
        ):
            result = _drive(behaviour.get_mechs_info())

        assert result is None
        assert (
            behaviour._context.state.last_failure_reason
            == "pinned_mechs_no_usable_tools"
        )

    def test_pinned_mech_in_mech_info_does_not_trip_pinned_mechs_offline(self) -> None:
        """When a pinned address IS visible, no failure reason is written."""
        behaviour = _make_mech_info_behaviour()
        api = _setup_api(behaviour)
        api.process_response.return_value = ["tool_a"]

        mech = _make_mech_info(address="0xa", relevant_tools=set())

        def mock_fetch_mechs_info() -> Generator[None, None, List[MechInfo]]:
            behaviour._fetch_status = FetchStatus.SUCCESS
            yield
            return [mech]

        behaviour.fetch_mechs_info = mock_fetch_mechs_info  # type: ignore[method-assign]
        _wire_get_http_response(behaviour, [MagicMock()])

        with patch.object(
            MechInformationBehaviour,
            "synchronized_data",
            new_callable=lambda: property(
                lambda _self: MagicMock(selected_mechs=["0xA"])
            ),
        ):
            result = _drive(behaviour.get_mechs_info())

        assert result is not None
        assert behaviour._context.state.last_failure_reason is None


class TestCleanUp:
    """Tests for clean_up."""

    def test_clean_up_clears_failed_mechs_and_resets_retries(self) -> None:
        """clean_up resets the per-round quarantine set and the retry counter."""
        behaviour = _make_mech_info_behaviour()
        behaviour._failed_mechs = {"0xbroken"}
        api = MagicMock()
        behaviour._context.mech_tools = api

        behaviour.clean_up()

        assert behaviour._failed_mechs == set()
        api.reset_retries.assert_called_once()
