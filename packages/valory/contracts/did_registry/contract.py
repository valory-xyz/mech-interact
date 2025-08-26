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

"""This module contains the class to connect to a DIDRegistry contract."""

from aea.common import JSONLike
from aea.configurations.base import PublicId
from aea.contracts.base import Contract
from aea_ledger_ethereum import EthereumApi


PUBLIC_ID = PublicId.from_str("valory/did_registry:0.1.0")


class DIDRegistry(Contract):
    """The DIDRegistry contract."""

    contract_id = PUBLIC_ID

    @classmethod
    def get_ddo(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
        did: str,
    ) -> JSONLike:
        """Get the balance of a requester."""
        contract_address = ledger_api.api.to_checksum_address(contract_address)
        contract_instance = cls.get_instance(ledger_api, contract_address)
        registered_values = ledger_api.contract_method_call(
            contract_instance, "getDIDRegister", did=did
        )
        return dict(data=registered_values)
