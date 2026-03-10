# Audit Verification & Remediation Plan

**Scope:** `packages/valory/skills/mech_interact_abci/`
**Audit Date:** 2026-03-05
**Verification Date:** 2026-03-09
**Source:** `AUDIT.md`

---

## Finding Verification

### T2: Round Test Attribute Name Mismatch

**Claim:** `TestMechRequestRound` and `TestMechResponseRound` define `round_class` but the base class `BaseMechInteractRoundTest` declares and uses `round_cls`. Would raise `AttributeError` if tests ran.

**Verdict: TRUE**

**Reasoning:**
- `BaseMechInteractRoundTest` declares `round_cls: Type[AbstractRound]` (test_rounds.py:58) and references it as `self.round_cls(...)` in `run_test()` (test_rounds.py:68).
- `TestMechRequestRound` sets `round_class = MechRequestRound` (test_rounds.py:89) -- different attribute name.
- `TestMechResponseRound` sets `round_class = MechResponseRound` (test_rounds.py:102) -- same mismatch.
- The framework's `BaseRoundTestClass` does NOT define either attribute, so there is no inherited fallback. The mismatch is a latent `AttributeError`.
- Masked today because `@pytest.mark.parametrize("test_case", [])` produces zero test iterations.

---

### T3: Incomplete Test Configuration -- `next_behaviour_class` set to Ellipsis

**Claim:** `next_behaviour_class` is set to `...` (Ellipsis) in both behaviour test classes. The `complete()` method asserts `self.current_behaviour_id == self.next_behaviour_class.behaviour_id`, which would fail on Ellipsis.

**Verdict: TRUE**

**Reasoning:**
- `TestMechRequestBehaviour` sets `next_behaviour_class: Type[BaseBehaviour] = ...` (test_behaviours.py:104).
- `TestMechResponseBehaviour` sets `next_behaviour_class: Type[BaseBehaviour] = ...` (test_behaviours.py:122).
- `BaseMechInteractTest.complete()` (test_behaviours.py:96) calls `self.next_behaviour_class.behaviour_id` -- Ellipsis has no `.behaviour_id` attribute, so this would raise `AttributeError`.
- TODO comments at lines 102 and 120 confirm this is deliberately incomplete scaffolding.
- Masked by empty parametrize lists.

---

### T5: No Round Event Test Coverage

**Claim:** All round parametrize lists are empty. Zero test cases execute. No test classes exist for `MechVersionDetectionRound`, `MechInformationRound`, or `MechPurchaseSubscriptionRound`.

**Verdict: TRUE**

**Reasoning:**
- `TestMechRequestRound.test_run` has `@pytest.mark.parametrize("test_case", [])` (test_rounds.py:92).
- `TestMechResponseRound.test_run` has `@pytest.mark.parametrize("test_case", [])` (test_rounds.py:105).
- The FSM defines 5 active rounds: `MechVersionDetectionRound`, `MechInformationRound`, `MechRequestRound`, `MechPurchaseSubscriptionRound`, `MechResponseRound`.
- Only 2 have test classes, both with zero test cases. The other 3 have no test classes at all.
- 0% round logic coverage.

---

### T5-b: No Behaviour Event Test Coverage

**Claim:** Same empty-parametrize pattern for behaviour tests. No test classes for 3 of 5 behaviours.

**Verdict: TRUE**

**Reasoning:**
- `TestMechRequestBehaviour.test_run` has empty parametrize (test_behaviours.py:107).
- `TestMechResponseBehaviour.test_run` has empty parametrize (test_behaviours.py:125).
- 5 behaviour classes exist: `MechVersionDetectionBehaviour`, `MechInformationBehaviour`, `MechRequestBehaviour`, `MechPurchaseSubscriptionBehaviour`, `MechResponseBehaviour`.
- Only 2 have test classes, both with zero cases. 3 are entirely missing.

---

## Remediation Plan

### CI/Linting Constraints (from `.github/workflows/main_workflow.yml`)

Any changes to test files must pass these CI checks:

| Check | Tool | Key Rules |
|-------|------|-----------|
| Copyright headers | `tomte check-copyright --author valory` | Apache 2.0 header with `Copyright 20XX-20XX Valory AG` |
| Code formatting | `tomte check-code` (runs black, isort, flake8, mypy, pylint) | black line-length=88, isort profile, flake8 select=B,C,D,E,F,I,W |
| Spelling | `tomte check-spelling` | mdspell on markdown files |
| Package hashes | `tox -e check-hash` | Hashes in `packages.json` must match file contents |
| ABCI specs | `tox -e check-abciapp-specs` | FSM spec consistency |
| Docstrings | `tox -e check-abci-docstrings` | darglint sphinx-style docstrings |
| Handlers | `tox -e check-handlers` | Handler consistency |

**Important:** After editing any file under `packages/`, the package hash changes. The `check-hash` CI step will fail unless hashes are regenerated with `autonomy packages lock`. This must be the final step after all code changes.

---

### Fix 1: T2 -- Rename `round_class` to `round_cls`

**File:** `packages/valory/skills/mech_interact_abci/tests/test_rounds.py`

**Change:** Lines 89 and 102 -- rename `round_class` to `round_cls`.

```python
# BEFORE (line 89)
round_class = MechRequestRound

# AFTER
round_cls = MechRequestRound
```

```python
# BEFORE (line 102)
round_class = MechResponseRound

# AFTER
round_cls = MechResponseRound
```

---

### Fix 2: T3 -- Set correct `next_behaviour_class`

**File:** `packages/valory/skills/mech_interact_abci/tests/test_behaviours.py`

**Challenge:** The FSM transitions from `MechRequestRound` and `MechResponseRound` go to degenerate/final rounds (`FinishedMechRequestRound`, `FinishedMechResponseRound`) which have no matching behaviour in this skill. The `next_behaviour_class` depends on how the composing app chains this skill.

**Approach:** Since the behaviour tests need substantial mocking of contract calls, HTTP interactions, and a2a transactions (the behaviours are 600-900 lines each), and the `complete()` method asserts against `next_behaviour_class.behaviour_id`, the pragmatic fix is:

1. Remove the Ellipsis assignments.
2. Override `complete()` in each test class to skip the `next_behaviour_class` assertion (since the next behaviour is external to this skill).
3. Alternatively, set `next_behaviour_class` to the FSM's `initial_behaviour_cls` (`MechRequestBehaviour`) as a sentinel -- this works if the round behaviour loops back to the initial state after reaching a final round.

**Recommended approach:** Override `complete()` to not assert on `next_behaviour_class` since the transition target is outside this skill's scope.

---

### Fix 3: T5 -- Add meaningful round test cases

**File:** `packages/valory/skills/mech_interact_abci/tests/test_rounds.py`

The test infrastructure (`BaseMechInteractRoundTest`) uses `BaseRoundTestClass` and calls `_test_round` from `BaseCollectSameUntilThresholdRoundTest` (for `CollectSameUntilThresholdRound`-based rounds) or `BaseVotingRoundTest` (for `VotingRound`-based rounds). The `run_test` method expects `RoundTestCase` dataclasses with payloads, initial/final data, and expected events.

#### 3a. Fix existing test classes

For `TestMechRequestRound` and `TestMechResponseRound`:
- Fix `round_class` -> `round_cls` (Fix 1).
- Populate the parametrize list with `RoundTestCase` instances.

#### 3b. Add missing test classes

Create test classes for:
- `TestMechVersionDetectionRound` -- uses `VotingRound` base, so needs a separate base test class or direct use of `BaseVotingRoundTest`.
- `TestMechInformationRound` -- uses `MechInteractionRound` (CollectSameUntilThreshold).
- `TestMechPurchaseSubscriptionRound` -- uses `MechInteractionRound` (CollectSameUntilThreshold).

#### 3c. Test cases per round

**MechRequestRound** (CollectSameUntilThreshold, payload=`MechRequestPayload`):

| Test Case | Payloads | Expected Event | What it tests |
|-----------|----------|----------------|---------------|
| `done_with_requests` | All agents send same `MechRequestPayload(tx_submitter="submitter", tx_hash="0xabc", price=100, chain_id="1", safe_contract_address="0xsafe", mech_requests='[{"prompt":"q","tool":"t","nonce":"1"}]', mech_responses='[]')` | `Event.DONE` | Happy path -- threshold reached with non-None values and non-empty requests |
| `skip_request` | All agents send same payload with `mech_requests=None, mech_responses=None` but `tx_submitter` non-None | `Event.SKIP_REQUEST` | Custom `end_block` logic: DONE event converts to SKIP_REQUEST when no requests/responses |
| `buy_subscription` | All agents send same payload with all-None values | `Event.BUY_SUBSCRIPTION` | `none_event` fires when all payload attributes are None |
| `no_majority` | Agents send different payloads (no consensus) | `Event.NO_MAJORITY` | No majority possible |

**MechResponseRound** (CollectSameUntilThreshold, payload=`JSONPayload`):

| Test Case | Payloads | Expected Event | What it tests |
|-----------|----------|----------------|---------------|
| `done` | All agents send same `JSONPayload(information='[{"nonce":"1","result":"answer"}]')` | `Event.DONE` | Happy path |
| `no_majority` | Agents send different payloads | `Event.NO_MAJORITY` | No consensus |

**MechVersionDetectionRound** (VotingRound, payload=`VotingPayload`):

| Test Case | Payloads | Expected Event | What it tests |
|-----------|----------|----------------|---------------|
| `vote_v2` | All agents vote `True` | `Event.V2` | Positive vote -> V2 detected, `is_marketplace_v2=True` |
| `vote_v1` | All agents vote `False` | `Event.V1` | Negative vote -> V1, `is_marketplace_v2=False` |
| `vote_no_marketplace` | All agents vote `None` | `Event.NO_MARKETPLACE` | None vote -> no marketplace, `is_marketplace_v2=None` |
| `no_majority` | Mixed votes, no threshold | `Event.NO_MAJORITY` | No consensus |

**MechInformationRound** (CollectSameUntilThreshold, payload=`JSONPayload`):

| Test Case | Payloads | Expected Event | What it tests |
|-----------|----------|----------------|---------------|
| `done` | All agents send same `JSONPayload(information='[{"id":"1","address":"0x1",...}]')` | `Event.DONE` | Happy path -- mechs info collected |
| `none` | All agents send `JSONPayload(information=None)` | `Event.NONE` | `none_event` fires |
| `no_majority` | Agents disagree | `Event.NO_MAJORITY` | No consensus |

**MechPurchaseSubscriptionRound** (CollectSameUntilThreshold, payload=`PrepareTxPayload`):

| Test Case | Payloads | Expected Event | What it tests |
|-----------|----------|----------------|---------------|
| `done` | All agents send `PrepareTxPayload(tx_submitter="sub", tx_hash="0xhash")` | `Event.DONE` | Happy path -- subscription tx prepared |
| `none` | All agents send `PrepareTxPayload(tx_submitter=None, tx_hash=None)` | `Event.NONE` | `none_event` fires |
| `no_majority` | Agents disagree | `Event.NO_MAJORITY` | No consensus |

#### 3d. Implementation notes

- `BaseMechInteractRoundTest` inherits from `BaseRoundTestClass`, but `MechInteractionRound` extends `CollectSameUntilThresholdRound`. The `_test_round` method it calls needs `most_voted_payload` as a kwarg. This is passed via `test_case.kwargs`.
- For `MechVersionDetectionRound`, a new base test class is needed since it inherits from `VotingRound`, not `CollectSameUntilThresholdRound`. Use `BaseVotingRoundTest` from the framework.
- `most_voted_payload` is `payload.values[0]` -- the first field after `sender`, `round_count`, `id_`. For `MechRequestPayload` this is `tx_submitter`. For `JSONPayload` this is `information`. For `PrepareTxPayload` this is `tx_submitter`.
- Payloads must be created as `{agent_id: payload}` mappings where agent IDs match the participants in `synchronized_data`.

---

### Fix 4: T5-b -- Add meaningful behaviour test cases

**File:** `packages/valory/skills/mech_interact_abci/tests/test_behaviours.py`

#### 4a. Fix existing test classes

- Remove `next_behaviour_class = ...` assignments.
- Override `complete()` to skip the `next_behaviour_class` assertion since transitions go to degenerate rounds outside this skill.
- Populate parametrize lists.

#### 4b. Add missing test classes

Create test classes for:
- `TestMechVersionDetectionBehaviour`
- `TestMechInformationBehaviour`
- `TestMechPurchaseSubscriptionBehaviour`

#### 4c. Mocking strategy

The behaviours are complex (600-900 lines each) with contract calls, HTTP requests, IPFS uploads, and multisend tx building. Each test needs:

1. **`fast_forward()`** -- set up `SynchronizedData` with required fields.
2. **Mock contract calls** -- use `self.mock_contract_api_request()` for on-chain interactions.
3. **Mock HTTP requests** -- use `self.mock_http_request()` for subgraph queries and IPFS.
4. **`complete()`** -- assert the correct event is emitted after `act_wrapper()`.

Minimal meaningful tests per behaviour:

| Behaviour | Test Case | Mocks Needed |
|-----------|-----------|--------------|
| `MechVersionDetectionBehaviour` | Detect v2 marketplace | Contract call to check marketplace version |
| `MechInformationBehaviour` | Fetch mech info successfully | HTTP request to subgraph |
| `MechRequestBehaviour` | Prepare mech request tx | Contract calls for balance, allowance, Safe tx |
| `MechPurchaseSubscriptionBehaviour` | Prepare subscription purchase | Contract calls for NVM subscription |
| `MechResponseBehaviour` | Collect mech response | HTTP request to delivery endpoint |

---

### Fix 5: Post-change CI compliance

After all code changes, run these steps to ensure CI passes:

```bash
# 1. Format code
black packages/valory/skills/mech_interact_abci/tests/
isort packages/valory/skills/mech_interact_abci/tests/

# 2. Run linters locally
flake8 packages/valory/skills/mech_interact_abci/tests/
pylint packages/valory/skills/mech_interact_abci/tests/
mypy packages/valory/skills/mech_interact_abci/tests/

# 3. Regenerate package hashes (REQUIRED after any file change)
autonomy packages lock

# 4. Run the tests
pytest packages/valory/skills/mech_interact_abci/tests/test_rounds.py -v
pytest packages/valory/skills/mech_interact_abci/tests/test_behaviours.py -v

# 5. Run full CI check suite
tox -e check-packages
tox -e check-hash
tox -e check-abci-docstrings
tox -e check-abciapp-specs
tox -e check-handlers
```

---

## Implementation Priority

| Priority | Fix | Effort | Impact |
|----------|-----|--------|--------|
| 1 | T2: `round_class` -> `round_cls` | Trivial (2 lines) | Unblocks round tests |
| 2 | T5: Add round test cases | Medium | Covers round logic & event transitions |
| 3 | T3: Fix `next_behaviour_class` | Small | Unblocks behaviour tests |
| 4 | T5-b: Add behaviour test cases | High (complex mocking) | Covers behaviour logic |
| 5 | Regenerate hashes | Trivial | Required for CI |

---

## Summary

All 4 audit findings are **confirmed TRUE**. All are in test code -- production code is unaffected. The fixes are:

1. **T2:** One-line rename per test class (`round_class` -> `round_cls`).
2. **T3:** Remove Ellipsis, override `complete()` to handle degenerate round transitions.
3. **T5:** Add `RoundTestCase` entries covering DONE, NONE, SKIP_REQUEST, BUY_SUBSCRIPTION, NO_MAJORITY events. Add 3 missing round test classes.
4. **T5-b:** Add `BehaviourTestCase` entries with proper mocking. Add 3 missing behaviour test classes.
5. **CI:** Run `autonomy packages lock` after all changes; verify with `tox -e check-hash`.
