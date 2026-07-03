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

"""Off-chain response polling for the mech-interact skill.

When ``MechMarketplaceConfig.use_offchain`` is true, ``MechResponseBehaviour``
hands off to :class:`OffchainResponsePoller` instead of scanning the on-chain
``Deliver`` event log.

Wire shape (mirrors the mech server at
``mech/packages/valory/skills/task_execution/handlers.py``):

* Endpoint: ``GET <mech_url>/fetch_offchain_info``. The ``request_id`` is
  carried in the form-urlencoded body (yes, a GET with a body); the mech
  server's parser ignores the URL path suffix and reads the field from the
  body.
* 200 with ``{}`` means "still processing"; the poller waits and retries.
* Terminal: 200 with ``status == "ok"`` (result available) or 200 with
  ``status == "rejected"`` (the mech aborted the task).

State carried across rounds:

* ``synchronized_data.offchain_pending_request`` — the ``PendingRequest``
  pinned by the request behaviour, points at the mech URL we POSTed to.
* ``synchronized_data.mech_responses`` — pre-populated by the request
  behaviour with a placeholder ``MechInteractionResponse`` keyed on the
  ``request_id``. The poller fills in ``result``/``error`` in place once
  the mech delivers.
"""

import dataclasses
import json
import time
from typing import Any, Generator, List, Optional
from urllib.parse import urlencode

from packages.valory.skills.mech_interact_abci.behaviours.offchain_request import (
    PendingRequest,
)
from packages.valory.skills.mech_interact_abci.states.base import (
    MechInteractionResponse,
)


@dataclasses.dataclass(frozen=True)
class _PollSnapshot:
    """One terminal observation from ``/fetch_offchain_info``."""

    status: str  # "ok" / "rejected" / "processing"
    result: Optional[str] = None
    error: Optional[str] = None


_PROCESSING_STATUSES = frozenset({"processing", ""})

# Number of consecutive 5xx responses tolerated before the poll loop
# fast-fails. Bounds an outage where the mech server stays unhealthy for
# the entire ``offchain_poll_timeout_seconds`` budget — previously each
# 5xx was treated as "still processing" and the loop ran the full 300s.
_MAX_CONSECUTIVE_5XX = 5


class OffchainResponsePoller:
    """Poll a single mech for the pending request's result.

    Stateless across periods — the pending request is loaded from
    synchronized data on each entry, so a retry after a deposit-settled
    re-POST resumes against the same target without leaking state from the
    earlier attempt.
    """

    def __init__(self, behaviour: "Any") -> None:
        """Wire the poller to the calling behaviour for context access."""
        self._b = behaviour

    @property
    def _config(self) -> Any:
        return self._b.params.mech_marketplace_config

    @property
    def _synced(self) -> Any:
        return self._b.synchronized_data

    @property
    def _logger(self) -> Any:
        return self._b.context.logger

    def run(self) -> Generator[None, None, List[MechInteractionResponse]]:
        """Poll until terminal or the total budget runs out.

        Returns the updated list of :class:`MechInteractionResponse`
        instances, with ``result`` or ``error`` filled in for the
        request the poller was assigned. The caller serialises and writes
        them onto ``mech_responses`` exactly as the on-chain branch does.
        """
        pending = self._load_pending()
        if pending is None:
            self._logger.warning(
                "Offchain response polling reached with no pending request "
                "on synced data; returning the existing response list as-is."
            )
            return list(self._synced.mech_responses)

        responses = list(self._synced.mech_responses)
        target = self._find_target_response(responses, pending.request_id)
        if target is None:
            self._logger.warning(
                f"No pre-populated MechInteractionResponse for request_id "
                f"{pending.request_id[:10]}…; appending a fresh placeholder."
            )
            target = MechInteractionResponse(
                data=pending.request_id,
                requestId=int(pending.request_id, 16),
                requestIds=[int(pending.request_id, 16)],
                numRequests=1,
                nonce=str(pending.nonce),
                result=None,
                error="Unknown",
            )
            responses.append(target)

        snapshot = yield from self._poll_until_terminal(
            mech_url=pending.mech_url,
            request_id_int_str=str(int(pending.request_id, 16)),
        )

        self._apply_snapshot(target, snapshot)
        return responses

    # ---------- polling loop -----------------------------------------------

    def _poll_until_terminal(
        self,
        mech_url: str,
        request_id_int_str: str,
    ) -> Generator[None, None, _PollSnapshot]:
        """Drive the GET loop until a terminal status arrives or time runs out.

        Polls at ``offchain_poll_interval_seconds`` and bails out after
        ``offchain_poll_timeout_seconds``. The boundary is checked with
        the wall clock rather than a counter so any drift inside ``sleep``
        is bounded by the total budget.

        Fast-fail policy (review C6):

        * **404** is treated as a permanent "request unknown" (e.g. after a
          mech restart). Polling won't recover; surface immediately.
        * **5xx** is treated as transient up to ``_MAX_CONSECUTIVE_5XX``
          consecutive failures, then fast-fails. Any non-5xx response
          (including a 200 reporting "processing") resets the counter.
        """
        interval = float(self._config.offchain_poll_interval_seconds)
        budget = float(self._config.offchain_poll_timeout_seconds)
        deadline = time.monotonic() + budget
        url = mech_url.rstrip("/") + "/fetch_offchain_info"
        body = self._build_body(request_id_int_str)

        consecutive_5xx = 0
        while True:
            try:
                response = yield from self._b.get_http_response(
                    method="GET",
                    url=url,
                    content=body,
                    headers={"Content-Type": "application/json"},
                )
            except Exception as exc:  # pragma: no cover - framework boundary
                self._logger.warning(
                    f"Offchain poll {url} raised {exc.__class__.__name__}: {exc}"
                )
                response = None

            status_code = (
                getattr(response, "status_code", None) if response is not None else None
            )

            if status_code == 404:
                self._logger.error(
                    f"Offchain poll {url} returned 404 for request_id "
                    f"{request_id_int_str}; the mech does not know about "
                    "this request (likely restarted). Fast-failing."
                )
                return _PollSnapshot(status="rejected", error="not_found")

            if status_code is not None and 500 <= status_code < 600:
                consecutive_5xx += 1
                self._logger.warning(
                    f"Offchain poll {url} returned {status_code} "
                    f"(consecutive 5xx={consecutive_5xx}/{_MAX_CONSECUTIVE_5XX})"
                )
                if consecutive_5xx >= _MAX_CONSECUTIVE_5XX:
                    self._logger.error(
                        f"Offchain poll {url} returned {_MAX_CONSECUTIVE_5XX} "
                        "consecutive 5xx responses; fast-failing."
                    )
                    return _PollSnapshot(status="rejected", error="server_unavailable")
            else:
                consecutive_5xx = 0

            snapshot = self._parse(response)
            if snapshot.status not in _PROCESSING_STATUSES:
                return snapshot

            if time.monotonic() >= deadline:
                self._logger.warning(
                    f"Offchain poll budget {budget:.0f}s exhausted; "
                    f"surfacing poll_timeout for {url}."
                )
                return _PollSnapshot(
                    status="rejected",
                    error="poll_timeout",
                )

            yield from self._b.sleep(interval)

    @staticmethod
    def _build_body(request_id_int_str: str) -> bytes:
        """Form-urlencode the GET body the mech server's ``parse_qs`` expects."""
        return urlencode({"request_id": request_id_int_str}).encode("utf-8")

    def _parse(self, response: Any) -> _PollSnapshot:
        """Translate the HTTP response into a :class:`_PollSnapshot`.

        Only 200 responses are interpreted; non-200 status codes are
        classified by the caller (404 / 5xx fast-fail). A non-200 still
        returns ``processing`` here so the caller can apply its own retry
        policy for the transient bucket (e.g. 502 mid-rollout).
        """
        if response is None:
            return _PollSnapshot(status="processing")
        status_code = getattr(response, "status_code", None)
        body_bytes = getattr(response, "body", b"") or b""
        if status_code != 200:
            # Non-200 logging is owned by the caller (it knows the
            # consecutive-failure counter and the fast-fail threshold).
            return _PollSnapshot(status="processing")
        try:
            payload = json.loads(body_bytes) if body_bytes else {}
        except (json.JSONDecodeError, TypeError, ValueError):
            self._logger.warning(
                "Offchain poll returned a non-JSON body; ignoring and retrying."
            )
            return _PollSnapshot(status="processing")
        if not isinstance(payload, dict) or not payload:
            return _PollSnapshot(status="processing")
        status = str(payload.get("status", "")).lower()
        if status == "ok":
            # Match the on-chain `mech_response` ApiSpec (`response_key: result`):
            # extract the inner `result` field so downstream (DecisionReceive
            # on trader) sees the same string shape as the on-chain branch.
            # Passing the full envelope here broke `_get_decision` — on tool
            # failure the envelope JSON-parses but has no `p_yes`, hitting
            # KeyError in `PredictionResponse.__init__`.
            envelope = payload.get("response")
            inner_result = (
                envelope.get("result") if isinstance(envelope, dict) else envelope
            )
            return _PollSnapshot(
                status="ok",
                result=self._serialise_result(inner_result),
            )
        if status == "rejected":
            return _PollSnapshot(
                status="rejected",
                error=str(payload.get("reason") or "rejected"),
            )
        return _PollSnapshot(status="processing")

    # ---------- response-list helpers ---------------------------------------

    def _find_target_response(
        self, responses: List[MechInteractionResponse], request_id_hex: str
    ) -> Optional[MechInteractionResponse]:
        """Locate the placeholder response for ``request_id_hex``."""
        try:
            request_id_int = int(request_id_hex, 16)
        except ValueError:
            return None
        for response in responses:
            if response.requestId == request_id_int:
                return response
            if request_id_int in (response.requestIds or []):
                return response
        return None

    @staticmethod
    def _serialise_result(value: Any) -> Optional[str]:
        """Serialise the tool result into the string shape downstream expects."""
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=True)

    @staticmethod
    def _apply_snapshot(
        target: MechInteractionResponse, snapshot: _PollSnapshot
    ) -> None:
        """Mutate ``target`` in place with the terminal poll outcome."""
        if snapshot.status == "ok":
            target.result = snapshot.result
            target.error = "Unknown" if snapshot.error is None else snapshot.error
        else:
            target.result = None
            target.error = snapshot.error if snapshot.error is not None else "rejected"

    def _load_pending(self) -> Optional[PendingRequest]:
        """Load the pending request from synced data.

        :py:meth:`SynchronizedData.offchain_pending_request` already
        normalises both the JSON-string and dict shapes the round writes,
        so this branch only has to handle the dict the accessor returns
        (or ``None`` when nothing is in flight).
        """
        raw = self._synced.offchain_pending_request
        if not isinstance(raw, dict):
            return None
        return PendingRequest.from_dict(raw)


def serialise_responses(responses: List[MechInteractionResponse]) -> str:
    """Serialise a list of ``MechInteractionResponse`` to the wire shape."""
    return json.dumps([dataclasses.asdict(r) for r in responses], ensure_ascii=True)


__all__ = [
    "OffchainResponsePoller",
    "serialise_responses",
]
