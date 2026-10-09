# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2026 Valory AG
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

"""Tests for the operator identity rules."""

from typing import Any, Optional

import pytest

from packages.valory.skills.mech_interact_abci.operator_identity import (
    MAX_DOMAIN_LENGTH,
    is_operator_domain_verified,
    normalize_operator_domain,
    parse_operator_domain,
    record_identity,
)

OPERATOR_DOMAIN = "www.valory.xyz"
MECH = "0x" + "ab" * 20
OTHER_MECH = "0x" + "cd" * 20
VERIFIED = {MECH: OPERATOR_DOMAIN}


@pytest.mark.parametrize(
    "value, expected",
    [
        (OPERATOR_DOMAIN, OPERATOR_DOMAIN),
        ("WWW.Valory.XYZ", OPERATOR_DOMAIN),
        ("a-b.example.co.uk", "a-b.example.co.uk"),
        ("localhost", None),
        ("https://www.valory.xyz", None),
        ("www.valory.xyz/path", None),
        ("www.valory.xyz:443", None),
        ("www.valory.xyz.", None),
        (".valory.xyz", None),
        ("-a.valory.xyz", None),
        ("a..valory.xyz", None),
        ("valory::xyz.com", None),
        (" www.valory.xyz", None),
        ("", None),
        (None, None),
        (42, None),
        ("a." * 126 + "xyz", None),
    ],
)
def test_normalize_operator_domain(value: Any, expected: Optional[str]) -> None:
    """Only a bare hostname with a dot is accepted, lowercased."""
    assert normalize_operator_domain(value) == expected


def test_normalize_operator_domain_length_boundary() -> None:
    """253 characters is the last accepted length."""
    label = "a" * 61
    longest = ".".join([label] * 4) + "." + "b" * (MAX_DOMAIN_LENGTH - 4 * 62)
    assert len(longest) == MAX_DOMAIN_LENGTH
    assert normalize_operator_domain(longest) == longest
    assert normalize_operator_domain("c" + longest) is None


@pytest.mark.parametrize(
    "manifest, expected",
    [
        ({"operator": {"domain": "WWW.VALORY.XYZ"}}, OPERATOR_DOMAIN),
        ({"operator": {"name": "Valory"}}, None),
        ({"operator": {"domain": "https://valory.xyz"}}, None),
        ({"operator": "www.valory.xyz"}, None),
        ({"tools": ["t"]}, None),
        (["operator"], None),
        (None, None),
    ],
)
def test_parse_operator_domain(manifest: Any, expected: Optional[str]) -> None:
    """The domain comes only from ``operator.domain`` and must be well formed."""
    assert parse_operator_domain(manifest) == expected


@pytest.mark.parametrize(
    "address, declared, expected",
    [
        (MECH, OPERATOR_DOMAIN, True),
        (MECH.upper().replace("0X", "0x"), OPERATOR_DOMAIN, True),
        (MECH, "other.example", False),
        (MECH, None, False),
        (OTHER_MECH, OPERATOR_DOMAIN, False),
    ],
)
def test_is_operator_domain_verified(
    address: str, declared: Optional[str], expected: bool
) -> None:
    """A declaration counts only when it equals the domain verified for that mech."""
    assert is_operator_domain_verified(address, declared, VERIFIED) is expected


def test_record_identity_pools_verified_mechs_by_domain() -> None:
    """A verified mech is recorded under its domain."""
    assert record_identity(MECH, OPERATOR_DOMAIN, VERIFIED) == OPERATOR_DOMAIN


def test_record_identity_claiming_an_unverified_domain_falls_back_to_address() -> None:
    """Declaring someone else's domain does not join its record."""
    assert record_identity(OTHER_MECH.upper(), OPERATOR_DOMAIN, VERIFIED) == OTHER_MECH


def test_record_identity_without_declaration_is_the_address() -> None:
    """A verified mech whose manifest declares nothing is recorded by address."""
    assert record_identity(MECH, None, VERIFIED) == MECH
