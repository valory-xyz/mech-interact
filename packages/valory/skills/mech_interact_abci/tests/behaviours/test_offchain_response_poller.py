# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2026 Valory AG
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

"""Behavioural tests for ``OffchainResponsePoller`` (review C6, C8).

Covers the fast-fail policy (404, persistent 5xx) and the wall-clock
budget exhaustion path. The poll loop drives ``get_http_response`` and
``sleep`` through the executor's behaviour stub; tests inject a sequence
of canned responses and assert the terminal snapshot the loop emits.
"""

import json
from types import SimpleNamespace
from typing import Any, List

import pytest

from packages.valory.skills.mech_interact_abci.behaviours.offchain_response import (
    OffchainResponsePoller,
)


def _http_response(status_code: int, body: bytes = b"") -> SimpleNamespace:
    """Mimic the framework's HTTP response shape."""
    return SimpleNamespace(status_code=status_code, body=body)


class _StubBehaviour:
    """Minimal stub for the parent behaviour exposed to the poller."""

    def __init__(
        self,
        *,
        http_responses: List[Any],
        poll_interval: float = 0.0,
        poll_budget: float = 100.0,
        pending: Any = None,
        mech_responses: Any = None,
    ) -> None:
        self.context = SimpleNamespace(
            logger=SimpleNamespace(
                info=lambda *a, **k: None,
                warning=lambda *a, **k: None,
                error=lambda *a, **k: None,
                debug=lambda *a, **k: None,
            )
        )
        self.params = SimpleNamespace(
            mech_marketplace_config=SimpleNamespace(
                offchain_poll_interval_seconds=poll_interval,
                offchain_poll_timeout_seconds=poll_budget,
            )
        )
        self.synchronized_data = SimpleNamespace(
            offchain_pending_request=pending,
            mech_responses=mech_responses if mech_responses is not None else [],
        )
        self._http_responses = list(http_responses)
        self.sleep_calls = 0

    def get_http_response(self, **_kwargs: Any) -> Any:
        if False:
            yield
        return self._http_responses.pop(0)

    def sleep(self, _seconds: float) -> Any:
        if False:
            yield
        self.sleep_calls += 1


def _drive(gen: Any) -> Any:
    """Run the generator to completion (the stub never yields control)."""
    try:
        while True:
            next(gen)
    except StopIteration as exc:
        return exc.value


class TestPollUntilTerminal:
    """End-to-end behaviour of :meth:`_poll_until_terminal`."""

    def test_404_fast_fails(self) -> None:
        """404 → terminal "not_found", no further polling.

        Previously a 404 (mech restarted and lost the request) was treated
        as "still processing" and the loop ran the full poll budget.
        """
        stub = _StubBehaviour(http_responses=[_http_response(404)])
        poller = OffchainResponsePoller(stub)  # type: ignore[arg-type]
        snapshot = _drive(poller._poll_until_terminal("https://m", "42"))
        assert snapshot.status == "rejected"
        assert snapshot.error == "not_found"
        # No sleep — fast-fail did not wait for the budget.
        assert stub.sleep_calls == 0

    def test_five_consecutive_5xx_fast_fails(self) -> None:
        """5 in a row → terminal "server_unavailable" (review C6).

        Previously every 5xx was treated as transient indefinitely, so a
        permanently-broken mech ran the full 300s budget before surfacing
        any signal.
        """
        stub = _StubBehaviour(
            http_responses=[_http_response(503) for _ in range(5)],
        )
        poller = OffchainResponsePoller(stub)  # type: ignore[arg-type]
        snapshot = _drive(poller._poll_until_terminal("https://m", "42"))
        assert snapshot.status == "rejected"
        assert snapshot.error == "server_unavailable"

    def test_5xx_counter_resets_on_success_status(self) -> None:
        """A 200-"processing" response between 5xx replies resets the counter.

        Otherwise a flaky mech that recovers briefly would still trip the
        fast-fail after enough cumulative 5xx.
        """
        stub = _StubBehaviour(
            http_responses=[
                _http_response(503),
                _http_response(503),
                _http_response(200, b'{"status":"processing"}'),
                _http_response(503),
                _http_response(503),
                _http_response(200, b'{"status":"ok","response":"answer"}'),
            ],
        )
        poller = OffchainResponsePoller(stub)  # type: ignore[arg-type]
        snapshot = _drive(poller._poll_until_terminal("https://m", "42"))
        assert snapshot.status == "ok"
        assert snapshot.result == "answer"

    def test_ok_status_extracts_inner_result_field(self) -> None:
        """A 200 with ``status="ok"`` surfaces only the envelope's ``result``.

        The real mech wraps the tool payload in a ``response`` envelope
        (``{schema_version, requestId, result, tool, executed_at}``). To match
        the on-chain ``mech_response`` ApiSpec (``response_key: result``), we
        extract just the inner ``result`` field so ``DecisionReceive`` on
        trader sees the same string shape as the on-chain branch.
        """
        body = json.dumps(
            {
                "status": "ok",
                "response": {
                    "schema_version": "2.0",
                    "requestId": "42",
                    "result": '{"p_yes": 0.6, "p_no": 0.4}',
                    "tool": "prediction-online",
                    "executed_at": "2026-07-02T14:24:27Z",
                },
            }
        ).encode()
        stub = _StubBehaviour(http_responses=[_http_response(200, body)])
        poller = OffchainResponsePoller(stub)  # type: ignore[arg-type]
        snapshot = _drive(poller._poll_until_terminal("https://m", "42"))
        assert snapshot.status == "ok"
        assert snapshot.result == '{"p_yes": 0.6, "p_no": 0.4}'

    def test_ok_status_passes_invalid_response_string_through_unwrapped(
        self,
    ) -> None:
        """Tool-failure envelopes carry a non-JSON ``result`` (``"Invalid response"``).

        The downstream ``_get_decision`` in trader parses ``mech_response.result``
        with ``json.loads`` and catches ``JSONDecodeError`` to safely skip. If we
        emitted the full envelope here, that catch would miss and the trader
        would blow up in ``PredictionResponse(**dict)`` with ``KeyError('p_yes')``.
        """
        body = json.dumps(
            {
                "status": "ok",
                "response": {
                    "schema_version": "2.0",
                    "requestId": "42",
                    "result": "Invalid response",
                    "tool": "prediction-online",
                    "executed_at": "2026-07-02T14:24:27Z",
                },
            }
        ).encode()
        stub = _StubBehaviour(http_responses=[_http_response(200, body)])
        poller = OffchainResponsePoller(stub)  # type: ignore[arg-type]
        snapshot = _drive(poller._poll_until_terminal("https://m", "42"))
        assert snapshot.status == "ok"
        assert snapshot.result == "Invalid response"

    def test_rejected_status_surfaces_reason(self) -> None:
        """A 200 with ``status="rejected"`` carries the reason as ``error``."""
        body = json.dumps({"status": "rejected", "reason": "bad prompt"}).encode()
        stub = _StubBehaviour(http_responses=[_http_response(200, body)])
        poller = OffchainResponsePoller(stub)  # type: ignore[arg-type]
        snapshot = _drive(poller._poll_until_terminal("https://m", "42"))
        assert snapshot.status == "rejected"
        assert snapshot.error == "bad prompt"

    def test_budget_exhausted_surfaces_poll_timeout(self) -> None:
        """When the wall-clock budget runs out, the loop emits ``poll_timeout``.

        Driven with a budget of 0 so the first deadline check fires
        immediately after the first observation.
        """
        stub = _StubBehaviour(
            http_responses=[_http_response(200, b'{"status":"processing"}')],
            poll_budget=0.0,
        )
        poller = OffchainResponsePoller(stub)  # type: ignore[arg-type]
        snapshot = _drive(poller._poll_until_terminal("https://m", "42"))
        assert snapshot.status == "rejected"
        assert snapshot.error == "poll_timeout"


class TestLoadPending:
    """The poller only accepts dict pending shapes (review C13)."""

    def test_none_pending_yields_none(self) -> None:
        """Nothing in flight → ``_load_pending`` returns ``None``."""
        stub = _StubBehaviour(http_responses=[], pending=None)
        poller = OffchainResponsePoller(stub)  # type: ignore[arg-type]
        assert poller._load_pending() is None

    def test_non_dict_pending_yields_none(self) -> None:
        """Unexpected shapes (str, list) are rejected without raising."""
        stub = _StubBehaviour(http_responses=[], pending="not-a-dict")
        poller = OffchainResponsePoller(stub)  # type: ignore[arg-type]
        assert poller._load_pending() is None

    @pytest.mark.parametrize("missing_key", ["request_id", "nonce", "mech_address"])
    def test_partial_dict_yields_none(self, missing_key: str) -> None:
        """A partial blob yields ``None`` (callers treat as "no pending")."""
        raw = {
            "request_id": "ab" * 32,
            "nonce": 7,
            "mech_address": "0x" + "aa" * 20,
            "mech_url": "https://m",
            "sender": "0x" + "bb" * 20,
            "delivery_rate": 1,
            "ipfs_hash": "0x" + "cc" * 31,
            "ipfs_data": "{}",
        }
        raw.pop(missing_key)
        stub = _StubBehaviour(http_responses=[], pending=raw)
        poller = OffchainResponsePoller(stub)  # type: ignore[arg-type]
        assert poller._load_pending() is None
