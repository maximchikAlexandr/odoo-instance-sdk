## 1. Enforce command projection parity

- [ ] 1.1 In `Command.from_prepared()`, compare `plan.steps` with the complete ordered public projection of `prepared.steps` before fingerprinting, command creation, or `_COMMANDS` registration.
- [ ] 1.2 Raise a secret-free `PlanValidationError` for any missing, extra, reordered, differently typed, or differently projected step while preserving valid plan observations, warnings, fingerprints, and repeated-run behavior.

## 2. Synchronize detached launch planning

- [ ] 2.1 Build one detached command-step tuple containing the existing preparation/process steps and all four lifecycle actions, and use that tuple for both the public plan and private prepared command.
- [ ] 2.2 Preserve detached execution order, redaction, action classifications, observations, and side-effect-free construction/dry-run behavior.

## 3. Add regression coverage

- [ ] 3.1 Add focused command-construction tests for private-only, public-only, same-identifier field/type, and order mismatches, plus a valid matching command.
- [ ] 3.2 Extend detached-run coverage to assert that dry-run exposes `instance.detached.assert_port`, `instance.detached.spawn`, `instance.detached.confirm_alive`, and `instance.detached.persist` together with preparation/process steps and starts no effect.
- [ ] 3.3 Update any invalid production builder or test double exposed by the invariant to construct public and private steps from the same captured sequence, without weakening validation or changing unrelated behavior.

## 4. Verify repository contracts

- [ ] 4.1 Run the focused execution-model and detached-run unit tests, then the complete automated test suite.
- [ ] 4.2 Run repository formatting, lint, strict type, production line-limit, and execution-architecture checks; resolve only failures attributable to this change.
