# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2025-2026 Valory AG
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

"""The payment types a marketplace mech can charge in."""

from enum import Enum


class PaymentType(str, Enum):
    """Mech payment types."""

    # this is for native token payments, e.g. xDAI on Gnosis, ETH in Ethereum, etc.
    NATIVE = "0xba699a34be8fe0e7725e93dcbce1701b0211a8ca61330aaeb8a05bf2ec7abed1"
    # this is for OLAS token payments, e.g., OLAS on Gnosis, OLAS on Ethereum, etc.
    TOKEN_OLAS = "0x3679d66ef546e66ce9057c4a052f317b135bc8e8c509638f7966edfd4fcf45e9"  # nosec B105
    # This is for USDC token payments, e.g. USDC on Gnosis, USDC on Polygon, etc.
    TOKEN_USDC = "0x6406bb5f31a732f898e1ce9fdd988a80a808d36ab5d9a4a4805a8be8d197d5e3"  # nosec B105
    # This is for native token payments using NVM.
    NATIVE_NVM = "0x803dd08fe79d91027fc9024e254a0942372b92f3ccabc1bd19f4a5c2b251c316"
    # This is for USDC payments using NVM.
    TOKEN_NVM_USDC = "0x0d6fd99afa9c4c580fab5e341922c2a5c4b61d880da60506193d7bf88944dd14"  # nosec B105


TOKEN_PAYMENT_TYPES = frozenset({PaymentType.TOKEN_USDC, PaymentType.TOKEN_OLAS})
NVM_PAYMENT_TYPES = frozenset({PaymentType.NATIVE_NVM, PaymentType.TOKEN_NVM_USDC})
