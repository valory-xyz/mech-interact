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
import re
from typing import Any, Dict, Generator, List, Optional, Set, Tuple

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
from packages.valory.skills.mech_interact_abci.payment_types import (
    PAYMENT_ASSETS,
    PaymentType,
)
from packages.valory.skills.mech_interact_abci.states.base import MechInfoEncoder
from packages.valory.skills.mech_interact_abci.states.mech_info import (
    MechInformationRound,
)
from packages.valory.skills.mech_interact_abci.utils import loads_bounded

CID_PREFIX = "f01701220"


# Tool names become keys in consumers' records, payloads and messages, so a
# manifest entry is used only as a short lowercase identifier, at most
# MAX_TOOLS_PER_MECH of them per manifest.
TOOL_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
MAX_TOOLS_PER_MECH = 50


def manifest_tools(raw: Any) -> Set[str]:
    """The usable tool names in a manifest's ``tools`` list.

    :param raw: the ``tools`` value read from the manifest.
    :return: the lowercase names matching ``TOOL_NAME_PATTERN``, first
        ``MAX_TOOLS_PER_MECH`` distinct ones; empty when ``raw`` is not a list.
    """
    if not isinstance(raw, list):
        return set()
    tools: List[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            continue
        name = entry.strip().lower()
        if TOOL_NAME_PATTERN.fullmatch(name) and name not in tools:
            tools.append(name)
            if len(tools) == MAX_TOOLS_PER_MECH:
                break
    return set(tools)


# An expected cost is signed into a contract call, so it is a positive
# decimal string that fits a uint256.
MAX_AMOUNT_DIGITS = 78
UINT256_MAX = 2**256 - 1


def _base_units(value: Any) -> Optional[int]:
    """Read a manifest amount: a positive decimal integer string within uint256.

    :param value: the raw ``amount`` value.
    :return: the amount, or None when it is not one.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text.isascii() or not text.isdecimal() or len(text) > MAX_AMOUNT_DIGITS:
        return None
    amount = int(text)
    return amount if 0 < amount <= UINT256_MAX else None


def manifest_expected_costs(payload: Any) -> Dict[str, Tuple[str, int]]:
    """The expected cost each tool publishes under ``toolMetadata``.

    :param payload: the parsed manifest.
    :return: ``(asset, amount)`` by lowercase tool name; a tool whose entry
        is missing or malformed is left out.
    """
    if not isinstance(payload, dict):
        return {}
    tool_metadata = payload.get("toolMetadata")
    if not isinstance(tool_metadata, dict):
        return {}
    costs: Dict[str, Tuple[str, int]] = {}
    for tool, entry in tool_metadata.items():
        if not isinstance(tool, str) or not isinstance(entry, dict):
            continue
        block = entry.get("expectedCost")
        if not isinstance(block, dict):
            continue
        asset = block.get("asset")
        amount = _base_units(block.get("amount"))
        if not isinstance(asset, str) or not asset.strip() or amount is None:
            continue
        costs[tool.strip().lower()] = (asset.strip().lower(), amount)
    return costs


def costs_in_asset(
    costs: Dict[str, Tuple[str, int]], payment_type: str
) -> Dict[str, int]:
    """The expected costs stated in the asset a mech is paid in.

    :param costs: ``manifest_expected_costs`` output.
    :param payment_type: the mech's ``paymentType`` selector, hex.
    :return: amount by tool; empty for an unknown payment type.
    """
    try:
        asset = PAYMENT_ASSETS[PaymentType(str(payment_type).lower())]
    except (ValueError, KeyError):
        return {}
    return {tool: amount for tool, (name, amount) in costs.items() if name == asset}


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

            metadata_tools = manifest_tools(res)
            if not metadata_tools:
                self.context.logger.warning(
                    f"Tools manifest at {self.mech_tools_api.url} lists no usable "
                    f"tools (shared by {len(mechs)} mech(s)); will retry on next "
                    "round entry."
                )
                for mech in mechs:
                    self._failed_mechs.add(mech.address)
                self.mech_tools_api.reset_retries()
                continue

            # Pull the off-chain ``url`` field out of the same IPFS manifest.
            # The field is the operator-published HTTP endpoint that
            # ``MechRequestBehaviour`` POSTs to under ``use_offchain=true``.
            # Operators inject it via ``make update-metadata`` in the
            # mech-deployments repo (see its README). Manifests that predate
            # the offchain rollout will not carry the key; the offchain
            # behaviour falls back to ``MechMarketplaceConfig.offchain_url``
            # for those mechs.
            manifest_url = self._extract_manifest_url(res_raw)
            expected_costs = manifest_expected_costs(self._manifest_payload(res_raw))
            for mech in mechs:
                mech.relevant_tools |= metadata_tools
                mech.expected_costs = costs_in_asset(expected_costs, mech.payment_type)
                if manifest_url is not None:
                    mech.http_url = manifest_url
            self.mech_tools_api.reset_retries()

        return True

    @staticmethod
    def _manifest_payload(res_raw: Any) -> Any:
        """Parse the full manifest body, or None when it is missing or unparseable."""
        body = getattr(res_raw, "body", None)
        if not body:
            return None
        try:
            return loads_bounded(body)
        except (TypeError, ValueError, RecursionError):
            return None

    def _extract_manifest_url(self, res_raw: Any) -> Optional[str]:
        """Parse the manifest body once more to extract the off-chain ``url``.

        The shared ``MechToolsSpecs`` only surfaces ``tools`` via its
        ``response_key`` config, so the full manifest is re-parsed here from
        ``res_raw.body``. Returns ``None`` when the body is missing,
        unparseable, has no ``url`` key, or carries a non-string value;
        callers treat that as ``http_url`` simply not being published yet
        for the mechs sharing this CID.
        """
        payload = self._manifest_payload(res_raw)
        if not isinstance(payload, dict):
            return None
        url = payload.get("url")
        if not isinstance(url, str) or not url:
            return None
        return url

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
                "No mechs have usable tools after fetch (manifest empty or "
                "unreachable); emitting NONE to retry."
            )
            self.shared_state.last_failure_reason = "no_usable_tools_in_mechs"
            return None

        pinned = {addr.lower() for addr in self.synchronized_data.selected_mechs}
        visible = {mech.address.lower() for mech in mech_info}
        usable = {mech.address.lower() for mech in mech_info if mech.relevant_tools}
        if pinned and not pinned & usable:
            self.context.logger.warning(
                "No pinned mech is usable, so mechs are selected as if none were "
                f"pinned until one is: not discovered {sorted(pinned - visible)}, "
                f"no usable tools {sorted(pinned & visible)}."
            )

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
