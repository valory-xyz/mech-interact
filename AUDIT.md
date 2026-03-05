# FSM Audit Report

**Scope:** `packages/valory/skills/mech_interact_abci/`
**Date:** 2026-03-05

## CLI Tool Results

All framework analysis tools passed:
- `autonomy analyse fsm-specs` -- Check successful
- `autonomy analyse docstrings` -- No update needed
- `autonomy analyse handlers` -- All handlers verified

## Critical Findings

No findings.

## High Findings

No findings.

## Medium Findings

### T2: Round Test Attribute Name Mismatch

- **File:** `packages/valory/skills/mech_interact_abci/tests/test_rounds.py:89,102`
- **Issue:** Both `TestMechRequestRound` and `TestMechResponseRound` define `round_class` but the base class `BaseMechInteractRoundTest` declares and uses `round_cls` (line 58, 68). If test cases were provided, `run_test()` would raise `AttributeError`.
- **Code:**
  ```python
  class TestMechRequestRound(BaseMechInteractRoundTest):
      round_class = MechRequestRound  # wrong attribute name

  class TestMechResponseRound(BaseMechInteractRoundTest):
      round_class = MechResponseRound  # wrong attribute name
  ```
- **Fix:**
  ```python
  class TestMechRequestRound(BaseMechInteractRoundTest):
      round_cls = MechRequestRound

  class TestMechResponseRound(BaseMechInteractRoundTest):
      round_cls = MechResponseRound
  ```
- **Note:** Currently masked because `@pytest.mark.parametrize("test_case", [])` means zero test cases run.

### T3: Incomplete Test Configuration -- `next_behaviour_class` set to Ellipsis

- **File:** `packages/valory/skills/mech_interact_abci/tests/test_behaviours.py:104,122`
- **Issue:** `next_behaviour_class` is set to `...` (Ellipsis) in both `TestMechRequestBehaviour` and `TestMechResponseBehaviour`. The `complete()` method (line 96) asserts the current behaviour matches `next_behaviour_class.behaviour_id`, which would fail on Ellipsis.
- **Code:**
  ```python
  class TestMechRequestBehaviour(BaseMechInteractTest):
      next_behaviour_class: Type[BaseBehaviour] = ...

  class TestMechResponseBehaviour(BaseMechInteractTest):
      next_behaviour_class: Type[BaseBehaviour] = ...
  ```
- **Fix:** Set to the actual next behaviour class per the FSM transition.
- **Note:** Also masked by empty parametrize lists.

### T5: No Round Event Test Coverage

- **File:** `packages/valory/skills/mech_interact_abci/tests/test_rounds.py`
- **Issue:** All `@pytest.mark.parametrize("test_case", [])` decorators have empty lists. Zero test cases execute for any round. Additionally, no test classes exist for `MechVersionDetectionRound`, `MechInformationRound`, or `MechPurchaseSubscriptionRound`.
- **Events untested:** All 5 rounds x all event transitions = 0% coverage of round logic.

### T5-b: No Behaviour Event Test Coverage

- **File:** `packages/valory/skills/mech_interact_abci/tests/test_behaviours.py`
- **Issue:** Same pattern -- empty parametrize lists for both behaviour test classes. No test classes for `MechVersionDetectionBehaviour`, `MechInformationBehaviour`, or `MechPurchaseSubscriptionBehaviour`.

## Low Findings

No findings.

## Summary

| Severity | Count |
|----------|-------|
| Critical | 0     |
| High     | 0     |
| Medium   | 4     |
| Low      | 0     |

## Notes

- All structural checks (C1-C4, H1-H3, M1-M2, M4-M5) passed clean. The FSM definition, transition function, event wiring, payload classes, and collection/selection keys are all correct.
- The medium findings are all in tests -- the tests are scaffolded with TODO comments but have no actual test cases. The `round_class` vs `round_cls` typo and Ellipsis `next_behaviour_class` are latent bugs that would surface once test cases are added.
- No background apps, no composition/chaining, no resource lifecycle issues, no shared mutable references.
