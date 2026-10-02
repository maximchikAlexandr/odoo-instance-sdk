## Why

`odcli stop` currently rebuilds the expected project-runtime argv from mutable checkout configuration, so an unchanged process that OdCLI launched and still owns can become impossible to stop after ordinary checkout evolution. The same rejection is reported only as `runtime identity mismatch: argv`, which is too coarse to distinguish configuration drift from PID reuse while preserving the fail-closed termination boundary.

## What Changes

- Capture a JSON-safe, secret-free launch-identity snapshot from the same immutable detached/foreground process step that is executed, and persist it with the owner-neutral runtime record.
- Validate a live process against that captured launch identity instead of reconstructing the authoritative project identity from current checkout configuration.
- Preserve the existing PID/create-time, executable, cwd, config, process-group, owner, conditional-clear, and bounded termination checks.
- Return bounded sanitized mismatch components such as `argv: executable-prefix` or `argv: --database`, without exposing argument values, passwords, or raw secret-bearing data.
- Migrate existing catalogues explicitly. Legacy runtime rows without captured identity remain fail-closed and are not silently validated from mutable configuration.
- Extend the existing parameterized runtime-stop, detached-launch, catalogue migration, redaction, and public CLI leaf coverage; do not add a second runner, registry, or test inventory.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `instance-runtime-binding`: Persist and consume the immutable, secret-free launch identity for safe owner-neutral runtime stop.
- `cli-odcli`: Keep an owned runtime stoppable across unrelated checkout drift and expose sanitized field-level rejection evidence.

## Impact

- Affected implementation: instance command capture and runtime persistence, owner-neutral runtime identity validation, SQLite catalogue schema/migration, and CLI error projection.
- Affected tests: `tests/unit/test_runtime_stop.py`, detached/foreground runtime identity tests, catalogue record/migration tests, and existing CLI output/security matrices.
- Public successful command payloads remain unchanged. Failure text becomes more specific but remains sanitized.
- No new runtime dependency, process abstraction, supervisor, port-based ownership inference, or automatic termination on mismatch is introduced.
