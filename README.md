# Mech interact

An Open Autonomy skill that implements interactions with mechs.

## Terms

Each Mech is run by its own operator, and you agree to that operator's terms by submitting a request to their Mech. Mechs operated by Valory are subject to the [Valory Mech Terms](https://www.valory.xyz/terms/mechs). The skill logs this once at startup.

Valory creates one DNS record under `mech.valory.xyz` for each Mech it operates, so a Mech is operated by Valory if its own name there resolves. The name is the Mech address without `0x`, then the chain id:

```bash
dig +short c05e7412439bd7e91730a6880e18d5d5873f632c.100.mech.valory.xyz
```

An address back means Valory operates that Mech, and no answer means it does not. The answer does not depend on the Mech being up. If a name you make up at random also resolves, the zone is answering every name and the result tells you nothing.

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
