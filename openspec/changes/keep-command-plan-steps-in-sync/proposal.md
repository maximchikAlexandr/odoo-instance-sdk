## Why

`Command.from_prepared()` currently accepts the public `ExecutionPlan` and private `PreparedCommand` independently, so dry-run can omit work that `.run()` performs or advertise work that execution never performs. The detached Odoo launch already exhibits this defect: its private snapshot contains four lifecycle actions that its public plan does not expose.

## What Changes

- Reject command construction when the ordered public step projection differs from the ordered projection of the private executable snapshot.
- Keep projection validation in the shared command-construction layer so every builder receives the same fail-closed invariant.
- Include detached-launch port validation, spawn, liveness confirmation, and runtime persistence actions in its dry-run plan in execution order.
- Add focused regression coverage for both mismatch directions and detached-launch plan completeness while preserving side-effect-free dry-run behavior.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `command-execution`: Strengthen the immutable command contract so construction requires exact ordered parity between public plan steps and the private executable snapshot.

## Impact

- Affected shared boundary: `src/odoo_instance_sdk/execution.py`.
- Exposed existing caller mismatch: detached launch planning in `src/odoo_instance_sdk/resources/instance/planning.py`.
- Regression coverage: execution-model and detached-run unit tests.
- No public signature, dependency, persistence schema, or migration change is required. Independently mismatched internal plan/snapshot construction is intentionally rejected because accepting it is the defect.
