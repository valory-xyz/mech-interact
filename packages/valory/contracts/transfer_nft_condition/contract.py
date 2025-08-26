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

"""This module contains the class to connect to a TransferNFTCondition contract."""

from typing import List
from aea.common import JSONLike
from aea.configurations.base import PublicId
from aea.contracts.base import Contract
from aea_ledger_ethereum import EthereumApi


PUBLIC_ID = PublicId.from_str("valory/transfer_nft_condition:0.1.0")


class TransferNFTCondition(Contract):
    """The TransferNFTCondition contract."""

    contract_id = PUBLIC_ID

    @classmethod
    def get_hash_values(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
        did: str,
        from_address: str,
        to_address: str,
        amount: int,
        lock_condition_id: bytes,
        nft_contract_address: str,
        _is_transfer: bool,
    ) -> JSONLike:
        """Get the hash values."""
        contract_address = ledger_api.api.to_checksum_address(contract_address)
        contract_instance = cls.get_instance(ledger_api, contract_address)
        hash = ledger_api.contract_method_call(
            contract_instance,
            "hashValues",
            did=did,
            from_address=from_address,
            to_address=to_address,
            amount=amount,
            lock_condition_id=lock_condition_id,
            nft_contract_address=nft_contract_address,
            _is_transfer=_is_transfer,
        )
        return dict(hash=hash.hex())

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
