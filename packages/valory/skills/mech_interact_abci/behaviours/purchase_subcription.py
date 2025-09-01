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
import secrets
from typing import Any, Dict, Generator, List, Optional, Iterable, Union

from aea.common import JSONLike

from packages.valory.contracts.agreement_storage_manager.contract import (
    AgreementStorageManager,
)
from packages.valory.contracts.did_registry.contract import DIDRegistry
from packages.valory.contracts.erc20.contract import ERC20
from packages.valory.contracts.escrow_payment_condition.contract import (
    EscrowPaymentConditionContract,
)
from packages.valory.contracts.gnosis_safe.contract import (
    GnosisSafeContract,
    SafeOperation,
)
from packages.valory.contracts.lock_payment_condition.contract import (
    LockPaymentCondition,
)
from packages.valory.contracts.multisend.contract import MultiSendContract
from packages.valory.contracts.nft_sales.contract import NFTSalesTemplate
from packages.valory.contracts.subscription_provider.contract import (
    SubscriptionProvider,
)
from packages.valory.contracts.transfer_nft_condition.contract import (
    TransferNFTCondition,
)
from packages.valory.protocols.contract_api import ContractApiMessage
from packages.valory.skills.mech_interact_abci.behaviours.base import (
    MechInteractBaseBehaviour,
    WaitableConditionType,
    SAFE_GAS,
)
from packages.valory.skills.mech_interact_abci.models import NVMConfig, Ox
from packages.valory.skills.mech_interact_abci.states.request import (
    MechPurchaseSubscriptionRound,
)
from packages.valory.skills.transaction_settlement_abci.rounds import TX_HASH_LENGTH


EMPTY_PAYMENT_DATA_HEX = Ox
HTTP_OK = 200
SEED_BYTES_LENGTH = 32
DDO_ENDPOINT_IDX = 2
GET_METHOD = "GET"
DDO_ENDPOINT_HEADERS = {"accept": "application/json"}
SERVICE_KEY = "service"
SERVICE_TYPE_KEY = "type"
SERVICE_TYPE = "nft-sales"
OWNER_KEY = "owner"
RECEIVERS_PATH = (
    "attributes",
    "serviceAgreementTemplate",
    "conditions",
    0,
    "parameters",
    -1,
    "value",
)


def dig(
    data: JSONLike, path: Iterable[Union[str, int]], default: Optional[Any] = None
) -> Any:
    """Try to extract information from a JSON data structure, given a path of str or int indexes."""
    for key in path:
        try:
            data = data[key]
        except (KeyError, IndexError, TypeError):
            return default
    return data


class MechPurchaseSubscriptionBehaviour(MechInteractBaseBehaviour):
    """A behaviour in which the agents prepare a tx to initiate purchase subscription."""

    matching_round = MechPurchaseSubscriptionRound

    def __init__(self, **kwargs: Any) -> None:
        """Initialize Behaviour."""
        super().__init__(**kwargs)
        # Initialize protected attributes for properties
        self._agreement_id: Optional[bytes] = None
        self._agreement_id_seed: Optional[str] = None
        self._ddo_register: Optional[List] = None
        self._ddo_values: Optional[Dict] = None
        self._receivers: Optional[List[str]] = None
        self._lock_hash: Optional[bytes] = None
        self._lock_id: Optional[bytes] = None
        self._transfer_hash: Optional[bytes] = None
        self._transfer_id: Optional[bytes] = None
        self._escrow_hash: Optional[bytes] = None
        self._escrow_id: Optional[bytes] = None
        self._create_agreement_tx_data: Optional[str] = None
        self._subscription_token_approval_tx_data: Optional[str] = None
        self._create_fulfill_tx_data: Optional[str] = None

    @property
    def nvm_config(self) -> NVMConfig:
        """Return the NVM configuration for the specified mech chain id."""
        return self.params.nvm_config

    @property
    def ddo_register(self) -> Optional[List]:
        """Get the fetched ddo register."""
        if self._ddo_register is None:
            self.context.logger.error(
                "Accessing `ddo_register` before it has been fetched."
            )
        return self._ddo_register

    @property
    def ddo_endpoint(self) -> Optional[str]:
        """Return the ddo endpoint."""
        try:
            return self.ddo_register[DDO_ENDPOINT_IDX]
        except IndexError:
            self.context.logger.error(
                f"Cannot get ddo endpoint from the fetched {self.ddo_register=}."
            )
            return None

    @property
    def ddo_values(self) -> Optional[Dict]:
        """Get the fetched ddo values."""
        if self._ddo_values is None:
            self.context.logger.error(
                "Accessing `ddo_values` before it has been fetched."
            )
        return self._ddo_values

    @ddo_values.setter
    def ddo_values(self, ddo_values: Dict) -> None:
        """Set the fetched ddo values."""
        self._ddo_values = ddo_values

    @property
    def receivers(self) -> Optional[List[str]]:
        """Get the fetched receivers."""
        if self._receivers is None:
            self.context.logger.error(
                "Accessing `receivers` before it has been fetched."
            )
        return self._receivers

    @receivers.setter
    def receivers(self, receivers: List[str]) -> None:
        """Set the fetched receivers."""
        self._receivers = receivers

    @property
    def agreement_id_seed(self) -> Optional[str]:
        """Get the fetched agreement id seed."""
        if self._agreement_id_seed is None:
            self.context.logger.error(
                "Accessing `agreement_id_seed` before it has been generated."
            )
        return self._agreement_id_seed

    @agreement_id_seed.setter
    def agreement_id_seed(self, agreement_id_seed: str) -> None:
        """Set the `agreement_id_seed`."""
        self._agreement_id_seed = agreement_id_seed

    @property
    def agreement_id(self) -> Optional[bytes]:
        """Get the fetched agreement id."""
        if self._agreement_id is None:
            self.context.logger.error(
                "Accessing `agreement_id` before it has been fetched."
            )
        return self._agreement_id

    @property
    def lock_hash(self) -> Optional[bytes]:
        """Get the fetched lock hash."""
        if self._lock_hash is None:
            self.context.logger.error(
                "Accessing `_lock_hash` before it has been fetched."
            )
        return self._lock_hash

    @property
    def lock_id(self) -> Optional[bytes]:
        """Get the fetched lock id."""
        if self._lock_id is None:
            self.context.logger.error(
                "Accessing `_lock_id` before it has been fetched."
            )
        return self._lock_id

    @property
    def transfer_hash(self) -> Optional[bytes]:
        """Get the fetched transfer hash."""
        if self._transfer_hash is None:
            self.context.logger.error(
                "Accessing `_transfer_hash` before it has been fetched."
            )
        return self._transfer_hash

    @property
    def transfer_id(self) -> Optional[bytes]:
        """Get the fetched transfer id."""
        if self._transfer_id is None:
            self.context.logger.error(
                "Accessing `_transfer_id` before it has been fetched."
            )
        return self._transfer_id

    @property
    def escrow_hash(self) -> Optional[bytes]:
        """Get the fetched escrow hash."""
        if self._escrow_hash is None:
            self.context.logger.error(
                "Accessing `_escrow_hash` before it has been fetched."
            )
        return self._escrow_hash

    @property
    def escrow_id(self) -> Optional[bytes]:
        """Get the fetched escrow id."""
        if self._escrow_id is None:
            self.context.logger.error(
                "Accessing `_escrow_id` before it has been fetched."
            )
        return self._escrow_id

    @property
    def from_address(self) -> Optional[str]:
        """Get the from_address from the ddo values."""
        if not self.ddo_values or OWNER_KEY not in self.ddo_values:
            self.context.logger.error(
                f"{self.ddo_values=} attribute not set correctly after contract call."
            )
            return None

        return self.ddo_values[OWNER_KEY]

    @property
    def amounts(self) -> List[int]:
        """Get the amounts."""
        amounts = [self.nvm_config.plan_fee_nvm, self.nvm_config.plan_price_mech]
        return amounts

    @staticmethod
    def _generate_agreement_id_seed() -> str:
        """Generate a random agreement id seed prefixed with 0x."""
        return Ox + secrets.token_hex(SEED_BYTES_LENGTH)

    def _get_ddo_register(self) -> WaitableConditionType:
        """Get the ddo register from the did registry."""
        status = yield from self.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.did_registry_address,
            contract_public_id=DIDRegistry.contract_id,
            contract_callable="get_ddo",
            data_key="data",
            placeholder="_ddo_register",
            did=self.nvm_config.did,
            chain_id=self.params.mech_chain_id,
        )
        return status

    def _extract_and_set_receivers(self) -> None:
        """Extract and set the receivers."""
        service = next(
            (
                s
                for s in self.ddo_values.get(SERVICE_KEY, [])
                if s.get(SERVICE_TYPE_KEY) == SERVICE_TYPE
            ),
            None,
        )
        if not service:
            self.context.logger.error(f"No {SERVICE_TYPE} service found in DDO.")
            return

        self.context.logger.info(f"Fetched service from DDO: {service}")

        receivers = dig(
            service,
            RECEIVERS_PATH,
        )
        if receivers is None:
            self.context.logger.error(f"Could not get the receivers from {service=}.")

        self.receivers = receivers

    def _get_ddo_data(self) -> WaitableConditionType:
        """Get the ddo data from the did endpoint."""
        response = yield from self.get_http_response(
            GET_METHOD, self.ddo_endpoint, headers=DDO_ENDPOINT_HEADERS
        )

        # Handle HTTP errors
        if response.status_code != HTTP_OK:
            self.context.logger.error(
                f"Error while pulling the data from ddo endpoint: {response.body}"
            )
            return False

        # Load the response
        try:
            ddo = json.loads(response.body)
        except json.JSONDecodeError:
            self.context.logger.error(f"Failed to decode ddo: {response.body}.")
            return False

        self.context.logger.info(f"Fetched ddo endpoint data: {ddo}")
        self.ddo_values = ddo
        self._extract_and_set_receivers()
        if self.receivers is None:
            return False

        return True

    def _get_agreement_id(self) -> WaitableConditionType:
        """Get the agreement id."""
        status = yield from self.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.agreement_store_manager_address,
            contract_public_id=AgreementStorageManager.contract_id,
            contract_callable="get_agreement_id",
            data_key="agreement_id",
            placeholder="_agreement_id",
            agreement_id_seed=self.agreement_id_seed,
            subscriber=self.synchronized_data.safe_contract_address,
            chain_id=self.params.mech_chain_id,
        )
        return status

    def _lock_contract_interact(
        self, contract_callable: str, data_key: str, placeholder: str, **kwargs: Any
    ) -> WaitableConditionType:
        """Interact with the lock payment condition contract."""
        status = yield from self.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.lock_payment_condition_address,
            contract_public_id=LockPaymentCondition.contract_id,
            contract_callable=contract_callable,
            data_key=data_key,
            placeholder=placeholder,
            chain_id=self.params.mech_chain_id,
            **kwargs,
        )
        return status

    def _get_lock_hash(self) -> WaitableConditionType:
        """Get the lock hash."""
        status = yield from self._lock_contract_interact(
            contract_callable="get_hash_values",
            data_key="hash",
            placeholder="_lock_hash",
            did=self.nvm_config.did,
            reward_address=self.params.escrow_payment_condition_address,
            token_address=self.nvm_config.subscription_token_address,
            amounts=self.amounts,
            receivers=self.receivers,
            chain_id=self.params.mech_chain_id,
        )
        return status

    def _get_lock_id(self) -> WaitableConditionType:
        """Get the lock id."""
        status = yield from self._lock_contract_interact(
            contract_callable="get_generate_id",
            data_key="condition_id",
            placeholder="_lock_id",
            agreement_id=self.agreement_id,
            hash_value=self.lock_hash,
        )
        return status

    def _transfer_nft_interact(
        self, contract_callable: str, data_key: str, placeholder: str, **kwargs: Any
    ) -> WaitableConditionType:
        """Interact with the Transfer NFT condition contract."""
        status = yield from self.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.lock_payment_condition_address,
            contract_public_id=TransferNFTCondition.contract_id,
            contract_callable=contract_callable,
            data_key=data_key,
            placeholder=placeholder,
            chain_id=self.params.mech_chain_id,
            **kwargs,
        )
        return status

    def _get_transfer_nft_hash(self) -> WaitableConditionType:
        """Get the transfer nft hash."""
        status = yield from self._transfer_nft_interact(
            contract_callable="get_hash_values",
            data_key="hash",
            placeholder="_transfer_hash",
            did=self.nvm_config.did,
            from_address=self.from_address,
            to_address=self.synchronized_data.safe_contract_address,
            amount=self.nvm_config.subscription_credits,
            lock_condition_id=self.lock_id,
            nft_contract_address=self.nvm_config.subscription_nft_address,
            is_transfer=False,
        )
        return status

    def _get_transfer_id(self) -> WaitableConditionType:
        """Get the transfer id."""
        status = yield from self._transfer_nft_interact(
            contract_callable="get_generate_id",
            data_key="condition_id",
            placeholder="_transfer_id",
            agreement_id=self.agreement_id,
            hash_value=self.transfer_hash,
        )
        return status

    def _escrow_interact(
        self, contract_callable: str, data_key: str, placeholder: str, **kwargs: Any
    ) -> WaitableConditionType:
        """Interact with the escrow payment condition contract."""
        status = yield from self.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.lock_payment_condition_address,
            contract_public_id=EscrowPaymentConditionContract.contract_id,
            contract_callable=contract_callable,
            data_key=data_key,
            placeholder=placeholder,
            chain_id=self.params.mech_chain_id,
            **kwargs,
        )
        return status

    def _get_escrow_payment_hash(self) -> WaitableConditionType:
        """Get the escrow payment hash."""
        status = yield from self._escrow_interact(
            contract_callable="get_hash_values",
            data_key="hash",
            placeholder="_escrow_hash",
            did=self.nvm_config.did,
            amounts=self.amounts,
            receivers=self.receivers,
            sender=self.synchronized_data.safe_contract_address,
            receiver=self.params.escrow_payment_condition_address,
            token_address=self.nvm_config.subscription_token_address,
            lock_condition_id=self.lock_id,
            release_condition_id=self.transfer_id,
        )
        return status

    def _get_escrow_id(self) -> WaitableConditionType:
        """Get the escrow id."""
        status = yield from self._escrow_interact(
            contract_callable="get_generate_id",
            data_key="condition_id",
            placeholder="_escrow_id",
            agreement_id=self.agreement_id,
            hash_value=self.escrow_hash,
        )
        return status

    def _nft_sales_contract_interact(
        self, contract_callable: str, data_key: str, placeholder: str, **kwargs: Any
    ) -> WaitableConditionType:
        """Interact with the NFT Sales Template contract."""
        status = yield from self.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.nvm_config.nft_sales_address,
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

        self.context.logger.info(f"Fetched lock hash: {lock_hash.hex()}")

        transfer_hash = yield from self._get_transfer_nft_hash()
        if not transfer_hash:
            self.context.logger.error("Error fetching transfer nft hash.")
            return None

        self.context.logger.info(f"Fetched transfer nft hash: {transfer_hash.hex()}")

        escrow_hash = yield from self._get_escrow_payment_hash()
        if not escrow_hash:
            self.context.logger.error("Error fetching escrow payment hash.")
            return None

        self.context.logger.info(f"Fetched escrow payment hash: {escrow_hash.hex()}")

        status = self._nft_sales_contract_interact(
            contract_callable="build_create_agreement_tx",
            data_key="data",
            placeholder="_create_agreement_tx_data",
            agreement_id_seed=self.agreement_id_seed,
            did=self.nvm_config.did,
            condition_seeds=[lock_hash, transfer_hash, escrow_hash],
            timelocks=[0, 0, 0],
            timeouts=[0, 90, 0],
            publisher=self.synchronized_data.safe_contract_address,
            service_index=0,
            reward_address=self.params.escrow_payment_condition_address,
            token_address=self.nvm_config.subscription_token_address,
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
            contract_address=self.nvm_config.subscription_token_address,
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
            contract_address=self.nvm_config.subscription_provider_address,
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
            self.nvm_config.subscription_credits,
            # lockPaymentCondition
            Ox + lock_id.hex(),
            # nftContractAddress
            self.nvm_config.subscription_nft_address,
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
            # returnAddress # noqa: E800
            self.synchronized_data.safe_contract_address,
            # lockPaymentAddress
            self.params.escrow_payment_condition_address,
            # tokenAddress
            self.nvm_config.subscription_token_address,
            # lockCondition
            Ox + lock_id.hex(),
            # releaseCondition
            Ox + transfer_id.hex(),
        )
        self.context.logger.info(f"{fulfill_for_delegate_params=}")
        self.context.logger.info(f"{fulfill_params=}")
        status = self._subscription_provider_contract_interact(
            contract_callable="build_create_fulfill_tx",
            data_key="data",
            placeholder="_create_fulfill_tx_data",
            agreement_id_seed=self.agreement_id_seed,
            did=self.nvm_config.did,
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

    def _prepare_safe_tx(self) -> WaitableConditionType:
        """Prepare a multisend safe tx for sending requests to a mech and return the hex for the tx settlement skill."""
        # @todo

    def setup(self) -> None:
        """Setup the `MechPurchaseSubscriptionBehaviour` behaviour."""
        self.agreement_id_seed = self._generate_agreement_id_seed()

    def async_act(self) -> Generator:
        """Do the action."""
        # @todo
