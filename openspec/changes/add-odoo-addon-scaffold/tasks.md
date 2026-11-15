# Tasks

## 1. Config and merge

- [x] 1.1 Add `UserConfig`, `ScaffoldConfig`, and `get_user_config_path()`, and load an optional `[scaffold]` table from the user file and `ProjectConfig`. Verify with unit tests that a missing user file is valid and that project round-trip keeps the section.
- [x] 1.2 Implement precedence, component selection, and manifest validation. Verify with parametrized tests for override, reversal, conflicts, missing author, reserved `data`/`demo`, and secret keys.

## 2. Native scaffold command

- [x] 2.1 Compose repository template fragments and capture `create_command()` with filesystem actions plus the exact Odoo process step. Verify dry-run creates no temporary or destination files and that an existing target, a path-like name, and a missing executable fail first.
- [x] 2.2 Execute that same command through `internal/proc`, delete the temporary tree on every outcome, and roll back a partial addon when the native process fails. Verify with a fake executable and result serialization.

## 3. CLI, inventory, and docs

- [x] 3.1 Add `odcli scaffold` and `odcli config path`, register both in `PUBLIC_LEAF_CASES`, and regenerate the command matrix. Verify the leaf inventory and JSON/TOON tests accept the new rows.
- [x] 3.2 Document the global path, project override, CLI examples, and SDK example in the README. Verify the command inventory matches the Click tree and the Python example imports.

## 4. Real Odoo check

- [x] 4.1 Add one real Odoo 19 integration test that runs `scaffold --template` and loads the generated manifest. Verify the test is marked for the GitHub real-Odoo tier and is excluded from the local default pytest selection.
