# Mech Interact ABCI

Below, we describe the additional manual steps required to upgrade between different versions:

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
    "skill/valory/mech_interact_abci/0.1.0": "bafybeievndqg7cjipzinkoshych5ea4k554c2yxwcumvqcq6j273pfmghe",
    "contract/valory/ierc1155/0.1.0": "bafybeig5lnuicphvwnu55fgmwegpma44l6jldakyfyjciscl274unb5iqy",
    "contract/valory/nvm_balance_tracker_token/0.1.0": "bafybeifrj4w5zz7dnvakfuuxcse54intris7uplnxtkihflpcd3o6i2nji",
    "contract/valory/nvm_balance_tracker_native/0.1.0": "bafybeicmrhh7lqxgfwk4ck3ppqigud7xphkhensbkb2ouyluewyqezj2wm",
    "contract/valory/escrow_payment_condition/0.1.0": "bafybeiaz4vctejchz2rapd3glrcfstlmltjlv2dt63s3pwyymcwkch572e",
    "contract/valory/did_registry/0.1.0": "bafybeibs6edqrck4vqjbfeflh2yvx54r5lcdxp2fxinin6u4lrphpr4yhy",
    "contract/valory/nft_sales/0.1.0": "bafybeif2lrbyfno26xivybiohw4wm4il4kfaywvvaf6patgaqbyxjb37sa",
    "contract/valory/lock_payment_condition/0.1.0": "bafybeigai3izamf7abolq347krjoafrogbdoz2sis4zfco7u6ougjskjzy",
    "contract/valory/agreement_store_manager/0.1.0": "bafybeib4arfjefyephkdoovgtqsafgzdxpvtcqjdjlhvcq5m3wtkdqdfem",
    "contract/valory/transfer_nft_condition/0.1.0": "bafybeibvcrlgfuy5wt2xolzqsywbzfaquwfdvqn44ppcdka2ymk3e5kxm4",
    "contract/valory/subscription_provider/0.1.0": "bafybeibrt3446p5lzig3igoez3wyg6qtmcacgzvuubrhmhrqj6kxqsq4nq"
  }
  ```

  and as dependencies to your agent:
  ```yaml
  contracts:
  - valory/ierc1155:0.1.0:bafybeig5lnuicphvwnu55fgmwegpma44l6jldakyfyjciscl274unb5iqy
  - valory/nvm_balance_tracker_token:0.1.0:bafybeifrj4w5zz7dnvakfuuxcse54intris7uplnxtkihflpcd3o6i2nji
  - valory/nvm_balance_tracker_native:0.1.0:bafybeicmrhh7lqxgfwk4ck3ppqigud7xphkhensbkb2ouyluewyqezj2wm
  - valory/escrow_payment_condition:0.1.0:bafybeiaz4vctejchz2rapd3glrcfstlmltjlv2dt63s3pwyymcwkch572e
  - valory/did_registry:0.1.0:bafybeibs6edqrck4vqjbfeflh2yvx54r5lcdxp2fxinin6u4lrphpr4yhy
  - valory/nft_sales:0.1.0:bafybeif2lrbyfno26xivybiohw4wm4il4kfaywvvaf6patgaqbyxjb37sa
  - valory/lock_payment_condition:0.1.0:bafybeigai3izamf7abolq347krjoafrogbdoz2sis4zfco7u6ougjskjzy
  - valory/agreement_store_manager:0.1.0:bafybeib4arfjefyephkdoovgtqsafgzdxpvtcqjdjlhvcq5m3wtkdqdfem
  - valory/transfer_nft_condition:0.1.0:bafybeibvcrlgfuy5wt2xolzqsywbzfaquwfdvqn44ppcdka2ymk3e5kxm4
  - valory/subscription_provider:0.1.0:bafybeibrt3446p5lzig3igoez3wyg6qtmcacgzvuubrhmhrqj6kxqsq4nq
  skills:
  - valory/mech_interact_abci:0.1.0:bafybeievndqg7cjipzinkoshych5ea4k554c2yxwcumvqcq6j273pfmghe
  ```

#### Misc
- The `mech_chain_id` attribute of the `MechParams` class in `packages.valory.skills.mech_interact_abci.models` 
  has been converted to a non-optional `str`.
- The `mech_request_price` configuration and attribute of the `MechParams` class 
  in `packages.valory.skills.mech_interact_abci.models` was removed as it was not being used in any way by the skill. 
