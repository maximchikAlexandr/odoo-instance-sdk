## 1. Foundation and characterization

- [x] 1.1 Rebase/merge the accepted MYL-55 CLI-output implementation into `feat/MYL-57-database-refresh` before command work; verify the shared typed context/output modules exist and keep the foundation merge separate from database semantic commits.
- [x] 1.2 Add/confirm characterization tests for current backup download audit ordering, restore existence postcondition/mapping, project manifest atomic writes, checkout preflight-before-artifacts, dry-run no-write behavior, and copy-mode failure retention before changing those paths.
- [x] 1.3 Record the current catalog schema version and migration fixtures, then identify the next sequential version used by this change; fail a focused test if a fixture version is skipped.

## 2. Typed models and project configuration

- [x] 2.1 Add frozen `BackupBranchOrigin`, `BackupProvenanceStatus`, `BackupProvenanceComparison`, `BackupFreshness`, `DatabasePreparationAction`, `DatabaseRefreshOptions`, `DatabasePreparationResult`, `AdminPasswordResetResult`, secret-free `EnvironmentCheckoutPlan`, and `EnvironmentCheckoutResult` types; make reset environment ID optional, export only intended public types, and cover construction/serialization/static typing plus rejection/absence of private config/path/argv fields.
- [x] 2.2 Add nullable `Backup.source_git_branch` and matching `NoBackup` default, update every production/test constructor, and verify legacy decoding yields `None`.
- [x] 2.3 Add frozen `TestInstanceProjectConfig` plus `ProjectConfig.test_instance`, `default_base_ref`, and finite-positive `refresh_after_hours`; reject unknown/secret-like keys and invalid empty values without echoing input secrets.
- [x] 2.4 Extend `ProjectConfig.load()`/`to_manifest()` and init/dry-run manifest projections for exact legacy and configured round-trips, preserving omission of absent sections and all existing PostgreSQL/runtime fields.
- [x] 2.5 Add project-config tests for explicit-base precedence, `HEAD` fallback, URL normalization, positive freshness boundaries, secret scan defense, and idempotent byte-stable serialization.

## 3. Catalog provenance migration

- [x] 3.1 Add exactly `backups.source_git_branch TEXT NULL` to the fresh schema and the next idempotent migration; preserve all backup/event/restore/environment/runtime rows and foreign keys.
- [x] 3.2 Extend `start_download`, row conversion, list/latest/latest-restore, and catalog identity verification to round-trip immutable nullable branch provenance without altering later state/path/checksum updates; add a restore-provenance lookup through `restores.backup_id` that does not filter by backup availability/state/file.
- [x] 3.3 Add migration tests from every supported legacy catalog fixture, including populated restore mappings/events, retry/idempotency, `NULL` legacy reads, known-branch reads, and proof that no duplicate table/column/store is created.
- [x] 3.4 Add catalog/resource tests that reject an in-memory `Backup` whose branch differs from its persisted row and that recover the original branch through `restores.backup_id`.

## 4. Instance-bound database primitives

- [x] 4.1 Extend `DatabaseResource.backup()` with keyword-only `source_git_branch`, centralized trim/control-character validation before mutation, pre-request catalog persistence, and unchanged behavior for omitted provenance.
- [x] 4.2 Add no-argument `DatabaseResource.reset_admin_password()` with local/exactly-one-configured-database binding and one constant committed Odoo shell script using `env.ref('base.user_admin')`, `ensure_one()`, and ORM `write`; prove `_run_shell_script_exclusive()` holds the instance's claimed artifact lock and rechecks its bound cluster, and do not add SQL, hashing, login, or numeric-ID paths.
- [x] 4.3 Add focused backup tests for explicit/configured/unknown branches, invalid branch rejection before catalog/HTTP, download failure audit, result parity, and restore identity verification.
- [x] 4.4 Add shell tests that inspect the bound database, executed source, and commit flag; reject zero/multiple configured databases; simulate missing/ambiguous XML ID and shell failure; and assert the password sentinel never appears in returned models, repr, stdout, stderr, exceptions, or logs.

## 5. Private project preparation workflow

- [x] 5.1 Add `database_preparation_lock_path()` and project+database-keyed `database_preparation_artifact_lock_path()` using canonical project identity; test preparation → PostgreSQL lifecycle → target/environment artifact ordering and verify CLI code never imports/acquires either lock.
- [x] 5.2 Implement pure helpers in `internal/database_preparation.py` for test-source/branch-origin resolution, ref normalization/comparison, freshness, collision-resistant UTF-8-safe target names, retained-artifact error context, and relevant-manifest conflict detection; table-test every boundary.
- [x] 5.3 Implement download-only preparation through the existing remote `DatabaseResource.backup()` and typed result, reading only `ODCLI_TEST_MASTER_PASSWORD`; verify missing config/secret fails before network/catalog/local mutation and manual calls always create a new backup.
- [x] 5.4 Implement restore-mode preflight under the preparation lock: reload state, validate local source config/master password/URL, resolve and validate one `ProjectRuntimeBinding` with exact Python/Odoo prefix and cwd, bind/call `PostgresCluster.ensure_running()`, prove the database manager with `names()`, and choose/recheck a unique target before remote download.
- [x] 5.5 Compose backup → existing neutralized copy restore → optional ORM reset → conflict-checked atomic default switch; add private `build_target_instance()` combining a mode-`0600` target-only ephemeral config with the preflighted runtime binding, project cluster, and canonical project+target artifact lock (not bare `from_config()`), then remove the config in `finally`, preserve unrelated manifest fields, and retain backup/database/mapping on every later failure.
- [x] 5.6 Expose manual preparation through the existing `EnvironmentResource.refresh_database()` typed method and have checkout freshness call the same private coordinator without recursive public calls or duplicate orchestration.
- [x] 5.7 Add deterministic failure-injection tests at preflight, after download, after restore/mapping, during admin reset, during manifest conflict, and during atomic write; assert the exact old default and retained-artifact contract at each point.
- [x] 5.8 Add same-project concurrency tests proving one stale-checkout refresh executes, a waiter rechecks/reuses it, manual refreshes serialize but do not freshness-skip, different projects proceed independently, and no target collision/duplicate default switch occurs.

## 6. Checkout provenance, freshness, and dry-run

- [x] 6.1 Retain private `_CheckoutPlan` for config/path/argv execution state; add the exact secret-free public `EnvironmentCheckoutPlan` fields specified in `models-types`, plus `EnvironmentCheckoutResult(environment, plan)`, and populate audit provenance independently from availability-aware freshness/preparation intent.
- [x] 6.2 Enforce known mismatch before catalog, filesystem, Git, Python, database, or process mutation in both shared/copy modes; cover normalized `refs/heads/` equality and intentionally textual unequal aliases.
- [x] 6.3 Enforce the legacy-unknown rule: only current-call explicit `source_database` proceeds with the stable warning; inferred project/config sources fail with refresh/`--source-db` guidance.
- [x] 6.4 Integrate configured freshness so stale/missing/unavailable mapped backups trigger restore preparation before final source/target planning, while absent `refresh_after_hours` never triggers age-based work; re-resolve all plan inputs after a successful default switch.
- [x] 6.5 Add public `plan_checkout() -> EnvironmentCheckoutPlan` and additive `checkout_with_plan() -> EnvironmentCheckoutResult`; keep canonical `checkout() -> DevelopmentEnvironment` as a delegating compatibility wrapper, re-plan after preparation, and use the same pure decisions/read-only catalog for dry-run without mutation.
- [x] 6.6 Add parameterized execution/dry-run tests for matched, mismatched, explicitly accepted unknown, inferred rejected unknown, known mismatch with missing/unreadable archive, threshold boundary, missing mapping/row, fresh waiter reuse, preparation failure before environment creation, shared/copy behavior, identical typed decisions after re-plan, canonical checkout return compatibility, and public-model secret/path/argv exclusion.

## 7. CLI adapter and output contract

- [x] 7.1 Add focused `commands/db.py` on the MYL-55 boundary and register `odcli db refresh` / `odcli db reset-admin-password` through the stable entry point without moving unrelated commands.
- [x] 7.2 Wire refresh project resolution, `--restore`, `--source-branch`, and `--reset-admin-password`; reject reset-without-restore as Click usage exit 2 before resource invocation and provide no password option/prompt.
- [x] 7.3 Wire standalone reset through the shared ready-environment resolver and `client.instance.from_environment()`, require exact single generated-config/recorded target-or-source database binding, return its environment ID, and reject project-root/ambiguous/remote contexts without choosing by recency or single-project membership.
- [x] 7.4 Make `commands/env.py` call public `plan_checkout()` for dry-run and additive `checkout_with_plan()` for execution; delete `_checkout_plan_dict`, never serialize `_CheckoutPlan`, use command-local Rich/shared direct JSON/TOON projection of only the public models, and cover audit/freshness/preparation/warnings plus sentinel exclusion.
- [x] 7.5 Add CLI tests for full help tree, context precedence, option validation, exit codes, stdout/stderr split, no prompts/ANSI in machine modes, semantic JSON/TOON equality, and sentinel redaction across success and every retained-artifact failure.

## 8. Integration, documentation, and delivery gates

- [x] 8.1 Add a disposable integration flow that downloads a ZIP+filestore backup, records source branch, restores uniquely with neutralization/mapping, and proves reset uses the exact preflighted Python/Odoo executable prefix, runtime cwd, target-only database, ready project PostgreSQL binding, and canonical exclusive target lock; verify ephemeral config removal, ORM reset, and atomic default selection.
- [x] 8.2 Add disposable failure/concurrency integration coverage for unhealthy local PostgreSQL before download, reset failure after restore, manifest-switch failure, known checkout mismatch before mutation, stale concurrent checkouts, and retained old/new databases/backups.
- [x] 8.3 Document `[test_instance]`, `default_base_ref`, `refresh_after_hours`, `ODCLI_TEST_MASTER_PASSWORD`, the exact-origin `ODCLI_TEST_INSTANCE_ORIGIN_PINS` approval flow, command examples, legacy-unknown warning/opt-in, failure retention/manual cleanup, and the prohibition on automatic checkout password reset.
- [x] 8.4 Run `openspec validate prepare-project-database`, migration/config/unit tests, focused database/checkout/CLI/integration suites, secret scans, `git diff --check`, static typing/lint/format, packaging tests, and the repository's full `make pr` gate; separate environmental fixture failures from regressions.
- [x] 8.5 Verify the selected remote push URL is SSH (`git@github.com:maximchikAlexandr/odoo-instance-sdk.git` or equivalent), push `feat/MYL-57-database-refresh` without changing unrelated remotes, and, per the user-approved delivery override, do not create a PR; record the final pushed SHA and branch.
- [x] 8.6 Normalize the main `database-backup` spec to a valid `## Requirements` structure without changing or dropping any requirement or scenario; verify strict validation and exact content preservation.
- [x] 8.7 Normalize the `backup-catalog` durable-path requirement with an explicit `MUST` while preserving its meaning and scenarios; verify strict validation and exact content preservation.
- [x] 8.8 Normalize the main `database-management` spec structure without changing its requirements or scenarios; verify strict validation and exact content preservation.
- [x] 8.9 Normalize the main `database-restore-tracking` spec structure without changing its requirements or scenarios; verify strict validation and exact content preservation.
- [x] 8.10 Normalize the `development-environment` requirements with explicit `MUST` wording without changing their meaning or scenarios; verify strict validation and exact content preservation.
- [x] 8.11 Normalize the `project-init` VS Code launch mapping requirement with explicit `MUST` wording without changing its meaning or scenarios; verify strict validation and exact content preservation.
- [x] 8.12 Normalize the main `server-cli` spec structure without changing its requirements or scenarios; verify strict validation and exact content preservation.
