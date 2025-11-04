# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2023-2025 Valory AG
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

"""This module contains the request state of the mech interaction abci app."""

import json
from dataclasses import asdict
from enum import Enum
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, Generator, List, Optional, cast

import multibase
import multicodec
from aea.configurations.data_types import PublicId
from aea.exceptions import AEAEnforceError
from aea.helpers.cid import to_v1
from hexbytes import HexBytes

from packages.valory.contracts.erc20.contract import ERC20
from packages.valory.contracts.ierc1155.contract import IERC1155
from packages.valory.contracts.nvm_balance_tracker_native.contract import (
    BalanceTrackerNvmSubscriptionNative,
)
from packages.valory.contracts.nvm_balance_tracker_token.contract import (
    BalanceTrackerNvmSubscriptionToken,
)
from packages.valory.protocols.contract_api import ContractApiMessage
from packages.valory.protocols.ledger_api.message import LedgerApiMessage
from packages.valory.skills.abstract_round_abci.base import get_name
from packages.valory.skills.abstract_round_abci.io_.store import SupportedFiletype
from packages.valory.skills.mech_interact_abci.behaviours.base import (
    MechInteractBaseBehaviour,
    WaitableConditionType,
)
from packages.valory.skills.mech_interact_abci.models import MultisendBatch
from packages.valory.skills.mech_interact_abci.payloads import (
    MechRequestPayload,
    PrepareTxPayload,
)
from packages.valory.skills.mech_interact_abci.states.base import (
    MechInteractionResponse,
    MechMetadata,
    SERIALIZED_EMPTY_LIST,
)
from packages.valory.skills.mech_interact_abci.states.request import MechRequestRound
from packages.valory.skills.mech_interact_abci.utils import DataclassEncoder


METADATA_FILENAME = "metadata.json"
V1_HEX_PREFIX = "f01"
Ox = "0x"
EMPTY_PAYMENT_DATA_HEX = Ox


class PaymentType(str, Enum):
    """Mech payment types."""

    NATIVE = "0xba699a34be8fe0e7725e93dcbce1701b0211a8ca61330aaeb8a05bf2ec7abed1"
    TOKEN = "0x3679d66ef546e66ce9057c4a052f317b135bc8e8c509638f7966edfd4fcf45e9"  # nosec B105
    NATIVE_NVM = "0x803dd08fe79d91027fc9024e254a0942372b92f3ccabc1bd19f4a5c2b251c316"
    TOKEN_NVM_USDC = "0x0d6fd99afa9c4c580fab5e341922c2a5c4b61d880da60506193d7bf88944dd14"  # nosec B105


NVM_PAYMENT_TYPES = frozenset({PaymentType.NATIVE_NVM, PaymentType.TOKEN_NVM_USDC})
PAYMENT_TYPE_TO_NVM_CONTRACT = {
    PaymentType.NATIVE_NVM: BalanceTrackerNvmSubscriptionNative.contract_id,
    PaymentType.TOKEN_NVM_USDC: BalanceTrackerNvmSubscriptionToken.contract_id,
}


class MechRequestBehaviour(MechInteractBaseBehaviour):
    """A behaviour in which the agents prepare a tx to initiate a request to a mech."""

    matching_round = MechRequestRound

    def __init__(self, **kwargs: Any) -> None:
        """Initialize Behaviour."""
        super().__init__(**kwargs)
        self._v1_hex_truncated: str = ""
        self._request_data: bytes = b""
        self._price: int = 0
        self._mech_requests: List[MechMetadata] = []
        self._pending_responses: List[MechInteractionResponse] = []

        # Initialize internal attributes that will hold on-chain values once fetched
        self.token_balance: int = 0
        self.wallet_balance: int = 0
        self._mech_payment_type: Optional[PaymentType] = None
        self._mech_max_delivery_rate: Optional[int] = None
        self._subscription_balance: Optional[int] = None
        self._nvm_balance: Optional[int] = None
        self._subscription_address: Optional[str] = None
        self._subscription_id: Optional[int] = None
        self._balance_tracker: Optional[str] = None
        self._approval_data: Optional[str] = None

    @property
    def metadata_filepath(self) -> str:
        """Get the filepath to the metadata."""
        return str(Path(mkdtemp()) / METADATA_FILENAME)

    @property
    def request_data(self) -> bytes:
        """Get the request data."""
        return self._request_data

    @request_data.setter
    def request_data(self, data: bytes) -> None:
        """Set the request data."""
        self._request_data = data

    @property
    def price(self) -> int:
        """Get the price."""
        return self._price

    @price.setter
    def price(self, price: int) -> None:
        """Set the price."""
        self._price = price

    @property
    def mech_payment_type(self) -> Optional[PaymentType]:
        """Get the fetched mech payment type."""
        if self._mech_payment_type is None:
            self.context.logger.error(
                "Accessing mech_payment_type before it has been fetched."
            )
        return self._mech_payment_type

    @mech_payment_type.setter
    def mech_payment_type(self, payment_type: str) -> None:
        """Set the fetched mech payment type."""
        try:
            self._mech_payment_type = PaymentType(payment_type)
        except ValueError:
            self.context.logger.warning(f"Unknown {payment_type=}.")

    @property
    def using_native(self) -> bool:
        """Whether we are using a native mech."""
        return self.mech_payment_type == PaymentType.NATIVE

    @property
    def using_token(self) -> bool:
        """Whether we are using a token mech."""
        return self.mech_payment_type == PaymentType.TOKEN

    @property
    def using_nevermined(self) -> bool:
        """Whether we are using a Nevermined mech."""
        return self.mech_payment_type in NVM_PAYMENT_TYPES

    @property
    def nvm_balance_tracker_contract_id(self) -> PublicId:
        """Get the NVM balance tracker contract id."""
        contract_id = PAYMENT_TYPE_TO_NVM_CONTRACT.get(self.mech_payment_type, None)
        if contract_id is None:
            raise ValueError(f"Unknown {self.mech_payment_type=}!")

        return contract_id

    @property
    def subscription_balance(self) -> Optional[int]:
        """Get the fetched token subscription balance."""
        if self._subscription_balance is None:
            self.context.logger.error(
                "Accessing `subscription_balance` before it has been fetched."
            )
        return self._subscription_balance

    @property
    def nvm_balance(self) -> Optional[int]:
        """Get the fetched NVM balance."""
        if self._nvm_balance is None:
            self.context.logger.error(
                "Accessing `_nvm_balance` before it has been fetched."
            )
        return self._nvm_balance

    @property
    def total_nvm_balance(self) -> Optional[int]:
        """Get the total NVM balance."""
        balance0 = self.subscription_balance
        balance1 = self.nvm_balance
        if balance0 is not None and balance1 is not None:
            return balance0 + balance1
        return None

    @property
    def subscription_address(self) -> Optional[str]:
        """Get the token subscription address."""
        if self._subscription_address is None:
            self.context.logger.error(
                "Accessing `_subscription_address` before it has been fetched."
            )
        return self._subscription_address

    @property
    def subscription_id(self) -> Optional[str]:
        """Get the subscription id."""
        if self._subscription_id is None:
            self.context.logger.error(
                "Accessing `_subscription_id` before it has been fetched."
            )
        return self._subscription_id

    @property
    def mech_max_delivery_rate(self) -> Optional[int]:
        """Get the fetched max delivery rate."""
        if self._mech_max_delivery_rate is None:
            self.context.logger.error(
                "Accessing mech_max_delivery_rate before it has been fetched."
            )
        return self._mech_max_delivery_rate

    @property
    def balance_tracker(self) -> Optional[str]:
        """Get the balance tracker."""
        if self._balance_tracker is None:
            self.context.logger.warning(
                "Accessing balance_tracker before it has been fetched."
            )
        return self._balance_tracker

    @property
    def approval_data(self) -> Optional[str]:
        """Get the approval data."""
        if self._approval_data is None:
            self.context.logger.warning(
                "Accessing approval_data before they have been built."
            )
        return self._approval_data

    @staticmethod
    def wei_to_unit(wei: int) -> float:
        """Convert WEI to unit token."""
        return wei / 10**18

    def _get_native_balance(self, account: str) -> Generator[None, None, Optional[int]]:
        """Get native balance for account."""
        ledger_api_response = yield from self.get_ledger_api_response(
            performative=LedgerApiMessage.Performative.GET_STATE,  # type: ignore
            ledger_callable="get_balance",
            block_identifier="latest",
            account=account,
            chain_id=self.params.mech_chain_id,
        )

        try:
            balance_result = ledger_api_response.state.body.get("get_balance_result")
            if balance_result is not None:
                if isinstance(balance_result, (int, str)):
                    balance = int(balance_result)
                else:
                    self.context.logger.error(
                        f"Invalid balance result type: {type(balance_result)}"
                    )
                    balance = None
            else:
                balance = None
        except (AEAEnforceError, KeyError, ValueError, TypeError):
            balance = None

        if balance is None:
            log_msg = f"Failed to get the native balance for account {account}."
            self.context.logger.error(f"{log_msg}: {ledger_api_response}")
            return None

        self.context.logger.info(
            f"Account {account} has {self.wei_to_unit(balance)} native tokens."
        )
        return balance

    def _get_token_balance(self, account: str) -> Generator[None, None, Optional[int]]:
        """Get the balance of an account for the given token."""

        token_address = (
            self.params.price_token
            if self.using_token
            else self.params.mech_wrapped_native_token_address
        )
        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=token_address,
            contract_id=str(ERC20.contract_id),
            contract_callable="check_balance",
            account=account,
            chain_id=self.params.mech_chain_id,
        )
        message = (
            f"Failed to get the {token_address} token's balance for account {account}."
        )
        if response_msg.performative != ContractApiMessage.Performative.RAW_TRANSACTION:
            self.context.logger.warning(f"{message} {response_msg}")
            return None

        token = response_msg.raw_transaction.body.get("token", None)
        wallet = response_msg.raw_transaction.body.get("wallet", None)
        if token is None or wallet is None:
            self.context.logger.warning(f"{message} {response_msg}")
            return None

        try:
            if isinstance(token, (int, str)):
                token_int = int(token)
            else:
                self.context.logger.warning(
                    f"Invalid token value type: {type(token)}. Expected int or str."
                )
                return None
        except (ValueError, TypeError):
            self.context.logger.warning(
                f"Invalid token value: {token}. Expected integer."
            )
            return None

        tokens_type = " wrapped native" if self.using_native else ""
        self.context.logger.info(
            f"Account {account} has {self.wei_to_unit(token_int)}{tokens_type} tokens."
        )
        return token_int

    def update_safe_balances(self) -> WaitableConditionType:
        """Check the safe's balance."""
        account = self.synchronized_data.safe_contract_address

        if self.using_native:
            wallet = yield from self._get_native_balance(account)
            if wallet is None:
                return False
            self.wallet_balance = int(wallet)

        token = yield from self._get_token_balance(account)
        if token is None:
            return False

        self.token_balance = int(token)
        return True

    def _build_unwrap_tokens_tx(self) -> WaitableConditionType:
        """Exchange wrapped native tokens to native tokens."""

        if not self.params.mech_wrapped_native_token_address:
            return True

        # A total of price - wallet_balance wrapped native tokens are required
        amount = self.price - self.wallet_balance
        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,  # type: ignore
            contract_address=self.params.mech_wrapped_native_token_address,
            contract_id=str(ERC20.contract_id),
            contract_callable="build_withdraw_tx",
            amount=amount,
            chain_id=self.params.mech_chain_id,
        )

        if response_msg.performative != ContractApiMessage.Performative.STATE:
            self.context.logger.info(f"Could not build withdraw tx: {response_msg}")
            return False

        withdraw_data = response_msg.state.body.get("data")
        if withdraw_data is None:
            self.context.logger.info(f"Could not build withdraw tx: {response_msg}")
            return False

        try:
            if isinstance(withdraw_data, str) or isinstance(
                withdraw_data, (bytes, bytearray)
            ):
                hex_data = HexBytes(withdraw_data)
            else:
                hex_data = HexBytes(str(withdraw_data))
        except (ValueError, TypeError) as e:
            self.context.logger.error(
                f"Could not convert withdraw_data to HexBytes: {e}"
            )
            return False

        batch = MultisendBatch(
            to=self.params.mech_wrapped_native_token_address,
            data=hex_data,
        )
        self.multisend_batches.append(batch)
        self.context.logger.info(f"Built transaction to unwrap {amount} tokens.")
        return True

    def _nvm_balance_tracker_contract_interact(
        self, contract_callable: str, data_key: str, placeholder: str, **kwargs: Any
    ) -> WaitableConditionType:
        """Interact with the NVM balance tracker contract."""
        status = yield from self.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.nvm_config.balance_tracker_address,
            contract_public_id=self.nvm_balance_tracker_contract_id,
            contract_callable=contract_callable,
            data_key=data_key,
            placeholder=placeholder,
            chain_id=self.params.mech_chain_id,
            **kwargs,
        )
        return status

    def get_nvm_balance(self) -> WaitableConditionType:
        """Get the NVM balance."""
        status = yield from self._nvm_balance_tracker_contract_interact(
            contract_callable="get_balance",
            data_key="balance",
            placeholder="_nvm_balance",
            address=self.synchronized_data.safe_contract_address,
        )
        return status

    def get_subscription_nft(self) -> WaitableConditionType:
        """Get the subscription NFT."""
        status = yield from self._nvm_balance_tracker_contract_interact(
            contract_callable="get_subscription_nft",
            data_key="address",
            placeholder="_subscription_address",
        )
        return status

    def get_subscription_token_id(self) -> WaitableConditionType:
        """Get the subscription NFT."""
        status = yield from self._nvm_balance_tracker_contract_interact(
            contract_callable="get_subscription_token_id",
            data_key="id",
            placeholder="_subscription_id",
        )
        return status

    def _subscription_contract_interact(
        self, contract_callable: str, data_key: str, placeholder: str, **kwargs: Any
    ) -> WaitableConditionType:
        """Interact with the subscription contract."""
        status = yield from self.contract_interact(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.subscription_address,
            contract_public_id=IERC1155.contract_id,
            contract_callable=contract_callable,
            data_key=data_key,
            placeholder=placeholder,
            chain_id=self.params.mech_chain_id,
            **kwargs,
        )
        return status

    def get_subscription_balance(self) -> WaitableConditionType:
        """Get the subscription's balance."""
        status = yield from self._subscription_contract_interact(
            contract_callable="get_balance",
            data_key="balance",
            placeholder="_subscription_balance",
            account=self.synchronized_data.safe_contract_address,
            subscription_id=self.subscription_id,
        )
        return status

    def set_total_nvm_balance(self) -> Generator:
        """Get teh total NVM balance."""
        steps = [
            self.get_nvm_balance,
            self.get_subscription_nft,
            self.get_subscription_token_id,
            self.get_subscription_balance,
        ]
        for step in steps:
            yield from self.wait_for_condition_with_sleep(step)

    def _ensure_available_balance(self) -> WaitableConditionType:
        """
        Ensures available payment for the mech request and unwraps tokens if needed.

        This method does not check the balance tracker as for native payments the agent will never send a surplus.
        The balance tracker is considered for NVM payments only.

        Assuming that no one sends money outside the mech-interact logic,
        the balance tracker will always return zero.
        The assumption is sensible,
        since mech-interact always sends the value corresponding to the mech's delivery rate.
        Therefore, we agreed on this simplified path to reduce contract calls.
        """
        yield from self.wait_for_condition_with_sleep(self.update_safe_balances)

        price = self.price if self.using_native else self.mech_max_delivery_rate

        # There is enough balance using native tokens
        if self.using_native and price <= self.wallet_balance:
            return True

        # There is enough balance using native and wrapped tokens
        if self.using_native and price <= self.wallet_balance + self.token_balance:
            yield from self.wait_for_condition_with_sleep(self._build_unwrap_tokens_tx)
            return True

        # There is enough balance using token payment method
        if price <= self.token_balance:
            return True

        # the wallet balance will be 0 if using token payment method,
        # therefore the following calculation stands for both cases
        shortage = price - self.wallet_balance - self.token_balance

        if self.using_native and self.params.mech_wrapped_native_token_address:
            missing_tokens = "native or wrapped native"
        elif self.using_native:
            missing_tokens = "native"
        else:
            missing_tokens = self.params.price_token

        self.context.logger.warning(
            "The balance is not enough to pay for the mech's price. "
            f"Please refill the safe with at least {self.wei_to_unit(shortage)} {missing_tokens} tokens."
        )
        self.sleep(self.params.sleep_time)
        return False

    def setup(self) -> None:
        """Set up the `MechRequest` behaviour."""
        self._mech_requests = self.synchronized_data.mech_requests
        self.context.logger.info(f"Processing mech requests: {self._mech_requests}")

    def _send_metadata_to_ipfs(
        self,
    ) -> WaitableConditionType:
        """Send Mech metadata to IPFS."""
        metadata = self._mech_requests.pop()
        metadata_hash = yield from self.send_to_ipfs(
            self.metadata_filepath, asdict(metadata), filetype=SupportedFiletype.JSON
        )
        if metadata_hash is None:
            return False

        v1_file_hash = to_v1(metadata_hash)
        cid_bytes = cast("bytes", multibase.decode(v1_file_hash))
        multihash_bytes = multicodec.remove_prefix(cid_bytes)
        v1_file_hash_hex = V1_HEX_PREFIX + multihash_bytes.hex()
        ipfs_link = self.params.ipfs_address + v1_file_hash_hex
        self.context.logger.info(f"Prompt uploaded: {ipfs_link}")
        mech_request_data = v1_file_hash_hex[9:]
        pending_response = MechInteractionResponse(
            nonce=metadata.nonce, data=mech_request_data
        )
        self._v1_hex_truncated = Ox + mech_request_data
        self._pending_responses.append(pending_response)
        return True

    def _get_payment_type(self) -> WaitableConditionType:
        """Get payment type from the mech contract. Returns True on success, False otherwise."""
        status = yield from self._mech_mm_contract_interact(
            contract_callable="get_payment_type",
            data_key="payment_type",
            placeholder=get_name(MechRequestBehaviour.mech_payment_type),
            chain_id=self.params.mech_chain_id,
        )
        if not status:
            self.context.logger.error("Failed to get payment type from contract")
            return False

        # Verify the attribute was set (optional, property handles None)
        if self.mech_payment_type is None:
            self.context.logger.error(
                "Payment type attribute not set correctly after contract call."
            )
            return False

        self.context.logger.info(f"Payment type fetched: {self.mech_payment_type}")
        return True

    def _get_max_delivery_rate(self) -> WaitableConditionType:
        """Get max delivery rate from the mech contract. Returns True on success, False otherwise."""
        status = yield from self._mech_mm_contract_interact(
            contract_callable="get_max_delivery_rate",
            data_key="max_delivery_rate",
            placeholder="_mech_max_delivery_rate",  # Store in private attribute
            chain_id=self.params.mech_chain_id,
        )
        if not status:
            # This might be acceptable depending on the contract.
            self.context.logger.warning(
                "Failed step: Could not get max delivery rate. Proceeding without it."
            )
            # No need to explicitly set attribute to None, property will return None
            return True  # Return True as the step itself didn't fail catastrophically, just didn't get the value

        # Verify the attribute was set (optional, property handles None)
        if self.mech_max_delivery_rate is None:
            self.context.logger.error(
                "Max delivery rate attribute not set correctly after contract call."
            )
            # Even if status was True, if the value is None unexpectedly, treat as failure
            return False

        self.context.logger.info(
            f"Max delivery rate fetched: {self.mech_max_delivery_rate}"
        )
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

    def _fetch_and_validate_payment_type(self) -> WaitableConditionType:
        """Fetch and validate the payment type from the contract."""
        if not self.should_use_marketplace_v2():
            return True

        self.context.logger.info("Getting payment type")
        if not (yield from self._get_payment_type()):
            self.context.logger.error("Failed step: Could not get payment type.")
            return False

        if self.mech_payment_type is None:
            self.context.logger.error(
                "Payment type was not successfully fetched or is unexpectedly None."
            )
            return False

        return True

    def _fetch_and_validate_max_delivery_rate(self) -> WaitableConditionType:
        """Fetch and validate the max delivery rate from the contract."""
        self.context.logger.info("Getting max delivery rate")
        if not (yield from self._get_max_delivery_rate()):
            self.context.logger.warning(
                "Failed step: Could not get max delivery rate. Proceeding without it, which might cause issues."
            )
            # The original logic considered missing max_delivery_rate an error for marketplace.
            self.context.logger.error(
                "Max delivery rate is required for marketplace request but was not fetched. Cannot build request data."
            )
            return False

        # Although _get_max_delivery_rate handles logging if the attribute isn't set,
        # we double-check here to be explicit about the requirement for this flow.
        if self.mech_max_delivery_rate is None:
            self.context.logger.error(
                "Max delivery rate is required for marketplace request but is None. Cannot build request data."
            )
            return False

        if self.using_native:
            self.price = self.mech_max_delivery_rate

        return True

    def _get_balance_tracker(self) -> WaitableConditionType:
        """Get the balance tracker for the mech."""
        self.context.logger.info("Getting balance tracker...")

        return (
            yield from self._mech_marketplace_contract_interact(
                contract_callable="get_balance_tracker",
                data_key="balance_tracker",
                placeholder="_balance_tracker",
                payment_type=self.mech_payment_type.value,
                chain_id=self.params.mech_chain_id,
            )
        )

    def _approve_balance_tracker(self) -> WaitableConditionType:
        """Build approval for the balance tracker."""
        self.context.logger.info("Building approval for token payment.")

        return (
            yield from self.contract_interact(
                performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
                contract_address=self.params.price_token,
                contract_public_id=ERC20.contract_id,
                contract_callable="build_approval_tx",
                data_key="data",
                placeholder="_approval_data",
                spender=self.balance_tracker,
                amount=self.mech_max_delivery_rate,
                chain_id=self.params.mech_chain_id,
            )
        )

    def _build_token_approval(self) -> WaitableConditionType:
        """Get the balance tracker, build approval for the token payment and add it to the multisend batch."""
        if not self._balance_tracker:
            status = yield from self._get_balance_tracker()
            if not status:
                self.context.logger.warning("Failed to get balance tracker.")
                return False

        status = yield from self._approve_balance_tracker()
        if not status:
            self.context.logger.error("Failed to build approval data.")
            return False

        batch = MultisendBatch(
            to=self.params.price_token,
            data=HexBytes(self.approval_data),
        )
        self.multisend_batches.append(batch)
        self.context.logger.info("Successfully built approval data.")
        return True

    def _build_marketplace_v2_request_data(self) -> WaitableConditionType:
        """Build the request data for the Mech Marketplace v2 flow using helper methods."""
        if self.using_token:
            status = yield from self._build_token_approval()
            if not status:
                return False

        self.context.logger.info("Building request data for Mech Marketplace v2 flow.")

        request_data_bytes = self._decode_hex_to_bytes(
            self._v1_hex_truncated, "request_data"
        )
        if request_data_bytes is None:
            return False

        payment_data_bytes = self._decode_hex_to_bytes(
            EMPTY_PAYMENT_DATA_HEX, "payment_data"
        )
        if payment_data_bytes is None:
            return False

        # Call the contract to get the encoded request data
        status = yield from self._mech_marketplace_contract_interact(
            contract_callable="get_request_data",
            data_key="data",
            placeholder=get_name(MechRequestBehaviour.request_data),
            request_data=request_data_bytes,
            priority_mech=self.priority_mech_address,
            payment_data=payment_data_bytes,
            payment_type=self.mech_payment_type.value,
            response_timeout=self.mech_marketplace_config.response_timeout,
            chain_id=self.params.mech_chain_id,
            max_delivery_rate=self.mech_max_delivery_rate,
        )
        return status

    def _build_marketplace_v1_request_data(self) -> WaitableConditionType:
        """Build request data for marketplace v1 (legacy marketplace without payment_type)."""
        self.context.logger.info("Building request data for legacy marketplace flow.")

        status = yield from self._mech_marketplace_legacy_contract_interact(
            "get_request_data",
            "data",
            get_name(MechRequestBehaviour.request_data),
            request_data=self._v1_hex_truncated,
            priority_mech=self.priority_mech_address,
            priority_mech_staking_instance=self.mech_marketplace_config.priority_mech_staking_instance_address,
            priority_mech_service_id=self.mech_marketplace_config.priority_mech_service_id,
            requester_staking_instance=self.mech_marketplace_config.requester_staking_instance_address,
            requester_service_id=self.params.on_chain_service_id,
            response_timeout=self.mech_marketplace_config.response_timeout,
            chain_id=self.params.mech_chain_id,
        )
        return status

    def _build_legacy_request_data(self) -> WaitableConditionType:
        """Build the request data for the legacy Mech flow."""
        self.context.logger.info("Building request data for legacy Mech flow.")
        # Legacy mech logic
        status = yield from self._mech_contract_interact(
            "get_request_data",
            "data",
            get_name(MechRequestBehaviour.request_data),
            request_data=self._v1_hex_truncated,  # Note: Legacy mech expects hex string
            chain_id=self.params.mech_chain_id,
        )
        return status

    def _build_request_data(self) -> WaitableConditionType:
        """Build the request data by dispatching to the appropriate method."""
        self.context.logger.info("Building request data")

        # Perform compatibility check if marketplace is enabled
        if self.params.use_mech_marketplace:
            # Use detected compatibility instead of static flag
            if self.should_use_marketplace_v2():
                self.context.logger.info("Using detected marketplace v2 flow")
                status = yield from self._build_marketplace_v2_request_data()
            else:
                self.context.logger.info("Using detected marketplace v1 (legacy) flow")
                status = yield from self._build_marketplace_v1_request_data()
        else:
            self.context.logger.info("Using direct mech flow (marketplace disabled)")
            status = yield from self._build_legacy_request_data()

        if status:
            to = self.priority_mech_address
            batch = MultisendBatch(
                to=to,
                data=HexBytes(self.request_data),
                value=self.price,
            )
            self.multisend_batches.append(batch)
            self.context.logger.info(
                f"Successfully built request data and added batch for address {to}."
            )
        else:
            self.context.logger.error("Failed to build request data.")

        return status

    def _get_price(self) -> WaitableConditionType:
        """Get the price of the mech request."""
        if self.should_use_marketplace_v2():
            return (yield from self._fetch_and_validate_max_delivery_rate())

        result = yield from self._mech_contract_interact(
            "get_price",
            "price",
            get_name(MechRequestBehaviour.price),
            chain_id=self.params.mech_chain_id,
        )
        return result

    def _prepare_safe_tx(self) -> Generator[None, None, bool]:
        """Prepare a multisend safe tx for sending requests to a mech and return the hex for the tx settlement skill."""
        steps = (
            self._fetch_and_validate_payment_type,
            self._get_price,
        )
        for step in steps:
            yield from self.wait_for_condition_with_sleep(step)

        steps = []
        if self.using_nevermined:
            yield from self.set_total_nvm_balance()
            if self.total_nvm_balance < self.mech_max_delivery_rate:
                # if the total nvm balance is not enough, we should stop and return to buy a subscription first
                return True
        else:
            steps.append(self._ensure_available_balance)

        n_iters = min(self.params.multisend_batch_size, len(self._mech_requests))
        steps.extend((self._send_metadata_to_ipfs, self._build_request_data) * n_iters)
        steps.extend((self._build_multisend_data, self._build_multisend_safe_tx_hash))
        for step in steps:
            yield from self.wait_for_condition_with_sleep(step)

        return False

    def async_act(self) -> Generator:
        """Do the action."""

        if not self._mech_requests:
            with self.context.benchmark_tool.measure(self.behaviour_id).local():
                payload = MechRequestPayload(
                    self.context.agent_address,
                    self.matching_round.auto_round_id(),
                    None,
                    None,
                    self.params.mech_chain_id,
                    self.synchronized_data.safe_contract_address,
                    SERIALIZED_EMPTY_LIST,
                    SERIALIZED_EMPTY_LIST,
                )
            yield from self.finish_behaviour(payload)
            return

        with self.context.benchmark_tool.measure(self.behaviour_id).local():
            self.context.logger.info(f"Preparing mech requests: {self._mech_requests}")
            should_buy_subscription = yield from self._prepare_safe_tx()

        if should_buy_subscription:
            payload = MechRequestPayload(
                self.context.agent_address,
                *(None,)
                * (
                    len(MechRequestPayload.__annotations__)
                    + len(PrepareTxPayload.__annotations__)
                ),
            )
            yield from self.finish_behaviour(payload)
            return

        with self.context.benchmark_tool.measure(self.behaviour_id).local():
            serialized_requests = json.dumps(self._mech_requests, cls=DataclassEncoder)
            serialized_responses = json.dumps(
                self._pending_responses, cls=DataclassEncoder
            )

            self.context.logger.info(
                f"Preparing mech request:\ntx_hex: {self.tx_hex}\nprice: {self.price}\n"
                f"delivery rate: {self.mech_max_delivery_rate}\n"
                f"serialized_requests: {serialized_requests}\nserialized_responses: {serialized_responses}\n"
            )
            payload = MechRequestPayload(
                self.context.agent_address,
                self.matching_round.auto_round_id(),
                self.tx_hex,
                self.price,
                self.params.mech_chain_id,
                self.synchronized_data.safe_contract_address,
                serialized_requests,
                serialized_responses,
            )
        yield from self.finish_behaviour(payload)
