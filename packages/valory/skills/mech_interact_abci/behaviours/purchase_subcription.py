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
from typing import Any, Dict, Generator, List, Optional, Iterable, Union, Tuple

from aea.common import JSONLike
from autonomy.chain.config import ChainType
from hexbytes import HexBytes

from packages.valory.contracts.agreement_storage_manager.contract import (
    AgreementStorageManager,
)
from packages.valory.contracts.did_registry.contract import DIDRegistry
from packages.valory.contracts.erc20.contract import ERC20
from packages.valory.contracts.escrow_payment_condition.contract import (
    EscrowPaymentConditionContract,
)
from packages.valory.contracts.lock_payment_condition.contract import (
    LockPaymentCondition,
)
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
)
from packages.valory.skills.mech_interact_abci.models import (
    NVMConfig,
    Ox,
    MultisendBatch,
)
from packages.valory.skills.mech_interact_abci.payloads import PrepareTxPayload
from packages.valory.skills.mech_interact_abci.states.request import (
    MechPurchaseSubscriptionRound,
)

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
TIMELOCKS = [0, 0, 0]
TIMEOUTS = [0, 90, 0]
SERVICE_INDEX = 0
SUBSCRIPTION_COST = 10**6
EXPIRATION_BLOCK = 0


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
        self._agreement_tx_data: Optional[str] = None
        self._subscription_token_approval_tx_data: Optional[str] = None
        self._fulfill_tx_data: Optional[str] = None

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

    @property
    def agreement_tx_data(self) -> Optional[HexBytes]:
        """Get the built create-agreement tx data."""
        if self._agreement_tx_data is None:
            self.context.logger.error(
                "Accessing `_agreement_tx_data` before they have been built."
            )
        return HexBytes(self._agreement_tx_data)

    @property
    def subscription_token_approval_tx_data(self) -> Optional[HexBytes]:
        """Get the built subscription token approval tx data."""
        if self._subscription_token_approval_tx_data is None:
            self.context.logger.error(
                "Accessing `_subscription_token_approval_tx_data` before they have been built."
            )
        return HexBytes(self._subscription_token_approval_tx_data)

    @property
    def fulfill_tx_data(self) -> Optional[HexBytes]:
        """Get the built fulfill tx data."""
        if self._fulfill_tx_data is None:
            self.context.logger.error(
                "Accessing `_fulfill_tx_data` before they have been built."
            )
        return HexBytes(self._fulfill_tx_data)

    @property
    def fulfill_for_delegate_params(self) -> Tuple[str, str, int, str, str, bool, int]:
        """Get the fulfill for delegate parameters."""
        return (
            # nftHolder
            self.from_address,
            # nftReceiver
            self.synchronized_data.safe_contract_address,
            # nftAmount
            self.nvm_config.subscription_credits,
            # lockPaymentCondition
            Ox + self.lock_id.hex(),
            # nftContractAddress
            self.nvm_config.subscription_nft_address,
            # transfer
            False,
            # expirationBlock
            EXPIRATION_BLOCK,
        )

    def fulfill_params(self) -> Tuple[List[int], List[str], str, str, str, str, str]:
        """Get the fulfill parameters."""
        return (
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
            Ox + self.lock_id.hex(),
            # releaseCondition
            Ox + self.transfer_id.hex(),
        )

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

    def _transfer_nft_contract_interact(
        self, contract_callable: str, data_key: str, placeholder: str, **kwargs: Any
    ) -> WaitableConditionType:
        """Interact with the Transfer NFT condition contract."""
        status = yield from self.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.transfer_nft_condition_address,
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
        status = yield from self._transfer_nft_contract_interact(
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
        status = yield from self._transfer_nft_contract_interact(
            contract_callable="get_generate_id",
            data_key="condition_id",
            placeholder="_transfer_id",
            agreement_id=self.agreement_id,
            hash_value=self.transfer_hash,
        )
        return status

    def _escrow_contract_interact(
        self, contract_callable: str, data_key: str, placeholder: str, **kwargs: Any
    ) -> WaitableConditionType:
        """Interact with the escrow payment condition contract."""
        status = yield from self.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.escrow_payment_condition_address,
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
        status = yield from self._escrow_contract_interact(
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
        status = yield from self._escrow_contract_interact(
            contract_callable="get_generate_id",
            data_key="condition_id",
            placeholder="_escrow_id",
            agreement_id=self.agreement_id,
            hash_value=self.escrow_hash,
        )
        return status

    def _build_create_agreement_tx_data(self) -> WaitableConditionType:
        """Builds the create-agreement tx data on NFT sales template contract."""
        status = yield from self.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.nvm_config.nft_sales_address,
            contract_public_id=NFTSalesTemplate.contract_id,
            contract_callable="build_create_agreement_tx",
            data_key="data",
            placeholder="_agreement_tx_data",
            agreement_id_seed=self.agreement_id_seed,
            did=self.nvm_config.did,
            condition_seeds=[self.lock_hash, self.transfer_hash, self.escrow_hash],
            timelocks=TIMELOCKS,
            timeouts=TIMEOUTS,
            publisher=self.synchronized_data.safe_contract_address,
            service_index=SERVICE_INDEX,
            reward_address=self.params.escrow_payment_condition_address,
            token_address=self.nvm_config.subscription_token_address,
            amounts=self.amounts,
            receivers=self.receivers,
            chain_id=self.params.mech_chain_id,
        )
        if not status:
            self.context.logger.error("Failed to build create-agreement tx data.")
            return False

        batch = MultisendBatch(
            to=self.nvm_config.nft_sales_address,
            data=self.agreement_tx_data,
        )
        self.multisend_batches.append(batch)
        self.context.logger.info(f"Built transaction to create agreement.")
        return True

    def _build_subscription_token_approval_tx_data(self) -> WaitableConditionType:
        """
        Build a subscription token approval tx.

        This is only required for the base chain as USDC is the payment token for the subscription there.
        """
        status = yield from self.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.nvm_config.subscription_token_address,
            contract_public_id=ERC20.contract_id,
            contract_callable="build_approval_tx",
            data_key="data",
            placeholder="_subscription_token_approval_tx_data",
            spender=self.params.lock_payment_condition_address,
            amount=SUBSCRIPTION_COST,
            chain_id=self.params.mech_chain_id,
        )
        if not status:
            self.context.logger.error("Failed to build data for a USDC approval tx.")
            return False

        batch = MultisendBatch(
            to=self.nvm_config.subscription_token_address,
            data=self.subscription_token_approval_tx_data,
        )
        self.multisend_batches.append(batch)
        self.context.logger.info(f"Built transaction to approve USDC spending.")
        return True

    def _build_create_fulfill_tx_data(self):
        self.context.logger.info(
            f"Creating a fulfill tx with {self.fulfill_for_delegate_params=} and {self.fulfill_params=}."
        )
        status = self.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.nvm_config.subscription_provider_address,
            contract_public_id=SubscriptionProvider.contract_id,
            contract_callable="build_create_fulfill_tx",
            data_key="data",
            placeholder="_create_fulfill_tx_data",
            agreement_id_seed=self.agreement_id_seed,
            did=self.nvm_config.did,
            fulfill_for_delegate_params=self.fulfill_for_delegate_params,
            fulfill_params=self.fulfill_params,
            chain_id=self.params.mech_chain_id,
        )
        if not status:
            self.context.logger.error("Failed to build data for a fulfill tx.")
            return False

        batch = MultisendBatch(
            to=self.nvm_config.subscription_provider_address,
            data=self.fulfill_tx_data,
        )
        self.multisend_batches.append(batch)
        self.context.logger.info(f"Built transaction to fulfill.")
        return True

    def _get_approval_steps(self) -> List[WaitableConditionType]:
        """Get the approval steps, if necessary, otherwise return an empty list."""
        return (
            [self._build_subscription_token_approval_tx_data]
            if ChainType(self.params.mech_chain_id) == ChainType.BASE
            else []
        )

    def _prepare_safe_tx(self) -> Generator:
        """Prepare a multisend safe tx for buying an NVM subscription."""
        steps = [
            self._get_ddo_register,
            self._get_ddo_data,
            self._get_agreement_id,
            self._get_lock_hash,
            self._get_lock_id,
            self._get_transfer_nft_hash,
            self._get_transfer_id,
            self._get_escrow_payment_hash,
            self._get_escrow_id,
            self._build_create_agreement_tx_data,
            *self._get_approval_steps(),
            self._build_create_fulfill_tx_data,
            self._build_multisend_data,
            self._build_multisend_safe_tx_hash,
        ]

        for step in steps:
            yield from self.wait_for_condition_with_sleep(step)

    def setup(self) -> None:
        """Setup the `MechPurchaseSubscriptionBehaviour` behaviour."""
        self.agreement_id_seed = self._generate_agreement_id_seed()

    def async_act(self) -> Generator:
        """Do the action."""
        with self.context.benchmark_tool.measure(self.behaviour_id).local():
            self.context.logger.info(f"Preparing a multisend transaction to buy an NVM subscription.")
            yield from self._prepare_safe_tx()
            tx_hex = self.tx_hex
            submitter = None if tx_hex is None else self.matching_round.auto_round_id()
            payload = PrepareTxPayload(
                self.context.agent_address,
                submitter,
                tx_hex,
            )
        yield from self.finish_behaviour(payload)
