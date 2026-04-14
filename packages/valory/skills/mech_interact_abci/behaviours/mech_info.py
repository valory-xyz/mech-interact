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


"""This module contains the behaviour responsible for gathering information about the mech marketplace."""

import json
import time
from typing import Any, Callable, Dict, Generator, List, Optional, Set

from aea.protocols.base import Message

from packages.valory.protocols.http import HttpMessage
from packages.valory.skills.mech_interact_abci.behaviours.base import (
    MechInteractBaseBehaviour,
    WaitableConditionType,
)
from packages.valory.skills.mech_interact_abci.graph_tooling.requests import (
    FetchStatus,
    MAX_LOG_SIZE,
    QueryingBehaviour,
)
from packages.valory.skills.mech_interact_abci.models import (
    MechToolsSpecs,
    MechsSubgraphResponseType,
)
from packages.valory.skills.mech_interact_abci.payloads import JSONPayload
from packages.valory.skills.mech_interact_abci.states.base import MechInfoEncoder
from packages.valory.skills.mech_interact_abci.states.mech_info import (
    MechInformationRound,
)

CID_PREFIX = "f01701220"
PARALLEL_FETCH_POLL_INTERVAL = 0.1


def _make_parallel_fetch_callback(
    results: Dict[str, Optional[HttpMessage]], nonce: str
) -> Callable[[Message, Any], None]:
    """Build a nonce-scoped callback that stores the response in results.

    Stays behaviour-state-agnostic so it dispatches regardless of whether the
    owning behaviour is in WAITING_MESSAGE state (we aren't — we poll via
    self.sleep while the fetch is in flight).
    """

    def _callback(message: Message, current_behaviour: Any) -> None:
        # The mech_interact parallel fetcher only ever receives HttpMessage
        # responses for these nonces; the cast is safe by construction.
        results[nonce] = message  # type: ignore[assignment]

    return _callback


class MechInformationBehaviour(QueryingBehaviour, MechInteractBaseBehaviour):
    """A behaviour in which the agents fetch the information of all the mechs of the marketplace from the subgraph."""

    matching_round = MechInformationRound

    def __init__(self, **kwargs: Any) -> None:  # pragma: no cover
        """Initialize Behaviour."""
        super().__init__(**kwargs)
        self._fetch_status: FetchStatus = FetchStatus.NONE
        self._failed_mechs: Set[str] = set()
        # Injectable clock for deterministic parallel-fetch timeout tests.
        self._clock: Callable[[], float] = time.monotonic

    @property
    def mech_tools_api(self) -> MechToolsSpecs:  # pragma: no cover
        """Get the mech agent api specs."""
        return self.context.mech_tools

    def set_mech_agent_specs(self, metadata: str) -> None:
        """Set the mech's agent specs."""
        ipfs_link = self.params.ipfs_address + CID_PREFIX + metadata
        # The url needs to be dynamically generated as it depends on the ipfs hash
        self.mech_tools_api.__dict__["_frozen"] = False
        self.mech_tools_api.url = ipfs_link
        self.mech_tools_api.__dict__["_frozen"] = True

    def _quarantine_mech(self, mech_address: str, reason: str) -> None:
        """Log the quarantine and mark the mech failed for this round.

        Retry-counter resets are owned by populate_tools' end-of-pass logic
        in the parallel design, not by this helper — calling reset_retries
        here would clobber any in-flight transient counter for the rest of
        the batch.
        """
        self.context.logger.error(f"Quarantining mech {mech_address}: {reason}")
        self._failed_mechs.add(mech_address)

    def _fetch_http_parallel(
        self,
        specs: List[Dict[str, Any]],
        timeout: float,
        poll_interval: float = PARALLEL_FETCH_POLL_INTERVAL,
    ) -> Generator[None, None, List[Optional[HttpMessage]]]:
        """Fire N HTTP requests concurrently; return responses in input order.

        :param specs: list of kwargs dicts for _build_http_request_message.
        :param timeout: wall-clock budget (via self._clock) for the whole batch.
        :param poll_interval: sleep between readiness checks.
        :return: list with one entry per input spec; None for timed-out
            requests. Unresolved callbacks are popped from the request
            registry on exit.
        :yield: None while awaiting responses.
        """
        results: Dict[str, Optional[HttpMessage]] = {}
        nonces: List[str] = []
        registry = self.context.requests.request_id_to_callback

        try:
            # Registration is inside the try so any framework-method raise
            # mid-fan-out still triggers the finally-block cleanup of the
            # nonces already inserted (otherwise their closures would leak
            # across rounds and capture stale `results` dicts).
            for spec in specs:
                message, dialogue = self._build_http_request_message(**spec)
                nonce = self._get_request_nonce_from_dialogue(dialogue)
                nonces.append(nonce)
                registry[nonce] = _make_parallel_fetch_callback(results, nonce)
                self.context.outbox.put_message(message=message)

            deadline = self._clock() + timeout
            while len(results) < len(nonces):
                if self._clock() >= deadline:
                    break
                yield from self.sleep(poll_interval)
        finally:
            for nonce in nonces:
                registry.pop(nonce, None)

        return [results.get(n) for n in nonces]

    def populate_tools(
        self, mech_info: MechsSubgraphResponseType
    ) -> WaitableConditionType:
        """Populate the tools of the mech info, using the metadata, in parallel."""
        pending = [
            mech
            for mech in mech_info
            if not mech.relevant_tools and mech.address not in self._failed_mechs
        ]
        if not pending:
            return True

        specs_per_mech: List[Dict[str, Any]] = []
        for mech in pending:
            # set_mech_agent_specs mutates mech_tools_api.url. Snapshot
            # get_spec() BEFORE advancing to the next mech so each sent
            # request carries its own mech's CID.
            self.set_mech_agent_specs(mech.service.metadata_str)
            specs_per_mech.append(dict(self.mech_tools_api.get_spec()))

        responses = yield from self._fetch_http_parallel(
            specs_per_mech,
            timeout=self.params.mech_tools_parallel_timeout,
        )

        any_transient = False
        # Zip in specs_per_mech so URL logging uses the per-mech snapshot
        # rather than self.mech_tools_api.url, which has been mutated to the
        # last mech's CID by the snapshot loop above.
        for mech, spec, res_raw in zip(pending, specs_per_mech, responses):
            mech_url = spec.get("url", "<unknown>")
            if res_raw is None:
                self.context.logger.warning(
                    f"Timed out fetching {mech.address} mech agent's tools "
                    f"from {mech_url}."
                )
                any_transient = True
                continue

            res = self.mech_tools_api.process_response(res_raw)

            if res is None:
                self.context.logger.warning(
                    f"Could not get the {mech.address} mech agent's tools "
                    f"from {mech_url}."
                )
                if self.mech_tools_api.is_permanent_error(res_raw):
                    self._quarantine_mech(
                        mech.address,
                        f"permanent content error at {mech_url} "
                        f"(status={res_raw.status_code}); retries skipped.",
                    )
                else:
                    any_transient = True
                continue

            if len(res) == 0:
                self.context.logger.warning(
                    f"Quarantining mech {mech.address}: tools manifest at "
                    f"{mech_url} is empty. Empty lists are "
                    f"deterministic per-CID; will retry on next round entry."
                )
                self._failed_mechs.add(mech.address)
                continue

            relevant_tools = set(res) - self.params.irrelevant_tools
            mech.relevant_tools |= relevant_tools

        # Advance the shared retry counter once per pass that had transient
        # failures. On exhaustion, quarantine every still-unpopulated mech.
        if any_transient:
            self.mech_tools_api.increment_retries()
            if self.mech_tools_api.is_retries_exceeded():
                for mech in pending:
                    if (
                        not mech.relevant_tools
                        and mech.address not in self._failed_mechs
                    ):
                        self._quarantine_mech(
                            mech.address,
                            "retries exhausted after transient failures.",
                        )
                self.mech_tools_api.reset_retries()
        else:
            self.mech_tools_api.reset_retries()

        return all(
            mech.relevant_tools or mech.address in self._failed_mechs
            for mech in pending
        )

    def get_mechs_info(
        self,
    ) -> Generator[None, None, Optional[str]]:
        """Get the mechs' information serialized as a JSON string."""
        mech_info = yield from self.fetch_mechs_info()

        if self._fetch_status != FetchStatus.SUCCESS:
            return None

        if not mech_info:
            # if the info is None or empty, return None
            return None

        while True:
            tools_populated = yield from self.populate_tools(mech_info)
            if tools_populated:
                break

        if self._failed_mechs:
            self.context.logger.warning(
                f"Skipped {len(self._failed_mechs)} mech(s) with unreachable "
                f"tools manifests: {sorted(self._failed_mechs)}"
            )

        if not any(mech.relevant_tools for mech in mech_info):
            self.context.logger.warning(
                "No mechs have usable tools after fetch; emitting NONE to retry."
            )
            return None

        # truncate the information, otherwise logs get too big
        serialized_info = json.dumps(mech_info, cls=MechInfoEncoder)
        info_str = serialized_info[:MAX_LOG_SIZE]
        self.context.logger.info(f"Updated mechs' information: {info_str}")
        return serialized_info

    def async_act(self) -> Generator:  # pragma: no cover
        """Do the action."""
        with self.context.benchmark_tool.measure(self.behaviour_id).local():
            mechs_info = yield from self.get_mechs_info()
            if mechs_info is None:
                self.context.logger.warning(
                    "Failed to fetch mech information for the marketplace."
                )

            payload = JSONPayload(
                self.context.agent_address,
                mechs_info,
            )

        yield from self.finish_behaviour(payload)

    def clean_up(self) -> None:  # pragma: no cover
        """Clean up the behaviour."""
        self.mech_tools_api.reset_retries()
        self._failed_mechs.clear()
