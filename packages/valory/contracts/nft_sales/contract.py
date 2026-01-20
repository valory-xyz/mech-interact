# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2025-2026 Valory AG
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

"""This module contains the class to connect to a NFTSalesTemplate contract."""

from typing import List

from aea.common import JSONLike
from aea.configurations.base import PublicId
from aea.contracts.base import Contract
from aea_ledger_ethereum import EthereumApi


PUBLIC_ID = PublicId.from_str("valory/nft_sales:0.1.0")
HEX_PREFIX_LENGTH = 2


class NFTSalesTemplate(Contract):
    """The NFTSalesTemplate contract."""

    contract_id = PUBLIC_ID

    @classmethod
    def build_create_agreement_tx(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
        agreement_id_seed: str,
        did: str,
        condition_seeds: List[bytes],
        timelocks: List[int],
        timeouts: List[int],
        publisher: str,
        service_index: int,
        reward_address: str,
        token_address: str,
        amounts: List[int],
        receivers: List[str],
    ) -> JSONLike:
        """Get the tx for create agreement."""
        contract_address = ledger_api.api.to_checksum_address(contract_address)
        contract_instance = cls.get_instance(ledger_api, contract_address)
        encoded_data = contract_instance.encode_abi(
            abi_element_identifier="createAgreementAndPayEscrow",
            args=(
                agreement_id_seed,
                did,
                condition_seeds,
                timelocks,
                timeouts,
                publisher,
                service_index,
                reward_address,
                token_address,
                amounts,
                receivers,
            ),
        )
        return {"data": bytes.fromhex(encoded_data[HEX_PREFIX_LENGTH:])}
