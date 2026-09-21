# Mech interact

An Open Autonomy skill that implements interactions with mechs.

## Terms

Each Mech is run by its own operator. By submitting a request to a Mech operated by Valory, you agree to be bound by [Valory AG's Mech Terms (v1.0)](https://www.valory.xyz/terms/mechs). The skill logs this once at startup.

Valory creates one DNS record under `mech.valory.xyz` for each Mech it operates, and for no other Mech, so a Mech is operated by Valory if its own name there resolves in the public DNS. The name is the Mech address without `0x`, a hyphen, then the chain id:

```bash
dig +short c05e7412439bd7e91730a6880e18d5d5873f632c-100.mech.valory.xyz
```

If the name resolves, Valory operates that Mech. If it does not resolve, the Mech is not operated by Valory. The answer does not depend on the Mech being up. A lookup that fails outright, with no network or an unreachable resolver, tells you nothing either way, so try again. If a name you make up at random also resolves, the zone or your resolver is answering every name and the result tells you nothing.

## For Developers

- Clone the repository:

      git clone git@github.com:valory-xyz/mech-interact.git

- System requirements:

    - Python `>=3.8`
    - [Tendermint](https://docs.tendermint.com/v0.34/introduction/install.html) `==0.34.19`
    - [IPFS node](https://docs.ipfs.io/install/command-line/#official-distributions) `==0.6.0`
    - [uv](https://docs.astral.sh/uv/getting-started/installation/)
    - [Docker Engine](https://docs.docker.com/engine/install/) `<25.0`
    - [Docker Compose](https://docs.docker.com/compose/install/)

- Pull pre-built images:

      docker pull valory/autonolas-registries:latest
      docker pull valory/safe-contract-net:latest

- Create development environment:

      uv sync

- Configure command line:

      uv run autonomy init --reset --author valory --remote --ipfs --ipfs-node "/dns/registry.autonolas.tech/tcp/443/https"

- Pull packages:

      uv run autonomy packages sync --update-packages
