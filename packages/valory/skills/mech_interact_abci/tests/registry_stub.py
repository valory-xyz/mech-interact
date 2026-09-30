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

"""A stand-in for the slot registry an agent binds into shared state.

Deliberately not the real one: this skill must not depend on the package
that owns it, so what the tests pin down is the contract between them.
"""

import threading
from typing import Dict, Set, Tuple


class _Registry:
    """Stand-in for the registry the agent binds, same contract.

    Deliberately not the real one: this skill must not depend on the
    package that owns it, and the point of the test is the contract.
    """

    def __init__(self) -> None:
        self.live: Dict[Tuple[str, str], Set[int]] = {}
        self._guard = threading.Lock()

    def reserve(self, chain: str, safe: str, floor: int, settled_below: int) -> int:
        """Take the lowest free slot at or above ``floor``.

        ``settled_below`` is what can be forgotten, which is not the same as
        ``floor``: a facilitator's first free slot sits above its own
        unsettled rows, so pruning there would drop slots it still holds.
        """
        key = (chain.lower(), safe.lower())
        with self._guard:
            live = self.live.setdefault(key, set())
            live.difference_update([slot for slot in live if slot < settled_below])
            slot = floor
            while slot in live:
                slot += 1
            live.add(slot)
            return slot

    def release(self, chain: str, safe: str, slot: int) -> None:
        """Hand a slot back."""
        key = (chain.lower(), safe.lower())
        with self._guard:
            live = self.live.get(key)
            if live is None:
                return
            live.discard(slot)
            if not live:
                del self.live[key]
