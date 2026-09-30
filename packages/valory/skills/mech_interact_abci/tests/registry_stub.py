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
from typing import Dict, Iterable, List, Set, Tuple


class _Registry:
    """Stand-in for the registry the agent binds, same contract.

    Deliberately not the real one: this skill must not depend on the
    package that owns it, and the point of the test is the contract.
    """

    def __init__(self) -> None:
        self._reserved: Dict[Tuple[str, str], Set[int]] = {}
        self._published: Dict[Tuple[str, str], Set[int]] = {}
        self._expiry: Dict[Tuple[Tuple[str, str], int], int] = {}
        self._guard = threading.Lock()

    @property
    def live(self) -> Dict[Tuple[str, str], Set[int]]:
        """Every slot in use: reserved here plus reported by the facilitator.

        A snapshot, not the working set. The two halves retire differently,
        so mutating this would change nothing; use ``reserve``, ``release``
        or ``publish``.
        """
        with self._guard:
            keys = set(self._reserved) | set(self._published)
            merged = {
                key: self._reserved.get(key, set()) | self._published.get(key, set())
                for key in keys
            }
            return {key: slots for key, slots in merged.items() if slots}

    def clear(self) -> None:
        """Forget everything."""
        with self._guard:
            self._reserved.clear()
            self._published.clear()
            self._expiry.clear()

    def reserve(self, chain: str, safe: str, floor: int, settled_below: int) -> int:
        """Take the lowest free slot at or above ``floor``.

        ``settled_below`` is what can be forgotten, which is not the same as
        ``floor``: a facilitator's first free slot sits above its own
        unsettled rows, so pruning there would drop slots it still holds.
        """
        key = (chain.lower(), safe.lower())
        with self._guard:
            reserved = self._reserved.setdefault(key, set())
            reserved.difference_update(
                [slot for slot in reserved if slot < settled_below]
            )
            in_use = reserved | self._published.get(key, set())
            slot = floor
            while slot in in_use:
                slot += 1
            reserved.add(slot)
            return slot

    def release(self, chain: str, safe: str, slot: int) -> None:
        """Hand back a slot reserved here.

        One the facilitator reports is retired by it dropping out of a later
        report, so this leaves those alone.
        """
        key = (chain.lower(), safe.lower())
        with self._guard:
            reserved = self._reserved.get(key)
            if reserved is None:
                return
            reserved.discard(slot)
            if not reserved:
                del self._reserved[key]

    def publish(self, chain: str, safe: str, slots: Iterable[int]) -> None:
        """Replace what the facilitator is known to hold for ``safe``.

        Wholesale, because this is the only thing that can retire one of
        its rows: the chain counter never passes a slot that never settled.
        """
        key = (chain.lower(), safe.lower())
        with self._guard:
            held = {int(slot) for slot in slots}
            if held:
                self._published[key] = held
            else:
                self._published.pop(key, None)
            reserved = self._reserved.get(key)
            if reserved is not None:
                reserved.difference_update(held)
                if not reserved:
                    del self._reserved[key]

    def retire_expired(self, chain: str, safe: str, now: int) -> List[int]:
        """Free reserved slots whose signed request can no longer be admitted.

        Never touches what the facilitator reports: an expired request it
        already admitted is its row, retired only by its next report.
        """
        key = (chain.lower(), safe.lower())
        with self._guard:
            freed = []
            for slot in list(self._reserved.get(key, set())):
                expires_at = self._expiry.get((key, slot))
                if expires_at is None or expires_at > int(now):
                    continue
                self._reserved[key].discard(slot)
                if not self._reserved[key]:
                    del self._reserved[key]
                self._expiry.pop((key, slot), None)
                freed.append(slot)
            return freed

    def note_expiry(self, chain: str, safe: str, slot: int, expires_at: int) -> None:
        """Record when the request signed at ``slot`` stops being admissible."""
        key = (chain.lower(), safe.lower())
        with self._guard:
            self._expiry[(key, slot)] = int(expires_at)

    def hand_over(self, chain: str, safe: str, slot: int) -> None:
        """Record that the facilitator has taken responsibility for ``slot``."""
        key = (chain.lower(), safe.lower())
        with self._guard:
            self._published.setdefault(key, set()).add(slot)
            reserved = self._reserved.get(key)
            if reserved is not None:
                reserved.discard(slot)
                if not reserved:
                    del self._reserved[key]
