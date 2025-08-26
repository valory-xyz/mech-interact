# Mech Interact ABCI

Below, we describe the additional manual steps required to upgrade between different versions:

## `v0.19.10` to `v0.20.0` (built with `open-aea@1.65.0` and `open-autonomy@0.19.9`)

#### Breaking Changes
- The new `FinishedMechRequestBuySubscriptionRound` final state of the mech-interact indicates that an NVM subscription 
  should be bought before sending the request. The new final state should be mapped in the composition of your skill.
- New contracts were introduced. 
  If you are using the mech interact abci, you will need to add them as dependencies to your agent:
  - valory/ierc1155:0.1.0:bafybeig5lnuicphvwnu55fgmwegpma44l6jldakyfyjciscl274unb5iqy
  - valory/nvm_balance_tracker_token:0.1.0:bafybeib5s3h6htopcl4gkexyijjqhk5dkmrru2ltsvvwk47r7dvx46iraa
  - valory/nvm_balance_tracker_native:0.1.0:bafybeihlbnuistppdcjbxdqwleji4mqhmygezo4n5pwxkn5tbp6rlmithy

#### Non-breaking Changes
- The `nvm_balance_tracker_address` was introduced and is used to get the balance information for the NVM subscription. 
  You may add it to the configurations of your composed skill, your agent and your service, to make it overridable.
