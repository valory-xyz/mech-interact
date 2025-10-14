# Mech Interact ABCI

Below, we describe the additional manual steps required to upgrade between different versions:

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
