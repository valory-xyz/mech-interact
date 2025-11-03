# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2025 Valory AG
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
from dataclasses import asdict
from enum import Enum, auto
from typing import Any, Generator, Optional

from packages.valory.skills.mech_interact_abci.behaviours.base import (
    MechInteractBaseBehaviour,
    WaitableConditionType,
)
from packages.valory.skills.mech_interact_abci.graph_tooling.requests import (
    MAX_LOG_SIZE,
    QueryingBehaviour,
)
from packages.valory.skills.mech_interact_abci.models import (
    MechToolsSpecs,
    MechsSubgraphResponseType,
)
from packages.valory.skills.mech_interact_abci.payloads import JSONPayload
from packages.valory.skills.mech_interact_abci.states.mech_info import (
    MechInformationRound,
)


CID_PREFIX = "f01701220"


class FetchStatus(Enum):
    """The status of a fetch operation."""

    SUCCESS = auto()
    IN_PROGRESS = auto()
    FAIL = auto()
    NONE = auto()


class MechInformationBehaviour(QueryingBehaviour, MechInteractBaseBehaviour):
    """A behaviour in which the agents fetch the information of all the mechs of the marketplace from the subgraph."""

    matching_round = MechInformationRound

    def __init__(self, **kwargs: Any) -> None:
        """Initialize Behaviour."""
        super().__init__(**kwargs)
        self._fetch_status: FetchStatus = FetchStatus.NONE

    @property
    def mech_tools_api(self) -> MechToolsSpecs:
        """Get the mech agent api specs."""
        return self.context.mech_tools

    def set_mech_agent_specs(self, metadata: str) -> None:
        """Set the mech's agent specs."""
        ipfs_link = self.params.ipfs_address + CID_PREFIX + metadata
        # The url needs to be dynamically generated as it depends on the ipfs hash
        self.mech_tools_api.__dict__["_frozen"] = False
        self.mech_tools_api.url = ipfs_link
        self.mech_tools_api.__dict__["_frozen"] = True

    def populate_tools(
        self, mech_info: MechsSubgraphResponseType
    ) -> WaitableConditionType:
        """Populate the tools of the mech info, using the metadata."""
        for mech in mech_info:
            if mech.relevant_tools:
                continue

            self.set_mech_agent_specs(mech.service.metadata_str)
            specs = self.mech_tools_api.get_spec()
            res_raw = yield from self.get_http_response(**specs)
            res = self.mech_tools_api.process_response(res_raw)

            if res is None:
                msg = f"Could not get the {mech.address} mech agent's tools from {self.mech_tools_api.url}."
                self.context.logger.warning(msg)
                self.mech_tools_api.increment_retries()
                return False

            if len(res) == 0:
                self.context.logger.warning(
                    f"The {mech.address} mech agent's tools are empty!"
                )

            # store only the relevant mech tools
            relevant_tools = set(res) - self.params.irrelevant_tools
            mech.relevant_tools |= relevant_tools
            self.mech_tools_api.reset_retries()

        return True

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
            # fails even if a single mech's set of tools cannot be fetched
            tools_populated = yield from self.populate_tools(mech_info)
            if tools_populated:
                break

            if not self.mech_tools_api.is_retries_exceeded():
                continue

            msg = "Retries were exceeded while trying to get the mech tools."
            self.context.logger.warning(msg)
            self.mech_tools_api.reset_retries()
            return None

        # truncate the information, otherwise logs get too big
        serialized_info = json.dumps([asdict(info) for info in mech_info])
        info_str = serialized_info[:MAX_LOG_SIZE]
        self.context.logger.info(f"Updated mechs' information: {info_str}")
        return serialized_info

    def async_act(self) -> Generator:
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

    def clean_up(self) -> None:
        """Clean up the behaviour."""
        self.mech_tools_api.reset_retries()
