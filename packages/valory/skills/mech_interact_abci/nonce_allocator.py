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

"""Marketplace slots for a Safe that more than one thing pays from.

The marketplace consumes a requester's slots in order, and ``mapNonces``
only moves when a delivery settles. So a slot that is taken but unsettled
is invisible on chain, and signing at the chain counter hands out one
somebody else is already holding.

That only happens when an agent pays from a Safe through more than one
route, which is not something this skill can know about. So the agent
supplies a registry if it has one, under ``MECH_SLOT_REGISTRY``, and
without one the chain counter is the whole picture and is used as is.
"""

import time
from typing import Any, Dict, List, Optional, Tuple

# Shared-state key the agent binds its slot registry to. The object needs
# ``reserve(chain, safe, floor, settled_below)``, ``release(chain, safe, slot)``
# and ``live``.
MECH_SLOT_REGISTRY = "mech_slot_registry"

# When each slot this skill reserved was taken, keyed ``(chain, safe, slot)``.
# A slot whose request never reached the mech looks exactly like one the mech is
# serving, until enough time has passed that a mech holding it would have
# answered. Kept here rather than in the registry because it is this path's
# problem: the facilitator proves the same thing from its signed expiry.
MECH_SLOT_RESERVED_AT = "mech_slot_reserved_at"


def _registry(shared_state: Dict[str, Any], chain: str) -> Optional[Any]:
    """Return the agent's slot registry, or ``None`` when it cannot be used.

    :param shared_state: the agent's shared state.
    :param chain: the chain name this call would key by.
    :return: the registry, or ``None``.

    The registry is keyed by chain name, and the other payer on the Safe
    keys by its own configured name for the same chain. An empty name here
    would still produce a key, just one nothing else writes to, so the two
    would never meet and the registry would silently do nothing. Treated as
    "no registry" instead, which is at least the documented behaviour.
    """
    if not chain:
        return None
    return shared_state.get(MECH_SLOT_REGISTRY)


def reserve_slot(
    shared_state: Dict[str, Any], *, chain: str, safe: str, on_chain_nonce: int
) -> int:
    """Take a marketplace slot for ``safe`` at or above the chain counter.

    :param shared_state: the agent's shared state.
    :param chain: the chain the marketplace is on.
    :param safe: the requester Safe paying for the request.
    :param on_chain_nonce: ``mapNonces(safe)``, the floor.
    :return: the slot to sign at.

    Reserved on the way out rather than on success, since a caller that
    has picked a slot and not finished with it still owns it. Hand it back
    with ``release_slot`` when the request does not land.
    """
    registry = _registry(shared_state, chain)
    if registry is None:
        return on_chain_nonce
    # Both bounds are the chain counter here: this path floors at ``mapNonces``
    # and everything below it has settled. They differ for a caller that floors
    # at a facilitator's first free slot, which sits above its own unsettled rows.
    slot = int(registry.reserve(chain, safe, on_chain_nonce, on_chain_nonce))
    taken_at: Dict[Tuple[str, str, int], float] = shared_state.setdefault(
        MECH_SLOT_RESERVED_AT, {}
    )
    taken_at[(chain.lower(), safe.lower(), slot)] = time.time()
    return slot


def note_slot_accepted(
    shared_state: Dict[str, Any], *, chain: str, safe: str, slot: int
) -> None:
    """Record that a mech took the request signed at ``slot``.

    :param shared_state: the agent's shared state.
    :param chain: the chain the marketplace is on.
    :param safe: the requester Safe paying for the request.
    :param slot: the slot the accepted request was signed at.

    The sweep exists for a slot whose POST went unanswered, where the mech
    may never have received it. Once a mech has answered and taken the
    request that question is settled, so the slot stops being sweepable.
    Without this it would still be swept: ``mapNonces`` moves when the mech
    settles on chain rather than when it answers, so the counter can sit on
    an accepted slot for longer than the sweep's age bound, and handing it
    back would let another payer on the Safe sign the same slot.
    """
    shared_state.get(MECH_SLOT_RESERVED_AT, {}).pop(
        (chain.lower(), safe.lower(), slot), None
    )


def slot_is_held(
    shared_state: Dict[str, Any], *, chain: str, safe: str, slot: int
) -> bool:
    """Return whether something in this agent is already using ``slot``.

    :param shared_state: the agent's shared state.
    :param chain: the chain the marketplace is on.
    :param safe: the requester Safe.
    :param slot: the slot to check.
    :return: whether it is taken.

    For a caller that cannot choose its slot. An on-chain ``request()``
    takes ``mapNonces`` at execution time, so the only thing the agent can
    decide is whether to send now, and that turns on whether the slot the
    contract is about to take is one something else is already using.
    """
    registry = _registry(shared_state, chain)
    if registry is None:
        return False
    key = (chain.lower(), safe.lower())
    return slot in registry.live.get(key, set())


def release_slot(
    shared_state: Dict[str, Any], *, chain: str, safe: str, slot: int
) -> None:
    """Give back a slot whose request never landed.

    :param shared_state: the agent's shared state.
    :param chain: the chain the marketplace is on.
    :param safe: the requester Safe.
    :param slot: the slot reserved earlier.

    Holding a slot nothing will ever settle stalls every later request
    for the Safe, because the marketplace consumes slots in order.
    """
    registry = _registry(shared_state, chain)
    if registry is not None:
        registry.release(chain, safe, slot)
    shared_state.get(MECH_SLOT_RESERVED_AT, {}).pop(
        (chain.lower(), safe.lower(), slot), None
    )


def retire_expired_slots(
    shared_state: Dict[str, Any],
    *,
    chain: str,
    safe: str,
    older_than_secs: float,
) -> List[int]:
    """Free slots another payer signed for and whose request has expired.

    :param shared_state: the agent's shared state.
    :param chain: the chain the marketplace is on.
    :param safe: the requester Safe paying for the request.
    :param older_than_secs: margin past the expiry before acting, to absorb
        the difference between this agent's clock and the facilitator's.
    :return: the slots freed.

    The other payer retires its own expired slots whenever it next talks to
    its server, but it may not do that for days. Until then a slot it
    stranded blocks this skill, which has no server of its own to ask. The
    registry records when each signed request stops being admissible, so
    this needs only a clock. Slots the other payer's server reports holding
    are left alone by the registry itself.
    """
    registry = _registry(shared_state, chain)
    if registry is None:
        return []
    retire = getattr(registry, "retire_expired", None)
    if retire is None:
        # A registry from a release that predates this; nothing to do.
        return []
    return list(retire(chain, safe, int(time.time() - older_than_secs)))


def sweep_dead_slots(
    shared_state: Dict[str, Any],
    *,
    chain: str,
    safe: str,
    on_chain_nonce: int,
    older_than_secs: float,
) -> List[int]:
    """Hand back a held slot that nothing can be using any more.

    :param shared_state: the agent's shared state.
    :param chain: the chain the marketplace is on.
    :param safe: the requester Safe.
    :param on_chain_nonce: ``mapNonces(safe)``.
    :param older_than_secs: how long a mech has to answer before a slot it
        never acknowledged is treated as dead.
    :return: the slots handed back.

    A request whose POST went unanswered may be one the mech is serving, so
    its slot is kept: re-issuing it would put two requests on one slot,
    which is what this registry exists to stop. But if the mech never
    received it, nothing will settle that slot, the counter never moves,
    and every later request for the Safe queues above a slot that will
    never clear.

    The two cases look identical at the time. They stop looking identical
    once the counter still sits at that slot and long enough has passed
    that a mech holding it would have answered: it cannot have been
    accepted, so the slot is free.
    """
    registry = _registry(shared_state, chain)
    if registry is None:
        return []
    key = (chain.lower(), safe.lower())
    if on_chain_nonce not in registry.live.get(key, set()):
        # Either never ours or already settled; nothing to reclaim.
        return []
    taken_at: Dict[Tuple[str, str, int], float] = shared_state.setdefault(
        MECH_SLOT_RESERVED_AT, {}
    )
    when = taken_at.get((key[0], key[1], on_chain_nonce))
    if when is None or time.time() - when < older_than_secs:
        return []
    release_slot(shared_state, chain=chain, safe=safe, slot=on_chain_nonce)
    return [on_chain_nonce]
