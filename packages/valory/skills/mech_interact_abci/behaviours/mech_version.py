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


"""This module contains the behaviour responsible for detecting the version of the mech marketplace."""

import logging
from contextlib import contextmanager
from typing import Generator, Optional

from packages.valory.skills.mech_interact_abci.behaviours.base import (
    MechInteractBaseBehaviour,
)
from packages.valory.skills.mech_interact_abci.payloads import VotingPayload
from packages.valory.skills.mech_interact_abci.states.mech_version import (
    MechVersionDetectionRound,
)


V1 = "v1"
V2 = "v2"


@contextmanager
def suppress_logs(level: int = logging.CRITICAL) -> Generator:
    """Context manager to suppress the logs for a specific code block."""
    previous_level = logging.root.manager.disable
    logging.disable(level)
    try:
        yield
    finally:
        logging.disable(previous_level)


def get_version_name(version: bool) -> str:
    """Get the string version from its bool."""
    return V2 if version else V1


class MechVersionDetectionBehaviour(MechInteractBaseBehaviour):
    """A behaviour in which the agents detect the version of the marketplace."""

    matching_round = MechVersionDetectionRound

    def detect_marketplace_compatibility(
        self,
    ) -> Generator[None, None, Optional[bool]]:
        """Detect if the marketplace supports v2 features."""
        if not self.params.use_mech_marketplace:
            return None

        if self.synchronized_data.versioning_check_performed:
            return self.synchronized_data.is_marketplace_v2

        self.context.logger.info(
            f"Detecting marketplace compatibility for {self.marketplace_address=}"
        )

        with suppress_logs():
            # the `get_max_fee_factor` is only available in the new marketplace
            is_new_mm = yield from self._mech_marketplace_contract_interact(
                contract_callable="get_max_fee_factor",
                data_key="max_fee_factor",
                placeholder="_",
                chain_id=self.params.mech_chain_id,
            )

        version = get_version_name(is_new_mm)
        self.context.logger.info(
            f"Marketplace {self.marketplace_address!r} supports {version} features."
        )
        return is_new_mm

    def async_act(self) -> Generator:
        """Do the action."""
        with self.context.benchmark_tool.measure(self.behaviour_id).local():
            is_v2 = yield from self.detect_marketplace_compatibility()
            if is_v2 is None:
                self.context.logger.warning(
                    "Failed to detect the marketplace's version."
                )

            payload = VotingPayload(
                self.context.agent_address,
                is_v2,
            )

        yield from self.finish_behaviour(payload)
