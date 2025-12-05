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

"""Tooling to perform subgraph requests from a behaviour."""

import json
from abc import ABC
from enum import Enum, auto
from typing import Any, Generator, cast

from packages.valory.skills.abstract_round_abci.behaviour_utils import BaseBehaviour
from packages.valory.skills.abstract_round_abci.models import ApiSpecs
from packages.valory.skills.mech_interact_abci.graph_tooling.queries.mechs_info import (
    info as mechs_info_query,
)
from packages.valory.skills.mech_interact_abci.models import (
    MechParams,
    MechsSubgraph,
    MechsSubgraphResponseType,
)


MechsInfoFetcher = Generator[None, None, MechsSubgraphResponseType]


QUERY_BATCH_SIZE = 1000
MAX_LOG_SIZE = 1000


def to_content(query: str) -> bytes:
    """Convert the given query string to payload content, i.e., add it under a `queries` key and convert it to bytes."""
    finalized_query = {"query": query}
    encoded_query = json.dumps(finalized_query, sort_keys=True).encode("utf-8")

    return encoded_query


class FetchStatus(Enum):
    """The status of a fetch operation."""

    SUCCESS = auto()
    IN_PROGRESS = auto()
    FAIL = auto()
    NONE = auto()


class QueryingBehaviour(BaseBehaviour, ABC):
    """Abstract behaviour that implements subgraph querying functionality."""

    def __init__(self, **kwargs: Any) -> None:
        """Initialize a querying behaviour."""
        super().__init__(**kwargs)
        self._call_failed: bool = False
        self._fetch_status: FetchStatus = FetchStatus.NONE

    @property
    def params(self) -> MechParams:
        """Return the params."""
        return cast(MechParams, super().params)

    @property
    def mechs_subgraph(self) -> MechsSubgraph:
        """Get the mechs' subgraph."""
        return self.context.mechs_subgraph

    def _handle_response(
        self,
        subgraph: ApiSpecs,
        res: MechsSubgraphResponseType,
        res_context: str,
        sleep_on_fail: bool = True,
    ) -> MechsInfoFetcher:
        """Handle a response from a subgraph.

        :param subgraph: the subgraph to handle the response for.
        :param res: the response to handle.
        :param res_context: the context of the current response.
        :param sleep_on_fail: whether we want to sleep if we fail to get the response's result.
        :return: the response's result, using the given keys. `None` if response is `None` (has failed).
        :yield: None
        """
        if res is None:
            self.context.logger.error(
                f"Could not get {res_context} from {subgraph.api_id}"
            )
            self._call_failed = True
            subgraph.increment_retries()

            if subgraph.is_retries_exceeded():
                self._fetch_status = FetchStatus.FAIL

            if sleep_on_fail:
                sleep_time = subgraph.retries_info.suggested_sleep_time
                yield from self.sleep(sleep_time)
            return None

        # truncate the response, otherwise logs get too big
        res_str = str(res)[:MAX_LOG_SIZE]
        self.context.logger.info(f"Retrieved {res_context}: {res_str}.")
        self._call_failed = False
        subgraph.reset_retries()
        self._fetch_status = FetchStatus.SUCCESS
        return res

    def fetch_mechs_info_batch(self, mechs_id_gt: int) -> MechsInfoFetcher:
        """Fetch a batch of mechs' information from the subgraph."""
        query = mechs_info_query.substitute(
            first=QUERY_BATCH_SIZE,
            mechs_id_gt=mechs_id_gt,
            ignored_mechs='", "'.join(self.params.ignored_mechs),
        )
        res_raw = yield from self.get_http_response(
            content=to_content(query),
            **self.mechs_subgraph.get_spec(),
        )
        res = self.mechs_subgraph.process_response(res_raw)

        info_batch = yield from self._handle_response(
            self.mechs_subgraph,
            res,
            res_context="mechs' information",
        )
        if info_batch is None:
            # we failed to get the information
            self.context.logger.warning(
                f"Failed to get the information for the mechs from {self.mechs_subgraph.api_id}!"
            )

        return info_batch

    def fetch_mechs_info(
        self,
    ) -> MechsInfoFetcher:
        """Fetch mechs' information from the subgraph."""
        self._fetch_status = FetchStatus.IN_PROGRESS

        # used to allow for pagination based on mechs' ids
        mechs_id_gt = 0
        mechs_info = []
        while True:
            info_batch = yield from self.fetch_mechs_info_batch(mechs_id_gt)

            if info_batch is None:
                # failed, return None
                return None

            if len(info_batch) == 0:
                # no more information to fetch
                return mechs_info

            mechs_info.extend(info_batch)
            mechs_id_gt = info_batch[-1].id

    def clean_up(self) -> None:
        """Clean up the resources."""
        self.mechs_subgraph.reset_retries()
