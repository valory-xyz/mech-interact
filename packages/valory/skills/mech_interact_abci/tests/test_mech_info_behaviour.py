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
    # Deterministic clock for parallel-fetch tests; always returns 0 until
    # tests override. Overflow-safe for any reasonable timeout budget.
    behaviour._clock = lambda: 0.0

    for key, value in overrides.items():
        setattr(behaviour, key, value)

    return behaviour


def _wire_parallel_fetch(
    behaviour: MechInformationBehaviour,
    clock_values=None,
    registry=None,
):
    """Install outbox + requests mocks and a deterministic clock.

    :param clock_values: iterable of floats returned by successive _clock()
        calls. Default: constant 0.0 so timeout never fires.
    :param registry: pre-populated request_id_to_callback dict; default empty.
    """
    behaviour._context.outbox = MagicMock()
    behaviour._context.requests = MagicMock()
    behaviour._context.requests.request_id_to_callback = registry or {}
    if clock_values is not None:
        it = iter(clock_values)
        behaviour._clock = lambda: next(it)
    return behaviour


def _install_fake_parallel_fetch(behaviour, responses):
    """Replace behaviour._fetch_http_parallel with a generator returning responses.

    Each element of `responses` maps 1:1 to a pending mech in populate_tools.
    A value of None represents a timed-out request.
    """

    def _fake_fetch(specs, timeout, poll_interval=0.1):
        yield
        return list(responses)

    behaviour._fetch_http_parallel = _fake_fetch
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
        behaviour._context.params.mech_tools_parallel_timeout = 10.0

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        mock_api.get_spec.return_value = {"url": "http://test", "method": "GET"}
        mock_api.process_response.return_value = ["tool_a", "tool_b", "irrelevant_tool"]
        behaviour._context.mech_tools = mock_api

        mech = _make_mech_info(relevant_tools=set())
        _install_fake_parallel_fetch(behaviour, [MagicMock()])

        result = _drive(behaviour.populate_tools([mech]))

        assert result is True
        assert mech.relevant_tools == {"tool_a", "tool_b"}
        mock_api.reset_retries.assert_called_once()

    def test_returns_false_on_failed_http(self) -> None:
        """Test that failed HTTP response returns False and increments retries."""
        behaviour = _make_mech_info_behaviour()
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"
        behaviour._context.params.mech_tools_parallel_timeout = 10.0

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        mock_api.get_spec.return_value = {"url": "http://test", "method": "GET"}
        mock_api.process_response.return_value = None  # failed
        mock_api.is_retries_exceeded.return_value = False
        mock_api.is_permanent_error.return_value = False
        mock_api.url = "http://test/hash"
        behaviour._context.mech_tools = mock_api

        mech = _make_mech_info(relevant_tools=set())
        _install_fake_parallel_fetch(behaviour, [MagicMock()])

        result = _drive(behaviour.populate_tools([mech]))

        assert result is False
        mock_api.increment_retries.assert_called_once()
        behaviour.context.logger.warning.assert_called()

    def test_quarantines_on_empty_tools(self) -> None:
        """Empty tools list is deterministic per-CID; mech is quarantined."""
        behaviour = _make_mech_info_behaviour()
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"
        behaviour._context.params.irrelevant_tools = set()
        behaviour._context.params.mech_tools_parallel_timeout = 10.0

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        mock_api.get_spec.return_value = {"url": "http://test", "method": "GET"}
        mock_api.process_response.return_value = []  # empty
        behaviour._context.mech_tools = mock_api

        mech = _make_mech_info(address="0xempty", relevant_tools=set())
        _install_fake_parallel_fetch(behaviour, [MagicMock()])

        result = _drive(behaviour.populate_tools([mech]))

        assert result is True
        assert "0xempty" in behaviour._failed_mechs
        assert mech.relevant_tools == set()
        warning_calls = behaviour.context.logger.warning.call_args_list
        assert any("empty" in str(call) for call in warning_calls)
        mock_api.reset_retries.assert_called_once()

    def test_multiple_mechs_processes_all(self) -> None:
        """Test that populate_tools processes all mechs in parallel in one pass."""
        behaviour = _make_mech_info_behaviour()
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"
        behaviour._context.params.irrelevant_tools = set()
        behaviour._context.params.mech_tools_parallel_timeout = 10.0

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        mock_api.get_spec.return_value = {"url": "http://test", "method": "GET"}
        mock_api.process_response.side_effect = [["tool_1"], ["tool_2"]]
        behaviour._context.mech_tools = mock_api

        mech1 = _make_mech_info(address="0xmech1", relevant_tools=set())
        mech2 = _make_mech_info(address="0xmech2", relevant_tools=set())
        _install_fake_parallel_fetch(behaviour, [MagicMock(), MagicMock()])

        result = _drive(behaviour.populate_tools([mech1, mech2]))

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
        """A transient mech whose retries are exhausted is added to _failed_mechs."""
        behaviour = _make_mech_info_behaviour()
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"
        behaviour._context.params.mech_tools_parallel_timeout = 10.0

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        mock_api.get_spec.return_value = {"url": "http://test", "method": "GET"}
        mock_api.process_response.return_value = None
        mock_api.is_retries_exceeded.return_value = True
        mock_api.is_permanent_error.return_value = False
        mock_api.url = "http://test/broken-cid"
        behaviour._context.mech_tools = mock_api

        mech = _make_mech_info(address="0xbroken", relevant_tools=set())
        _install_fake_parallel_fetch(behaviour, [MagicMock()])

        result = _drive(behaviour.populate_tools([mech]))

        assert result is True  # mech now quarantined, no more work
        assert "0xbroken" in behaviour._failed_mechs
        mock_api.reset_retries.assert_called_once()
        behaviour.context.logger.error.assert_called()

    def test_populate_tools_does_not_quarantine_before_retries_exceeded(self) -> None:
        """Transient failure with retries remaining leaves _failed_mechs empty."""
        behaviour = _make_mech_info_behaviour()
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"
        behaviour._context.params.mech_tools_parallel_timeout = 10.0

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        mock_api.get_spec.return_value = {"url": "http://test", "method": "GET"}
        mock_api.process_response.return_value = None
        mock_api.is_retries_exceeded.return_value = False
        mock_api.is_permanent_error.return_value = False
        mock_api.url = "http://test/broken-cid"
        behaviour._context.mech_tools = mock_api

        mech = _make_mech_info(address="0xtransient", relevant_tools=set())
        _install_fake_parallel_fetch(behaviour, [MagicMock()])

        result = _drive(behaviour.populate_tools([mech]))

        assert result is False
        assert behaviour._failed_mechs == set()
        mock_api.increment_retries.assert_called_once()
        mock_api.reset_retries.assert_not_called()

    def test_quarantine_only_fires_on_final_retry(self) -> None:
        """Across N passes, retry counter advances once per pass; quarantine only on exhaustion."""
        behaviour = _make_mech_info_behaviour()
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"
        behaviour._context.params.mech_tools_parallel_timeout = 10.0

        retries_limit = 3
        call_count = {"n": 0}

        def is_retries_exceeded_side_effect():
            return call_count["n"] >= retries_limit

        def increment_retries_side_effect():
            call_count["n"] += 1

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        mock_api.get_spec.return_value = {"url": "http://test", "method": "GET"}
        mock_api.process_response.return_value = None
        mock_api.is_retries_exceeded.side_effect = is_retries_exceeded_side_effect
        mock_api.increment_retries.side_effect = increment_retries_side_effect
        mock_api.is_permanent_error.return_value = False
        mock_api.url = "http://test/broken-cid"
        behaviour._context.mech_tools = mock_api

        mech = _make_mech_info(address="0xflaky", relevant_tools=set())

        # Each populate_tools pass sees one transient failure.
        # The fake is re-used across passes.
        iterations = 0
        while True:
            iterations += 1
            _install_fake_parallel_fetch(behaviour, [MagicMock()])
            outcome = _drive(behaviour.populate_tools([mech]))
            if outcome:
                break
            if iterations < retries_limit:
                assert behaviour._failed_mechs == set()

        # Quarantine fires on the pass that exhausts retries (same iteration
        # as the Nth increment), so we expect exactly retries_limit passes.
        assert iterations == retries_limit
        assert "0xflaky" in behaviour._failed_mechs
        assert mock_api.increment_retries.call_count == retries_limit

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
        behaviour._context.params.mech_tools_parallel_timeout = 10.0

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
        mock_api.process_response.side_effect = [["tool_good"], None]
        mock_api.is_retries_exceeded.return_value = True
        mock_api.is_permanent_error.return_value = False
        mock_api.url = "http://test/broken"
        behaviour._context.mech_tools = mock_api
        _install_fake_parallel_fetch(behaviour, [MagicMock(), MagicMock()])

        result = _drive(behaviour.get_mechs_info())

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
        behaviour._context.params.mech_tools_parallel_timeout = 10.0

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
        mock_api.is_permanent_error.return_value = False
        mock_api.url = "http://test/broken"
        behaviour._context.mech_tools = mock_api
        _install_fake_parallel_fetch(behaviour, [MagicMock()])

        result = _drive(behaviour.get_mechs_info())

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


class TestPermanentErrorClassification:
    """Tests for the permanent-vs-transient error branch in populate_tools."""

    def test_permanent_error_quarantines_without_incrementing_retries(self) -> None:
        """Permanent classification skips the retry counter entirely."""
        behaviour = _make_mech_info_behaviour()
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"
        behaviour._context.params.mech_tools_parallel_timeout = 10.0

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        mock_api.get_spec.return_value = {"url": "http://test", "method": "GET"}
        mock_api.process_response.return_value = None
        mock_api.is_permanent_error.return_value = True
        mock_api.url = "http://test/permanent-cid"
        behaviour._context.mech_tools = mock_api

        mech = _make_mech_info(address="0xpermanent", relevant_tools=set())

        http_message = MagicMock()
        http_message.status_code = 500
        _install_fake_parallel_fetch(behaviour, [http_message])

        result = _drive(behaviour.populate_tools([mech]))

        assert result is True  # quarantined; nothing more to do
        assert "0xpermanent" in behaviour._failed_mechs
        mock_api.increment_retries.assert_not_called()
        mock_api.reset_retries.assert_called_once()
        mock_api.is_permanent_error.assert_called_once_with(http_message)
        error_calls = behaviour.context.logger.error.call_args_list
        assert any("permanent content error" in str(call) for call in error_calls)

    def test_transient_error_still_increments_retries_and_does_not_quarantine(
        self,
    ) -> None:
        """Transient path keeps the existing retry semantics."""
        behaviour = _make_mech_info_behaviour()
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"
        behaviour._context.params.mech_tools_parallel_timeout = 10.0

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        mock_api.get_spec.return_value = {"url": "http://test", "method": "GET"}
        mock_api.process_response.return_value = None
        mock_api.is_permanent_error.return_value = False
        mock_api.is_retries_exceeded.return_value = False
        mock_api.url = "http://test/transient"
        behaviour._context.mech_tools = mock_api

        mech = _make_mech_info(address="0xtransient", relevant_tools=set())
        _install_fake_parallel_fetch(behaviour, [MagicMock()])

        result = _drive(behaviour.populate_tools([mech]))

        assert result is False
        assert behaviour._failed_mechs == set()
        mock_api.increment_retries.assert_called_once()
        mock_api.reset_retries.assert_not_called()

    def test_transient_then_permanent_resets_retries_on_quarantine(self) -> None:
        """Retry counter bumped on transient call, cleared on permanent quarantine.

        Pins the cross-batch bookkeeping: a prior transient increments the
        counter, and the later permanent quarantine triggers the end-of-pass
        reset so a requeued / next-period fetch starts clean.
        """
        behaviour = _make_mech_info_behaviour()
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"
        behaviour._context.params.irrelevant_tools = set()
        behaviour._context.params.mech_tools_parallel_timeout = 10.0

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        mock_api.get_spec.return_value = {"url": "http://test", "method": "GET"}
        mock_api.process_response.return_value = None
        mock_api.is_retries_exceeded.return_value = False
        mock_api.url = "http://test/flaky-then-permanent"
        behaviour._context.mech_tools = mock_api

        mech = _make_mech_info(address="0xflaky", relevant_tools=set())

        # Call 1: transient — increment_retries, no quarantine, no reset.
        mock_api.is_permanent_error.return_value = False
        _install_fake_parallel_fetch(behaviour, [MagicMock()])
        _drive(behaviour.populate_tools([mech]))
        assert "0xflaky" not in behaviour._failed_mechs
        mock_api.increment_retries.assert_called_once()
        mock_api.reset_retries.assert_not_called()

        # Call 2: gateway flips to a permanent body on the same mech.
        mock_api.is_permanent_error.return_value = True
        _install_fake_parallel_fetch(behaviour, [MagicMock()])
        _drive(behaviour.populate_tools([mech]))
        assert "0xflaky" in behaviour._failed_mechs
        # increment_retries still only fired once (the first, transient call).
        mock_api.increment_retries.assert_called_once()
        # reset_retries must be called by end-of-pass logic in the permanent
        # branch so the next fetch period starts with a clean counter.
        mock_api.reset_retries.assert_called_once()

    def test_get_mechs_info_permanent_error_needs_only_one_http_call(self) -> None:
        """Permanent broken mech quarantined on first attempt.

        Before Fix 2 the broken mech would consume 6 attempts before
        quarantine. Fix 2 made it 1 attempt. Fix 4 (this change) runs
        the good + broken fetches in parallel, so total wall time =
        max(per-mech) instead of sum. Asserts one parallel pass, two
        HTTP sends.
        """
        behaviour = _make_mech_info_behaviour()
        behaviour._fetch_status = FetchStatus.SUCCESS
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"
        behaviour._context.params.irrelevant_tools = set()
        behaviour._context.params.mech_tools_parallel_timeout = 10.0

        good = _make_mech_info(
            address="0xgood", metadata_str="good", relevant_tools=set()
        )
        broken = _make_mech_info(
            address="0xbroken", metadata_str="broken", relevant_tools=set()
        )

        def mock_fetch_mechs_info():
            behaviour._fetch_status = FetchStatus.SUCCESS
            yield
            return [good, broken]

        behaviour.fetch_mechs_info = mock_fetch_mechs_info

        mock_api = MagicMock()
        mock_api.__dict__["_frozen"] = True
        mock_api.get_spec.return_value = {"url": "http://test", "method": "GET"}
        # pending order is [good, broken]; mock returns in that order.
        # URL-keyed dispatch can't work here: the parallel design snapshots
        # all specs upfront, so by the time process_response runs the api's
        # mutable .url is fixed at the last (broken) mech's URL.
        mock_api.process_response.side_effect = [["tool_good"], None]
        mock_api.is_permanent_error.return_value = True
        mock_api.url = "http://test/broken"
        behaviour._context.mech_tools = mock_api

        parallel_call_count = {"n": 0}
        total_requests = {"n": 0}

        def _fake_fetch(specs, timeout, poll_interval=0.1):
            parallel_call_count["n"] += 1
            total_requests["n"] += len(specs)
            yield
            # good first, broken second (per pending order)
            return [MagicMock(), MagicMock()]

        behaviour._fetch_http_parallel = _fake_fetch

        result = _drive(behaviour.get_mechs_info())

        assert result is not None
        assert "0xgood" in result
        assert "0xbroken" in result
        assert good.relevant_tools == {"tool_good"}
        assert broken.relevant_tools == set()
        assert "0xbroken" in behaviour._failed_mechs
        # One parallel pass, two total HTTP sends (one per mech).
        # Without the classifier this would be 7 (1 good + 6 broken retries).
        assert parallel_call_count["n"] == 1
        assert total_requests["n"] == 2
        mock_api.increment_retries.assert_not_called()


def _drive(gen):
    """Run a generator to StopIteration, returning its .value."""
    try:
        while True:
            next(gen)
    except StopIteration as e:
        return e.value


def _step(gen):
    """Advance a generator one yield; return True if still running."""
    try:
        next(gen)
        return True
    except StopIteration:
        return False


class TestFetchHttpParallel:
    """Unit tests for MechInformationBehaviour._fetch_http_parallel."""

    def _install_build_helpers(self, behaviour, nonces):
        """Wire _build_http_request_message + _get_request_nonce_from_dialogue.

        Returns a list of the dialogues so tests can introspect.
        """
        dialogues = []
        build_calls = []

        def build(**kwargs):
            idx = len(build_calls)
            build_calls.append(kwargs)
            msg = MagicMock(name=f"msg-{idx}")
            dlg = MagicMock(name=f"dlg-{idx}")
            dlg._parallel_nonce = nonces[idx]
            dialogues.append(dlg)
            return msg, dlg

        behaviour._build_http_request_message = build
        behaviour._get_request_nonce_from_dialogue = lambda d: d._parallel_nonce
        behaviour._build_calls = build_calls
        return dialogues

    def _install_counting_sleep(self, behaviour):
        """Replace self.sleep with a counting generator yielding once."""
        sleep_calls = []

        def sleep(seconds):
            sleep_calls.append(seconds)
            yield

        behaviour.sleep = sleep
        behaviour._sleep_calls = sleep_calls

    def test_fan_out_sends_all_messages_before_any_sleep(self) -> None:
        """A1: put_message is called N times before the first sleep fires."""
        behaviour = _wire_parallel_fetch(_make_mech_info_behaviour())
        self._install_build_helpers(behaviour, ["n1", "n2", "n3"])
        self._install_counting_sleep(behaviour)

        gen = behaviour._fetch_http_parallel(
            [
                {"method": "GET", "url": "u1"},
                {"method": "GET", "url": "u2"},
                {"method": "GET", "url": "u3"},
            ],
            timeout=10.0,
        )
        # Drive just to the first yield (first sleep call).
        _step(gen)

        assert behaviour._context.outbox.put_message.call_count == 3
        assert len(behaviour._sleep_calls) == 1
        # All three callbacks registered before the first sleep.
        registry = behaviour._context.requests.request_id_to_callback
        assert set(registry.keys()) == {"n1", "n2", "n3"}

    def test_responses_returned_in_input_order(self) -> None:
        """A2: output order matches input order, not arrival order."""
        behaviour = _wire_parallel_fetch(_make_mech_info_behaviour())
        self._install_build_helpers(behaviour, ["n1", "n2", "n3"])
        self._install_counting_sleep(behaviour)

        registry = behaviour._context.requests.request_id_to_callback
        gen = behaviour._fetch_http_parallel(
            [{"url": "u1"}, {"url": "u2"}, {"url": "u3"}], timeout=10.0
        )
        _step(gen)  # fan-out done; first sleep yielded

        msg1, msg2, msg3 = (
            MagicMock(name="m1"),
            MagicMock(name="m2"),
            MagicMock(name="m3"),
        )
        # Fire in reverse arrival order.
        registry["n2"](msg2, behaviour)
        registry["n3"](msg3, behaviour)
        registry["n1"](msg1, behaviour)

        result = _drive(gen)
        assert result == [msg1, msg2, msg3]

    def test_timed_out_entries_are_none(self) -> None:
        """A3: timeout yields None for unresponded requests."""
        behaviour = _wire_parallel_fetch(
            _make_mech_info_behaviour(),
            # _clock() is called at deadline-setup then on each loop iteration.
            clock_values=[0.0, 0.05, 100.0],
        )
        self._install_build_helpers(behaviour, ["n1", "n2", "n3"])
        self._install_counting_sleep(behaviour)

        registry = behaviour._context.requests.request_id_to_callback
        gen = behaviour._fetch_http_parallel(
            [{"url": "u1"}, {"url": "u2"}, {"url": "u3"}], timeout=1.0
        )
        _step(gen)

        msg2 = MagicMock(name="m2")
        registry["n2"](msg2, behaviour)

        result = _drive(gen)
        assert result == [None, msg2, None]

    def test_cleanup_pops_unresolved_nonces_from_registry(self) -> None:
        """A4: on exit, registry is cleared of every nonce we registered."""
        behaviour = _wire_parallel_fetch(
            _make_mech_info_behaviour(), clock_values=[0.0, 0.05, 100.0]
        )
        self._install_build_helpers(behaviour, ["n1", "n2", "n3"])
        self._install_counting_sleep(behaviour)

        registry = behaviour._context.requests.request_id_to_callback
        gen = behaviour._fetch_http_parallel(
            [{"url": "u1"}, {"url": "u2"}, {"url": "u3"}], timeout=1.0
        )
        _step(gen)
        # No responses; timeout expires.
        _drive(gen)

        assert "n1" not in registry
        assert "n2" not in registry
        assert "n3" not in registry

    def test_cleanup_runs_on_exception(self) -> None:
        """A5: unresolved nonces popped even when sleep raises."""
        behaviour = _wire_parallel_fetch(_make_mech_info_behaviour())
        self._install_build_helpers(behaviour, ["n1", "n2"])

        def boom_sleep(seconds):
            raise RuntimeError("boom")
            yield  # pragma: no cover - make it a generator function

        behaviour.sleep = boom_sleep
        registry = behaviour._context.requests.request_id_to_callback

        gen = behaviour._fetch_http_parallel(
            [{"url": "u1"}, {"url": "u2"}], timeout=10.0
        )
        try:
            _drive(gen)
        except RuntimeError:
            pass

        assert "n1" not in registry
        assert "n2" not in registry

    def test_custom_callback_writes_to_results_without_touching_behaviour(self) -> None:
        """A6: _make_parallel_fetch_callback is behaviour-state-agnostic."""
        from packages.valory.skills.mech_interact_abci.behaviours.mech_info import (
            _make_parallel_fetch_callback,
        )

        results: dict = {}
        cb = _make_parallel_fetch_callback(results, "nX")
        mock_message = MagicMock()
        mock_current_behaviour = MagicMock()

        cb(mock_message, mock_current_behaviour)

        assert results["nX"] is mock_message
        # No calls of any kind on current_behaviour — not try_send, not state.
        mock_current_behaviour.assert_not_called()
        assert not mock_current_behaviour.method_calls


class TestPopulateToolsParallelSemantics:
    """Tests for the parallel-batch semantics of populate_tools (Fix 4)."""

    def _setup(self, behaviour, api_side_effects=None):
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"
        behaviour._context.params.irrelevant_tools = set()
        behaviour._context.params.mech_tools_parallel_timeout = 10.0
        api = MagicMock()
        api.__dict__["_frozen"] = True
        api.get_spec.return_value = {"url": "http://test", "method": "GET"}
        api.url = "http://test/hash"
        if api_side_effects is not None:
            api.process_response.side_effect = api_side_effects
        behaviour._context.mech_tools = api
        return api

    def test_mixed_permanent_transient_good_in_one_batch(self) -> None:
        """B3: three mechs with different outcomes; all handled in one pass."""
        behaviour = _make_mech_info_behaviour()
        api = self._setup(behaviour, api_side_effects=[["tool_x"], None, None])
        api.is_permanent_error.side_effect = [
            True,
            False,
        ]  # order: permanent, transient
        api.is_retries_exceeded.return_value = False

        mech_good = _make_mech_info(
            address="0xgood", metadata_str="good", relevant_tools=set()
        )
        mech_permanent = _make_mech_info(
            address="0xpermanent", metadata_str="permanent", relevant_tools=set()
        )
        mech_transient = _make_mech_info(
            address="0xtransient", metadata_str="transient", relevant_tools=set()
        )

        perm_response = MagicMock()
        perm_response.status_code = 500
        _install_fake_parallel_fetch(
            behaviour, [MagicMock(), perm_response, MagicMock()]
        )

        result = _drive(
            behaviour.populate_tools([mech_good, mech_permanent, mech_transient])
        )

        assert result is False  # transient still has retries
        assert mech_good.relevant_tools == {"tool_x"}
        assert "0xpermanent" in behaviour._failed_mechs
        assert "0xtransient" not in behaviour._failed_mechs
        # Single increment for the batch, not per-mech.
        api.increment_retries.assert_called_once()

    def test_all_transient_batch_advances_shared_counter_once(self) -> None:
        """B4: shared retry counter advances once per pass, not per mech."""
        behaviour = _make_mech_info_behaviour()
        api = self._setup(behaviour, api_side_effects=[None, None, None])
        api.is_permanent_error.return_value = False
        api.is_retries_exceeded.return_value = False

        mechs = [
            _make_mech_info(address=f"0x{i}", metadata_str=str(i), relevant_tools=set())
            for i in range(3)
        ]
        _install_fake_parallel_fetch(behaviour, [MagicMock()] * 3)

        result = _drive(behaviour.populate_tools(mechs))

        assert result is False
        assert behaviour._failed_mechs == set()
        # Three transient failures in one pass -> ONE counter advance, not three.
        assert api.increment_retries.call_count == 1

    def test_retries_exhausted_during_batch_quarantines_all_pending(self) -> None:
        """B5: on retries exhaustion, quarantine every still-unpopulated mech."""
        behaviour = _make_mech_info_behaviour()
        api = self._setup(behaviour, api_side_effects=[None, None, None])
        api.is_permanent_error.return_value = False
        api.is_retries_exceeded.return_value = True

        mechs = [
            _make_mech_info(address=f"0x{i}", metadata_str=str(i), relevant_tools=set())
            for i in range(3)
        ]
        _install_fake_parallel_fetch(behaviour, [MagicMock()] * 3)

        result = _drive(behaviour.populate_tools(mechs))

        assert result is True  # every mech quarantined; no more work
        for i in range(3):
            assert f"0x{i}" in behaviour._failed_mechs
        api.reset_retries.assert_called_once()

    def test_timed_out_fetch_is_treated_as_transient(self) -> None:
        """B6: None response (timeout) increments retries; no quarantine."""
        behaviour = _make_mech_info_behaviour()
        api = self._setup(behaviour)
        api.is_permanent_error.return_value = False
        api.is_retries_exceeded.return_value = False

        mechs = [
            _make_mech_info(address=f"0x{i}", metadata_str=str(i), relevant_tools=set())
            for i in range(3)
        ]
        _install_fake_parallel_fetch(behaviour, [None, None, None])

        result = _drive(behaviour.populate_tools(mechs))

        assert result is False
        assert behaviour._failed_mechs == set()
        api.increment_retries.assert_called_once()

    def test_zero_pending_mechs_returns_true_without_fetching(self) -> None:
        """B2: all mechs pre-populated — no fetch, no put_message."""
        behaviour = _make_mech_info_behaviour()
        self._setup(behaviour)

        mechs = [
            _make_mech_info(address="0xm1", relevant_tools={"already"}),
            _make_mech_info(address="0xm2", relevant_tools={"here"}),
        ]

        fetch_calls = []

        def _fake_fetch(specs, timeout, poll_interval=0.1):
            fetch_calls.append(specs)
            yield
            return [MagicMock() for _ in specs]

        behaviour._fetch_http_parallel = _fake_fetch

        result = _drive(behaviour.populate_tools(mechs))

        assert result is True
        assert fetch_calls == []

    def test_specs_snapshotted_per_mech(self) -> None:
        """B1: each mech's spec is snapshotted before the next mech's URL mutation."""
        behaviour = _make_mech_info_behaviour()
        behaviour._context.params = MagicMock()
        behaviour._context.params.ipfs_address = "https://ipfs.io/"
        behaviour._context.params.irrelevant_tools = set()
        behaviour._context.params.mech_tools_parallel_timeout = 10.0

        # Real ApiSpecs-style url mutation via set_mech_agent_specs.
        api = MagicMock()
        api.__dict__["_frozen"] = True
        api.is_permanent_error.return_value = False
        api.is_retries_exceeded.return_value = False
        api.process_response.side_effect = [["tool_a"], ["tool_b"]]
        # get_spec returns the current URL at the time of the call.
        api.get_spec = lambda: {"url": api.url, "method": "GET"}
        behaviour._context.mech_tools = api

        mech_a = _make_mech_info(
            address="0xa", metadata_str="aaa", relevant_tools=set()
        )
        mech_b = _make_mech_info(
            address="0xb", metadata_str="bbb", relevant_tools=set()
        )

        captured_specs = []

        def _fake_fetch(specs, timeout, poll_interval=0.1):
            captured_specs.extend(specs)
            yield
            return [MagicMock(), MagicMock()]

        behaviour._fetch_http_parallel = _fake_fetch

        _drive(behaviour.populate_tools([mech_a, mech_b]))

        # Each spec must carry its own mech's CID, not the last-mutated URL.
        assert CID_PREFIX + "aaa" in captured_specs[0]["url"]
        assert CID_PREFIX + "bbb" in captured_specs[1]["url"]

    def test_primitive_wall_time_scales_with_max_not_sum_of_latencies(self) -> None:
        """_fetch_http_parallel elapsed time tracks max(latencies), not sum.

        Drives the real primitive with a simulated clock and callback
        dispatcher: 5 requests with staggered arrival latencies
        (50, 100, 150, 200, 500 ms). A sequential implementation would need
        sum = 1000 ms of simulated time to complete; the parallel primitive
        completes at max = 500 ms plus at most one poll_interval of slop.

        The `elapsed < sum` assertion is the falsifiability point — it fails
        for any serialized implementation.
        """
        # Reuse the TestFetchHttpParallel class's build helper by hand.
        behaviour = _make_mech_info_behaviour()
        behaviour._context.outbox = MagicMock()
        behaviour._context.requests = MagicMock()
        registry: dict = {}
        behaviour._context.requests.request_id_to_callback = registry

        nonces = [f"n{i}" for i in range(5)]
        build_calls: list = []

        def build(**kwargs):
            idx = len(build_calls)
            build_calls.append(kwargs)
            msg = MagicMock(name=f"msg-{idx}")
            dlg = MagicMock(name=f"dlg-{idx}")
            dlg._parallel_nonce = nonces[idx]
            return msg, dlg

        behaviour._build_http_request_message = build
        behaviour._get_request_nonce_from_dialogue = lambda d: d._parallel_nonce

        latencies_ms = [50, 100, 150, 200, 500]
        scheduled = {nonces[i]: latencies_ms[i] / 1000.0 for i in range(5)}
        poll_interval = 0.1

        current_time = [0.0]
        behaviour._clock = lambda: current_time[0]

        def fake_sleep(seconds):
            current_time[0] += seconds
            # Fire callbacks whose scheduled arrival has been reached.
            for nonce in list(scheduled):
                if scheduled[nonce] <= current_time[0] and nonce in registry:
                    registry[nonce](MagicMock(name=nonce), behaviour)
                    del scheduled[nonce]
            yield

        behaviour.sleep = fake_sleep

        gen = behaviour._fetch_http_parallel(
            [{"url": f"u{i}"} for i in range(5)],
            timeout=10.0,
            poll_interval=poll_interval,
        )
        result = _drive(gen)

        assert all(r is not None for r in result)

        max_lat = max(latencies_ms) / 1000.0  # 0.5s
        sum_lat = sum(latencies_ms) / 1000.0  # 1.0s

        # Elapsed tracks max, bounded by max + one poll_interval of slop.
        assert current_time[0] <= max_lat + poll_interval * 1.5
        # Falsifiability: sequential code needs sum_lat; parallel needs max_lat.
        assert current_time[0] < sum_lat
