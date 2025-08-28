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

"""This module contains the class to connect to a EscrowPaymentConditionContract contract."""

from typing import List
from aea.common import JSONLike
from aea.configurations.base import PublicId
from aea.contracts.base import Contract
from aea_ledger_ethereum import EthereumApi


PUBLIC_ID = PublicId.from_str("valory/escrow_payment_condition:0.1.0")


class EscrowPaymentConditionContract(Contract):
    """The EscrowPaymentConditionContract contract."""

    contract_id = PUBLIC_ID

    @classmethod
    def get_hash_values(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
        did: str,
        amounts: List[int],
        receivers: List[str],
        sender: str,
        receiver: str,
        token_address: str,
        lock_condition_id: bytes,
        release_condition_id: bytes,
    ) -> JSONLike:
        """Get the hash values."""
        contract_address = ledger_api.api.to_checksum_address(contract_address)
        contract_instance = cls.get_instance(ledger_api, contract_address)
        hash_ = ledger_api.contract_method_call(
            contract_instance,
            "hashValues",
            _did=did,
            _amounts=amounts,
            _receivers=receivers,
            _returnAddress=sender,
            _lockPaymentAddress=receiver,
            _tokenAddress=token_address,
            _lockCondition=lock_condition_id,
            _releaseCondition=release_condition_id,
        )
        return dict(hash=hash_)

    @classmethod
    def get_generate_id(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
        agreement_id: str,
        hash_value: str,
    ) -> JSONLike:
        """Get the id."""
        contract_address = ledger_api.api.to_checksum_address(contract_address)
        contract_instance = cls.get_instance(ledger_api, contract_address)
        condition_id = ledger_api.contract_method_call(
            contract_instance,
            "generateId",
            _agreementId=agreement_id,
            _valueHash=hash_value,
        )
        return dict(condition_id=condition_id)
