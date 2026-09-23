## 1. Foreground `run` releases the artifact lock before waiting (item 1)

- [ ] 1.1 Add a unit regression test that reproduces the lock conflict: foreground `run` holds the shared artifact lock during `wait_foreground_process()` and a parallel `stop` fails with `Lock conflict ... (exclusive)`; assert the fix lets `stop` acquire the exclusive lock and terminate the registered runtime.
- [ ] 1.2 Narrow `run_foreground_command()` to enter the shared lock only for atomic spawn/runtime-identity registration and cleanup/revalidation; release the lock before `wait_foreground_process()`. Convenience `run_foreground()` only delegates. Expression SHALL NOT appear in this lock/lifecycle.
- [ ] 1.3 Verify PID/create-time/process-group validation, stale-runtime recognition, foreground stdio, signals, and exit code are preserved.
- [ ] 1.4 Add a live E2E test that runs real `odcli run` in one CLI session and real `odcli stop` in a second, asserts `stop` succeeds, the registered process group terminates, the HTTP port is released, and runtime identity is cleaned up.
- [ ] 1.5 Add the scenario to the mandatory live E2E regression set so subsequent lifecycle changes cannot reintroduce the defect.
- [ ] 1.6 Run focused tests, Ruff, and mypy.

## 2. `git commit` uses already-resolved project ticket settings (item 2)

- [ ] 2.1 Add a regression test that `odcli git commit ... --ticket PROJ-123 --dry-run` in an environment worktree without its own `.odcli/project.toml` still appends the configured ticket URL as the second paragraph.
- [ ] 2.2 Pass the already-resolved project settings into the Git resource instead of re-loading `.odcli/project.toml` from the worktree root.
- [ ] 2.3 Add tests covering different project root and worktree root combinations.
- [ ] 2.4 Run focused tests, Ruff, and mypy.

## 3. `env rm --force-connections` (item 3)

- [ ] 3.1 Add `--force-connections` to `odcli env rm` and route it through `EnvironmentManager.remove_command()` into `build_database_drop_command()` with COPY-only terminate scope.
- [ ] 3.2 Make the drop checker command-aware so `env rm` without `--force-connections` names the now-existing flag (and MAY mention `odcli stop`).
- [ ] 3.3 Add a CLI/SDK test with one active session on the COPY database: with `--force-connections` only that database's sessions are terminated; without the flag removal stays fail-closed.
- [ ] 3.4 Verify protected/default/shared databases and sessions of other databases are untouched.
- [ ] 3.5 Run focused tests, Ruff, and mypy.

## 4. Truthful PostgreSQL state (item 4)

- [ ] 4.1 In `postgres up`, build the diagnostic result from the captured cluster even when `ensure_running_command()` returns `None`.
- [ ] 4.2 In the monitor, stop deriving `STOPPED` from an empty or unparseable Docker resource snapshot; `stopped` follows only from a successful `PostgresCluster.status_command()`.
- [ ] 4.3 Make `stats_failed` degrade only metrics; lifecycle state comes from `PostgresCluster.status_command()`.
- [ ] 4.4 Add a test covering successful `Command[None]` and a running container with unavailable statistics.
- [ ] 4.5 Run focused tests, Ruff, and mypy.

## 5. Self-contained `init` (item 5)

- [ ] 5.1 Add `--test-url`, `--test-database`, `--test-branch`, `--local-config`, and `--allow-partial` to `odcli init`.
- [ ] 5.2 Fill the existing `[test_instance]` from the new options; select generated `.odcli/odoo.conf` as effective local `source_config` under `--local-config`.
- [ ] 5.3 Create `.odcli/.env` with `ODCLI_TEST_INSTANCE_ORIGIN_PINS=<canonical origin>` (existing `test_instance_trust` grammar) and an empty `ODCLI_TEST_MASTER_PASSWORD=` under `0600`; ignore it in Git.
- [ ] 5.4 Record `data_dir` as the absolute `{project_root}/.odcli/filestore` in generated `.odcli/odoo.conf` for self-contained Compose setups.
- [ ] 5.5 Implement the blocking completeness check using the missing-group predicates in design D5. `--dry-run` SHALL NOT call `DatabaseResource.names()`; `test_database` is missing on dry-run when absent from flags and manifest. Execute MAY call `names()` as an HTTP ActionStep and drop `test_database` when the list has exactly one name. Interactive: Click confirm default cancel when not `no_input` and RICH. Non-RICH uses the `--no-input` path. `--no-input` fails with `init_incomplete` unless `--allow-partial`. `--dry-run` does not prompt. `getpass` is MAY, not required.
- [ ] 5.6 Preserve an existing valid `[test_instance]` on re-run without test-instance options; explicit new options replace it atomically after validation.
- [ ] 5.7 Keep `--dry-run` side-effect-free: no manifest/config/dotenv writes, no secret generation, no cluster mutation.
- [ ] 5.8 Add tests covering full setup, partial setup with `--allow-partial`, `--no-input` `init_incomplete`, re-run preserving `[test_instance]`, unique remote database not listed as missing, and external PostgreSQL still requiring `--allow-partial` when test instance is missing.
- [ ] 5.9 Add a regression test for the public CLI flow `init → fill password → db refresh --restore --dry-run` without manual TOML/config edits.
- [ ] 5.10 Run focused tests, Ruff, and mypy.

## 5a. Bootstrap database `tmp` (item 5 GitHub comment)

- [ ] 5a.1 After owned Compose cluster start, create Odoo database `tmp` as a ProcessStep on the existing `init` command via the instance runtime builder plus `--database tmp --init=base --stop-after-init` through `internal/proc`, `shell=False`. Tests use the proc recorder (or live E2E); do not add a module-local subprocess patch.
- [ ] 5a.2 Idempotent: valid `tmp` is not recreated; invalid same-named DB fails with `init_bootstrap_failed`.
- [ ] 5a.3 After `--stop-after-init`, confirm `base` via SQL `SELECT state FROM ir_module_module WHERE name = 'base'` on `tmp` = `installed`. Do not POST `version_info` at init.
- [ ] 5a.4 Auxiliary restore uses `tmp` and does not add hidden `--database=__odcli_restore__` or `--db-filter=^$`.
- [ ] 5a.5 After a successful restore, switch the project default to the restored database; `tmp` remains bootstrap.
- [ ] 5a.6 `--dry-run` shows the `tmp` step and does not spawn; a failure does not return successful project setup.
- [ ] 5a.7 Add a regression flow with `pytest.mark.parametrize` for Odoo 13 and Odoo 19: Compose cluster → `tmp` → stopped-project `db restore` → Database Manager `303` → PostgreSQL postcondition → default DB switch.
- [ ] 5a.8 If an owned Compose project has no valid `tmp`, first `odcli run` runs the same ProcessStep+SQL.
- [ ] 5a.9 Run focused tests, Ruff, and mypy.

## 6. Large-backup validation (item 6)

- [ ] 6.1 Remove the fixed 512 MiB per-member and 2 GiB total ceilings as unconditional invalidity criteria.
- [ ] 6.2 Stream CRC/test with a 65536-byte buffer; keep `_MAX_ZIP_ENTRIES = 4096`; do not read `dump.sql` or filestore fully into memory.
- [ ] 6.3 Before restore, sum declared uncompressed sizes with overflow-safe arithmetic and compare to `shutil.disk_usage(Path(data_dir).resolve()).free` when `data_dir` is set, else `shutil.disk_usage(get_backups_dir()).free`, minus `max(1 GiB, 10%)`; return `backup_insufficient_disk`.
- [ ] 6.4 Add optional `backup.max_uncompressed_bytes` in `get_config_root()/user.toml` with error `backup_operator_limit`.
- [ ] 6.5 Keep `_MAX_ZIP_COMPRESSION_RATIO = 100` from invalidating `dump.sql` alone; `backup_corrupt` for malformed ZIP/CRC; `backup_unsafe` for traversal/duplicate/encrypted/unsupported and non-`dump.sql` ratio > 100.
- [ ] 6.6 Read `manifest["version"]` else `f"{manifest['major_version']}.0"` so Odoo 13 reports `13.0`.
- [ ] 6.7 Keep path traversal, malformed ZIP, encrypted/unsupported/duplicate members, CRC, and `_MAX_ZIP_ENTRIES = 4096`.
- [ ] 6.8 Add tests using `pytest.mark.parametrize` covering the 1.21 GB fixture, a >100 GB modeled via ZIP metadata/streaming fixture, insufficient disk, operator maximum, ZIP bomb/path traversal/duplicate/encrypted regressions, bounded-memory streaming, and `13.0` version reading.
- [ ] 6.9 Make Rich/JSON/TOON distinguish corrupted archive, unsafe archive structure, operator size policy, and insufficient local resources.
- [ ] 6.10 Run focused tests, Ruff, and mypy.

## 7. Proven filestore `data_dir` on self-contained restore (item 7)

- [ ] 7.1 Full self-contained `init` records `data_dir={project_root}/.odcli/filestore` (depends on 5.4).
- [ ] 7.2 Restore writes the canonical `data_dir` into the restore binding alongside cluster/database/backup identity.
- [ ] 7.3 Before restore, verify the owned `data_dir` is a regular directory inside allowed project storage and not a symlink.
- [ ] 7.4 `db rm` deletes only the exact contained non-symlink filestore and returns `deleted` or `absent` with the path.
- [ ] 7.5 External config without a provable `data_dir` stays fail-closed `unknown`; no directory is deleted by database name or platform default.
- [ ] 7.6 `doctor` MAY emit an existing-style finding when a restore binding exists without `data_directory`; no new doctor subsystem and no auto-ownership.
- [ ] 7.7 Add a test covering `init → restore with filestore → db rm` and absence of filestore after the command.
- [ ] 7.8 Run focused tests, Ruff, and mypy.

## 8. Explicit admin password reset (item 8)

- [ ] 8.1 Replace the literal `admin` value in the reset flow with one mandatory secret input.
- [ ] 8.2 Prompt with `getpass` twice only when RICH and not `--no-input`; `--dry-run`/`json`/`toon` never prompt; `--yes` does not skip prompt; otherwise read process `ODCLI_ADMIN_PASSWORD` then `.odcli/.env`.
- [ ] 8.3 If the secret is absent, fail with `admin_password_required` before any restore/drop mutation.
- [ ] 8.4 Use the same mechanism for `db reset-admin-password`, restore, and COPY replacement.
- [ ] 8.5 Verify the secret is absent from argv, shell history, manifest, catalog, plan, Rich/JSON/TOON, logs, and exception text; the result reports only the fact and provenance (`prompt` or `environment`).
- [ ] 8.6 Add a regression flow covering Odoo 13 and Odoo 19 that logs in as `base.user_admin` via the real-Odoo XML-RPC test-support helper.
- [ ] 8.7 Run focused tests, Ruff, and mypy.

## 9. Optional `test_instance.database` (item 9)

- [ ] 9.1 Make `test_instance.database` optional in the manifest model and parser/writer.
- [ ] 9.2 In remote-refresh preflight, when `database` is absent, obtain names via `DatabaseResource.names()`; select exactly one; zero=`remote_database_none`; many=`remote_database_ambiguous` listing names; unavailable list=`remote_database_list_unavailable`; all three fail before download.
- [ ] 9.3 Explicit `database` has priority and works without available database list.
- [ ] 9.4 Reflect the auto-detected name in plan/result and backup provenance; do not write it back to `project.toml`.
- [ ] 9.5 Manifest round-trip does not add `database` when it was not set.
- [ ] 9.6 Add tests covering explicit, one, zero, many, and unavailable-list scenarios using `pytest.mark.parametrize`.
- [ ] 9.7 Run focused tests, Ruff, and mypy.

## 10. Long-restore Rich progress (item 10)

- [ ] 10.1 Reuse `StepObserver`/`StepEvent` and Rich Live with stage ids `backup_prepare`, `auxiliary_start`, `db_restore`, `db_verify`, `filestore_restore`, `admin_reset`, `default_switch`.
- [ ] 10.2 Emit `StepEvent` every 0.5 s during blocking actions without child stdout.
- [ ] 10.3 Show percent only when streaming dump/filestore bytes provide a total.
- [ ] 10.4 On error, preserve the safe primary cause, last stage id, and elapsed seconds.
- [ ] 10.5 Keep JSON/TOON single-document contract and exit code; terminal progress does not pollute machine stdout.
- [ ] 10.6 Add a test covering a long restore without stdout and Rich indicator updates until completion.
- [ ] 10.7 Run focused tests, Ruff, and mypy.

## 11. Auxiliary Odoo pipe drain (item 11)

- [ ] 11.1 Extend the process boundary for spawned long-running handles to drain `stdout` and `stderr` concurrently and continuously.
- [ ] 11.2 Keep the last `_TIMEOUT_TAIL_BYTES = 8192` bytes per stream after redaction; do not redirect to `DEVNULL`.
- [ ] 11.3 Attach the bounded tail and the original error type to readiness/startup failures instead of `raise ... from None`.
- [ ] 11.4 Terminate readers together with the owned process group on cleanup and interrupt.
- [ ] 11.5 Add tests covering a noisy auxiliary child exceeding pipe capacity, simultaneous drain, bounded memory, readiness failure diagnostics, and cleanup/interrupt without leaked threads/handles.
- [ ] 11.6 Verify JSON/TOON keep one final document and the diagnostic stream does not pollute stdout.
- [ ] 11.7 Run focused tests, Ruff, and mypy.

## 12. `module update` traceback preservation (item 12)

- [ ] 12.1 When a valid nonce-framed payload is present, use `user_error` or `finalization_error` from the common shell wrapper with priority.
- [ ] 12.2 When payload is missing or malformed, fall back to the last `_TIMEOUT_TAIL_BYTES = 8192` redacted bytes of `stderr`, not the first N characters.
- [ ] 12.3 Do not emit full unlimited tracebacks; preserve existing redaction.
- [ ] 12.4 Ensure Rich, JSON, and TOON return the same stable error code and safe details.
- [ ] 12.5 Add a regression test covering a long startup prefix, traceback at the end, and malformed/missing payload.
- [ ] 12.6 Run focused tests, Ruff, and mypy.

## 13. `odcli --version` VCS revision (item 13)

- [ ] 13.1 Read optional PEP 610 `direct_url.json` via `importlib.metadata`.
- [ ] 13.2 If `vcs_info.commit_id` is hex of length >= 7, append the first 7 characters.
- [ ] 13.3 Wheel/sdist installs without `direct_url.json` keep the package version; malformed/missing metadata safely falls back.
- [ ] 13.4 Keep the command fast; no Git call, no checkout, no network, no operation-only dependency imports.
- [ ] 13.5 Add tests covering VCS, non-VCS, and malformed metadata.
- [ ] 13.6 Run focused tests, Ruff, and mypy.

## 14. `odcli bug-report` and `odcli-bug-report` skill (item 14)

- [ ] 14.1 Implement `bug_report_init_command()` writing `get_user_root()/bug-reports/<REPORT_ID>/`; CLI delegates; no `cli_only_reason`.
- [ ] 14.2 Implement submit `--dry-run` with 262144-byte `report.md` cap, default repo `maximchikAlexandr/odoo-instance-sdk`, GitHub labels `["alpha-testing"]` only, and separate `report_valid`/`submit_ready` booleans.
- [ ] 14.3 Submit when last `reviews/N.json` (N in 1..3, written by the skill, validated by CLI) is `approved` for the payload hash; no `--skip-review`/`--force`.
- [ ] 14.4 Invoke `gh` through `internal/proc` with argv `--body-file -` after an ActionStep recording submit intent; inherit `GH_TOKEN`; do not copy it.
- [ ] 14.5 Lock `get_locks_dir()/bug-report-{REPORT_ID}.lock` without Expression; unknown network outcome does not blind re-POST.
- [ ] 14.6 Config `[bug_report].repository` in `get_config_root()/user.toml`; labels always `alpha-testing`; do not infer repo from the Odoo project Git remote. `submit_outcome_unknown` on uncertain `gh`.
- [ ] 14.7 Create `.agents/skills/odcli-bug-report/` with emergency unblock and reviewer prompt (skill-only).
- [ ] 14.8 Make the skill runtime-agnostic and Ponytail-aware (works without Ponytail).
- [ ] 14.9 Add tests with `pytest.mark.parametrize` covering two drafts, dry-run, early local errors, no submit without approval, stale hash, third refusal, successful submit, repeat URL, concurrent submit, timeout, multiline body, secrets, path confinement. `gh` spawn tests use the `internal/proc` recorder (or live E2E); do not add a module-local subprocess patch.
- [ ] 14.10 Add skill scenario tests: approval first time, approval after correction, three refusals, unavailable reviewer, changed text after approval.
- [ ] 14.11 Register leaves in `PUBLIC_LEAF_CASES` with named `sdk_primitive` and `e2e_disposition=not-applicable`.
- [ ] 14.12 Run focused tests, Ruff, and mypy.

## 15. Centralized HTTP and XML-RPC transport (item 15)

- [ ] 15.1 Introduce a narrow internal transport layer with a minimal base HTTP client wrapping `httpx` (context manager/`close()`, connection pooling, dependency injection for tests via a concrete protocol, no cross-origin cookie/auth reuse, preserved timeout/streaming semantics). The transport layer SHALL be lazily imported; `httpx` SHALL remain absent after `import odoo_instance_sdk.cli` (startup-evidence gate preserved).
- [ ] 15.2 Implement `OdooHttpClient` for all Odoo HTTP endpoints (health/status, database list/create/drop/backup/restore) with centralized redacted logging and typed error conversion without leaking `httpx` exceptions.
- [ ] 15.3 Migrate `database`, `health`, and `monitor` to depend on `OdooHttpClient`; CLI SHALL NOT import it; construct/close one client per resource operation; preserve public results, error codes, and exit behaviour.
- [ ] 15.4 Add a line-specific architecture inventory allowlist only for files under `src/odoo_instance_sdk/internal/transport/`. `OdooHttpClient` is internal, not a public SDK type.
- [ ] 15.5 Preserve streaming, timeout, cancellation, and cleanup for backup/restore; large payloads are not buffered whole.
- [ ] 15.6 Add an architecture test confirming `httpx` types/exceptions do not leak into public SDK/resource interfaces.
- [ ] 15.7 Do not add retry/backoff/circuit-breaker; add a test confirming one network attempt per previously-single operation.
- [ ] 15.8 Extract `_xmlrpc_probe` into a test-support helper; do not add production `OdooXmlRpcClient`; architecture gate: zero `ServerProxy` in `src/`.
- [ ] 15.9 Record HTTP characterization tests before/after; XML-RPC stays on the test helper.
- [ ] 15.10 Unit tests substitute the client/transport boundary, not global `httpx.Client`/`httpx.get`/`ServerProxy`; cover streaming/session/error cases separately.
- [ ] 15.11 Run focused tests, Ruff, mypy, and the architecture gates.

## 16. `odcli update` (item 16)

- [ ] 16.1 Implement `update_command()`; `--check` is `process-previewable-read-only` with the uv `--dry-run` argv from design D16 (no pip fallback); mutating `already_current` only for a 40-char SHA matching PEP 610; CLI delegates; no `cli_only_reason`; no Expression.
- [ ] 16.2 Implement `odcli update --dry-run`: emit frozen ProcessSteps without launching `uv` or `odcli`.
- [ ] 16.3 Implement Inspect as in-process ActionSteps (PEP 610, `sys.executable`, uv-tool layout on disk).
- [ ] 16.4 Default `--ref` is `main`. Mutating Command ProcessStep 1 is the frozen `uv tool install --force ...@<ref>` argv (resolve+install together).
- [ ] 16.5 Implement Preflight: Python/platform compatibility, available space, user-data schema, full migration path to target before any mutation.
- [ ] 16.6 Implement Quiesce with `exclusive_lock` on `get_locks_dir()/odcli-update.lock`.
- [ ] 16.7 Implement Snapshot: rollback snapshot of affected metadata (exact install requirement/ref, package revision, SQLite catalog, affected files); no large backup/filestore copy without need.
- [ ] 16.8 Implement Install as ProcessStep 1 through `internal/proc` (`shell=False`).
- [ ] 16.9 Implement Migrate as ProcessStep 2: `(<uv-tool-odcli>, "update", "--format", "json")` with `ODCLI_MAINTENANCE=1`; journal schema from D16; parent deserializes stdout; maintenance SHALL NOT call uv or re-enter install; unfinished journal: `update` resumes, other commands fail `update_incomplete`.
- [ ] 16.10 Implement Verify: new executable checks `odcli --version`, expected full revision, health/doctor startup, reached schema versions, and no unfinished migration journal.
- [ ] 16.11 Implement Commit/Cleanup: mark success only after verify; delete `get_user_root()/update/snapshot/`.
- [ ] 16.12 Wrap existing Alembic and `internal/storage_migration.py`; no second version scheme.
- [ ] 16.13 Rollback before first irreversible Alembic step via uv install of snapshot SHA as a frozen ProcessStep; otherwise `update_incomplete` with that same uv argv as recovery ProcessStep (`internal/proc`, `shell=False`), not an ActionStep.
- [ ] 16.14 Implement the typed result contract: `updated`, `already_current`, `unsupported_install`, `preflight_failed`, `rolled_back`, `update_incomplete`; source repo, previous/target/final version and full SHA; executable/tool env path; executed/skipped migration IDs and final schema versions; snapshot/journal state, rollback outcome, one concrete next step; per-phase duration without secrets.
- [ ] 16.15 Support `--yes` for non-interactive execution after preflight; `--no-input` without `--yes` exits before mutations; interactive confirmation before install/data changes.
- [ ] 16.16 Refuse to change pipx/system/editable/unknown installations; do not infer source from cwd or Odoo project Git remote.
- [ ] 16.17 Ensure no phase logs GitHub credentials, environment secrets, project passwords, or private config contents.
- [ ] 16.18 Add unit/component tests with the `internal/proc` recorder (not a module-local `subprocess` patch) using `pytest.mark.parametrize` covering update/SHA already_current/unsupported/preflight failure/install failure/migration failure/rollback/interruption/concurrency/downgrade.
- [ ] 16.19 Add a packaging E2E that installs an old fixture GitHub revision as a uv tool, updates to target, applies real test migrations, and verifies the new executable in a separate process.
- [ ] 16.20 Register `update` with `sdk_primitive=update_command`, `--check` variant, and `e2e_disposition=not-applicable`.
- [ ] 16.21 Run focused tests, Ruff, and mypy.

## 17. E/F test cleanup (item 17)

- [ ] 17.1 Write `openspec/changes/address-alpha-testing-defects-74/ef-inventory.md` using design D17 heuristics, with merge-base coverage percents.
- [ ] 17.2 For each, decide rewritten/replaced/deleted with a reason; preserve behavioural coverage elsewhere; reject "did not crash" as a replacement assertion.
- [ ] 17.3 Enforce line and branch coverage not below baseline; preserve requirement and regression coverage.
- [ ] 17.4 No new skips, xfails, or weakened assertions without justification; no cosmetic gaming.
- [ ] 17.5 Run the full `make pr` gate and confirm all test/CI gates pass.

## 18. `run --detach` logfile provisioning (item 18)

- [ ] 18.1 Implement `resolve_effective_logfile()` in `resources/instance/runtime.py`; inject `--logfile {path}` after the protected-option check; do not edit the user's `odoo.conf`.
- [ ] 18.2 New isolated environments write an environment-owned `<environment-root>/odoo.log` into generated `odoo.conf` and create the file with the other artifacts.
- [ ] 18.3 Apply the same fallback to old/partial isolated configs.
- [ ] 18.4 Make `run --detach`, `logs`, the structured result, and runtime metadata use one resolved path.
- [ ] 18.5 `--dry-run` shows the resolved path without creating the directory or file.
- [ ] 18.6 Unwritable fallback fails before spawn with `logfile_unwritable` and the exact path.
- [ ] 18.7 Add regression tests for main checkout, new isolated environment, old isolated environment, explicit path priority, and unwritable fallback; verify two isolated environments use different logfiles.
- [ ] 18.8 Run focused tests, Ruff, and mypy.

## 19. Compatibility, documentation, and delivery gates

- [ ] 19.1 Update README/CLI documentation for `env rm --force-connections`, `init` new options, `db reset-admin-password` secret boundary, optional `test_instance.database`, long-restore progress, `module update` diagnostics, `--version` VCS revision, `bug-report`, `update`, transport centralization, and `run --detach` logfile.
- [ ] 19.2 Update `docs/python-sdk.md` with `bug_report_init_command()`, `bug_report_submit_command()`, and `update_command()` only; do not document `OdooHttpClient` as a public type.
- [ ] 19.3 Update `docs/execution-boundary.md` as a mirror of `PUBLIC_LEAF_CASES` for the new leaves and the httpx/`src/` ServerProxy/self-update lock notes.
- [ ] 19.4 Ensure `tests/unit/test_documentation_contract.py` passes: Click leaves match README, documentation examples compile/import, shell fences pass `bash -n`, relative Markdown links resolve.
- [ ] 19.5 Run the existing `make pr` gates that apply; record skipped external prerequisites separately from regressions. Do not add a FastAPI/dashboard surface for bug-report or update.
- [ ] 19.6 Run the full `make pr` gate and `uv build`; inspect wheel/sdist contents and metadata.
- [ ] 19.7 Review `git diff` for scoped changes, verify Conventional Commit messages, and ensure no unrelated feature work is included.
- [ ] 19.8 Open the implementation PR from `feat/issue-74-alpha-testing-defects-batch2` with `#74` in the title/body, link GitHub issue #74, and report local `make pr`/build results.
- [ ] 19.9 Sync delta specs and archive the completed OpenSpec change once implementation lands.