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
from typing import Any, Dict, Generator, List, Optional, Set

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


class MechInformationBehaviour(QueryingBehaviour, MechInteractBaseBehaviour):
    """A behaviour in which the agents fetch the information of all the mechs of the marketplace from the subgraph."""

    matching_round = MechInformationRound

    def __init__(self, **kwargs: Any) -> None:  # pragma: no cover
        """Initialize Behaviour."""
        super().__init__(**kwargs)
        self._fetch_status: FetchStatus = FetchStatus.NONE
        self._failed_mechs: Set[str] = set()

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
        """Log the quarantine and mark the mech failed for this round."""
        self.context.logger.error(f"Quarantining mech {mech_address}: {reason}")
        self._failed_mechs.add(mech_address)

    def populate_tools(
        self, mech_info: MechsSubgraphResponseType
    ) -> WaitableConditionType:
        """Populate the tools of the mech info, fetching once per distinct CID.

        Mechs that share the same metadata CID resolve to the same IPFS
        manifest, so they are grouped and fetched once per CID. The resulting
        tool set is applied to every mech in the group.
        """
        pending_by_cid: Dict[str, List[Any]] = {}
        for mech in mech_info or []:
            if mech.relevant_tools or mech.address in self._failed_mechs:
                continue
            metadata_str = mech.service.metadata_str
            if metadata_str is None:
                continue
            pending_by_cid.setdefault(metadata_str, []).append(mech)

        for metadata_str, mechs in pending_by_cid.items():
            self.set_mech_agent_specs(metadata_str)
            specs = self.mech_tools_api.get_spec()
            res_raw = yield from self.get_http_response(**specs)
            res = self.mech_tools_api.process_response(res_raw)

            if res is None:
                self.context.logger.warning(
                    f"Could not get tools manifest at {self.mech_tools_api.url} "
                    f"(shared by {len(mechs)} mech(s))."
                )
                if self.mech_tools_api.is_permanent_error(res_raw):
                    reason = (
                        f"permanent content error at {self.mech_tools_api.url} "
                        f"(status={res_raw.status_code}); retries skipped."
                    )
                    for mech in mechs:
                        self._quarantine_mech(mech.address, reason)
                    self.mech_tools_api.reset_retries()
                    return False

                self.mech_tools_api.increment_retries()
                if self.mech_tools_api.is_retries_exceeded():
                    reason = (
                        f"could not fetch tools manifest at "
                        f"{self.mech_tools_api.url} after retries exhausted."
                    )
                    for mech in mechs:
                        self._quarantine_mech(mech.address, reason)
                    self.mech_tools_api.reset_retries()
                return False

            if len(res) == 0:
                self.context.logger.warning(
                    f"Tools manifest at {self.mech_tools_api.url} is empty "
                    f"(shared by {len(mechs)} mech(s)). Empty lists are "
                    f"deterministic per-CID; will retry on next round entry."
                )
                for mech in mechs:
                    self._failed_mechs.add(mech.address)
                self.mech_tools_api.reset_retries()
                continue

            metadata_tools = {str(t).lower() for t in res}
            allowed_tools = (
                metadata_tools - self.params.irrelevant_tools
            ) & self.params.valid_tools
            for mech in mechs:
                mech.relevant_tools |= allowed_tools
            self.mech_tools_api.reset_retries()

        return True

    def get_mechs_info(
        self,
    ) -> Generator[None, None, Optional[str]]:
        """Get the mechs' information serialized as a JSON string."""
        self.shared_state.last_failure_reason = None
        mech_info = yield from self.fetch_mechs_info()

        if self._fetch_status != FetchStatus.SUCCESS:
            self.shared_state.last_failure_reason = "subgraph_unavailable"
            return None

        if not mech_info:
            if not self.params.valid_mechs:
                self.shared_state.last_failure_reason = "allowlist_not_configured"
            else:
                self.shared_state.last_failure_reason = "valid_mech_list_empty"
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
            self.shared_state.last_failure_reason = "no_overlap_with_valid_tools"
            return None

        pinned = self.synchronized_data.selected_mechs
        if pinned:
            pinned_set = {addr.lower() for addr in pinned}
            visible = {mech.address.lower() for mech in mech_info}
            if not (pinned_set & visible):
                self.context.logger.warning(
                    f"Pinned mechs {sorted(pinned_set)} are not visible in this "
                    f"round's mech_info (visible: {sorted(visible)})."
                )
                self.shared_state.last_failure_reason = "pinned_mechs_offline"
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
