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

"""The operator identity a consumer pools a mech's results under.

A mech's manifest may declare ``operator.domain``. Whether that claim holds is
decided once, at approval time, outside the agent; the agent receives the
outcome as a mapping from mech address to the domain that was verified. Inside
the agent the domain is an opaque string: nothing here resolves or fetches it,
so the result depends only on the manifest and the configuration, which are
identical for every agent of a service.
"""

import re
from typing import Any, Mapping, Optional

MAX_DOMAIN_LENGTH = 253
# Lowercase labels of letters, digits and inner hyphens, at least two of them.
DOMAIN_PATTERN = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


def normalize_operator_domain(value: Any) -> Optional[str]:
    """Lowercase a declared operator domain, or reject it.

    :param value: the declared value.
    :return: the lowercase domain, or ``None`` when it is not a bare hostname
        (no scheme, path, port, or trailing dot; at most 253 characters).
    """
    if not isinstance(value, str):
        return None
    domain = value.lower()
    if len(domain) > MAX_DOMAIN_LENGTH or not DOMAIN_PATTERN.match(domain):
        return None
    return domain


def parse_operator_domain(manifest: Any) -> Optional[str]:
    """Get the operator domain a parsed manifest declares.

    :param manifest: the manifest's decoded JSON body.
    :return: the lowercase domain, or ``None`` when absent or malformed.
    """
    if not isinstance(manifest, dict):
        return None
    operator = manifest.get("operator")
    if not isinstance(operator, dict):
        return None
    return normalize_operator_domain(operator.get("domain"))


def is_operator_domain_verified(
    address: str,
    declared_domain: Optional[str],
    verified_domains: Mapping[str, str],
) -> bool:
    """Check whether the mech's declared domain is the one verified at approval.

    :param address: the mech's address.
    :param declared_domain: the lowercase domain its manifest declares.
    :param verified_domains: lowercase mech address to the lowercase domain
        whose proof verified when the mech was approved.
    :return: whether the declaration matches the verified domain.
    """
    if declared_domain is None:
        return False
    return verified_domains.get(address.lower()) == declared_domain


def record_identity(
    address: str,
    declared_domain: Optional[str],
    verified_domains: Mapping[str, str],
) -> str:
    """Get the identity a mech's results are recorded under.

    Mechs whose declared domain was verified share one identity per domain;
    any other mech is recorded under its own address.

    :param address: the mech's address.
    :param declared_domain: the lowercase domain its manifest declares.
    :param verified_domains: see :func:`is_operator_domain_verified`.
    :return: the lowercase domain or the lowercase address.
    """
    if is_operator_domain_verified(address, declared_domain, verified_domains):
        return str(declared_domain)
    return address.lower()
