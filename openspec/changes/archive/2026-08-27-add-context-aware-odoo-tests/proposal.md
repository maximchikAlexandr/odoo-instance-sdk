## Why

Local Odoo tests currently require callers to supply both module names and tags even when the working directory or Git diff already identifies the relevant addons. GitHub issues #25 and #26 need one safe local test path that resolves this context deterministically, reuses the existing Odoo shell runner, and adopts the typed context and Rich/JSON/TOON boundary established by MYL-55.

## What Changes

- Add `odcli test [TARGET]` selection for an explicit addon module, the addon containing the current directory, or one Python test file beneath `<module>/tests/`.
- Add `odcli test --changed [--base REF] [--dry-run]` to select sorted unique directly changed addons from committed, staged, unstaged, and untracked non-ignored Git paths without network access.
- Resolve addons only through canonical, worktree-local configured `addons_path` roots and reject ambiguous, escaping, symlink-unsafe, or otherwise unmappable addon paths before starting Odoo.
- Introduce the minimal typed operation contract `OdooTestSpec` and `OdooTestResult`; retain `odoo.tests.shell.run_tests()` as the only runner and perform installed-module/database preflight without mutation.
- Preserve `odcli module test` as a backward-compatible alias into the same selection/execution path, including native Odoo test-tag grammar, zero-test policy, failure exit codes, sanitized diagnostics, and `--allow-empty`.
- Emit resolved environment, selector/base provenance, modules, and exit code through the shared `rich|json|toon` CLI output contract; executed runs additionally emit effective native tags, options, counts, and failure/zero flags, while `--dry-run` and `no_addon_changes` omit execution-only fields and never fabricate a runner result or execution progress.
- Keep changed-addon selection intentionally limited to directly changed modules; do not add dependency graphs, test catalogs, scheduler/resources, CI adapters, cache, coverage selection, sharding, database provisioning, or automatic module updates.

## Capabilities

### New Capabilities

- `local-odoo-testing`: Defines safe context/file/changed-addon selection, the typed test operation contract, preflight, single-runner execution, dry-run behavior, and deterministic result/exit semantics.

### Modified Capabilities

- `cli-odcli`: Adds the top-level `test` command, converts `module test` into a compatibility alias, and applies the shared Rich/JSON/TOON envelope and diagnostics contract to both entry points.

## Impact

- Affected implementation areas: the MYL-55 `commands/context.py` and `commands/output.py` seams, a focused `commands/test.py` adapter/selector, the existing `internal/automation.py` Odoo shell runner path, and only the smallest existing project/config helpers needed to expose recorded addon/base configuration.
- Affected public contract: new transport-neutral `OdooTestSpec` and `OdooTestResult` types; no `TestResource`, repository, service hierarchy, selector DSL, scheduler, or test database API.
- External processes: local `git` commands are read-only, argv-based, NUL-safe, and network-free; Odoo starts only after selection and preflight succeed.
- Validation: temporary Git repositories cover merge-base plus index/worktree/untracked state and path/symlink safety; one opt-in disposable Odoo integration test confirms selector-to-runner execution.
- Delivery: implementation follows the accepted MYL-55 foundation and is isolated on `feat/MYL-56-changed-odoo-tests`; its PR must reference MYL-56 and GitHub #25/#26 and use SSH for push.
