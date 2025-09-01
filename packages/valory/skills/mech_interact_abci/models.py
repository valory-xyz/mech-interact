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

"""This module contains the models for the abci skill of MechInteractAbciApp."""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from aea.exceptions import enforce
from hexbytes import HexBytes

from autonomy.chain.config import ChainType
from autonomy.chain.service import NULL_ADDRESS

from packages.valory.contracts.multisend.contract import MultiSendOperation
from packages.valory.skills.abstract_round_abci.models import ApiSpecs, BaseParams
from packages.valory.skills.abstract_round_abci.models import (
    BenchmarkTool as BaseBenchmarkTool,
)
from packages.valory.skills.abstract_round_abci.models import Requests as BaseRequests
from packages.valory.skills.abstract_round_abci.models import (
    SharedState as BaseSharedState,
)
from packages.valory.skills.mech_interact_abci.rounds import MechInteractAbciApp


Requests = BaseRequests
BenchmarkTool = BaseBenchmarkTool


PLAN_DID_PREFIX = "did:nv:"
Ox = "0x"


@dataclass
class NVMConfig:
    """NVM configuration."""

    plan_fee_nvm: int
    plan_price_mech: int
    subscription_credits: int
    subscription_nft_address: str
    nft_sales_address: str
    subscription_token_address: str
    subscription_provider_address: str
    plan_did: str

    @property
    def did(self) -> str:
        """Get the did."""
        return self.plan_did.replace(PLAN_DID_PREFIX, Ox)


# false positives for [B105:hardcoded_password_string] Possible hardcoded password
CHAIN_TO_NVM_CONFIG = {
    ChainType.GNOSIS: NVMConfig(  # nosec
        plan_fee_nvm=10000000000000000,
        plan_price_mech=990000000000000000,
        subscription_credits=1000000,
        subscription_nft_address="0x1b5DeaD7309b56ca7663b3301A503e077Be18cba",
        nft_sales_address="0x72201948087aE83f8Eac22cf7A9f2139e4cFA829",
        subscription_token_address="0x0000000000000000000000000000000000000000",
        subscription_provider_address="0x4a2f40E14309c20c0C3803c3CcCd5E9B5F2D4eCA",
        plan_did="did:nv:b0b28402e5a7229804579d4ac55b98a1dd94660d7a7eb4add78e5ca856f2aab7",
    ),
    ChainType.BASE: NVMConfig(  # nosec
        plan_fee_nvm=10000,
        plan_price_mech=990000,
        subscription_credits=1000000,
        subscription_nft_address="0xd5318d1A17819F65771B6c9277534C08Dd765498",
        nft_sales_address="0x468dC6d758129c4563005B49aC58DfF2e6f7F08e",
        subscription_token_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        subscription_provider_address="0x5050c577583D25Ff9C9492A39e8D1B94028ffA55",
        plan_did="did:nv:6f74c18fae7e5c3589b99d7cd0ba317593f00dee53c81a2ba4ac2244232f99da",
    ),
}


class MechResponseSpecs(ApiSpecs):
    """A model that wraps ApiSpecs for the Mech's response specifications."""


class SharedState(BaseSharedState):
    """Keep the current shared state of the skill."""

    abci_app_cls = MechInteractAbciApp


@dataclass(frozen=True)
class MechMarketplaceConfig:
    """The configuration for the Mech marketplace."""

    mech_marketplace_address: str
    priority_mech_address: str
    priority_mech_staking_instance_address: Optional[str]
    priority_mech_service_id: Optional[int]
    requester_staking_instance_address: Optional[str]
    response_timeout: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MechMarketplaceConfig":
        """Create an instance from a dictionary."""
        if not data["priority_mech_staking_instance_address"]:
            data["priority_mech_staking_instance_address"] = NULL_ADDRESS

        if not data["requester_staking_instance_address"]:
            data["requester_staking_instance_address"] = NULL_ADDRESS

        if not data["priority_mech_service_id"]:
            data["priority_mech_service_id"] = 975

        return cls(
            mech_marketplace_address=data["mech_marketplace_address"],
            priority_mech_address=data["priority_mech_address"],
            priority_mech_staking_instance_address=data[
                "priority_mech_staking_instance_address"
            ],
            priority_mech_service_id=data.get("priority_mech_service_id"),
            requester_staking_instance_address=data[
                "requester_staking_instance_address"
            ],
            response_timeout=data["response_timeout"],
        )

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if not self.mech_marketplace_address:
            raise ValueError("mech_marketplace_address cannot be empty")
        if not self.priority_mech_address:
            raise ValueError("priority_mech_address cannot be empty")
        if self.response_timeout <= 0:
            raise ValueError("response_timeout must be positive")


class MechParams(BaseParams):
    """The mech interact abci skill's parameters.

    This class manages all configuration parameters for the mech interaction
    system, including marketplace settings, compatibility detection, caching,
    and transaction parameters. It provides validation and utility methods
    for robust configuration management.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Set up the mech-interaction parameters.

        Args:
            *args: Positional arguments passed to BaseParams
            **kwargs: Keyword arguments containing configuration values

        Raises:
            ValueError: If required parameters are missing or invalid
        """
        multisend_address = kwargs.get("multisend_address")
        enforce(multisend_address is not None, "Multisend address not specified!")
        self.multisend_address: str = multisend_address
        self.multisend_batch_size: int = self._ensure(
            "multisend_batch_size", kwargs, int
        )
        self.mech_contract_address: str = self._ensure(
            "mech_contract_address", kwargs, str
        )
        self.mech_request_price: Optional[int] = kwargs.get("mech_request_price")
        self._ipfs_address: str = self._ensure("ipfs_address", kwargs, str)
        self.mech_chain_id: str = kwargs.get("mech_chain_id", "gnosis")
        self.mech_wrapped_native_token_address: Optional[str] = kwargs.get(
            "mech_wrapped_native_token_address"
        )
        self.mech_interaction_sleep_time: int = self._ensure(
            "mech_interaction_sleep_time", kwargs, int
        )
        self.use_mech_marketplace: bool = self._ensure("use_mech_marketplace", kwargs, bool)
        self.mech_marketplace_config: MechMarketplaceConfig = (
            MechMarketplaceConfig.from_dict(kwargs["mech_marketplace_config"])
        )
        self.agent_registry_address: str = kwargs.get("agent_registry_address")
        enforce(
            self.agent_registry_address is not None,
            "Agent registry address not specified!",
        )
        self.use_acn_for_delivers: bool = self._ensure("use_acn_for_delivers", kwargs, bool)
        self.nvm_balance_tracker_address: str = self._ensure(
            "nvm_balance_tracker_address", kwargs, str
        )
        self.did_registry_address: str = self._ensure("did_registry_address", kwargs, str)
        self.agreement_store_manager_address: str = self._ensure(
            "agreement_store_manager_address", kwargs, str
        )
        self.lock_payment_condition_address: str = self._ensure(
            "lock_payment_condition_address", kwargs, str
        )
        self.transfer_nft_condition_address: str = self._ensure(
            "transfer_nft_condition_address", kwargs, str
        )
        self.escrow_payment_condition_address: str = self._ensure(
            "escrow_payment_condition_address", kwargs, str
        )

        enforce(
            not self.use_mech_marketplace
            or self.mech_contract_address
            == self.mech_marketplace_config.priority_mech_address,
            "The mech contract address must be the same as the priority mech address when using the marketplace.",
        )
        super().__init__(*args, **kwargs)
        # Validate configuration after initialization
        self.validate_configuration()

    @property
    def ipfs_address(self) -> str:
        """Get the IPFS address."""
        if self._ipfs_address.endswith("/"):
            return self._ipfs_address
        return f"{self._ipfs_address}/"

    @property
    def nvm_config(self) -> NVMConfig:
        """Return the NVM configuration for the specified mech chain id."""
        return CHAIN_TO_NVM_CONFIG[ChainType(self.mech_chain_id)]

    def validate_configuration(self) -> None:
        """Validate the entire configuration for consistency."""
        try:
            # Validate marketplace configuration consistency
            if self.use_mech_marketplace:
                if not self.mech_marketplace_config.mech_marketplace_address:
                    raise ValueError(
                        "mech_marketplace_address is required when use_mech_marketplace is True"
                    )
                if not self.mech_marketplace_config.priority_mech_address:
                    raise ValueError(
                        "priority_mech_address is required when use_mech_marketplace is True"
                    )

            # Validate sleep time
            if self.mech_interaction_sleep_time <= 0:
                raise ValueError("mech_interaction_sleep_time must be positive")

            # Validate batch size
            if self.multisend_batch_size <= 0:
                raise ValueError("multisend_batch_size must be positive")

        except Exception as e:
            raise ValueError(f"Configuration validation failed: {e}") from e


Params = MechParams


@dataclass
class MultisendBatch:
    """A structure representing a single transaction of a multisend.

    This dataclass encapsulates the parameters needed for a single transaction
    within a multisend batch, providing a clean interface for transaction
    construction and validation.

    Attributes:
        to: Target contract address for the transaction
        data: Transaction data as HexBytes
        value: Wei value to send with the transaction (default: 0)
        operation: Type of operation (CALL or DELEGATECALL)
    """

    to: str
    data: HexBytes
    value: int = 0
    operation: MultiSendOperation = MultiSendOperation.CALL

    def __post_init__(self) -> None:
        """Validate the multisend batch after initialization."""
        if not self.to or not isinstance(self.to, str):
            raise ValueError("Target address 'to' must be a non-empty string")
        if self.value < 0:
            raise ValueError("Value must be non-negative")
        if not isinstance(self.data, HexBytes):
            raise ValueError("Data must be HexBytes instance")
