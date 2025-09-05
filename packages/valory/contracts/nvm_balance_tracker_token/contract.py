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

"""This module contains the class to connect to a BalanceTrackerNvmSubscriptionToken contract."""

from aea.common import JSONLike
from aea.configurations.base import PublicId
from aea.contracts.base import Contract
from aea_ledger_ethereum import EthereumApi


PUBLIC_ID = PublicId.from_str("valory/nvm_balance_tracker_token:0.1.0")


class BalanceTrackerNvmSubscriptionToken(Contract):
    """The BalanceTrackerNvmSubscriptionToken contract."""

    contract_id = PUBLIC_ID

    @classmethod
    def get_balance(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
        address: str,
    ) -> JSONLike:
        """Get the balance of a requester."""
        contract_address = ledger_api.api.to_checksum_address(contract_address)
        contract_instance = cls.get_instance(ledger_api, contract_address)
        balance = contract_instance.functions.mapRequesterBalances(address).call()
        return dict(balance=balance)

    @classmethod
    def get_subscription_nft(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
    ) -> JSONLike:
        """Get the subscription NFT."""
        contract_address = ledger_api.api.to_checksum_address(contract_address)
        contract_instance = cls.get_instance(ledger_api, contract_address)
        address = ledger_api.contract_method_call(contract_instance, "subscriptionNFT")
        return dict(address=address)

    @classmethod
    def get_subscription_token_id(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
    ) -> JSONLike:
        """Get the subscription token id."""
        contract_address = ledger_api.api.to_checksum_address(contract_address)
        contract_instance = cls.get_instance(ledger_api, contract_address)
        id_ = ledger_api.contract_method_call(contract_instance, "subscriptionTokenId")
        return dict(id=id_)
