# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2023-2026 Valory AG
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

"""Shared fixtures for contract tests."""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def ledger_api() -> MagicMock:
    """Create a mock ledger API with common Ethereum RPC stubs."""
    mock_api = MagicMock()
    mock_api.api.to_checksum_address = lambda addr: addr
    mock_api.api.eth.get_transaction_receipt.return_value = {
        "blockNumber": 100,
        "logs": [],
    }
    mock_api.api.eth.get_block.return_value = {"number": 100}
    return mock_api
