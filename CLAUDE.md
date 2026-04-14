# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Mech-interact is an Open Autonomy skill implementing interactions with AI mechs on the Autonolas protocol. It is built on the `open-aea` and `open-autonomy` frameworks. The project contains AEA (Autonomous Economic Agent) packages: one ABCI skill and multiple smart contract wrappers.

## Development Setup

```bash
uv sync
uv run autonomy init --reset --author valory --remote --ipfs --ipfs-node "/dns/registry.autonolas.tech/tcp/443/https"
uv run autonomy packages sync --update-packages
```

Prefix commands with `uv run` (e.g. `uv run pytest ...`, `uv run make test`).

## Common Commands

### Testing
```bash
# Run all tests (skill + contracts)
make test

# Run a single test file
pytest packages/valory/skills/mech_interact_abci/tests/test_file.py -rfE

# Run tests via tox (platform-specific, e.g. py3.12-darwin)
tox -e py3.12-darwin
```

### Linting & Formatting
```bash
# Auto-format code (isort + black)
make formatters

# Run all code checks (black-check, isort-check, flake8, mypy, pylint, darglint)
make code-checks

# Run security checks
make security

# Run all linters at once
make all-linters
```

### Package Management
```bash
# Regenerate ABCI docstrings, copyright headers, and package hashes
make generators

# Check package hashes and doc hashes
make common-checks-1

# Fix ABCI app FSM specs
make fix-abci-app-specs
```

## Architecture

### Package Structure

All code lives under `packages/valory/`:

- **`skills/mech_interact_abci/`** — The core ABCI skill implementing the mech interaction FSM (Finite State Machine)
- **`contracts/`** — Smart contract wrappers (mech, mech_mm, mech_marketplace_legacy, agent_registry, erc20, and Nevermined contracts)

### ABCI Skill Pattern

The skill follows the Open Autonomy round-based architecture:

- **`states/`** — Round definitions (FSM states). Each state file defines a round class with `Event` transitions. `base.py` contains `SynchronizedData` and shared `Event` enum.
- **`behaviours/`** — Behaviour implementations (logic executed during each round). `round_behaviour.py` wires all behaviours together.
- **`rounds.py`** — `MechInteractAbciApp` FSM definition with transition function mapping states to events.
- **`models.py`** — Shared configuration and data models.
- **`handlers.py`** — Message handlers (abci, http, contract_api, ledger_api, signing).
- **`payloads.py`** — Payload definitions for consensus.

The FSM flow starts at `MechVersionDetectionRound`, branching based on mech version (v1 legacy vs v2 marketplace), then proceeding through information gathering, request, subscription purchase, and response rounds.

### Contract Packages

Each contract package contains:
- `contract.py` — Python wrapper with typed methods for on-chain interactions
- `contract.yaml` — AEA component configuration
- `build/` — Compiled ABI JSON files

### Key Conventions

- Dependencies are managed via `pyproject.toml` + `uv.lock` (via `uv`) and `tox.ini` (CI).
- Package hashes in `packages/packages.json` must stay in sync — run `autonomy packages lock` after modifying packages.
- Code style: black (line length 88), isort, flake8, mypy with `--disallow-untyped-defs`, darglint (sphinx-style docstrings).
- Third-party AEA packages are synced from IPFS, not vendored. Only `packages/valory/` contains project-owned code.

### Pre-commit Workflow

Before pushing, run in order:
1. `make clean`
2. `make formatters`
3. `make code-checks`
4. `make generators` (if packages modified)
5. `make common-checks-1` (if packages modified)
