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
from typing import Any, Dict, List, Optional

from aea.exceptions import enforce
from hexbytes import HexBytes

from autonomy.chain.config import ChainType
from autonomy.chain.service import NULL_ADDRESS

from packages.valory.contracts.multisend.contract import MultiSendOperation
from packages.valory.protocols.http import HttpMessage
from packages.valory.skills.abstract_round_abci.models import ApiSpecs, BaseParams
from packages.valory.skills.abstract_round_abci.models import (
    BenchmarkTool as BaseBenchmarkTool,
)
from packages.valory.skills.abstract_round_abci.models import Requests as BaseRequests
from packages.valory.skills.abstract_round_abci.models import (
    SharedState as BaseSharedState,
)
from packages.valory.skills.mech_interact_abci.rounds import MechInteractAbciApp
from packages.valory.skills.mech_interact_abci.states.base import MechInfo, MechsInfo


Requests = BaseRequests
BenchmarkTool = BaseBenchmarkTool
MechsSubgraphResponseType = Optional[MechsInfo]


PLAN_DID_PREFIX = "did:nv:"
Ox = "0x"


class MechToolsSpecs(ApiSpecs):
    """A model that wraps ApiSpecs for the Mech agent's tools specifications."""


class MechsSubgraph(ApiSpecs):
    """Specifies `ApiSpecs` with common functionality for the Mechs' subgraph."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize MechsSubgraph."""
        self.delivery_rate_cap: int = self._ensure("delivery_rate_cap", kwargs, int)
        super().__init__(*args, **kwargs)

    def filter_info(self, unfiltered: List[Dict[str, str]]) -> MechsInfo:
        """Filter the information based on the metadata."""
        return [
            mech_info
            for info in unfiltered
            if not (mech_info := MechInfo(**info)).empty_metadata
            and mech_info.max_delivery_rate <= self.delivery_rate_cap
        ]

    def process_response(self, response: HttpMessage) -> MechsSubgraphResponseType:
        """Process the response."""
        res = super().process_response(response)
        if res is not None:
            return self.filter_info(res)

        error_data = self.response_info.error_data
        expected_error_type = getattr(builtins, self.response_info.error_type)
        if isinstance(error_data, expected_error_type):
            error_message_key = self.context.params.the_graph_error_message_key
            error_message = error_data.get(error_message_key, None)
            if self.context.params.the_graph_payment_required_error in error_message:
                err = "Payment required for subsequent requests for the current 'The Graph' API key!"
                self.context.logger.error(err)
        return None


@dataclass
class NVMConfig:
    """NVM configuration."""

    balance_tracker_address: str
    did_registry_address: str
    agreement_store_manager_address: str
    lock_payment_condition_address: str
    transfer_nft_condition_address: str
    escrow_payment_condition_address: str
    plan_fee_nvm: int
    plan_price_mech: int
    subscription_nft_address: str
    nft_sales_address: str
    subscription_token_address: str
    subscription_provider_address: str
    plan_did: str
    subscription_credits: int = int(1e6)
    subscription_cost: int = 0
    agreement_cost: int = 0

    @property
    def did(self) -> str:
        """Get the did."""
        return self.plan_did.replace(PLAN_DID_PREFIX, Ox)


# false positives for [B105:hardcoded_password_string] Possible hardcoded password
CHAIN_TO_NVM_CONFIG = {
    ChainType.GNOSIS: NVMConfig(  # nosec
        balance_tracker_address="0x7D686bD1fD3CFF6E45a40165154D61043af7D67c",
        did_registry_address="0xCB0A331cB1F57E01FF0FA2d664f2F100081cbc3b",
        agreement_store_manager_address="0x4Cd9b03bEB7D68bC397B64521DF0A272bE94a4Df",
        lock_payment_condition_address="0x2749DDEd394196835199471027713773736bffF2",
        transfer_nft_condition_address="0x659fCA7436936e9fe8383831b65B8B442eFc8Ea8",
        escrow_payment_condition_address="0x31B2D187d674C9ACBD2b25f6EDce3d2Db2B7f446",
        plan_fee_nvm=10000000000000000,
        plan_price_mech=990000000000000000,
        subscription_nft_address="0x1b5DeaD7309b56ca7663b3301A503e077Be18cba",
        nft_sales_address="0x72201948087aE83f8Eac22cf7A9f2139e4cFA829",
        subscription_token_address="0x0000000000000000000000000000000000000000",
        subscription_provider_address="0x4a2f40E14309c20c0C3803c3CcCd5E9B5F2D4eCA",
        plan_did="did:nv:b0b28402e5a7229804579d4ac55b98a1dd94660d7a7eb4add78e5ca856f2aab7",
        agreement_cost=10**18,
    ),
    ChainType.BASE: NVMConfig(  # nosec
        balance_tracker_address="0xaaFBeef195BDAb1Bb6f3Dc9cEbA875Cd72499230",
        did_registry_address="0x5FD091093152403BEE33a5c4Db60721Fc513985D",
        agreement_store_manager_address="0x173CFb11baa0Cf18FDA698cF82AEf6181D84B845",
        lock_payment_condition_address="0x7d1b782A347234d1442e57721444B778D5B2E6B7",
        transfer_nft_condition_address="0x17a49d0942b987ebDE9D6400A045159bd3936541",
        escrow_payment_condition_address="0xA33f6149563CfEC51C9e7961A3FB5DdF5F9D5B68",
        plan_fee_nvm=10000,
        plan_price_mech=990000,
        subscription_nft_address="0xd5318d1A17819F65771B6c9277534C08Dd765498",
        nft_sales_address="0x468dC6d758129c4563005B49aC58DfF2e6f7F08e",
        subscription_token_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        subscription_provider_address="0x5050c577583D25Ff9C9492A39e8D1B94028ffA55",
        plan_did="did:nv:6f74c18fae7e5c3589b99d7cd0ba317593f00dee53c81a2ba4ac2244232f99da",
        subscription_cost=10**6,
    ),
}

CHAIN_TO_PRICE_TOKEN = {
    ChainType.ETHEREUM: "0x0001A500A6B18995B03f44bb040A5fFc28E45CB0",
    ChainType.OPTIMISTIC: "0xFC2E6e6BCbd49ccf3A5f029c79984372DcBFE527",
    ChainType.GNOSIS: "0xcE11e14225575945b8E6Dc0D4F2dD4C570f79d9f",
    ChainType.POLYGON: "0xFEF5d947472e72Efbb2E388c730B7428406F2F95",
    ChainType.BASE: "0x54330d28ca3357F294334BDC454a032e7f353416",
    ChainType.CELO: "0x96ffa56a963EC33e5bC7057B9002722D1884fc01",
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
    response_timeout: int
    priority_mech_address: Optional[str] = None
    priority_mech_staking_instance_address: str = NULL_ADDRESS
    priority_mech_service_id: int = 975
    requester_staking_instance_address: Optional[str] = NULL_ADDRESS
    use_dynamic_mech_selection: bool = True

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
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
        self._ipfs_address: str = self._ensure("ipfs_address", kwargs, str)
        self.mech_chain_id: str = kwargs.get("mech_chain_id", "gnosis")
        self.mech_wrapped_native_token_address: Optional[str] = kwargs.get(
            "mech_wrapped_native_token_address"
        )
        if not self.mech_wrapped_native_token_address:
            self.context.logger.info(
                "Please configure 'mech_wrapped_native_token_address', "
                "if you want to use wrapped native tokens for mech requests."
            )

        self.mech_interaction_sleep_time: int = self._ensure(
            "mech_interaction_sleep_time", kwargs, int
        )
        self.use_mech_marketplace: bool = self._ensure(
            "use_mech_marketplace", kwargs, bool
        )
        self.mech_marketplace_config: MechMarketplaceConfig = MechMarketplaceConfig(
            **kwargs["mech_marketplace_config"]
        )

        if (
            self.mech_marketplace_config.use_dynamic_mech_selection
            and self.mech_marketplace_config.priority_mech_address
        ):
            self.context.logger.info(
                "A priority mech has been set while dynamic mech selection is enabled. "
                "The priority mech will be ignored."
            )

        self.agent_registry_address: str = kwargs.get("agent_registry_address")
        enforce(
            self.agent_registry_address is not None,
            "Agent registry address not specified!",
        )
        self.use_acn_for_delivers: bool = self._ensure(
            "use_acn_for_delivers", kwargs, bool
        )
        self.irrelevant_tools: set = set(self._ensure("irrelevant_tools", kwargs, list))

        if self.use_mech_marketplace:
            self.context.logger.info(
                "Using mech marketplace for mech interactions. "
                "The `mech_contract_address` will be ignored. "
                "The `mech_marketplace_config.priority_mech_address` will be used for V1, "
                "otherwise, the priority mech will be auto-selected for V2."
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

    @property
    def price_token(self) -> str:
        """Return the price token for the specified mech chain id."""
        return CHAIN_TO_PRICE_TOKEN[ChainType(self.mech_chain_id)]

    def validate_configuration(self) -> None:
        """Validate the entire configuration for consistency."""
        try:
            # Validate marketplace configuration consistency
            if self.use_mech_marketplace:
                if not self.mech_marketplace_config.mech_marketplace_address:
                    raise ValueError(
                        "mech_marketplace_address is required when use_mech_marketplace is True"
                    )
                if (
                    not self.mech_marketplace_config.priority_mech_address
                    and not self.mech_marketplace_config.use_dynamic_mech_selection
                ):
                    raise ValueError(
                        "priority_mech_address is required "
                        "when use_mech_marketplace is True and use_dynamic_mech_selection is False"
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
