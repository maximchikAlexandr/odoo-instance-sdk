## Context

The current `main` (after `address-alpha-testing-defects-67` landed) carries the second batch of confirmed alpha-testing defects collected in GitHub #74, plus four larger improvement requests submitted in the same issue. The eighteen items touch foreground lifecycle locking, Git commit formatting in environment worktrees, `env rm` safety flag surface, PostgreSQL state reporting, self-contained project initialization, large-backup validation, filestore provenance on self-contained restore, admin password reset, remote database name resolution, long-restore progress, auxiliary Odoo pipe drain, `module update` error preservation, VCS revision in `--version`, a new `odcli bug-report` workflow with an agent skill, centralized HTTP/XML-RPC transport, a new `odcli update` self-upgrade flow, E/F test cleanup, and automatic logfile provisioning for `run --detach`.

The repository already has the established CLI output boundary (`commands/`), the canonical `EnvironmentMonitor.snapshot()`, the `PUBLIC_LEAF_CASES` inventory, the immutable command/plan boundary in `internal/proc`, the frozen `msgspec` model convention, the Alembic + SQLAlchemy Core catalogue migration ledger, the `*_command()` sibling pattern, the SDK-first rule, the Rich/JSON/TOON output contract, and the architecture regression gates from #67. Several #74 items are divergences from those existing contracts rather than greenfield work.

Relevant prior changes: `address-alpha-testing-defects-67` established detached `run -d`, multi-target deletion, Rich local-time formatting, Alembic migrations, the file split, the SDK-first rule, the `process-inventory` capability, and the canonical `~/.odcli` worktree path; `refactor-cli-output-boundary` established the CLI transport boundary and `OutputMode`; `centralize-execution-dry-run-typed-output` established `PUBLIC_LEAF_CASES` and the `*_command()` sibling pattern.

## Goals / Non-Goals

**Goals:**

- Release the environment artifact lock before `wait_foreground_process()` so a parallel `stop` works on foreground `run`.
- Make `git commit` in environment worktrees use the already-resolved project ticket settings.
- Expose `--force-connections` on `env rm` and stop recommending a non-existent option.
- Make `postgres up` and `env list` report truthful PostgreSQL state from `PostgresCluster.status_command()`, not from a failed metrics snapshot.
- Make `init` produce a complete self-contained project in one command, with a blocking completeness check that does not treat a uniquely listable `test_instance.database` as missing, and `--allow-partial` opt-out.
- Create bootstrap database `tmp` with `base` installed on self-contained Compose `init`, as required by the owner comment on #74 item 5.
- Replace hardcoded backup validation byte ceilings with streaming validation, free-space preflight, an optional operator maximum, and correct Odoo manifest version reading.
- Record and reuse the proven filestore `data_dir` on self-contained restore so `db rm` manages the filestore lifecycle.
- Replace the hidden `admin` password reset with an explicit secret assignment shared by restore, COPY replacement, and `db reset-admin-password`.
- Make `test_instance.database` optional and resolve it via `DatabaseResource.names()` when absent.
- Show real long-restore stages and elapsed time in Rich without polluting machine output.
- Drain auxiliary Odoo stdout/stderr concurrently with a bounded redacted tail and attach it to readiness/startup failures.
- Preserve the final `module update` traceback behind a long startup log.
- Show the installed VCS revision in `odcli --version` for uv-tool VCS installs.
- Add `odcli bug-report init/submit` and the portable `odcli-bug-report` agent skill.
- Centralize Odoo HTTP behind an internal `OdooHttpClient`; keep XML-RPC in test support.
- Add `odcli update` with nine phases, a coordinator over existing Alembic/`storage_migration.py`, snapshot/rollback, and an honest `update_incomplete` contract.
- Remove or rewrite E/F-quality tests without dropping coverage.
- Provision an effective logfile for `run --detach` automatically.

**Non-Goals:**

- A plugin marketplace, universal lifecycle/provider framework, or dynamic Click command registration.
- Async transport rewrite, new retry/backoff/circuit-breaker, or a universal "client of everything".
- Automatic background auto-update, a scheduler/daemon for updates, or updating `uv`/Python/Odoo/PostgreSQL/Node.
- A second ticket/issue system, a GitHub Actions workflow for bug reports, or an in-CLI LLM duplicate detector.
- A password vault, credential generator, or separate secret store for admin password reset.
- Changing public CLI command names, exit codes, or the JSON envelope v1 shape.
- Mass renaming of public API or changing user behaviour during the E/F test cleanup.
- FastAPI/dashboard rendering of bug reports or update state.

## Decisions

### D1: Foreground `run` releases the artifact lock before waiting (item 1)

`run_foreground_command()` currently enters a shared artifact lock and holds it for the whole foreground wait, blocking `stop`'s exclusive lock. The fix is to narrow the lock scope on `run_foreground_command()` (the convenience `run_foreground()` SHALL only delegate). Expression SHALL NOT appear in lock acquire/release, spawn, wait, cleanup, or compensation. Under the shared artifact lock, perform atomic spawn and runtime-identity registration, then release the lock before `wait_foreground_process()`. On exit, re-acquire that same shared artifact lock for cleanup/revalidation. `stop` can then acquire the exclusive lock, read the persisted runtime identity, and call the existing `terminate_pid()`. PID/create-time/process-group validation and stale-runtime safety are preserved because they operate on the persisted identity, not on the lock holder.

Alternative considered: make `stop` use a non-exclusive read path. Rejected because the existing `stop` contract requires an exclusive lock to safely terminate and clean up, and weakening it risks double-termination and identity races.

### D2: `git commit` uses already-resolved project ticket settings (item 2)

The Git resource currently re-loads `.odcli/project.toml` from the environment worktree root via `_project_settings(root)`. When the worktree has no `.odcli/project.toml`, ticket links are silently disabled. The fix is to pass the already-resolved project settings (from the `--project` manifest) into the Git resource instead of re-loading them from the worktree. The worktree is a Git artifact, not a project manifest source; the selected project manifest is the authority for ticket settings.

Alternative considered: copy `.odcli/project.toml` into each worktree. Rejected because #74 explicitly says this should not be required and it would create manifest drift across worktrees.

### D3: `env rm --force-connections` with COPY-only terminate scope (item 3)

`env rm` gains `--force-connections`, routed through `EnvironmentManager.remove_command()` into the existing `build_database_drop_command()` with `force_connections=True` and terminate scope limited to the exact COPY database being removed. Without the flag, removal stays fail-closed. The error SHALL name `--force-connections` and SHALL NOT mention a missing option. The drop checker is command-aware so `db rm` versus `env rm` advice matches the invoking leaf.

Alternative considered: after adding the flag, still tell the user only to `odcli stop`. Rejected because #74's preferred contract is to add the flag; recommending a missing option was the defect.

### D4: Truthful PostgreSQL state from the canonical status probe (item 4)

`postgres up` builds its diagnostic result from the captured cluster even when `ensure_running_command()` returns `None` (success). The monitor no longer derives `STOPPED` from an empty or unparseable Docker resource snapshot: `stopped` follows only from a successful `PostgresCluster.status_command()`. A metrics failure degrades only metrics and keeps the actual lifecycle state. `stats_failed` no longer implies a stopped cluster.

Alternative considered: always run a status probe after `postgres up`. Rejected because `ensure_running_command()` already performs the canonical checks; the defect is in the renderer and the monitor's inference from a failed metrics snapshot, not in the probe execution.

### D5: Self-contained `init` with a blocking completeness check (item 5)

`init` gains `--test-url`, `--test-database`, `--test-branch`, `--local-config`, and `--allow-partial`. These fill the existing `[test_instance]`, select generated `.odcli/odoo.conf` as the effective local `source_config`, create `.odcli/.env` with `ODCLI_TEST_INSTANCE_ORIGIN_PINS` and an empty `ODCLI_TEST_MASTER_PASSWORD=` under `0600`, and set `data_dir` to the absolute path `{project_root}/.odcli/filestore`. Interactive completeness uses the same gate as `commands/cli_parts/registration.py`: prompt only when `no_input` is false and output mode is RICH; the question is Click confirm with default cancel. `--no-input` incomplete setup fails with error code `init_incomplete` before writes unless `--allow-partial` is explicit. `--yes` confirms manifest replacement, not partial setup. `--dry-run` does not prompt and does not write. Re-running `init` without test-instance options preserves an existing valid `[test_instance]`. Master password is never a CLI argument; interactive RICH mode MAY `getpass` it hidden; `--no-input` leaves empty `ODCLI_TEST_MASTER_PASSWORD=`. Secrets do not appear in argv, manifest, Rich/JSON/TOON, plan, or process-environment diagnostics.

Missing-group predicates (no other names): `test_url` if `--test-url` and `[test_instance].url` are both absent; `test_branch` if `--test-branch` and `[test_instance].branch` are both absent; `local_config` if `--local-config` is absent and generated `.odcli/odoo.conf` is not already the effective local `source_config`; `dotenv_origin` if a non-loopback test URL is being set and `.odcli/.env` has no `ODCLI_TEST_INSTANCE_ORIGIN_PINS=` line; `postgres_mode_image` if postgres mode is compose and `--postgres-image` and the manifest image are both absent. `--dry-run` completeness SHALL NOT call `DatabaseResource.names()` (no HTTP). On `--dry-run`, `test_database` is missing when it is absent from CLI flags and the manifest. Execute MAY call `names()` as an HTTP ActionStep and drop `test_database` from the missing set when the list has exactly one name. Non-RICH output (`json`/`toon`) SHALL use the `--no-input` completeness path (no Click confirm). External PostgreSQL/source-config remains a supported full profile when chosen explicitly; missing remote test instance still requires cancel-vs-partial or `--allow-partial`. `--local-config` does not create source/target aliases outside the selected mode. Interactive RICH mode MAY `getpass` the remote master password; it SHALL NOT be required for a successful `init` (#74: password can still be set afterwards). `--no-input` leaves empty `ODCLI_TEST_MASTER_PASSWORD=`.

`ODCLI_TEST_INSTANCE_ORIGIN_PINS` SHALL be written as the existing canonical origin of `--test-url` or `[test_instance].url` (lowercase scheme/host and effective port, `internal/test_instance_trust.py` grammar). Loopback still gets the key; refresh does not require a pin for loopback.

Alternative considered: a separate setup wizard command. Rejected because #74 explicitly says to extend the existing `init`.

### D5a: Valid Odoo bootstrap database `tmp` (item 5 GitHub comment)

The owner comment on #74 supplements item 5: self-contained Compose `init` SHALL create PostgreSQL and a valid empty Odoo database named exactly `tmp` with `base` installed, so Odoo 13 Database Manager/`ir.http` exist before the first restore. A plain empty PostgreSQL database is not enough.

Create `tmp` through the same project runtime (`odoo-bin`, Python, addons, generated config, `data_dir={project_root}/.odcli/filestore`). Spawn via `internal/proc` with argv from `resolve_runtime_argv` / `_build_cli_args` plus `--database tmp --init=base --stop-after-init`, `shell=False`. Do not add a second Odoo version or a second launcher.

After that ProcessStep the Odoo process has exited, so `POST /web/webclient/version_info` SHALL NOT run at `init`. Readiness ActionStep: on the owned cluster, SQL `SELECT state FROM ir_module_module WHERE name = 'base'` on database `tmp` MUST return `installed`. Idempotent: if that SQL already succeeds, do not recreate. Invalid same-named DB (missing relation or state not `installed`) fails `init_bootstrap_failed`. HTTP `version_info` / Database Manager `303` belong to auxiliary restore (Odoo is running then). If an already-initialized owned Compose project has no valid `tmp`, the first `odcli run` SHALL run the same ProcessStep+SQL (idempotent). `--dry-run` lists these steps and does not spawn. A failed `tmp` init SHALL NOT return a successful project-setup result.

Auxiliary restore SHALL use configured bootstrap `tmp` and SHALL NOT add hidden `--database=__odcli_restore__` or `--db-filter=^$`. After a successful restore, project default switches to the restored database; `tmp` stays the bootstrap database and is not a working backup copy.

Regression (parametrize Odoo 13 and 19): fresh owned Compose cluster → `tmp` init → stopped-project `db restore` → Database Manager HTTP `303` → PostgreSQL postcondition → default DB switch.

Alternative considered: create `tmp` lazily on first restore. Rejected by the #74 comment: Database Manager must be available before the first restore on Odoo 13.

### D6: Streaming backup validation without hardcoded ceilings (item 6)

The fixed 512 MiB per-member and 2 GiB total ceilings are removed as unconditional invalidity criteria. Keep `_MAX_ZIP_ENTRIES = 4096` and stream with a 65536-byte buffer; do not read `dump.sql` or filestore wholly into memory. `_MAX_ZIP_COMPRESSION_RATIO = 100` SHALL NOT invalidate `dump.sql` by itself. `backup_corrupt` = malformed ZIP or CRC failure. `backup_unsafe` = path traversal, duplicate/encrypted/unsupported members, or a non-`dump.sql` member whose compression ratio exceeds 100. Before restore, sum declared uncompressed sizes with overflow-safe arithmetic and compare to `shutil.disk_usage(Path(data_dir).resolve()).free` when `data_dir` is set, else `shutil.disk_usage(get_backups_dir()).free`, minus reserve `max(1 GiB, 10% of that free space)`; failure is `backup_insufficient_disk` with measured/available/reserve bytes. Optional operator maximum is `backup.max_uncompressed_bytes` in `get_config_root()/user.toml` (absent means no ceiling); the result SHALL set `limit_source` to `user.toml` or `unset` and error `backup_operator_limit` with measured/allowed bytes. `_MAX_ZIP_COMPRESSION_RATIO = 100` SHALL NOT invalidate `dump.sql`; other members that exceed ratio 100 SHALL be `backup_unsafe`. Manifest `db_version` is `str(manifest["version"])` if present, else `f"{manifest['major_version']}.0"`; never persist `null` for a standard Odoo 13 dump that has those keys.

Alternative considered: raise the ceilings to another arbitrary value. Rejected because it only moves the defect; production databases regularly produce SQL dumps of tens or hundreds of gigabytes.

### D7: Proven filestore `data_dir` on self-contained restore (item 7)

Full self-contained `init` (item 5) writes `data_dir = {project_root}/.odcli/filestore` (absolute) into generated `.odcli/odoo.conf`. Restore writes that canonical path into the restore binding alongside cluster/database/backup identity. `db rm` deletes only the exact contained non-symlink filestore and returns `deleted` or `absent` with the path. External config without a provable `data_dir` stays fail-closed `unknown`; no directory is deleted by database name or platform default. Before restore, the owned `data_dir` is verified to be a regular directory inside the project tree and not a symlink. `doctor` MAY emit an existing-style finding when a restore binding exists without `data_directory`; this change SHALL NOT add a doctor subsystem or auto-assign ownership.

Alternative considered: a second filestore registry. Rejected because #74 explicitly says to reuse `start_config.data_dir`, the restore binding, and `_cleanup_proven_filestore()`.

### D8: Explicit admin password secret (item 8)

The literal `admin` value in the reset flow is replaced by one mandatory secret input. Prompt with `getpass` twice only when output mode is RICH and `--no-input` is false. `--format json|toon`, `--no-input`, and `--dry-run` never prompt. `--yes` confirms mutation and does not skip the prompt. Non-prompt paths read process env `ODCLI_ADMIN_PASSWORD` first, then project `.odcli/.env`. Missing secret: error `admin_password_required` before restore/drop. Same mechanism for `db reset-admin-password`, restore, and COPY replacement. Secret never in argv, history, manifest, catalog, plan, Rich/JSON/TOON, logs, or exception text; result reports provenance `prompt` or `environment`. Regression for Odoo 13 and 19: after reset, login as `base.user_admin` through the existing real-Odoo XML-RPC test-support probe succeeds.

Alternative considered: generate a random password and print it once. Rejected because #74 requires the user to choose the secret in interactive mode and to supply it via environment in non-interactive mode; a generated password changes the contract and forces a copy-paste step the user did not ask for.

### D9: Optional `test_instance.database` (item 9)

`test_instance.database` becomes optional in the manifest model and parser/writer. If it is explicitly set, it is used without requiring database list availability — this preserves work with instances where listing is disabled. If it is absent, remote-refresh preflight obtains names through `DatabaseResource.names()`; exactly one database is selected; zero databases yield `remote_database_none`; multiple yield `remote_database_ambiguous` listing names; an unavailable list yields `remote_database_list_unavailable`. All three fail before download. The auto-detected name is reflected in plan/result and backup provenance but is not written back to `project.toml`. Manifest round-trip does not add `database` when it was not set.

Alternative considered: auto-write the detected name back to `project.toml`. Rejected because #74 explicitly says not to write the volatile instance state into the static manifest.

### D10: Long-restore Rich stages via existing StepObserver (item 10)

Reuse `StepObserver`/`StepEvent` and the Rich Live runner in `commands/output.py`. Stage ids: `backup_prepare`, `auxiliary_start`, `db_restore`, `db_verify`, `filestore_restore`, `admin_reset`, `default_switch`. Heartbeat: emit `StepEvent` every 0.5 s during blocking work without child stdout. Percent only when streaming dump/filestore bytes provide a total. On error, preserve safe primary cause, last stage id, and elapsed seconds. JSON/TOON: one final document, no heartbeat events on stdout.

Alternative considered: a new progress framework. Rejected because #74 explicitly says to reuse the existing `StepObserver`/`StepEvent` and Rich runner.

### D11: Concurrent auxiliary pipe drain with bounded redacted tail (item 11)

`internal/proc/run.py` already drains captured pipes. Extend that pump to every long-running handle with `inherit_stdio=False`. Tail budget is `_TIMEOUT_TAIL_BYTES = 8192` last bytes per stream after existing redaction. Attach tail plus original error type to readiness/startup failures. Readers die with the owned process group. No `DEVNULL`, no second log store. JSON/TOON remain one document; the drain MUST NOT write child bytes to CLI stdout. Canonical requirement lives in `command-execution`; `server-lifecycle` only references it.

Alternative considered: redirect auxiliary stdout/stderr to `DEVNULL`. Rejected because it destroys the diagnostics needed to explain readiness/startup failures.

### D12: `module update` prioritizes framed payload, falls back to bounded tail (item 12)

When a valid nonce-framed payload is present, `user_error` or `finalization_error` from the common shell wrapper is used with priority. When payload is missing or malformed, a bounded redacted tail of `stderr` of the last `_TIMEOUT_TAIL_BYTES = 8192` bytes is used, not the first N characters. Full unlimited tracebacks are not emitted and existing redaction is not weakened. Rich, JSON, and TOON return the same stable error code and safe details. The common shell-error contract is reused; no separate parser is added for `module update`.

Alternative considered: a separate parser for `module update`. Rejected because #74 explicitly says to reuse the common shell-error contract.

### D13: PEP 610 VCS revision in `--version` (item 13)

The CLI reads optional PEP 610 `direct_url.json` via `importlib.metadata`. If `vcs_info.commit_id` is a hex string of length >= 7, the human version output appends the first 7 characters, e.g. `odcli, version 0.1.0 (6a984c7)`. Wheel/sdist installs without `direct_url.json` keep the package version. Malformed or missing metadata falls back to the package version. No Git, checkout, network, or operation-only imports.

Alternative considered: embed the commit at build time. Rejected because #74 explicitly says not to introduce a build-time Git dependency and to read PEP 610 metadata at runtime.

### D14: `odcli bug-report` drafts, review, and skill (item 14)

Two new CLI leaves in a `bug-report` group. SDK: `bug_report_init_command()` and `bug_report_submit_command()`; `PUBLIC_LEAF_CASES` sets those names as `sdk_primitive` (`cli_only_reason` forbidden). Drafts live under `get_user_root()/bug-reports/<REPORT_ID>/`. Lock: `get_locks_dir()/bug-report-{REPORT_ID}.lock`. Expression SHALL NOT appear in that lock, submit intent write, or `gh` ProcessStep. Default destination if `get_config_root()/user.toml` has no `[bug_report].repository`: repository `maximchikAlexandr/odoo-instance-sdk`. `[bug_report]` MAY override `repository` only. GitHub labels are always `["alpha-testing"]` (frozen `--label alpha-testing`). `--kind` is `metadata.json` field `kind` plus a `Kind:` line in `report.md`, not a GitHub label; the factory reads `metadata.kind`. Uncertain `gh` create outcome SHALL return error code `submit_outcome_unknown`. `report.md` max 262144 bytes. The skill writes `reviews/N.json`; CLI only validates. Submit allowed iff the highest `reviews/N.json` with N in {1,2,3} has `verdict=approved` and matching SHA-256 of title+body+repo+labels. Preview SHALL return `report_valid` and `submit_ready` as separate booleans plus `report_errors` and `submit_blockers`. No `--skip-review`, no `--force`. Before the `gh` ProcessStep, write ActionStep `submit_intent` into `metadata.json`. `gh` argv SHALL be `("gh", "issue", "create", "--repo", <repo>, "--title", <title>, "--label", "alpha-testing", "--body-file", "-")` with the body on stdin through `internal/proc`, `shell=False`. The child inherits `GH_TOKEN`; OdCLI SHALL NOT copy it into config, draft, or argv. Skill-only: duplicate GitHub issue handling, emergency unblock, reviewer prompt checks 1–5 from #74. `e2e_disposition=not-applicable` (publishes GitHub issues).

Alternative considered: accept report text directly at `submit` time. Rejected because #74 chose the draft model.

### D15: Centralized HTTP and XML-RPC transport (item 15)

`src/odoo_instance_sdk/internal/transport/` contains one base HTTP wrapper and exactly one subclass `OdooHttpClient` because #74 item 15 requires that inheritance. Resources (`database`, `health`, `monitor`) depend on `OdooHttpClient`. CLI commands SHALL NOT import it; they call public SDK resource primitives. The client is not a public SDK type and SHALL NOT appear on public resource constructor signatures. Tests inject via an internal factory, not a documented public parameter. One client is constructed per operation/resource call and closed on that lifecycle; it is not a process-wide shared singleton. `httpx` is lazily imported. Architecture inventory allowlist is line-specific to files in that directory. No retry.

XML-RPC: production `src/` has no `ServerProxy` today (`tests/integration/real_odoo/test_critical_path.py` only). Keep a test-support helper wrapping `_xmlrpc_probe`; do not add production `OdooXmlRpcClient`. Architecture gate: zero `xmlrpc.client.ServerProxy` under `src/`.

Alternative considered: a universal client of everything. Rejected by #74.

### D16: `odcli update` nine-phase flow (item 16)

`update_command()` is the `sdk_primitive` (`cli_only_reason` forbidden). Expression SHALL NOT appear anywhere in this flow. Default `--ref` is `main`. `PUBLIC_LEAF_CASES` SHALL contain two rows for the same Click path: `update` (`mutating-or-spawning`) and `update --check` (`process-previewable-read-only`), both `sdk_primitive=update_command`.

`--dry-run` SHALL NOT launch a process. Inspect is in-process (PEP 610, `sys.executable`, uv-tool layout).

`already_current` without uv: only when `--ref` is a 40-character lowercase hex SHA equal to installed `vcs_info.commit_id`. Then the Command has zero ProcessSteps and does not reinstall.

Inspect canonical source: `read_uv_tool_direct_url()` reads PEP 610 `direct_url.json` from the uv-tool environment (same files as `--version`). `unsupported_install` result field `manual_argv` is `tuple[str, ...] | None`, never a shell string.

`--check`: one ProcessStep `("uv", "tool", "install", "--force", "--dry-run", "odoo-instance-sdk @ git+https://github.com/maximchikAlexandr/odoo-instance-sdk.git@<ref>")`. If uv exits non-zero because `--dry-run` is unknown, fail with `unsupported_install` naming that uv version; do not invent a pip fallback. Target SHA: if `--ref` is a 40-char lowercase hex SHA, `target_sha` is that ref. Else collect distinct matches of `(?i)\b[0-9a-f]{40}\b` in uv stdout+stderr (lowercase); zero matches → `unsupported_install` with `sha_unparsed`; one or more → last match is `target_sha`. `already_current` when `target_sha` equals installed `vcs_info.commit_id`.

Mutating `update` when not `already_current`: exactly two ProcessSteps, argv frozen at plan time:

1. `("uv", "tool", "install", "--force", "odoo-instance-sdk @ git+https://github.com/maximchikAlexandr/odoo-instance-sdk.git@<ref>")` through `internal/proc`. When `--ref` is a 40-char SHA this is an exact SHA install; when it is `main` or a tag, uv resolves inside this one install (no second argv rebuild; documented deviation from #74 “pre-resolved SHA then install” because a frozen Command cannot rebuild argv after a Resolve spawn, and `--dry-run` cannot spawn).
2. `(<uv-tool-odcli-absolute-path>, "update", "--format", "json")` with env `ODCLI_MAINTENANCE=1` only. Maintenance SHALL run Alembic/`storage_migration.py` then verify and write one JSON envelope v1 `UpdateResult` on stdout. Parent deserializes that document. Maintenance SHALL NOT call uv and SHALL NOT re-enter install.

Parent `.run()` waits for those two steps and does not spawn a third process. Verify lives inside ProcessStep 2.

Preflight free space: `shutil.disk_usage(get_user_root()).free` minus reserve `max(1 GiB, 10% of that free space)`; shortfall is `preflight_failed` with measured/available/reserve bytes. Quiesce is `exclusive_lock` on `get_locks_dir()/odcli-update.lock` only; on conflict return the existing lock error with that path (no process lister).

Coordinator: call existing Alembic and `internal/storage_migration.py` in order; do not add a second from/to engine or a registry type. Unfinished journal: `odcli update` resumes; every other command fails `update_incomplete` and does not resume. Journal file `get_user_root()/update/journal.json` frozen fields: `phase` (`inspect`|`preflight`|`quiesce`|`snapshot`|`install`|`migrate`|`verify`), `target_ref` (str), `snapshot_sha` (str or null), `maintenance_pid` (int or null). Snapshot `get_user_root()/update/snapshot/`; delete after successful verify. Recovery uv install of the snapshot SHA is a frozen ProcessStep through `internal/proc`, not an ActionStep. Passing `--check` and `--dry-run` together is a Click usage error with exit code 2.

Alternative considered: Resolve on mutating `--dry-run` then rebuild install argv to an exact SHA. Rejected: `--dry-run` must not spawn and the Command is frozen at plan time. `--check` is the Resolve leaf.

### D17: E/F test cleanup without coverage loss (item 17)

Issue #74 does not name nodeids. Task 17.1 writes `ef-inventory.md` by scanning **only tests files touched by this change's slices** (plus any test the implementer already knows is empty of behaviour). Grade F only when the test has no assertion except `assert True` or an implicit "did not raise". Grade E only when every assertion is on `_`-prefixed names or mock `assert_called` of private helpers. `pytest.raises` without `match=` is not F by itself. Each row: rewritten|replaced|deleted plus reason. Baseline: `make coverage` percents on merge-base `main`.

Alternative considered: leave E/F tests in place and add a comment. Rejected because #74 explicitly says to rewrite or delete them without reducing coverage.

### D18: Effective logfile resolver for `run --detach` (item 18)

One function `resolve_effective_logfile()` in `src/odoo_instance_sdk/resources/instance/runtime.py`. Explicit non-empty `logfile` in effective `odoo.conf` wins; else `{parent of effective odoo.conf}/odoo.log`. Create parent dir and file before detached spawn. After the protected-option check in `_PROTECTED_RUNTIME_OPTIONS`, inject `--logfile {resolved}` into argv; user `--logfile` after `--` remains rejected. Do not edit the user's `odoo.conf`. New isolated environments write `<environment-root>/odoo.log` into generated config as today. `--dry-run` shows the path without creating files. Unwritable path fails before spawn with error code `logfile_unwritable` and the exact path. `run --detach`, `logs`, result, and runtime metadata share that path.

Alternative considered: always require an explicit `logfile` in `odoo.conf`. Rejected because #74 explicitly says the absence of `logfile` must no longer be an error and the CLI can safely derive the path from the bound config.

## Risks / Trade-offs

- **Narrowing the foreground lock scope may expose a registration/cleanup race** → The atomic spawn/registration and cleanup/revalidation remain under the lock; only the wait is outside it. A regression test reproduces the original conflict and confirms the fix; a live E2E enforces the two-session scenario.
- **`init` completeness check may reject valid legacy setups** → External PostgreSQL/source-config remains a supported full profile when chosen explicitly; missing test instance still needs `--allow-partial` or cancel-vs-partial.
- **`--dry-run` of `update` cannot show a resolved SHA** → `--check` is the process-previewable variant; mutating `--dry-run` shows frozen uv argv only.
- **Large-backup validation may accept a malicious ZIP bomb** → Path traversal, duplicate/encrypted/unsupported members, CRC, and entry-count checks remain; ratio is a signal, not a sole criterion; free-space preflight and the streaming write limit bound the risk.
- **Admin password reset via `ODCLI_ADMIN_PASSWORD` may leak through process environment** → The existing dotenv/redaction boundary is reused; the secret is read once, not logged, not put in argv, not put in plan/result; the result reports only provenance.
- **`odcli bug-report` review JSON is not cryptographic proof of independent review** → The CLI verifies schema, sequence, hash, and verdict; real independence is ensured by the skill and the agent runtime journal; no attestation infrastructure is built.
- **`odcli update` may leave the tool in an inconsistent state** → Snapshot, journal, separate lock, new-subprocess migrations, honest `update_incomplete`, and exact recovery command bound the risk; no blind re-install after timeout.
- **Transport centralization may regress streaming backup/restore** → The client preserves timeout/streaming semantics; streaming response is not closed before the consumer; characterization tests before/after confirm no behavioural change.
- **E/F test cleanup may reduce coverage** → Line/branch coverage baseline is enforced; requirement and regression coverage is preserved; no cosmetic gaming.

## Migration Plan

1. Work on `feat/issue-74-alpha-testing-defects-batch2`.
2. Characterize current behaviour with tests where coverage is missing.
3. Apply functional slices in dependency-safe order: foreground lock scope (1); `git commit` ticket settings (2); `env rm --force-connections` (3); truthful PostgreSQL state (4); large-backup validation (6); admin password reset (8); optional `test_instance.database` (9); `module update` traceback (12); `--version` VCS revision (13); auxiliary pipe drain (11); long-restore progress (10); self-contained `init` + `tmp` bootstrap + `data_dir` + filestore provenance (5, 5a, 7); `run --detach` logfile (18); transport centralization (15); `odcli bug-report` + skill (14); `odcli update` (16); E/F test cleanup after `ef-inventory.md` (17).
4. Run focused tests after each slice and full `make pr` at handoff.
5. For transport centralization: record characterization tests for existing Odoo HTTP/XML-RPC scenarios before the refactor and confirm no behavioural change after.
6. For `odcli update`: exercise the nine-phase flow against a fixture GitHub revision as a uv tool in packaging E2E.

Rollback is commit-wise. The transport centralization is reversible by reverting the client-introduction commit before callers are migrated. The `odcli update` flow is reversible by reverting the snapshot/journal commits before the old migrator is removed.

## Open Questions

None. Capability boundaries, lock paths, error codes, completeness groups, backup numbers, update argv, XML-RPC placement, and logfile injection are fixed above. `--dry-run` of `update` does not spawn even though #74 asked to run Resolve there; `--check` covers Resolve.