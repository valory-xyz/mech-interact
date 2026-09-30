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

import time
from typing import Any, Dict

from packages.valory.skills.mech_interact_abci.nonce_allocator import (
    MECH_SLOT_REGISTRY,
    MECH_SLOT_RESERVED_AT,
    note_slot_accepted,
    release_slot,
    reserve_slot,
    retire_expired_slots,
    slot_is_held,
    sweep_dead_slots,
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


class TestTheTwoBoundsAreNotInterchangeable:
    """A facilitator's first free slot is not a settlement marker.

    It sits above that facilitator's own unsettled rows. Pruning the
    registry at that number forgets slots it is still holding, and this
    path, which floors at ``mapNonces``, is then handed one back. That is
    the collision the registry exists to prevent, so the contract this
    skill relies on has to keep the two bounds apart.
    """

    def test_a_slot_the_facilitator_holds_is_not_handed_to_this_path(self) -> None:
        """The chain counter is what may be forgotten, not the floor asked for."""
        state = _state()
        registry = state[MECH_SLOT_REGISTRY]

        # Two facilitator calls while the chain sits at 5: its first free slot
        # walks up, but nothing has settled.
        registry.reserve(_CHAIN, _SAFE, 5, 5)
        registry.reserve(_CHAIN, _SAFE, 6, 5)

        # This path floors at mapNonces, still 5.
        got = reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=5)

        assert got == 7, "handed a slot the facilitator is still holding"

    def test_settlement_is_what_frees_a_slot(self) -> None:
        """Once the counter moves past them the set must not grow forever."""
        state = _state()
        registry = state[MECH_SLOT_REGISTRY]
        registry.reserve(_CHAIN, _SAFE, 5, 5)
        registry.reserve(_CHAIN, _SAFE, 6, 5)

        assert reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=7) == 7
        assert registry.live[(_CHAIN, _SAFE.lower())] == {7}


class TestSweepingASlotNothingCanBeUsing:
    """A POST that went unanswered leaves a slot in an unknown state.

    The mech may be serving it, so it cannot simply be re-issued. But if
    the mech never received it, nothing will ever settle it and every
    later request for the Safe queues above a slot that never clears. The
    sweep is what tells the two apart, once the counter has stayed put
    long enough that a mech holding it would have answered.
    """

    _TIMEOUT = 300.0

    def test_a_slot_is_kept_while_the_mech_could_still_answer(self) -> None:
        """Re-issuing it here is the collision the registry exists to stop."""
        state = _state()
        slot = reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)

        swept = sweep_dead_slots(
            state,
            chain=_CHAIN,
            safe=_SAFE,
            on_chain_nonce=slot,
            older_than_secs=self._TIMEOUT,
        )

        assert swept == []
        assert slot_is_held(state, chain=_CHAIN, safe=_SAFE, slot=slot) is True

    def test_a_slot_is_reclaimed_once_no_answer_can_be_coming(self) -> None:
        """Otherwise the Safe stalls above it until the process restarts."""
        state = _state()
        slot = reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)
        # Long enough ago that a mech holding it would have answered.
        state[MECH_SLOT_RESERVED_AT][(_CHAIN, _SAFE.lower(), slot)] -= self._TIMEOUT + 1

        swept = sweep_dead_slots(
            state,
            chain=_CHAIN,
            safe=_SAFE,
            on_chain_nonce=slot,
            older_than_secs=self._TIMEOUT,
        )

        assert swept == [slot]
        assert slot_is_held(state, chain=_CHAIN, safe=_SAFE, slot=slot) is False

    def test_a_slot_the_counter_has_moved_past_is_left_alone(self) -> None:
        """It settled, so it was used; reclaiming it would mean nothing."""
        state = _state()
        slot = reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)
        state[MECH_SLOT_RESERVED_AT][(_CHAIN, _SAFE.lower(), slot)] -= self._TIMEOUT + 1

        # The counter has moved on, so this asks about a later slot.
        swept = sweep_dead_slots(
            state,
            chain=_CHAIN,
            safe=_SAFE,
            on_chain_nonce=slot + 1,
            older_than_secs=self._TIMEOUT,
        )

        assert swept == []

    def test_nothing_is_swept_without_a_registry(self) -> None:
        """An agent with a single payer has no registry and no slots to reclaim."""
        assert (
            sweep_dead_slots(
                {},
                chain=_CHAIN,
                safe=_SAFE,
                on_chain_nonce=10,
                older_than_secs=self._TIMEOUT,
            )
            == []
        )


class TestTheFacilitatorsOwnRowsAreVisibleHere:
    """The other payer on the Safe reports its rows; this skill must see them.

    The on-chain path cannot pick a slot, so all it can do is decline to
    send into one in use. A row the facilitator holds is in use, and it is
    reported rather than reserved through this module, so a registry that
    only tracked what this skill took would call it free.
    """

    def test_a_reported_row_reads_as_held(self) -> None:
        """Sending into it costs the whole per-sender settlement batch."""
        state = _state()
        state[MECH_SLOT_REGISTRY].publish(_CHAIN, _SAFE, [7])

        assert slot_is_held(state, chain=_CHAIN, safe=_SAFE, slot=7) is True

    def test_a_reported_row_is_stepped_over_when_taking_a_slot(self) -> None:
        """The off-chain path can pick, so it picks the next free one."""
        state = _state()
        state[MECH_SLOT_REGISTRY].publish(_CHAIN, _SAFE, [7])

        assert reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=7) == 8

    def test_a_row_that_stops_being_reported_frees_its_slot(self) -> None:
        """Nothing else can notice the facilitator gave up on it."""
        state = _state()
        registry = state[MECH_SLOT_REGISTRY]
        registry.publish(_CHAIN, _SAFE, [7])

        registry.publish(_CHAIN, _SAFE, [])

        assert slot_is_held(state, chain=_CHAIN, safe=_SAFE, slot=7) is False
        assert reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=7) == 7

    def test_each_report_replaces_the_last_rather_than_adding_to_it(self) -> None:
        """Merging would mean a row could only ever be added.

        One the facilitator gave up on would then stay held while its
        neighbours settled normally, which an empty report does not
        exercise because that clears the key outright.
        """
        state = _state()
        registry = state[MECH_SLOT_REGISTRY]
        registry.publish(_CHAIN, _SAFE, [7, 8])

        registry.publish(_CHAIN, _SAFE, [8])

        assert slot_is_held(state, chain=_CHAIN, safe=_SAFE, slot=7) is False
        assert slot_is_held(state, chain=_CHAIN, safe=_SAFE, slot=8) is True
        assert reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=7) == 7

    def test_the_sweep_leaves_a_reported_row_alone(self) -> None:
        """It is the facilitator's to retire, and it may still be serving it."""
        state = _state()
        state[MECH_SLOT_REGISTRY].publish(_CHAIN, _SAFE, [7])

        swept = sweep_dead_slots(
            state,
            chain=_CHAIN,
            safe=_SAFE,
            on_chain_nonce=7,
            older_than_secs=0.0,
        )

        assert swept == []
        assert slot_is_held(state, chain=_CHAIN, safe=_SAFE, slot=7) is True


class TestASlotTheOtherPayerStrandedDoesNotBlockForever:
    """The other payer retires its own expired slots when it next pays.

    It may not pay for days, and this skill has no server of its own to
    ask, so a slot it stranded would otherwise hold every request here for
    that whole time.
    """

    def test_an_expired_slot_is_freed_on_this_skills_clock(self) -> None:
        """No facilitator read needed, just the recorded expiry and a clock."""
        state = _state()
        registry = state[MECH_SLOT_REGISTRY]
        registry.reserve(_CHAIN, _SAFE, 7, 7)
        registry.note_expiry(_CHAIN, _SAFE, 7, 1_000)

        freed = retire_expired_slots(
            state, chain=_CHAIN, safe=_SAFE, older_than_secs=0.0
        )

        assert freed == [7]
        assert slot_is_held(state, chain=_CHAIN, safe=_SAFE, slot=7) is False

    def test_a_slot_still_admissible_is_left_alone(self) -> None:
        """Freeing one the other payer may still get served is the collision."""
        state = _state()
        registry = state[MECH_SLOT_REGISTRY]
        registry.reserve(_CHAIN, _SAFE, 7, 7)
        registry.note_expiry(_CHAIN, _SAFE, 7, int(time.time()) + 600)

        freed = retire_expired_slots(
            state, chain=_CHAIN, safe=_SAFE, older_than_secs=0.0
        )

        assert freed == []
        assert slot_is_held(state, chain=_CHAIN, safe=_SAFE, slot=7) is True

    def test_a_slot_only_just_expired_is_given_the_benefit_of_the_doubt(
        self,
    ) -> None:
        """The expiry is on the other server's clock, not this agent's.

        An agent running ahead would otherwise free a slot that server is
        still willing to admit, which is the collision this exists to stop.
        The margin is the slack.
        """
        state = _state()
        registry = state[MECH_SLOT_REGISTRY]
        registry.reserve(_CHAIN, _SAFE, 7, 7)
        registry.note_expiry(_CHAIN, _SAFE, 7, int(time.time()) - 10)

        freed = retire_expired_slots(
            state, chain=_CHAIN, safe=_SAFE, older_than_secs=60.0
        )

        assert freed == []
        assert slot_is_held(state, chain=_CHAIN, safe=_SAFE, slot=7) is True

    def test_a_slot_expired_well_past_the_margin_is_freed(self) -> None:
        """Otherwise the margin would just move the stall rather than end it."""
        state = _state()
        registry = state[MECH_SLOT_REGISTRY]
        registry.reserve(_CHAIN, _SAFE, 7, 7)
        registry.note_expiry(_CHAIN, _SAFE, 7, int(time.time()) - 600)

        freed = retire_expired_slots(
            state, chain=_CHAIN, safe=_SAFE, older_than_secs=60.0
        )

        assert freed == [7]

    def test_a_row_the_other_payers_server_reports_is_left_alone(self) -> None:
        """Expired or not, an admitted request is that server's to retire."""
        state = _state()
        state[MECH_SLOT_REGISTRY].publish(_CHAIN, _SAFE, [7])

        freed = retire_expired_slots(
            state, chain=_CHAIN, safe=_SAFE, older_than_secs=0.0
        )

        assert freed == []
        assert slot_is_held(state, chain=_CHAIN, safe=_SAFE, slot=7) is True

    def test_without_a_registry_there_is_nothing_to_retire(self) -> None:
        """Most agents have a single payer and bind none."""
        assert (
            retire_expired_slots({}, chain=_CHAIN, safe=_SAFE, older_than_secs=0.0)
            == []
        )


class TestAnAcceptedRequestStopsBeingSweepable:
    """The sweep is for a POST that may never have arrived.

    ``mapNonces`` moves when a mech settles on chain, not when it answers,
    so the counter can sit on a slot a mech accepted for longer than the
    sweep's age bound. Sweeping it would hand the slot to another payer on
    the Safe and the two would clash at settlement.
    """

    def test_an_accepted_slot_is_not_swept(self) -> None:
        """A mech answered and took it, so the question is already settled."""
        state = _state()
        slot = reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=7)
        note_slot_accepted(state, chain=_CHAIN, safe=_SAFE, slot=slot)

        swept = sweep_dead_slots(
            state,
            chain=_CHAIN,
            safe=_SAFE,
            on_chain_nonce=7,
            older_than_secs=0.0,
        )

        assert swept == []
        assert slot_is_held(state, chain=_CHAIN, safe=_SAFE, slot=7) is True

    def test_an_unanswered_slot_is_still_swept(self) -> None:
        """Otherwise nothing would ever reclaim one, which stalls the Safe."""
        state = _state()
        reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=7)

        swept = sweep_dead_slots(
            state,
            chain=_CHAIN,
            safe=_SAFE,
            on_chain_nonce=7,
            older_than_secs=0.0,
        )

        assert swept == [7]

    def test_noting_a_slot_nothing_reserved_is_harmless(self) -> None:
        """Called on every accepted attempt, including with no registry bound."""
        note_slot_accepted({}, chain=_CHAIN, safe=_SAFE, slot=7)


class TestAnUnnamedChainMeansNoRegistry:
    """The registry is keyed by chain name, and so is the other payer.

    An empty name still produces a key, just one nothing else writes to,
    so the two would never meet and the registry would quietly do nothing
    while looking like it worked. Better to behave as the documented
    no-registry case.
    """

    def test_reserve_falls_back_to_the_chain_counter(self) -> None:
        """Not a phantom key that no other payer will ever read."""
        state = _state()

        assert reserve_slot(state, chain="", safe=_SAFE, on_chain_nonce=10) == 10
        assert state[MECH_SLOT_REGISTRY].live == {}

    def test_nothing_is_reported_held(self) -> None:
        """A key nobody writes to would always answer 'free' anyway."""
        state = _state()
        reserve_slot(state, chain=_CHAIN, safe=_SAFE, on_chain_nonce=10)

        assert slot_is_held(state, chain="", safe=_SAFE, slot=10) is False

    def test_nothing_is_swept(self) -> None:
        """There is no bookkeeping to reclaim under a name nothing uses."""
        state = _state()

        assert (
            sweep_dead_slots(
                state,
                chain="",
                safe=_SAFE,
                on_chain_nonce=10,
                older_than_secs=0.0,
            )
            == []
        )
