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

"""One place that hands out marketplace slots for a Safe.

The marketplace consumes a requester's slots in order, and the on-chain
counter only moves when a delivery settles. So a slot that is taken but
not yet settled is invisible to the chain, and anyone reading the chain
to pick their next one will pick a slot somebody else already holds.

An agent can have more than one thing paying from the same Safe: mech
requests through this skill, and API calls through a facilitator. Each
of them tracks its own outstanding slots perfectly and cannot see the
other's, so both are right on their own and wrong together.

This is the one place that sees both. It lives in the agent's shared
state, which every skill in the agent reaches, and hands out a slot
above the on-chain counter and above anything it has already issued.
"""

from typing import Any, Dict

# Highest slot handed out per Safe, keyed ``chain:safe`` in lower case.
MECH_NONCE_ISSUED = "mech_nonce_issued"


def _key(chain: str, safe: str) -> str:
    return f"{chain.lower()}:{safe.lower()}"


def reserve_slot(
    shared_state: Dict[str, Any], *, chain: str, safe: str, on_chain_nonce: int
) -> int:
    """Hand out the next marketplace slot for ``safe`` and record it.

    :param shared_state: the agent's shared state, reachable from every skill.
    :param chain: the chain the marketplace is on.
    :param safe: the requester Safe paying for the request.
    :param on_chain_nonce: ``mapNonces(safe)``, the floor.
    :return: the slot to sign at.

    The on-chain counter is the floor because everything below it has
    settled and can never be used again. Above it, the highest slot
    already handed out wins, because those are taken but not yet
    visible on chain.

    Reserved on the way out rather than on success, since a caller that
    picks a slot and has not finished with it still owns it. Hand it
    back with ``release_slot`` when the request does not land.
    """
    issued: Dict[str, int] = shared_state.setdefault(MECH_NONCE_ISSUED, {})
    key = _key(chain, safe)
    last = issued.get(key)
    slot = on_chain_nonce if last is None else max(on_chain_nonce, last + 1)
    issued[key] = slot
    return slot


def release_slot(
    shared_state: Dict[str, Any], *, chain: str, safe: str, slot: int
) -> bool:
    """Give back a slot whose request never landed.

    :param shared_state: the agent's shared state.
    :param chain: the chain the marketplace is on.
    :param safe: the requester Safe.
    :param slot: the slot handed out earlier.
    :return: whether it was given back.

    Only the most recent slot can be handed back. Once something else
    has taken a higher one, returning this would hand the same slot to
    two callers, which is the thing this exists to prevent. A slot
    stranded in the middle leaves a gap that nothing here can fill; the
    marketplace needs an on-chain request to step over it.
    """
    issued: Dict[str, int] = shared_state.setdefault(MECH_NONCE_ISSUED, {})
    key = _key(chain, safe)
    if issued.get(key) != slot:
        return False
    if slot == 0:
        del issued[key]
    else:
        issued[key] = slot - 1
    return True


def payload_floor(context: Any, *, chain: str, safe: str, on_chain_nonce: int) -> int:
    """Reserve a slot and return it for a request served by something else.

    :param context: the skill context, whose shared state holds the count.
    :param chain: the chain the marketplace is on.
    :param safe: the requester Safe paying for the request.
    :param on_chain_nonce: ``mapNonces(safe)``, the floor.
    :return: the slot the request must not be signed below.

    For a paid call the agent hands to something it does not sign for
    itself, such as a connection that builds its own session. The slot
    is reserved here so the next caller does not get the same one, and
    travels with the request as a floor.
    """
    return reserve_slot(
        context.shared_state, chain=chain, safe=safe, on_chain_nonce=on_chain_nonce
    )
