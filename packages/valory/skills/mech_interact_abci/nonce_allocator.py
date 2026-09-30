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

from typing import Any, Dict, Optional

# Shared-state key the agent binds its slot registry to. The object needs
# ``reserve(chain, safe, floor)`` and ``release(chain, safe, slot)``.
MECH_SLOT_REGISTRY = "mech_slot_registry"


def _registry(shared_state: Dict[str, Any]) -> Optional[Any]:
    """Return the agent's slot registry, or ``None`` when it has none.

    :param shared_state: the agent's shared state.
    :return: the registry, or ``None``.
    """
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
    registry = _registry(shared_state)
    if registry is None:
        return on_chain_nonce
    return int(registry.reserve(chain, safe, on_chain_nonce))


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
    registry = _registry(shared_state)
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
    registry = _registry(shared_state)
    if registry is not None:
        registry.release(chain, safe, slot)
