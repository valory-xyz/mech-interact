# Mech interact

An Open Autonomy skill that implements interactions with mechs.

## For Developers

- Clone the repository:

      git clone git@github.com:valory-xyz/mech-interact.git

- System requirements:

    - Python `>=3.8`
    - [Tendermint](https://docs.tendermint.com/v0.34/introduction/install.html) `==0.34.19`
    - [IPFS node](https://docs.ipfs.io/install/command-line/#official-distributions) `==0.6.0`
    - [Pipenv](https://pipenv.pypa.io/en/latest/installation.html) `>=2021.x.xx`
    - [Docker Engine](https://docs.docker.com/engine/install/) `<25.0`
    - [Docker Compose](https://docs.docker.com/compose/install/)

- Pull pre-built images:

      docker pull valory/autonolas-registries:latest
      docker pull valory/safe-contract-net:latest

- Create development environment:

      make new_env && pipenv shell

- Configure command line:

      autonomy init --reset --author valory --remote --ipfs --ipfs-node "/dns/registry.autonolas.tech/tcp/443/https"

- Pull packages:

      autonomy packages sync --update-packages
