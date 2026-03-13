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

"""Test the dialogues.py module of the MechInteract."""

import pytest

from packages.valory.skills.mech_interact_abci import dialogues

EXPECTED_RE_EXPORTS = [
    "AbciDialogue",
    "AbciDialogues",
    "HttpDialogue",
    "HttpDialogues",
    "SigningDialogue",
    "SigningDialogues",
    "LedgerApiDialogue",
    "LedgerApiDialogues",
    "ContractApiDialogue",
    "ContractApiDialogues",
    "TendermintDialogue",
    "TendermintDialogues",
    "IpfsDialogue",
    "IpfsDialogues",
    "AcnDialogue",
    "AcnDialogues",
]


@pytest.mark.parametrize("name", EXPECTED_RE_EXPORTS)
def test_dialogue_exports_exist(name: str) -> None:
    """Test that all expected dialogue classes are exported."""
    assert hasattr(dialogues, name), f"Missing expected export: {name}"


def test_acn_dialogues_is_model_subclass() -> None:
    """Test that AcnDialogues is a Model subclass (not just a re-export)."""
    from aea.skills.base import Model

    assert issubclass(dialogues.AcnDialogues, Model)
