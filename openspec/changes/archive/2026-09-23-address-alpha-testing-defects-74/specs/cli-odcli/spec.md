## ADDED Requirements

### Requirement: `odcli bug-report` command group

The CLI SHALL register `odcli bug-report init` and `odcli bug-report submit <REPORT_ID>` as bounded structured leaves following the shared output contract, exit codes, and redaction rules. `bug-report init` SHALL be classified as `mutating-or-spawning` (writes files, no child process); `bug-report submit` SHALL be classified as `mutating-or-spawning` (invokes `gh` as a child process). `PUBLIC_LEAF_CASES` SHALL set `sdk_primitive` to `bug_report_init_command` and `bug_report_submit_command` respectively; `cli_only_reason` SHALL NOT be used. `e2e_disposition` SHALL be `not-applicable` with rationale that the leaves publish GitHub issues. The group SHALL NOT create a separate `bug` resource with a `report` command. Default target SHALL be repository `maximchikAlexandr/odoo-instance-sdk` and GitHub labels `["alpha-testing"]` only (`--kind` stays in `metadata.json`, not as a label), overridable in `get_config_root()/user.toml` under `[bug_report]`. The CLI SHALL NOT expose `--skip-review` or `--force`.

#### Scenario: help lists bug-report

- **WHEN** `odcli --help` runs
- **THEN** the command surface includes `bug-report`

#### Scenario: bug-report leaves are in PUBLIC_LEAF_CASES

- **WHEN** the `PUBLIC_LEAF_CASES` contract test runs
- **THEN** `bug-report init` carries `sdk_primitive=bug_report_init_command` and `bug-report submit` carries `sdk_primitive=bug_report_submit_command`

### Requirement: `odcli update` leaf

The CLI SHALL register `odcli update` with `--check`, `--dry-run`, `--ref`, `--yes`, `--no-input`, `--allow-downgrade`, and `--format rich|json|toon`. Default `--ref` SHALL be `main`. The mutating variant SHALL be `mutating-or-spawning`. `--check` SHALL be a `PublicLeafCase` variant `process-previewable-read-only` of the same `sdk_primitive=update_command`. `cli_only_reason` SHALL NOT be used. `e2e_disposition` SHALL be `not-applicable` because the leaf mutates the operator uv tool. `--dry-run` SHALL emit the frozen plan without launching `uv` or `odcli`. Passing `--check` and `--dry-run` together SHALL be a Click usage error with exit code 2 before SDK resolution. There SHALL NOT be a second Click command for maintenance; `ODCLI_MAINTENANCE=1` selects the migrate path of this leaf.

#### Scenario: help lists update

- **WHEN** `odcli --help` runs
- **THEN** the command surface includes `update`

#### Scenario: update leaf is in PUBLIC_LEAF_CASES

- **WHEN** the `PUBLIC_LEAF_CASES` contract test runs
- **THEN** `update` carries `sdk_primitive=update_command` and a `--check` variant classified `process-previewable-read-only`

### Requirement: `odcli env remove` force-connections

`odcli env rm` SHALL expose `--force-connections` and route it through `EnvironmentManager.remove_command()` into the existing `build_database_drop_command()` with terminate scope limited to the exact COPY database. Without the flag, removal SHALL stay fail-closed and the error SHALL name `--force-connections`; it MAY also name `odcli stop`. The leaf SHALL follow the multi-target deletion contract.

#### Scenario: env rm without force-connections names the existing flag

- **WHEN** `odcli env rm <ENV>` runs without `--force-connections` against a COPY database with an active session
- **THEN** removal is blocked and the error names `--force-connections`, not a missing option

#### Scenario: env rm with force-connections terminates only the COPY database sessions

- **WHEN** `odcli env rm <ENV> --force-connections` runs
- **THEN** only sessions of the exact COPY database are terminated and other databases' sessions are untouched

### Requirement: `odcli postgres up` reports truthful state

`odcli postgres up` SHALL build its diagnostic result from the captured cluster even when `ensure_running_command()` returns `None`. The result SHALL show actual `mode`, `owned`, `state`, and `endpoint` in Rich/JSON/TOON. A failed, empty, or unparseable Docker metrics snapshot SHALL NOT imply `STOPPED`; `stopped` SHALL follow only from a successful `PostgresCluster.status_command()`.

#### Scenario: successful postgres up shows real state

- **WHEN** `odcli postgres up` succeeds
- **THEN** the result shows actual `mode`, `owned`, `state`, and `endpoint`, not `unknown`/`false`/—

#### Scenario: failed metrics do not imply stopped

- **WHEN** a running cluster has an empty or unparseable Docker metrics snapshot
- **THEN** lifecycle state comes from `PostgresCluster.status_command()` and `stats_failed` degrades only metrics

## MODIFIED Requirements

### Requirement: `odcli run`

`odcli run` SHALL launch the resolved Odoo runtime from either a ready environment or an initialized project. For project context, it SHALL derive the Python executable, Odoo entry point, source Odoo config, runtime working directory, preferred HTTP port, default database, default run arguments, and project PostgreSQL binding from `.odcli/project.toml` and the referenced config. Missing required runtime fields or files SHALL fail before process construction with a sanitized actionable error.

The command SHALL preserve the existing literal `--` delimiter rule, exact passthrough argument order, protected runtime-identity validation, free-port preflight, dry-run rendering, inherited native streams, foreground process-group cleanup, and exit-code behavior. Project context has no environment use metadata, so it SHALL NOT call `EnvironmentResource.record_use()`; environment context SHALL retain its existing record-use behavior after successful preflight and before execution.

The command SHALL accept `-d, --detach` to launch Odoo detached. When the effective `odoo.conf` has an explicit non-empty `logfile`, that path SHALL be used. When `logfile` is absent or empty, the command SHALL choose `odoo.log` next to the effective `odoo.conf`, create the missing parent directory and file before spawn, and pass `--logfile {path}` via `resolve_effective_logfile()` in `resources/instance/runtime.py` after the protected-option check (user `--logfile` after `--` remains rejected) WITHOUT editing the user's `odoo.conf`. The same resolver SHALL be used by `odcli logs`, the structured result, and runtime metadata. New isolated environments SHALL write an environment-owned `<environment-root>/odoo.log` into generated `odoo.conf` and create the file with the other artifacts. The same fallback SHALL apply to old/partial isolated configs. Two isolated environments of one project SHALL use different logfiles. `--dry-run` SHALL show the resolved logfile path without creating the directory or file. An unwritable fallback SHALL fail before spawn with a clear message and the exact path. `-d` after the literal `--` SHALL be treated as a native Odoo argument and SHALL NOT be interpreted as the OdCLI option.

#### Scenario: Project run needs no runtime path arguments

- **WHEN** `odcli run` executes from an initialized main checkout whose manifest references valid Python, Odoo entry point, and config
- **THEN** it constructs and launches the foreground command without requiring those paths as CLI arguments

#### Scenario: Project defaults and passthrough compose deterministically

- **WHEN** project context defines default run arguments and the caller supplies allowed arguments after `--`
- **THEN** the captured command contains project defaults followed by the exact caller arguments in their original order

#### Scenario: Project dry-run has no side effects

- **WHEN** `odcli run --dry-run` resolves project context
- **THEN** it emits the bounded execution plan without starting Odoo, mutating the environment catalogue, or writing use metadata

#### Scenario: Environment run retains metadata behavior

- **WHEN** `odcli run` resolves a ready environment and the port preflight succeeds
- **THEN** it records environment use exactly once before executing the captured foreground command

#### Scenario: Native process contract is context-independent

- **WHEN** an environment-based or project-based foreground run exits non-zero or is interrupted
- **THEN** native streams are preserved, the actual exit code is returned, and interrupt cleanup returns exit `130`

#### Scenario: Port conflict deterministic error

- **WHEN** `odcli run -- --dev=reload` finds the effective bound port occupied
- **THEN** it returns `port-conflict` with ownership unknown and performs no foreground command construction, use update, config change, or process launch

#### Scenario: Free port starts Odoo

- **WHEN** `odcli run -- --dev=reload --log-level debug --dev=xml` finds the effective port free
- **THEN** it captures `run_foreground_command` once with the exact delimiter arguments and executes that captured command

#### Scenario: Delimiter is required for native arguments

- **WHEN** a caller invokes `odcli run --dev=reload` without the `--` delimiter
- **THEN** Click reports an unknown-option usage error with exit code `2` before SDK resolution or launch

#### Scenario: Bare positional input is rejected

- **WHEN** a caller invokes `odcli run sale` without a literal `--`
- **THEN** the command reports a usage error with exit code `2` and performs no SDK resolution, use update, command construction, or launch

#### Scenario: Protected override is rejected before spawn

- **WHEN** `odcli run -- --database other` or another protected runtime-identity override is invoked
- **THEN** the SDK validator returns a sanitized error before command execution and no child process starts

#### Scenario: Dry-run and execution use one captured argv

- **WHEN** the same allowed delimiter arguments are supplied to dry-run and normal execution under a recording executor
- **THEN** dry-run displays the exact captured foreground step and normal execution consumes it without reconstructing argv

#### Scenario: Native TTY and exit behavior remain unchanged

- **WHEN** `odcli run -- --workers=2` executes normally, exits non-zero, or is interrupted
- **THEN** inherited stdin/stdout/stderr remain native, the real exit code is returned, and interrupt cleanup preserves exit `130`

#### Scenario: Detached flag after literal delimiter is native

- **WHEN** `odcli run -- -d` is invoked
- **THEN** `-d` is passed to Odoo as a native argument and is not interpreted as the detach option

#### Scenario: Detached run provisions an effective logfile

- **WHEN** `odcli run --detach` runs with an `odoo.conf` that has no `logfile`
- **THEN** the command chooses `odoo.log` next to the effective config, creates the parent directory and file before spawn, passes the path to Odoo without editing the user's config, and uses the same path for `logs`, the structured result, and runtime metadata

#### Scenario: Explicit logfile has priority

- **WHEN** `odcli run --detach` runs with an `odoo.conf` that has a non-empty `logfile`
- **THEN** the explicit path is used and no fallback path is created

#### Scenario: Detached dry-run shows the resolved logfile

- **WHEN** `odcli run --detach --dry-run` runs
- **THEN** the resolved logfile path is shown and no directory or file is created

#### Scenario: Unwritable logfile fallback fails before spawn

- **WHEN** the chosen fallback logfile path cannot be created or opened
- **THEN** the command fails before spawn with `logfile_unwritable` and the exact path
#### Scenario: Two isolated environments use different logfiles

- **WHEN** two isolated environments of one project run detached
- **THEN** each writes to its own `<environment-root>/odoo.log` and their outputs do not mix

### Requirement: `init` wires `--postgres*` options

`odcli init` SHALL accept `--from-vscode`, `--launch-name`, `--postgres`, `--postgres-image`, `--no-input`, `--yes`, `--dry-run`, and the existing options. It SHALL additionally accept `--test-url URL`, `--test-database NAME`, `--test-branch BRANCH`, `--local-config`, and `--allow-partial`. `--test-url`, `--test-database`, and `--test-branch` SHALL fill the existing `[test_instance]` without a new config format. `--local-config` SHALL select self-contained project config: generated `.odcli/odoo.conf` becomes the effective local `source_config`, with existing protections for untracked regular file and `0600` mode, and SHALL NOT create source/target aliases outside the selected mode. For a remote origin, the command SHALL create or update `.odcli/.env` with `ODCLI_TEST_INSTANCE_ORIGIN_PINS` under `0600` and Git-ignored. The master password SHALL NOT be accepted as a CLI argument and SHALL NOT be stored in manifest/history; interactive RICH mode MAY prompt it hidden with `getpass`; in `--no-input` an empty `ODCLI_TEST_MASTER_PASSWORD=` SHALL be left with a clear next command, or an already-set value SHALL be used. Secrets SHALL NOT appear in argv, manifest, Rich/JSON/TOON, plan, or process-environment diagnostics.

For a self-contained Compose setup, `init` SHALL create bootstrap database `tmp` with `base` installed as specified in the `project-init` `tmp` requirement (design D5a).

Before any file write, the command SHALL compute missing groups `test_url`, `test_branch`, `local_config`, `dotenv_origin`, and `postgres_mode_image` as defined in design D5. `--dry-run` completeness SHALL NOT call `DatabaseResource.names()` (no HTTP); on `--dry-run`, `test_database` is missing when it is absent from CLI flags and the manifest. Execute MAY call `names()` as an HTTP ActionStep and drop `test_database` from the missing set when the list has exactly one name. If the set is incomplete, interactive `init` (`no_input` false and RICH) SHALL stop before any writes and ask Click confirm with default cancel (cancel vs continue partially). In `--no-input`, incomplete setup SHALL fail with `init_incomplete` before writes unless `--allow-partial` is explicit; `--yes` SHALL NOT bypass the check; `--dry-run` SHALL NOT prompt. Non-RICH output (`json`/`toon`) SHALL use the `--no-input` completeness path. A fully-specified setup SHALL NOT ask the question. Re-running `init` without test-instance options SHALL preserve an existing valid `[test_instance]`; explicit new options SHALL replace it atomically after validation. External PostgreSQL/source-config remains a supported full profile when chosen explicitly; missing remote test instance SHALL still require cancel-vs-partial or `--allow-partial`.

#### Scenario: Init with postgres and vscode import

- **WHEN** `odcli init --from-vscode launch.json --postgres compose --postgres-image ...` runs
- **THEN** both VS Code import and postgres section are persisted; provenance records both sources

#### Scenario: Init provenance records postgres option

- **WHEN** `odcli init --postgres compose --postgres-image ... --dry-run --json` runs
- **THEN** provenance includes `option` entry for `postgres`

#### Scenario: init full self-contained setup

- **WHEN** `odcli init --from-vscode .vscode/launch.json --launch-name "Odoo bunasta_ce" --postgres compose --postgres-image postgres:12 --test-url http://example --test-database db --test-branch main --local-config --yes` runs
- **THEN** the project gets an owned Compose cluster, generated local config, a valid `[test_instance]`, a `.odcli/.env` under `0600`, `data_dir` `{project_root}/.odcli/filestore`, and bootstrap database `tmp`

#### Scenario: init interactive incomplete asks cancel-vs-partial

- **WHEN** interactive `init` runs with an incomplete setup
- **THEN** before any write it shows the missing groups and asks one blocking question with cancel (recommended) and continue-partial choices

#### Scenario: init no-input incomplete fails

- **WHEN** `odcli init --no-input --yes` runs with an incomplete setup and without `--allow-partial`
- **THEN** it fails before writes and changes no manifest, config, dotenv, catalog, or cluster resources

#### Scenario: init allow-partial is the only non-interactive opt-out

- **WHEN** `odcli init --no-input --allow-partial` runs with an incomplete setup
- **THEN** partial initialization proceeds and the result contains a `partial initialization` warning with the missing capabilities

#### Scenario: init re-run preserves test_instance

- **WHEN** `odcli init` runs again without test-instance options and an existing valid `[test_instance]` is present
- **THEN** the existing `[test_instance]` is preserved

#### Scenario: init dry-run shows actions without writes

- **WHEN** `odcli init --dry-run` runs
- **THEN** completeness follows design D5 (no `names()` HTTP), manifest/config/dotenv/`tmp` steps are shown, and no file, secret, cluster, or HTTP mutation occurs

### Requirement: Context-aware `odcli db reset-admin-password`

`odcli db reset-admin-password` SHALL require an explicit secret. Prompt with `getpass` twice only when output mode is RICH and `--no-input` is false. `--format json|toon`, `--no-input`, and `--dry-run` SHALL NOT prompt. `--yes` SHALL NOT skip the prompt. Non-prompt paths SHALL read process `ODCLI_ADMIN_PASSWORD` then project `.odcli/.env`. If the secret is absent, the command SHALL fail with `admin_password_required` before any mutation. The secret SHALL NOT appear in argv, shell history, manifest, catalog, plan, Rich/JSON/TOON output, logs, or exception text. The result SHALL report only the fact of success and a safe provenance (`prompt` or `environment`). No implicit fallback to `admin` or any other common value SHALL remain. The same mechanism SHALL serve restore and COPY replacement. After a successful reset, a user SHALL be able to log in as `base.user_admin` with that secret (Odoo 13 and 19 regression via the real-Odoo XML-RPC test-support probe).

#### Scenario: Reset from registered worktree

- **WHEN** the command runs inside one ready registered worktree
- **THEN** it resets that environment's single bound database through the resource and ORM

#### Scenario: Project root is not enough

- **WHEN** the command runs outside a registered worktree without `--env`
- **THEN** it fails with candidate guidance and modifies no database

#### Scenario: interactive prompt hidden with confirmation

- **WHEN** `odcli db reset-admin-password` runs interactively
- **THEN** the password is prompted hidden with confirmation and is not echoed

#### Scenario: non-interactive without secret fails before mutation

- **WHEN** `odcli db reset-admin-password --yes` runs without `ODCLI_ADMIN_PASSWORD`
- **THEN** it fails before any mutation with an actionable error

#### Scenario: secret provenance is reported, not the secret

- **WHEN** a reset succeeds
- **THEN** the result reports the fact and provenance (`prompt` or `environment`) and the secret is absent from all outputs

### Requirement: `odcli db refresh` option and context rules

`db refresh` SHALL require project context through explicit `--project`, nearest manifest, or exact registered worktree. It SHALL source the remote instance only from project `[test_instance]`. `--source-branch` SHALL override its configured branch. `--reset-admin-password` without `--restore` SHALL be a Click usage error with exit code 2 before SDK/network/catalog mutation.

Without `--restore`, the command SHALL download only. With `--restore`, it SHALL request the complete preparation flow. It SHALL not prompt for the Odoo master password and SHALL never accept a master password option. When `--reset-admin-password` is combined with `--restore`, the admin reset SHALL use the explicit-secret mechanism from the `Context-aware odcli db reset-admin-password` requirement: interactive mode SHALL prompt hidden for the new admin password with confirmation; non-interactive mode SHALL read `ODCLI_ADMIN_PASSWORD` through the existing project dotenv/redaction boundary. The master-password prompt prohibition applies to the Odoo master password only; the admin-reset secret is a separate, explicit assignment and SHALL NOT fall back to `admin` or any other common value.

`db refresh` SHALL accept an optional `test_instance.database`. If it is explicitly set in `.odcli/project.toml`, it SHALL be used without requiring database list availability. If it is absent, remote-refresh preflight SHALL obtain names through the existing `DatabaseResource.names()`; exactly one database SHALL be selected for the current operation; zero databases SHALL fail with `remote_database_none`; multiple databases SHALL fail with `remote_database_ambiguous` listing available names; an unavailable list SHALL fail with `remote_database_list_unavailable`. All three fail before download. The auto-detected name SHALL be reflected in plan/result and backup provenance but SHALL NOT be written back to `project.toml`. Manifest round-trip SHALL NOT add `database` when it was not set.

#### Scenario: Download-only refresh

- **WHEN** `odcli db refresh` runs in a configured project
- **THEN** it downloads/catalogs a backup and does not touch local databases or the project default

#### Scenario: Reset flag requires restore

- **WHEN** `odcli db refresh --reset-admin-password` runs without `--restore`
- **THEN** Click exits 2 with a usage error before any operation begins

#### Scenario: explicit database has priority

- **WHEN** `odcli db refresh` runs with an explicit `test_instance.database`
- **THEN** that name is used and database list availability is not required

#### Scenario: single database auto-selected

- **WHEN** `odcli db refresh` runs without `test_instance.database` and the instance exposes exactly one database
- **THEN** that database is selected for the current operation and the name appears in plan/result and backup provenance

#### Scenario: zero databases actionable error

- **WHEN** `odcli db refresh` runs without `test_instance.database` and the instance exposes zero databases
- **THEN** the command fails before download with `remote_database_none`

#### Scenario: many databases list names

- **WHEN** `odcli db refresh` runs without `test_instance.database` and the instance exposes multiple databases
- **THEN** the command fails before download with `remote_database_ambiguous`, lists the available names, and asks to set `test_instance.database` explicitly

#### Scenario: unavailable database list is a distinct error

- **WHEN** `odcli db refresh` runs without `test_instance.database` and the database list is unavailable
- **THEN** the command fails before download with `remote_database_list_unavailable`, and does not guess a name

#### Scenario: auto-detected name is not written back

- **WHEN** a name is auto-detected and the operation completes
- **THEN** `project.toml` is unchanged and manifest round-trip does not add `database`

### Requirement: `module` commands

`odcli module list`, `odcli module update`, and `odcli module test` SHALL remain bounded structured leaves. `module update` SHALL prioritize a valid nonce-framed payload and use the `user_error` or `finalization_error` from the common shell wrapper; when the payload is missing or malformed, it SHALL fall back to the last `_TIMEOUT_TAIL_BYTES = 8192` redacted bytes of `stderr`, not the first N characters. Full unlimited tracebacks SHALL NOT be emitted and existing redaction SHALL NOT be weakened. Rich, JSON, and TOON SHALL return the same stable error code and safe details. Secrets, terminal escapes, and sensitive paths SHALL remain sanitized. The common shell-error contract SHALL be reused; no separate parser for `module update` SHALL be added.

#### Scenario: Module list

- **WHEN** `odcli module list --state installed` executes
- **THEN** installed modules are listed via local Odoo shell

#### Scenario: Requested names remain a list

- **WHEN** `odcli module update sale --yes` builds its Odoo shell source
- **THEN** the search domain receives `['sale']` as a list rather than the string `'["sale"]'`

#### Scenario: Empty recordset is not success

- **WHEN** Odoo returns no upgraded record for a requested module
- **THEN** the command exits non-zero with a sanitized failure and does not emit `ok=true` or claim that module was updated

#### Scenario: Project-context update

- **WHEN** `odcli module update sale --yes` runs from an initialized main checkout without an exact environment
- **THEN** it uses the project runtime and configured database through the existing update command path

#### Scenario: Module test compatibility

- **WHEN** an existing caller invokes `odcli module test comerta_base --test-tags /comerta_base`
- **THEN** the request reaches the same preflight, `OdooTestSpec`, runner, and result path as the top-level command

#### Scenario: long startup log does not hide the traceback

- **WHEN** `odcli module update <MODULE> --yes` fails with a startup log longer than the limit and a traceback at the end
- **THEN** the final error contains the exception type and the root cause

#### Scenario: framed payload has priority

- **WHEN** a valid nonce-framed `user_error` payload is present
- **THEN** it is used with priority over the general `stderr` tail

#### Scenario: fallback uses bounded tail

- **WHEN** the framed payload is missing or malformed
- **THEN** a bounded redacted tail of `stderr` is used and truncation is reported explicitly

#### Scenario: same error code across formats

- **WHEN** `module update` fails and is rendered in Rich, JSON, and TOON
- **THEN** all three return the same stable error code and safe details

### Requirement: Detached Odoo launch

`odcli run` SHALL accept `-d, --detach` to launch Odoo detached. Without the flag, the existing foreground contract SHALL be preserved. Detached mode SHALL use the existing project/environment resolution, port preflight, PostgreSQL preflight, argv construction, `internal/proc`, runtime identity, and ownership/stop mechanism. A separate daemon manager or second command-construction path SHALL NOT be introduced.

After spawn, the command SHALL confirm the process is alive and runtime identity is persisted, then return PID, project/environment identity, HTTP endpoint, and log path without waiting for Odoo to finish. If the process exits immediately, the command SHALL return an error and SHALL NOT leave a false `running` record. The Odoo lifetime SHALL NOT be tied to the terminal or CLI: exiting `odcli run -d` SHALL NOT terminate the child. Stopping SHALL work through the existing `odcli stop` and stale runtime records SHALL be recognized normally.

Logs SHALL use `resolve_effective_logfile()` from the `odcli run` requirement. Absence of `logfile` SHALL NOT be an error. `odcli logs --tail` and `odcli logs --follow` SHALL read that path. `--dry-run -d` SHALL show the sanitized process command, detached plan, and resolved logfile without spawning or creating the file.

#### Scenario: Detached run returns promptly

- **WHEN** `odcli run -d` launches successfully
- **THEN** the command returns promptly with PID, identity, endpoint, and log path while Odoo continues running

#### Scenario: Immediate exit is not success

- **WHEN** the detached Odoo process exits immediately after spawn
- **THEN** the command returns an error and leaves no stale active runtime record

#### Scenario: Stop targets the detached runtime

- **WHEN** `odcli stop` runs after a detached launch
- **THEN** it stops exactly that persisted runtime

#### Scenario: Logs work after detached launch

- **WHEN** `odcli logs --tail` and `odcli logs --follow` run after a detached launch
- **THEN** both read from the bound logfile and follow produces new lines

#### Scenario: Detached dry-run does not spawn

- **WHEN** `odcli run --dry-run -d` runs
- **THEN** the sanitized process command and detached lifecycle plan are shown and no process or runtime record is created

#### Scenario: Foreground behavior is unchanged

- **WHEN** `odcli run` runs without `-d`
- **THEN** signals, exit code, stdio, and cleanup match the existing foreground contract

#### Scenario: Detached run provisions an effective logfile

- **WHEN** `odcli run -d` runs with no configured `logfile`
- **THEN** `odoo.log` next to the effective config is created before spawn and used, without editing the user's `odoo.conf`, and absence of `logfile` is not an error

#### Scenario: Explicit logfile has priority

- **WHEN** `odcli run -d` runs with a non-empty `logfile` in the bound config
- **THEN** the explicit path is used and no fallback is created

#### Scenario: Detached dry-run shows the resolved logfile

- **WHEN** `odcli run --dry-run -d` runs
- **THEN** the resolved logfile path is shown and no process, runtime record, directory, or file is created

#### Scenario: Unwritable logfile fails fast

- **WHEN** the chosen fallback logfile path cannot be created or opened
- **THEN** the command fails before spawn with `logfile_unwritable` and the exact path
