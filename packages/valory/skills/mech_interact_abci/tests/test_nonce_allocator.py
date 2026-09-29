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

"""Tests for the shared marketplace slot allocator."""

import pytest

from packages.valory.skills.mech_interact_abci.nonce_allocator import (
    MECH_NONCE_ISSUED,
    release_slot,
    reserve_slot,
)

_CHAIN = "optimism"
_SAFE = "0x000000000000000000000000000000000000AbCd"
_OTHER = "0x000000000000000000000000000000000000BeEf"


class TestReserveSlot:
    """Two callers on one Safe must never be handed the same slot."""

    def test_the_counter_does_not_wait_for_settlement(self) -> None:
        """This is the whole point: the chain will not move until a delivery settles.

        Reading the chain twice in a row hands out the same slot, which
        is how two parts of one agent end up signing the same request.
        """
        state: dict = {}
        first = reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)
        second = reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)

        assert (first, second) == (10, 11)

    def test_the_on_chain_counter_is_a_floor_it_never_goes_below(self) -> None:
        """Everything below has settled, so those slots can never be used again."""
        state: dict = {}
        reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)

        assert reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=40) == 40

    def test_safes_are_counted_separately(self) -> None:
        """Slots belong to a requester, so one Safe's count must not move another's."""
        state: dict = {}
        reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)

        assert reserve_slot(state, chain=_CHAIN, safe=_OTHER, on_chain_nonce=3) == 3

    def test_the_same_safe_in_any_casing_is_one_count(self) -> None:
        """Callers get the address from different places and the casing differs."""
        state: dict = {}
        reserve_slot(state, chain=_CHAIN, safe=_SAFE.lower(), on_chain_nonce=10)

        assert (
            reserve_slot(state, chain=_CHAIN, safe=_SAFE.upper(), on_chain_nonce=10)
            == 11
        )


class TestReleaseSlot:
    """A slot handed out for a request that never landed has to come back."""

    def test_a_released_slot_is_handed_out_again(self) -> None:
        """Otherwise the gap it leaves stalls the Safe: nothing can fill it."""
        state: dict = {}
        reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)
        taken = reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)

        assert release_slot(state, chain=_CHAIN, safe=_SAFE, slot=taken) is True
        assert reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10) == taken

    def test_only_the_most_recent_slot_comes_back(self) -> None:
        """Returning an older one would hand the same slot to two callers."""
        state: dict = {}
        stranded = reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)
        newer = reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)

        assert release_slot(state, chain=_CHAIN, safe=_SAFE, slot=stranded) is False
        assert (
            reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)
            == newer + 1
        )

    def test_releasing_the_first_slot_clears_the_count(self) -> None:
        """Slot zero is a real slot, so it cannot be recorded as "one below"."""
        state: dict = {}
        first = reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=0)

        assert release_slot(state, chain=_CHAIN, safe=_SAFE, slot=first) is True
        assert state[MECH_NONCE_ISSUED] == {}
        assert reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=0) == 0

    @pytest.mark.parametrize("slot", [5, 99])
    def test_releasing_something_never_handed_out_changes_nothing(
        self, slot: int
    ) -> None:
        """A confused caller must not be able to rewind somebody else's count."""
        state: dict = {}
        reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)

        assert release_slot(state, chain=_CHAIN, safe=_SAFE, slot=slot) is False
        assert reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10) == 11
