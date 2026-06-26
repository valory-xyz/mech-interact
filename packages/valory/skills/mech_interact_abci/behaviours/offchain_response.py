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
from typing import Any, Dict, Generator, List, Optional

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
    content_cid: Optional[str] = None


_PROCESSING_STATUSES = frozenset({"processing", ""})


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
        ``offchain_poll_timeout_seconds``. The boundary is checked with the
        wall clock rather than a counter so any drift inside ``sleep`` is
        bounded by the total budget.
        """
        interval = float(self._config.offchain_poll_interval_seconds)
        budget = float(self._config.offchain_poll_timeout_seconds)
        deadline = time.monotonic() + budget
        url = mech_url.rstrip("/") + "/fetch_offchain_info"
        body = self._build_body(request_id_int_str)

        last_seen: _PollSnapshot = _PollSnapshot(status="processing")
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

            snapshot = self._parse(response)
            if snapshot.status not in _PROCESSING_STATUSES:
                return snapshot
            last_seen = snapshot

            if time.monotonic() >= deadline:
                self._logger.warning(
                    f"Offchain poll budget {budget:.0f}s exhausted; "
                    f"surfacing the last status={last_seen.status!r}."
                )
                return _PollSnapshot(
                    status="rejected",
                    error="poll_timeout",
                )

            yield from self._b.sleep(interval)

    @staticmethod
    def _build_body(request_id_int_str: str) -> bytes:
        """Form-urlencode the GET body the mech server's ``parse_qs`` expects."""
        from urllib.parse import urlencode

        return urlencode({"request_id": request_id_int_str}).encode("utf-8")

    def _parse(self, response: Any) -> _PollSnapshot:
        """Translate the HTTP response into a :class:`_PollSnapshot`."""
        if response is None:
            return _PollSnapshot(status="processing")
        status_code = getattr(response, "status_code", None)
        body_bytes = getattr(response, "body", b"") or b""
        if status_code != 200:
            self._logger.warning(
                f"Offchain poll returned status={status_code}; "
                f"treating as still-processing for retry."
            )
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
            return _PollSnapshot(
                status="ok",
                result=self._serialise_result(payload.get("response")),
                content_cid=payload.get("content_cid"),
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
        raw = self._synced.offchain_pending_request
        if not raw:
            return None
        if isinstance(raw, dict):
            return PendingRequest.from_dict(raw)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                return None
            if isinstance(parsed, dict):
                return PendingRequest.from_dict(parsed)
        return None


def serialise_responses(responses: List[MechInteractionResponse]) -> str:
    """Serialise a list of ``MechInteractionResponse`` to the wire shape."""
    return json.dumps([dataclasses.asdict(r) for r in responses], ensure_ascii=True)


__all__ = [
    "OffchainResponsePoller",
    "serialise_responses",
]


# Silence unused-import warnings for symbols only used in type annotations.
_ = Dict
