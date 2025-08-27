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

"""This module contains the purchase subscription of the mech interaction abci app."""

import json
import uuid
from dataclasses import asdict
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, Generator, List, Optional, Dict, cast

import multibase
import multicodec
from aea.configurations.data_types import PublicId
from aea.exceptions import AEAEnforceError
from aea.helpers.cid import to_v1
from hexbytes import HexBytes

from packages.valory.contracts.erc20.contract import ERC20
from packages.valory.contracts.gnosis_safe.contract import (
    GnosisSafeContract,
    SafeOperation,
)
from packages.valory.contracts.did_registry.contract import DIDRegistry
from packages.valory.contracts.agreement_storage_manager.contract import (
    AgreementStorageManager,
)
from packages.valory.contracts.lock_payment_condition.contract import (
    LockPaymentCondition,
)
from packages.valory.contracts.transfer_nft_condition.contract import (
    TransferNFTCondition,
)
from packages.valory.contracts.escrow_payment_condition.contract import (
    EscrowPaymentConditionContract,
)
from packages.valory.contracts.nft_sales.contract import NFTSalesTemplate
from packages.valory.contracts.subscription_provider.contract import (
    SubscriptionProvider,
)
from packages.valory.contracts.multisend.contract import MultiSendContract
from packages.valory.protocols.contract_api import ContractApiMessage
from packages.valory.skills.abstract_round_abci.base import get_name
from packages.valory.skills.abstract_round_abci.io_.store import SupportedFiletype
from packages.valory.skills.mech_interact_abci.behaviours.base import (
    MechInteractBaseBehaviour,
    WaitableConditionType,
)
from packages.valory.skills.mech_interact_abci.models import MultisendBatch
from packages.valory.skills.mech_interact_abci.payloads import MechRequestPayload
from packages.valory.skills.mech_interact_abci.states.base import (
    MechInteractionResponse,
    MechMetadata,
    SERIALIZED_EMPTY_LIST,
)
from packages.valory.skills.mech_interact_abci.states.request import (
    MechPurchaseSubscriptionRound,
)
from packages.valory.skills.mech_interact_abci.utils import DataclassEncoder
from packages.valory.skills.transaction_settlement_abci.payload_tools import (
    hash_payload_to_hex,
)
from packages.valory.skills.transaction_settlement_abci.rounds import TX_HASH_LENGTH


Ox = "0x"
EMPTY_PAYMENT_DATA_HEX = Ox
HTTP_OK = 200

# setting the safe gas to 0 means that all available gas will be used
# which is what we want in most cases
# more info here: https://safe-docs.dev.gnosisdev.com/safe/docs/contracts_tx_execution/
SAFE_GAS = 0


class MechPurchaseSubscriptionBehaviour(MechInteractBaseBehaviour):
    """A behaviour in which the agents prepare a tx to initiate purchase subscription."""

    matching_round = MechPurchaseSubscriptionRound

    def __init__(self, **kwargs: Any) -> None:
        """Initialize Behaviour."""
        super().__init__(**kwargs)
        # Initialize private attributes for properties
        self._agreement_id: Optional[bytes] = None
        self._agreement_id_seed: Optional[str] = None
        self._ddo_values: Optional[List] = None
        self._service: Optional[Dict] = None
        self._create_agreement_tx_data: Optional[str] = None
        self._subscription_token_approval_tx_data: Optional[str] = None
        self._create_fulfill_tx_data: Optional[str] = None

    @property
    def safe_tx_hash(self) -> str:
        """Get the safe_tx_hash."""
        return self._safe_tx_hash

    @safe_tx_hash.setter
    def safe_tx_hash(self, safe_hash: str) -> None:
        """Set the safe_tx_hash."""
        length = len(safe_hash)
        if length != TX_HASH_LENGTH:
            raise ValueError(
                f"Incorrect length {length} != {TX_HASH_LENGTH} detected "
                f"when trying to assign a safe transaction hash: {safe_hash}"
            )
        self._safe_tx_hash = safe_hash[2:]

    @property
    def multi_send_txs(self) -> List[dict]:
        """Get the multisend transactions as a list of dictionaries."""
        return [asdict(batch) for batch in self.multisend_batches]

    @property
    def txs_value(self) -> int:
        """Get the total value of the transactions."""
        return sum(batch.value for batch in self.multisend_batches)

    @property
    def tx_hex(self) -> Optional[str]:
        """Serialize the safe tx to a hex string."""
        if self.safe_tx_hash == "":
            raise ValueError(
                "Cannot prepare a multisend transaction without a safe transaction hash."
            )
        return hash_payload_to_hex(
            self.safe_tx_hash,
            self.txs_value,
            SAFE_GAS,
            self.params.multisend_address,
            self.multisend_data,
            SafeOperation.DELEGATE_CALL.value,
        )

    @property
    def ddo_values(self) -> Optional[List]:
        """Get the fetched ddo values."""
        if self._ddo_values is None:
            self.context.logger.error(
                "Accessing `ddo_values` before it has been fetched."
            )
        return self._ddo_values

    @property
    def service(self) -> Optional[List]:
        """Get the fetched ddo values."""
        if self._service is None:
            self.context.logger.error("Accessing `service` before it has been fetched.")
        return self._service

    @property
    def agreement_id(self) -> Optional[bytes]:
        """Get the fetched agreement id."""
        if self._agreement_id is None:
            self.context.logger.error(
                "Accessing `agreement_id` before it has been fetched."
            )
        return self._agreement_id

    @property
    def agreement_id_seed(self) -> Optional[str]:
        """Get the fetched agreement id."""
        if self._agreement_id_seed is None:
            self.context.logger.error(
                "Accessing `agreement_id_seed` before it has been fetched."
            )
        return self._agreement_id_seed

    @property
    def receivers(self) -> Optional[List]:
        """Get the receivers from service."""
        if not self.service:
            self.context.logger.error(
                "service attribute not set correctly after contract call."
            )
            return None

        self.context.logger.info(
            f"Fetched service data from ddo endpoint: {self.service}"
        )

        conditions = self.service["attributes"]["serviceAgreementTemplate"][
            "conditions"
        ]
        receivers = conditions[0]["parameters"][-1]["value"]
        return receivers

    @property
    def from_address(self) -> Optional[str]:
        """Get the from_address from service."""
        if not self.ddo_values:
            self.context.logger.error(
                "ddo_values attribute not set correctly after contract call."
            )
            return None

        from_address = self.ddo_values["owner"]
        return from_address

    @property
    def amounts(self) -> List:
        """Get the amounts."""
        amounts = [self.params.plan_fee_nvm, self.params.plan_price_mech]
        return amounts

    @staticmethod
    def wei_to_unit(wei: int) -> float:
        """Convert WEI to unit token."""
        return wei / 10**18

    @staticmethod
    def _generate_agreement_id_seed(self, length: int = 64) -> str:
        """Generate a random hex string prefixed with 0x."""
        seed = ""
        while len(seed) < length:
            seed += uuid.uuid4().hex
        return "0x" + seed[:length]

    def _get_ddo_data_from_endpoint(self) -> Generator[None, None, bool]:
        """Get the ddo data from endpoint."""
        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.did_registry_address,
            contract_public_id=str(DIDRegistry.public_id),
            contract_callable="get_ddo",
            did=self.params.did,
            chain_id=self.params.mech_chain_id,
        )
        if response_msg.performative != ContractApiMessage.Performative.RAW_TRANSACTION:
            self.context.logger.error(f"get_ddo unsuccessful: {response_msg}")
            return False

        data = response_msg.raw_transaction.body.get("data", None)
        placeholder = "_ddo_values"
        setattr(self, placeholder, data)

        if not self.ddo_values:
            self.context.logger.error(
                "ddo_values attribute not set correctly after contract call."
            )
            return False

        ddo_endpoint = self.ddo_values[2]
        if not ddo_endpoint:
            self.context.logger.error(
                f"Cannot fetch ddo endpoint from ddo values: {self._ddo_values}"
            )
            return False

        self.context.logger.info(f"Fetched ddo endpoint: {ddo_endpoint}")
        headers = {"accept": "application/json"}

        response = yield from self.get_http_response(
            method="GET", url=ddo_endpoint, headers=headers
        )

        # Handle HTTP errors
        if response.status_code != HTTP_OK:
            self.context.logger.error(
                f"Error while pulling the data from ddo endpoint: {response.body}"
            )
            return False

        # Load the response
        ddo = json.loads(response.body)
        self.context.logger.info(f"Fetched ddo endpoint data: {ddo}")
        placeholder = "_service"
        setattr(self, placeholder, ddo)

        service = next(
            (s for s in ddo.get("service", []) if s.get("type") == "nft-sales"), None
        )
        if not service:
            self.context.logger.error("No nft-sales service found in DDO")
            return False

        return True

    def _agreement_store_manager_contract_interact(
        self, contract_callable: str, data_key: str, placeholder: str, **kwargs: Any
    ) -> WaitableConditionType:
        """Interact with the Agreement Storage Manager contract."""
        status = yield from self.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.agreement_store_manager_address,
            contract_public_id=AgreementStorageManager.contract_id,
            contract_callable=contract_callable,
            data_key=data_key,
            placeholder=placeholder,
            **kwargs,
        )
        return status

    def get_agreement_id(self):
        """Get the agreement id."""
        agreement_id_seed = self._generate_agreement_id_seed()
        placeholder = "_agreement_id_seed"
        setattr(self, placeholder, agreement_id_seed)

        if self.agreement_id_seed is None:
            self.context.logger.error(
                "Agreement id seed attribute not set correctly after call."
            )
            return None

        self.context.logger.info(f"Fetched agreement id: {self.agreement_id_seed}")

        status = self._agreement_store_manager_contract_interact(
            contract_callable="get_agreement_id",
            data_key="agreement_id",
            placeholder="_agreement_id",
            agreement_id_seed=self.agreement_id_seed,
            subscriber=self.synchronized_data.safe_contract_address,
            chain_id=self.params.mech_chain_id,
        )
        return status

    def _get_lock_hash(self) -> Generator[None, None, Optional[bytes]]:
        """Get the lock hash."""
        if not self.receivers:
            self.context.logger.error(
                "receivers attribute not set correctly after contract call."
            )
            return None

        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.lock_payment_condition_address,
            contract_public_id=str(LockPaymentCondition.contract_id),
            contract_callable="get_hash_values",
            did=self.params.did,
            reward_address=self.params.escrow_payment_condition_address,
            token_address=self.params.token_address,
            amounts=self.amounts,
            receivers=self.receivers,
            chain_id=self.params.mech_chain_id,
        )
        if response_msg.performative != ContractApiMessage.Performative.RAW_TRANSACTION:
            self.context.logger.error(f"get_hash_values unsuccessful: {response_msg}")
            return None

        lock_hash = response_msg.raw_transaction.body.get("hash", None)
        return lock_hash

    def _get_lock_id(self) -> Generator[None, None, Optional[bytes]]:
        """Get the lock id."""
        lock_hash = yield from self._get_lock_hash()
        if not lock_hash:
            self.context.logger.error("Error fetching lock_hash")
            return None

        self.context.logger.info(f"Fetched lock hash: {lock_hash.hex()}")

        if self.agreement_id is None:
            self.context.logger.error(
                "Agreement id attribute not set correctly after contract call."
            )
            return None

        self.context.logger.info(f"Fetched agreement id: {self.agreement_id}")

        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.lock_payment_condition_address,
            contract_public_id=str(LockPaymentCondition.contract_id),
            contract_callable="get_generate_id",
            agreement_id=self.agreement_id,
            hash_value=lock_hash,
            chain_id=self.params.mech_chain_id,
        )
        if response_msg.performative != ContractApiMessage.Performative.RAW_TRANSACTION:
            self.context.logger.error(f"get_hash_values unsuccessful: {response_msg}")
            return None

        lock_id = response_msg.raw_transaction.body.get("condition_id", None)
        return lock_id

    def _get_transfer_nft_hash(self) -> Generator[None, None, Optional[bytes]]:
        """Get the transfer nft hash."""
        if not self.from_address:
            self.context.logger.error(
                "from_address attribute not set correctly after contract call."
            )
            return None

        lock_id = yield from self._get_lock_id()
        if not lock_id:
            self.context.logger.error("Error fetching lock id.")
            return None

        self.context.logger.info(f"Fetched lock id: {lock_id.hex()}")

        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.lock_payment_condition_address,
            contract_public_id=str(LockPaymentCondition.contract_id),
            contract_callable="get_hash_values",
            did=self.params.did,
            from_address=self.from_address,
            to_address=self.synchronized_data.safe_contract_address,
            amount=self.params.subscription_credits,
            lock_condition_id=lock_id,
            nft_contract_address=self.params.subscription_nft_address,
            _is_transfer=False,
            chain_id=self.params.mech_chain_id,
        )
        if response_msg.performative != ContractApiMessage.Performative.RAW_TRANSACTION:
            self.context.logger.error(
                f"LockPaymentCondition: get_hash_values unsuccessful: {response_msg}"
            )
            return None

        transfer_hash = response_msg.raw_transaction.body.get("hash", None)
        return transfer_hash

    def _get_transfer_id(self) -> Generator[None, None, Optional[bytes]]:
        """Get the transfer id."""
        transfer_hash = yield from self._get_transfer_nft_hash()
        if not transfer_hash:
            self.context.logger.error("Error fetching transfer_hash")
            return None

        self.context.logger.info(f"Fetched transfer hash: {transfer_hash.hex()}")

        if self.agreement_id is None:
            self.context.logger.error(
                "Agreement id attribute not set correctly after contract call."
            )
            return None

        self.context.logger.info(f"Fetched agreement id: {self.agreement_id}")

        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.lock_payment_condition_address,
            contract_public_id=str(TransferNFTCondition.contract_id),
            contract_callable="get_generate_id",
            agreement_id=self.agreement_id,
            hash_value=transfer_hash,
            chain_id=self.params.mech_chain_id,
        )
        if response_msg.performative != ContractApiMessage.Performative.RAW_TRANSACTION:
            self.context.logger.error(
                f"TransferNFTCondition: get_hash_values unsuccessful: {response_msg}"
            )
            return None

        transfer_id = response_msg.raw_transaction.body.get("condition_id", None)
        return transfer_id

    def _get_escrow_payment_hash(self) -> Generator[None, None, Optional[bytes]]:
        """Get the escrow payment hash."""
        if not self.receivers:
            self.context.logger.error(
                "receivers attribute not set correctly after contract call."
            )
            return None

        lock_id = yield from self._get_lock_id()
        if not lock_id:
            self.context.logger.error("Error fetching lock id.")
            return None

        self.context.logger.info(f"Fetched lock id: {lock_id.hex()}")

        transfer_id = yield from self._get_transfer_id()
        if not transfer_id:
            self.context.logger.error("Error fetching transfer id.")
            return None
        self.context.logger.info(f"Fetched transfer id: {transfer_id.hex()}")

        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.lock_payment_condition_address,
            contract_public_id=str(EscrowPaymentConditionContract.contract_id),
            contract_callable="get_hash_values",
            did=self.params.did,
            amounts=self.amounts,
            receivers=self.receivers,
            sender=self.synchronized_data.safe_contract_address,
            receiver=self.params.escrow_payment_condition_address,
            token_address=self.params.token_address,
            lock_condition_id=lock_id,
            release_condition_id=transfer_id,
            chain_id=self.params.mech_chain_id,
        )
        if response_msg.performative != ContractApiMessage.Performative.RAW_TRANSACTION:
            self.context.logger.error(
                f"EscrowPaymentConditionContract: get_hash_values unsuccessful: {response_msg}"
            )
            return None

        escrow_hash = response_msg.raw_transaction.body.get("hash", None)
        return escrow_hash

    def _get_escrow_id(self) -> Generator[None, None, Optional[str]]:
        """Get the escrow id."""
        escrow_payment_hash = yield from self._get_escrow_payment_hash()
        if not escrow_payment_hash:
            self.context.logger.error("Error fetching escrow_payment_hash")
            return None

        self.context.logger.info(f"Fetched escrow hash: {escrow_payment_hash.hex()}")

        if self.agreement_id is None:
            self.context.logger.error(
                "Agreement id attribute not set correctly after contract call."
            )
            return None

        self.context.logger.info(f"Fetched agreement id: {self.agreement_id}")

        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.lock_payment_condition_address,
            contract_public_id=str(EscrowPaymentConditionContract.contract_id),
            contract_callable="get_generate_id",
            agreement_id=self.agreement_id,
            hash_value=escrow_payment_hash,
            chain_id=self.params.mech_chain_id,
        )
        if response_msg.performative != ContractApiMessage.Performative.RAW_TRANSACTION:
            self.context.logger.error(
                f"EscrowPaymentConditionContract: get_generate_id unsuccessful: {response_msg}"
            )
            return None

        escrow_payment_id = response_msg.raw_transaction.body.get("condition_id", None)
        return escrow_payment_id

    def _nft_sales_contract_interact(
        self, contract_callable: str, data_key: str, placeholder: str, **kwargs: Any
    ) -> WaitableConditionType:
        """Interact with the NFT Sales Template contract."""
        status = yield from self.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.nft_sales_address,
            contract_public_id=NFTSalesTemplate.contract_id,
            contract_callable=contract_callable,
            data_key=data_key,
            placeholder=placeholder,
            **kwargs,
        )
        return status

    def _build_create_agreement_tx_data(self):
        """Builds the create agreement tx data on nft sales template contract."""
        if self.agreement_id_seed is None:
            self.context.logger.error(
                "Agreement id seed attribute not set correctly after contract call."
            )
            return None

        self.context.logger.info(f"Fetched agreement id: {self.agreement_id_seed}")

        lock_hash = yield from self._get_lock_hash()
        if not lock_hash:
            self.context.logger.error("Error fetching lock hash.")
            return None

        self.context.logger.info(f"Fetched lock hash: {lock_hash}")

        transfer_hash = yield from self._get_transfer_nft_hash()
        if not transfer_hash:
            self.context.logger.error("Error fetching transfer nft hash.")
            return None

        self.context.logger.info(f"Fetched transfer nft hash: {transfer_hash}")

        escrow_hash = yield from self._get_escrow_payment_hash()
        if not escrow_hash:
            self.context.logger.error("Error fetching escrow payment hash.")
            return None

        self.context.logger.info(f"Fetched escrow payment hash: {escrow_hash}")

        status = self._nft_sales_contract_interact(
            contract_callable="build_create_agreement_tx",
            data_key="data",
            placeholder="_create_agreement_tx_data",
            agreement_id_seed=self.agreement_id_seed,
            did=self.params.did,
            condition_seeds=[lock_hash, transfer_hash, escrow_hash],
            timelocks=[0, 0, 0],
            timeouts=[0, 90, 0],
            publisher=self.synchronized_data.safe_contract_address,
            service_index=0,
            reward_address=self.params.escrow_payment_condition_address,
            token_address=self.params.token_address,
            amounts=self.amounts,
            receivers=self.receivers,
            chain_id=self.params.mech_chain_id,
        )
        return status

    def _token_contract_interact(
        self, contract_callable: str, data_key: str, placeholder: str, **kwargs: Any
    ) -> WaitableConditionType:
        """Interact with the NFT Sales Template contract."""
        status = yield from self.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.subscription_token_address,
            contract_public_id=ERC20.contract_id,
            contract_callable=contract_callable,
            data_key=data_key,
            placeholder=placeholder,
            **kwargs,
        )
        return status

    # only required for base chain (usdc is the payment token for subscription)
    def _build_subscription_token_approval_tx_data(self):
        status = yield from self._token_contract_interact(
            contract_callable="build_approval_tx",
            placeholder="_subscription_token_approval_tx_data",
            data_key="data",
            spender=self.params.lock_payment_condition_address,
            amount=10**6,
            chain_id=self.params.mech_chain_id,
        )
        return status

    def _subscription_provider_contract_interact(
        self, contract_callable: str, data_key: str, placeholder: str, **kwargs: Any
    ) -> WaitableConditionType:
        """Interact with the Subscription Provider contract."""
        status = yield from self.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.subscription_provider_address,
            contract_public_id=SubscriptionProvider.contract_id,
            contract_callable=contract_callable,
            data_key=data_key,
            placeholder=placeholder,
            **kwargs,
        )
        return status

    def _build_create_fulfill_tx_data(self):
        if not self.from_address:
            self.context.logger.error(
                "from_address attribute not set correctly after contract call."
            )
            return None

        lock_id = yield from self._get_lock_id()
        if not lock_id:
            self.context.logger.error("Error fetching lock_id")
            return None

        self.context.logger.info(f"Fetched lock id: {lock_id.hex()}")

        if not self.receivers:
            self.context.logger.error(
                "receivers attribute not set correctly after contract call."
            )
            return None

        transfer_id = yield from self._get_transfer_id()
        if not transfer_id:
            self.context.logger.error("Error fetching transfer_id")
            return None

        self.context.logger.info(f"Fetched transfer id: {transfer_id.hex()}")

        fulfill_for_delegate_params = (
            # nftHolder
            self.from_address,
            # nftReceiver
            self.synchronized_data.safe_contract_address,
            # nftAmount
            self.params.subscription_credits,
            # lockPaymentCondition
            "0x" + lock_id.hex(),
            # nftContractAddress
            self.params.subscription_nft_address,
            # transfer
            False,
            # expirationBlock
            0,
        )
        fulfill_params = (
            # amounts
            self.amounts,
            # receivers
            self.receivers,
            # returnAddress
            self.synchronized_data.safe_contract_address,
            # lockPaymentAddress
            self.params.escrow_payment_condition_address,
            # tokenAddress
            self.params.token_address,
            # lockCondition
            "0x" + lock_id.hex(),
            # releaseCondition
            "0x" + transfer_id.hex(),
        )
        status = self._subscription_provider_contract_interact(
            contract_callable="build_create_fulfill_tx",
            data_key="data",
            placeholder="_create_fulfill_tx_data",
            agreement_id_seed=self.agreement_id_seed,
            did=self.params.did,
            fulfill_for_delegate_params=fulfill_for_delegate_params,
            fulfill_params=fulfill_params,
            chain_id=self.params.mech_chain_id,
        )
        return status

    def _build_multisend_data(
        self,
    ) -> WaitableConditionType:
        """Get the multisend tx."""
        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.multisend_address,
            contract_id=str(MultiSendContract.contract_id),
            contract_callable="get_tx_data",
            multi_send_txs=self.multi_send_txs,
            chain_id=self.params.mech_chain_id,
        )
        expected_performative = ContractApiMessage.Performative.RAW_TRANSACTION
        if response_msg.performative != expected_performative:
            self.context.logger.error(
                f"Couldn't compile the multisend tx. "
                f"Expected response performative {expected_performative.value}, "  # type: ignore
                f"received {response_msg.performative.value}: {response_msg}"
            )
            return False

        multisend_data_str = response_msg.raw_transaction.body.get("data", None)
        if multisend_data_str is None:
            self.context.logger.error(
                f"Something went wrong while trying to prepare the multisend data: {response_msg}"
            )
            return False

        # strip "0x" from the response
        multisend_data_str = str(response_msg.raw_transaction.body["data"])[2:]
        self.multisend_data = bytes.fromhex(multisend_data_str)
        return True

    def _build_multisend_safe_tx_hash(self) -> WaitableConditionType:
        """Prepares and returns the safe tx hash for a multisend tx."""
        self.context.logger.info(
            f"Building multisend safe tx hash: safe={self.synchronized_data.safe_contract_address}"
        )
        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,  # type: ignore
            contract_address=self.synchronized_data.safe_contract_address,
            contract_id=str(GnosisSafeContract.contract_id),
            contract_callable="get_raw_safe_transaction_hash",
            to_address=self.params.multisend_address,
            value=self.txs_value,
            data=self.multisend_data,
            safe_tx_gas=SAFE_GAS,
            operation=SafeOperation.DELEGATE_CALL.value,
            chain_id=self.params.mech_chain_id,
        )

        if response_msg.performative != ContractApiMessage.Performative.STATE:
            self.context.logger.error(
                "Couldn't get safe tx hash. Expected response performative "
                f"{ContractApiMessage.Performative.STATE.value}, "  # type: ignore
                f"received {response_msg.performative.value}: {response_msg}."
            )
            return False

        tx_hash = response_msg.state.body.get("tx_hash", None)
        if (
            tx_hash is None
            or not isinstance(tx_hash, str)
            or len(tx_hash) != TX_HASH_LENGTH
        ):
            self.context.logger.error(
                "Something went wrong while trying to get the buy transaction's hash. "
                f"Invalid hash {tx_hash!r} was returned."
            )
            return False

        self.safe_tx_hash = str(tx_hash)
        return True

    def setup(self) -> None:
        """Set up the `Purchase NVM Subscription` behaviour."""
        self.context.logger.info("Purchase NVM Subscription behaviour...")

    def _decode_hex_to_bytes(self, hex_string: str, data_name: str) -> Optional[bytes]:
        """Decode a hex string to bytes, handling potential errors."""
        try:
            return bytes.fromhex(
                hex_string[2:] if hex_string.startswith(Ox) else hex_string
            )
        except (ValueError, TypeError) as e:
            self.context.logger.error(
                f"Failed to decode {data_name} {hex_string!r}: {e}"
            )
            return None

    def _prepare_safe_tx(self) -> Generator[None, None, bool]:
        """Prepare a multisend safe tx for sending requests to a mech and return the hex for the tx settlement skill."""
        # @todo

    def async_act(self) -> Generator:
        """Do the action."""
        # @todo
