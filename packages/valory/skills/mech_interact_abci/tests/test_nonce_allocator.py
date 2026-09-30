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

"""Tests for the agent-supplied marketplace slot registry."""

from typing import Any, Dict

from packages.valory.skills.mech_interact_abci.nonce_allocator import (
    MECH_SLOT_REGISTRY,
    release_slot,
    reserve_slot,
    slot_is_held,
)
from packages.valory.skills.mech_interact_abci.tests.registry_stub import _Registry

_CHAIN = "optimism"
_SAFE = "0x000000000000000000000000000000000000AbCd"
_OTHER = "0x000000000000000000000000000000000000BeEf"


def _state() -> Dict[str, Any]:
    return {MECH_SLOT_REGISTRY: _Registry()}


class TestWithoutARegistry:
    """Most agents pay from their Safe through this skill and nothing else."""

    def test_the_chain_counter_is_used_as_is(self) -> None:
        """With one payer the chain counter is the whole picture.

        Anything else here would change the slot an agent signs at
        without the agent having asked for it.
        """
        assert reserve_slot({}, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10) == 10
        assert reserve_slot({}, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10) == 10

    def test_releasing_is_a_no_op_rather_than_an_error(self) -> None:
        """Nothing was reserved, so the failure path must not raise."""
        release_slot({}, chain=_CHAIN, safe=_SAFE, slot=10)


class TestWithARegistry:
    """An agent that also pays through a facilitator has two payers."""

    def test_the_second_caller_does_not_get_the_first_ones_slot(self) -> None:
        """The chain will not move until a delivery settles.

        Reading it twice in a row hands out the same slot, which is how
        two parts of one agent sign the same request.
        """
        state = _state()

        first = reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)
        second = reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)

        assert (first, second) == (10, 11)

    def test_settlement_moves_the_floor_up(self) -> None:
        """Slots below the chain counter are gone for good."""
        state = _state()
        reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)

        assert reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=40) == 40

    def test_each_safe_is_counted_separately(self) -> None:
        """The marketplace counts slots per requester, not per agent."""
        state = _state()
        reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)

        assert reserve_slot(state, chain=_CHAIN, safe=_OTHER, on_chain_nonce=3) == 3

    def test_the_safe_address_case_does_not_split_the_count(self) -> None:
        """A checksummed address and a lower-case one are the same Safe."""
        state = _state()
        reserve_slot(state, chain=_CHAIN, safe=_SAFE.lower(), on_chain_nonce=10)

        assert (
            reserve_slot(state, chain=_CHAIN, safe=_SAFE.upper(), on_chain_nonce=10)
            == 11
        )

    def test_a_released_slot_is_offered_again(self) -> None:
        """A slot nothing will settle stalls every later request for the Safe."""
        state = _state()
        reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)
        taken = reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)

        release_slot(state, chain=_CHAIN, safe=_SAFE, slot=taken)

        assert reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10) == taken

    def test_a_gap_is_filled_before_a_higher_slot(self) -> None:
        """The marketplace consumes a requester's slots in order.

        Skipping a freed slot leaves a hole that stalls everything above
        it until an on-chain request steps over it.
        """
        state = _state()
        reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)
        middle = reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)
        reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)

        release_slot(state, chain=_CHAIN, safe=_SAFE, slot=middle)

        assert (
            reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10) == middle
        )

    def test_releasing_a_slot_that_was_never_taken_changes_nothing(self) -> None:
        """A double release must not free a slot another caller now holds."""
        state = _state()
        held = reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)

        release_slot(state, chain=_CHAIN, safe=_SAFE, slot=held + 5)

        assert reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10) != held


class TestSlotIsHeld:
    """For a caller that cannot choose its slot.

    An on-chain ``request()`` takes ``mapNonces`` when it executes, so the
    agent cannot step over a slot in use. All it can decide is whether to
    send, and that turns on this answer.
    """

    def test_a_slot_another_payer_holds_is_reported_as_taken(self) -> None:
        """Sending into it costs the whole per-sender settlement batch."""
        state = _state()
        held = reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)

        assert slot_is_held(state, chain=_CHAIN, safe=_SAFE, slot=held) is True

    def test_a_free_slot_is_not_reported_as_taken(self) -> None:
        """Otherwise every on-chain request would be held back forever."""
        state = _state()
        held = reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)

        assert slot_is_held(state, chain=_CHAIN, safe=_SAFE, slot=held + 1) is False

    def test_a_released_slot_is_free_again(self) -> None:
        """A request that never landed must not block the on-chain path."""
        state = _state()
        held = reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)
        release_slot(state, chain=_CHAIN, safe=_SAFE, slot=held)

        assert slot_is_held(state, chain=_CHAIN, safe=_SAFE, slot=held) is False

    def test_another_safes_slot_does_not_block_this_one(self) -> None:
        """The marketplace counts slots per requester."""
        state = _state()
        reserve_slot(state, chain=_CHAIN, safe=_OTHER, on_chain_nonce=10)

        assert slot_is_held(state, chain=_CHAIN, safe=_SAFE, slot=10) is False

    def test_without_a_registry_nothing_is_reported_as_taken(self) -> None:
        """An agent with one payer must not hold its own requests back."""
        assert slot_is_held({}, chain=_CHAIN, safe=_SAFE, slot=10) is False
