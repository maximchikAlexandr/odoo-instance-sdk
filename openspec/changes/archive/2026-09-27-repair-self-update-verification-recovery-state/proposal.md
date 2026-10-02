## Why

`odcli update` can install the requested revision and then fail before commit because its final `odcli --version` subprocess is absent from the immutable execution plan. The resulting journal and rollback snapshot are subsequently hidden by `odcli update --check`, which can falsely report `already_current` solely because the installed SHA matches the target.

## What Changes

- Add the final executable-version verification as the planned process step `update.verify.version`, executed once through the existing command ledger before commit and cleanup.
- Make `odcli update --check` inspect the canonical update journal and snapshot before deciding that an installed revision is current.
- Report preserved incomplete state as `update_incomplete`, including the actual journal/snapshot state and one exact supported resume command; a dead recorded maintenance PID remains incomplete rather than being treated as active or absent.
- Preserve strict rejection of unplanned, duplicated, substituted, and omitted execution steps.
- Add unit and packaging regressions for successful verification/cleanup and interruption before verify/commit.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `self-update`: Make planned verification and read-only recovery-state reporting agree with the existing recoverable update lifecycle.

## Impact

- Affected implementation: `src/odoo_instance_sdk/internal/self_update_commands.py` and the canonical journal/snapshot inspection helpers in `src/odoo_instance_sdk/internal/self_update.py`.
- Affected contract: the self-update plan gains one process step and `update --check` gives incomplete recovery evidence priority over SHA equality.
- Affected tests: `tests/unit/test_self_update.py` and `tests/packaging/test_self_update.py`.
- No new dependency, data migration, CLI command, result model, or general-purpose execution abstraction is introduced.
