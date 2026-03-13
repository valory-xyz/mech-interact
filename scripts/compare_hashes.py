#!/usr/bin/env python3
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
"""Compare package hashes between source-of-truth and target repos."""

import json
from pathlib import Path

# Source of truth: open-autonomy dev packages
SOURCE_PACKAGES_JSON = Path("/home/lockhart/work/valory/repos/open-autonomy/packages/packages.json")

# Target: this repo's packages.json
TARGET_PACKAGES_JSON = Path("packages/packages.json")

# Alternatives for other repos:
# SOURCE_PACKAGES_JSON = Path("/home/lockhart/work/valory/repos/agent-academy-1/packages/packages.json")
# TARGET_PACKAGES_JSON = Path("/home/lockhart/work/valory/repos/mech/packages/packages.json")


def main() -> None:
    """Compare hashes."""
    with open(SOURCE_PACKAGES_JSON, encoding="utf-8") as f:
        source = json.load(f)
    with open(TARGET_PACKAGES_JSON, encoding="utf-8") as f:
        target = json.load(f)

    # Source dev packages = target third_party packages
    source_dev = source.get("dev", {})
    source_third = source.get("third_party", {})
    # Merge both sections as potential sources
    source_all = {**source_third, **source_dev}

    target_third = target.get("third_party", {})

    mismatches = []
    for pkg, target_hash in target_third.items():
        if pkg in source_all:
            source_hash = source_all[pkg]
            if target_hash != source_hash:
                mismatches.append((pkg, target_hash, source_hash))

    if not mismatches:
        print("All hashes match!")
        return

    print(f"Found {len(mismatches)} mismatched hashes:\n")
    for pkg, _, new in mismatches:
        print(f'  "{pkg}": "{new}",')
    print(f"\nReplace these in {TARGET_PACKAGES_JSON}")


if __name__ == "__main__":
    main()
