# Mech Interact ABCI

Below, we describe the additional manual steps required to upgrade between different versions:

## `v0.22.2` to `v0.22.3` (built with `open-aea@1.65.0` and `open-autonomy@0.19.11`)

#### Breaking Changes
- No backwards incompatible changes.

#### New features
- New configurations have been introduced,  
  and users may optionally override them in their composed skills, agents and services:
  - `ignored_mechs`: A list of mech addresses to ignore.
  - `penalize_mech_time_window`: The time window in seconds to penalize a mech for.
- The shared state has some new properties that may be optionally used:
  - `penalized_mechs`: Returns all the penalized mechs, 
    taking into consideration the `penalize_mech_time_window` and the time of penalization.
  - `penalize_mech`: Penalizes a given mech.
  - `penalize_last_called_mech`: Penalizes the last called mech.

## `v0.22.1` to `v0.22.2` (built with `open-aea@1.65.0` and `open-autonomy@0.19.11`)
- No backwards incompatible changes.

## `v0.22.0` to `v0.22.1` (built with `open-aea@1.65.0` and `open-autonomy@0.19.11`)
- No backwards incompatible changes.

## `v0.21.3` to `v0.22.0` (built with `open-aea@1.65.0` and `open-autonomy@0.19.11`)

#### New features
- This skill now performs a check to see which mech version is used (legacy, legacy marketplace, new marketplace).
  - The property `is_marketplace_v2` in the synced data holds the result:
    - None: legacy
    - True: new marketplace
    - False: legacy marketplace
- The priority mech is now dynamically picked (Discoverability feature).
  - There is a feature flag to enable/disable this, in `mech_marketplace_config`, called `use_dynamic_mech_selection`.
  - The mech's information and their tools are now gathered in this skill, to support the discoverability.
    This is only performed if the new mech marketplace is detected.
  - New properties introduced to support this feature:
    - `mechs_info`: All the mechs' information.
    - `relevant_mechs_info`: The mechs' information that are relevant to the user, 
      i.e., include tools which are not in the `irrelevant_tools` set.
    - `mech_tools`: The set of all the mechs' tools.
    - `priority_mech`: The dynamically picked priority mech.
    - `priority_mech_address`: The address of the dynamically picked priority mech.
- The valory/agent_mech/0.1.0 contract component now includes the new method `get_max_fee_factor`, 
  which reads the `MAX_FEE_FACTOR` constant from the contract.

#### Breaking Changes
- Due to the new feature that performs the mech versioning check, the following breaking changes have been introduced:
  - The initial round has been changed from `MechRequestRound` to `MechVersionDetectionRound`. 
    However, the `MechRequestRound` has remained in the `initial_states`, 
    which have been expanded to also include the new `MechVersionDetectionRound`.
  - New final states have been introduced:
    - `FinishedMarketplaceLegacyDetectedRound`: Triggered if the detected mech version was the legacy marketplace
    - `FinishedMechLegacyDetectedRound`: Triggered if the detected mech version was the legacy mech
    - `FinishedMechInformationRound`: Triggered if the mech information gathering was successfully performed.
    - `FailedMechInformationRound`: Triggered if the mech information gathering failed.
- The `irrelevant_tools` are now defined in this skill. This is a list of tools which should never be picked.
- New models have been introduced in `skill.yaml` and `models.py` and should be defined in the composed skills too:
  - `MechToolsSpecs`
  - `MechsSubgraph`
- The `from_dict` method has been removed from the `MechMarketplaceConfig` dataclass, as it is not necessary.

## `v0.21.2` to `v0.21.3` (built with `open-aea@1.65.0` and `open-autonomy@0.19.11`)
- No backwards incompatible changes.

## `v0.21.1` to `v0.21.2` (built with `open-aea@1.65.0` and `open-autonomy@0.19.11`)
- No backwards incompatible changes.

## `v0.21.0` to `v0.21.1` (built with `open-aea@1.65.0` and `open-autonomy@0.19.11`)
- No backwards incompatible changes.

## `v0.20.0` to `v0.21.0` (built with `open-aea@1.65.0` and `open-autonomy@0.19.9`)
- No backwards incompatible changes.

## `v0.19.10` to `v0.20.0` (built with `open-aea@1.65.0` and `open-autonomy@0.19.9`)

#### Breaking Changes
- The new `FinishedMechPurchaseSubscriptionRound` final state of the mech-interact indicates that an NVM subscription 
  should be bought before sending the request, and that the corresponding tx has been prepared. 
  The new final state should be mapped in the composition of your skill, with the transaction settlement skill, 
  and then the request round should be called again, after successfully sending the tx.
- New contracts were introduced. 
  If you are using the mech interact abci, you will need to add them as third party packages:
  ```json
  {
    "skill/valory/mech_interact_abci/0.1.0": "bafybeifpoee4q26fd3vmayiqrbw5inczuzwy7zrvez3k245y4pmhweyq2i",
    "contract/valory/ierc1155/0.1.0": "bafybeig5lnuicphvwnu55fgmwegpma44l6jldakyfyjciscl274unb5iqy",
    "contract/valory/nvm_balance_tracker_token/0.1.0": "bafybeifvyi4wtyjcsgjwus5kde3oa2bqdn5rza5jdnhw5czbl7bfqnprsq",
    "contract/valory/nvm_balance_tracker_native/0.1.0": "bafybeihbzlkfcqxvbfmnbxx4db7wto3ngjfn43gt6seo7d5i5looa6z2ju",
    "contract/valory/escrow_payment_condition/0.1.0": "bafybeiaz4vctejchz2rapd3glrcfstlmltjlv2dt63s3pwyymcwkch572e",
    "contract/valory/did_registry/0.1.0": "bafybeibs6edqrck4vqjbfeflh2yvx54r5lcdxp2fxinin6u4lrphpr4yhy",
    "contract/valory/nft_sales/0.1.0": "bafybeidychvnkjjw3ucrcicogxahfwn4j73dliyw7divyyrlsxnowav3wy",
    "contract/valory/lock_payment_condition/0.1.0": "bafybeigai3izamf7abolq347krjoafrogbdoz2sis4zfco7u6ougjskjzy",
    "contract/valory/agreement_store_manager/0.1.0": "bafybeib4arfjefyephkdoovgtqsafgzdxpvtcqjdjlhvcq5m3wtkdqdfem",
    "contract/valory/transfer_nft_condition/0.1.0": "bafybeibvcrlgfuy5wt2xolzqsywbzfaquwfdvqn44ppcdka2ymk3e5kxm4",
    "contract/valory/subscription_provider/0.1.0": "bafybeic4udkvbsajuwscnqsm7tf6monwqyos4qvgbako6lch3zhtfxrc7q"
  }
  ```

  and as dependencies to your agent:
  ```yaml
  contracts:
  - valory/ierc1155:0.1.0:bafybeig5lnuicphvwnu55fgmwegpma44l6jldakyfyjciscl274unb5iqy
  - valory/nvm_balance_tracker_token:0.1.0:bafybeifvyi4wtyjcsgjwus5kde3oa2bqdn5rza5jdnhw5czbl7bfqnprsq
  - valory/nvm_balance_tracker_native:0.1.0:bafybeihbzlkfcqxvbfmnbxx4db7wto3ngjfn43gt6seo7d5i5looa6z2ju
  - valory/escrow_payment_condition:0.1.0:bafybeiaz4vctejchz2rapd3glrcfstlmltjlv2dt63s3pwyymcwkch572e
  - valory/did_registry:0.1.0:bafybeibs6edqrck4vqjbfeflh2yvx54r5lcdxp2fxinin6u4lrphpr4yhy
  - valory/nft_sales:0.1.0:bafybeidychvnkjjw3ucrcicogxahfwn4j73dliyw7divyyrlsxnowav3wy
  - valory/lock_payment_condition:0.1.0:bafybeigai3izamf7abolq347krjoafrogbdoz2sis4zfco7u6ougjskjzy
  - valory/agreement_store_manager:0.1.0:bafybeib4arfjefyephkdoovgtqsafgzdxpvtcqjdjlhvcq5m3wtkdqdfem
  - valory/transfer_nft_condition:0.1.0:bafybeibvcrlgfuy5wt2xolzqsywbzfaquwfdvqn44ppcdka2ymk3e5kxm4
  - valory/subscription_provider:0.1.0:bafybeic4udkvbsajuwscnqsm7tf6monwqyos4qvgbako6lch3zhtfxrc7q
  skills:
  - valory/mech_interact_abci:0.1.0:bafybeifpoee4q26fd3vmayiqrbw5inczuzwy7zrvez3k245y4pmhweyq2i
  ```

#### Misc
- The `mech_chain_id` attribute of the `MechParams` class in `packages.valory.skills.mech_interact_abci.models` 
  has been converted to a non-optional `str`.
- The `mech_request_price` configuration and attribute of the `MechParams` class 
  in `packages.valory.skills.mech_interact_abci.models` was removed as it was not being used in any way by the skill. 
