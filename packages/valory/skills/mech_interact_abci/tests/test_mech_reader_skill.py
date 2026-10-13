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

"""The mech reader driven through the real skill: its params, dialogues and response handlers."""

import json
from pathlib import Path
from typing import Any, List, Optional, cast

from aea.helpers.transaction.base import State

from packages.valory.protocols.contract_api import ContractApiMessage
from packages.valory.protocols.http import HttpMessage
from packages.valory.skills.abstract_round_abci.behaviour_utils import (
    HTTP_CLIENT_PUBLIC_ID,
    LEDGER_API_ADDRESS,
)
from packages.valory.skills.abstract_round_abci.test_tools.base import (
    FSMBehaviourBaseCase,
)
from packages.valory.skills.mech_interact_abci.mech_reader import (
    CardStatus,
    MechFacts,
    RecordStatus,
    read_live_rate,
    read_mech,
)
from packages.valory.skills.mech_interact_abci.models import (
    DEFAULT_MARKETPLACE_API_URL,
)

MECH = "0x" + "e4" * 20
RATE = 10**16
SERVICE_ID = 3786


class TestTheReaderOnTheRealSkill(FSMBehaviourBaseCase):
    """Requests built from the skill's own config, replies routed by its own handlers.

    Unit tests give the reader a stand-in context; this one checks that the
    params it reads exist on the skill's `MechParams`, and that the nonce it
    registers is the one the skill's HTTP and contract API handlers look up.
    """

    path_to_skill = Path(__file__).parent.parent

    def _answer_http(self, body: Any, status: int = 200) -> HttpMessage:
        """Answer the request in the outbox through the skill's HTTP handler."""
        request = cast(HttpMessage, self.get_message_from_outbox())
        assert request is not None
        incoming = self.build_incoming_message(
            message_type=HttpMessage,
            dialogue_reference=(request.dialogue_reference[0], "stub"),
            performative=HttpMessage.Performative.RESPONSE,
            target=request.message_id,
            message_id=-1,
            to=str(self.skill.skill_context.skill_id),
            sender=str(HTTP_CLIENT_PUBLIC_ID),
            version="",
            status_code=status,
            status_text="",
            headers="",
            body=json.dumps(body).encode(),
        )
        self.http_handler.handle(incoming)
        return request

    def test_read_mech_uses_the_skill_params_and_handlers(self) -> None:
        """Both reads go out with the skill's URLs and both replies reach the callback."""
        context = self.skill.skill_context
        results: List[MechFacts] = []
        read_mech(context, MECH, results.append)

        record_request = self._answer_http(
            {
                "data": {
                    "meches": [
                        {
                            "address": MECH,
                            "maxDeliveryRate": str(RATE),
                            "paymentType": "0x" + "ba" * 32,
                            "receivedRequests": "10",
                            "selfDeliveredFromReceived": "9",
                            "deliveredByOthersFromReceived": "1",
                            "service": {"id": str(SERVICE_ID)},
                        }
                    ],
                    "createMeches": [],
                }
            }
        )
        card_request = self._answer_http(
            {
                "name": "Bob's mech",
                "skills": [{"name": "superforcaster"}],
                "metadata": {"mechAddress": f"eip155:100:{MECH}"},
            }
        )

        assert record_request.url == context.mechs_subgraph.url
        assert context.params.marketplace_api_url == DEFAULT_MARKETPLACE_API_URL
        assert card_request.url == (
            f"{DEFAULT_MARKETPLACE_API_URL}/{context.params.mech_chain_id}"
            f"/ai-agents/{SERVICE_ID}/agent-card.json"
        )
        assert len(results) == 1
        facts = results[0]
        assert (facts.record, facts.card) == (RecordStatus.FOUND, CardStatus.READ)
        assert (facts.max_delivery_rate, facts.tools) == (RATE, ("superforcaster",))
        assert context.requests.request_id_to_callback == {}

    def test_read_live_rate_uses_the_ledger_and_the_contract_handler(self) -> None:
        """The contract read names the default ledger and its STATE reply reaches the callback."""
        context = self.skill.skill_context
        results: List[Optional[int]] = []
        read_live_rate(context, MECH, results.append)

        request = cast(ContractApiMessage, self.get_message_from_outbox())
        assert request is not None
        assert request.ledger_id == context.default_ledger_id
        assert request.kwargs.body == {"chain_id": context.params.mech_chain_id}
        incoming = self.build_incoming_message(
            message_type=ContractApiMessage,
            dialogue_reference=(request.dialogue_reference[0], "stub"),
            performative=ContractApiMessage.Performative.STATE,
            target=request.message_id,
            message_id=-1,
            to=str(context.skill_id),
            sender=LEDGER_API_ADDRESS,
            state=State(
                ledger_id=context.default_ledger_id,
                body={"max_delivery_rate": RATE},
            ),
        )
        self.contract_handler.handle(incoming)

        assert results == [RATE]
        assert context.requests.request_id_to_callback == {}
