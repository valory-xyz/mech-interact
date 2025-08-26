# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2025 Valory AG
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

"""This module contains the class to connect to a LockPaymentCondition contract."""

from typing import List
from aea.common import JSONLike
from aea.configurations.base import PublicId
from aea.contracts.base import Contract
from aea_ledger_ethereum import EthereumApi


PUBLIC_ID = PublicId.from_str("valory/lock_payment_condition:0.1.0")


class LockPaymentCondition(Contract):
    """The LockPaymentCondition contract."""

    contract_id = PUBLIC_ID

    @classmethod
    def get_hash_values(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
        did: str,
        reward_address: str,
        token_address: str,
        amounts: List[int],
        receivers: List[str],
    ) -> JSONLike:
        """Get the hash values."""
        contract_address = ledger_api.api.to_checksum_address(contract_address)
        contract_instance = cls.get_instance(ledger_api, contract_address)
        hash = ledger_api.contract_method_call(
            contract_instance,
            "hashValues",
            did=did,
            reward_address=reward_address,
            token_address=token_address,
            amounts=amounts,
            receivers=receivers,
        )
        return dict(hash=hash)

    @classmethod
    def get_generate_id(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
        agreement_id: str,
        hash_value: str,
    ) -> JSONLike:
        """Get the hash values."""
        contract_address = ledger_api.api.to_checksum_address(contract_address)
        contract_instance = cls.get_instance(ledger_api, contract_address)
        condition_id = ledger_api.contract_method_call(
            contract_instance,
            "generateId",
            agreement_id=agreement_id,
            hash_value=hash_value,
        )
        return dict(condition_id=condition_id)
