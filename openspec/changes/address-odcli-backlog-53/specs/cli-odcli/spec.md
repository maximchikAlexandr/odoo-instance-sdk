## ADDED Requirements

### Requirement: Rich Click help and validation errors

The root command and every nested group and command SHALL use `rich-click` for Click-generated help, usage, and validation errors. Every visible entry SHALL have a useful one-line description; existing option types, metavars, defaults, required markers, choices, ranges, command names, parsing, exit codes, completion behavior, and `--help` behavior SHALL remain intact. Help MAY use at most four stable task-oriented panels and SHALL leave small pages ungrouped. Command results, output envelopes, passthrough streams, logs, and progress SHALL continue through their existing boundaries and SHALL NOT be rendered by `rich-click`.

#### Scenario: Typed leaf help remains informative
- **WHEN** a typed nested leaf is rendered at a narrow terminal width
- **THEN** its description, required/default/type metadata, and options remain readable without changing parsing

#### Scenario: Redirected help has no ANSI
- **WHEN** help or a Click validation error is redirected or color is disabled
- **THEN** the output is readable, contains no ANSI escapes, and retains Click's exit code

### Requirement: Shared human dry-run projection

Default Rich dry-run output SHALL use one shared projection showing the command goal, resolved project/environment/database/modules, intended mutations, preconditions, and warnings. It SHALL collapse implementation-only probes into meaningful operations and SHALL omit repeated classifications, raw argv, executable, cwd, timeout, and fingerprint. JSON and TOON SHALL retain the complete immutable plan and remain semantically equal.

#### Scenario: Human plan is decision-oriented
- **WHEN** a mutating command is invoked with `--dry-run` in default Rich mode
- **THEN** the user sees targets, mutations, preconditions, and warnings without low-level execution fields

#### Scenario: Machine plan remains complete
- **WHEN** the same dry-run is emitted as JSON or TOON
- **THEN** the full immutable execution snapshot, including exact redacted process details and fingerprint, is present

### Requirement: Dry-run reports failed runtime preconditions

`odcli run --dry-run` SHALL finish plan construction when the effective HTTP port is occupied and represent the conflict as a failed precondition or warning without spawning Odoo or changing state. Normal `odcli run` SHALL continue to fail before spawn.

#### Scenario: Occupied port is visible in dry-run
- **WHEN** `odcli run --dry-run` resolves an occupied configured HTTP port
- **THEN** it emits a side-effect-free plan identifying the failed port precondition instead of returning before the plan

### Requirement: Restore progress and command streams

Interactive Rich `odcli db refresh --restore` SHALL show current and completed plan steps by default. An explicit `--show-command-output` flag SHALL stream sanitized stdout and stderr associated with the producing step. JSON and TOON SHALL emit one deterministic final document without Rich rendering or raw stream injection. Existing exit codes, captured subprocess results, and redaction SHALL remain unchanged.

#### Scenario: Interactive restore shows plan progress
- **WHEN** restore runs in an interactive Rich terminal without the stream flag
- **THEN** current and completed logical steps are displayed without dumping subprocess output

#### Scenario: Machine restore remains bounded
- **WHEN** restore runs in JSON or TOON mode
- **THEN** stdout contains exactly one parseable document and no live progress or raw command stream

### Requirement: Safe database-drop command

The CLI SHALL expose `odcli db drop DATABASE [--force-default] [--force-connections] [--yes] [--dry-run]`. It SHALL require an exact database name, resolve only the current project PostgreSQL cluster, reject system/template databases, display the cluster and database before mutation, require interactive confirmation by default, and require `--yes` for noninteractive execution. Dropping the configured project default SHALL additionally require `--force-default`; terminating active sessions SHALL additionally require `--force-connections`. Rich, JSON, and TOON SHALL use the shared output and confirmation contracts.

#### Scenario: Protected default database is refused
- **WHEN** the exact target is the configured project default and `--force-default` is absent
- **THEN** the command fails before termination or drop and identifies the protection

#### Scenario: Dry-run needs no confirmation
- **WHEN** a valid drop target is invoked with `--dry-run` without force or yes flags unrelated to observed conditions
- **THEN** the command emits the resolved guarded plan without prompt, connection termination, database mutation, or catalogue write

## MODIFIED Requirements

### Requirement: Stable machine output

The exact bounded structured leaf inventory SHALL be the existing canonical inventory plus `db drop`: `init`, `doctor`, `env checkout`, `env list`, `env remove`, `env sync`, `db refresh`, `db reset-admin-password`, `db drop`, `eval`, `exec`, `test`, `module list`, `module update`, `module test`, `translations export`, `deps verify`, `vscode generate`, `db locks`, `db stats`, `db bloat`, `db init-monitoring`, `postgres approve-image`, `postgres status`, `postgres up`, and `postgres stop`. Every leaf SHALL retain its existing classification and format behavior; `db drop` SHALL be classified as a bounded `mutating-or-spawning` leaf with required dry-run support and SHALL accept command-local `--format rich|json|toon` plus the compatible `--json` alias. The canonical `PUBLIC_LEAF_CASES` table SHALL remain the single consumer/source for the documented leaf inventory and characterization matrix.

#### Scenario: Database drop is in the canonical bounded inventory
- **WHEN** the stable machine-output characterization gate compares discovered normal-execution leaves with `PUBLIC_LEAF_CASES`
- **THEN** `db drop` appears exactly once as a bounded mutating leaf, accepts every shared output mode, and its dry-run variant performs no mutation

### Requirement: Test output uses the shared CLI contract

Both `odcli test` and `odcli module test` SHALL remain bounded structured leaves under `OutputMode` and CLI envelope v1. Their common result SHALL add exactly these owner/context fields: `owner_kind: "environment" | "project"`, `project_id: str`, `environment_id: str | null`, `environment_name: str | null`, `worktree_root: str`, `database: str`, `http_url: str`, and `command_prefix: list[str]`. For an environment owner, both environment fields SHALL be non-null and identify the resolved environment; for a project owner, both SHALL be null. `project_id`, worktree, database, HTTP URL, and command prefix SHALL always describe the same resolved owner and SHALL never fabricate an environment.

Every successful result SHALL also contain selector kind/value and provenance, modules, and exit code. Executed results SHALL additionally contain effective native test tags, `reload_tests`, `allow_empty`, counts, and failure/zero-tests flags. A successful changed dry-run SHALL contain `dry_run=true` and complete base/Git provenance while omitting all execution-only fields. A changed selection with no addons SHALL contain `reason="no_addon_changes"`, complete base/Git provenance, empty modules, and `exit_code=0`, omitting those execution-only fields; if also a dry-run it MAY include `dry_run=true`. Neither non-executed state SHALL construct an `OdooTestResult` or emit execution progress. JSON and strict-decoded TOON SHALL be semantically equal in executed, no-op, and dry-run states. Rich SHALL render the same owner identity, common worktree/runtime context, selection/modules, and exit status, and SHALL show counts only for executed results. Machine stdout, diagnostics, aliases, error mapping, and command-name compatibility SHALL retain the existing shared CLI contract.

#### Scenario: Environment-owned result remains compatible
- **WHEN** an executed test resolves an environment owner
- **THEN** every format identifies that environment, its project and common runtime context with `owner_kind="environment"`

#### Scenario: Project-owned result is explicit
- **WHEN** an executed test resolves an initialized project without an environment
- **THEN** every format has `owner_kind="project"`, the canonical `project_id`, null environment fields, and the project worktree/runtime context

#### Scenario: Owner fields have parity in non-executed states
- **WHEN** changed selection returns either a no-addon no-op or a dry-run plan for either owner kind
- **THEN** JSON, strict-decoded TOON, and Rich expose the same owner/common context while execution-only fields and progress remain absent

### Requirement: `odcli db` command group

The Click adapter SHALL expose `db refresh`, `db reset-admin-password`, and `db drop DATABASE [--force-default] [--force-connections] [--yes] [--dry-run]`. It SHALL parse options, resolve project/environment context, render typed results, and map typed exceptions without duplicating SDK or transport logic. Refresh and password reset SHALL retain their existing public-resource paths. Guarded drop SHALL use a CLI-private cluster-bound PostgreSQL operation and SHALL NOT call, replace, or change the public Odoo HTTP `DatabaseResource.drop/drop_command` methods.

#### Scenario: Help exposes all database commands
- **WHEN** `odcli db --help` runs
- **THEN** it lists `refresh`, `reset-admin-password`, and `drop` with their documented options

### Requirement: `eval` and `exec`

```bash
odcli eval "env['res.users'].search_count([])"
odcli exec ./script.py -- arg1 arg2
```

- `eval` MUST вычислять одно Python expression в Odoo shell context (`env`, `odoo`, `self`) и возвращать scalar/collection JSON либо typed recordset summary `{model, ids, count}`; unknown objects MUST получать bounded sanitized `repr`.
- `eval` SHALL return captured user stdout separately from the expression result, including print-only `exec(...)`, Unicode/multiline output, and output emitted before an exception.
- Eval failure SHALL distinguish Odoo startup failure from user-code failure and preserve the exception type, message, and relevant traceback/source context after bounded truncation; startup-log prefixes SHALL NOT replace the actual exception.
- Rich SHALL label user output separately; JSON and TOON SHALL carry it as a structured field and SHALL never inject raw prints into machine stdout.
- `exec` MUST читать explicit file (`-` означает caller stdin), передавать script через shell stdin и устанавливать predictable `sys.argv` из tokens после `--`.
- default MUST быть best-effort shell rollback. Explicit `--commit` MUST быть виден в plan.
- project config MUST NOT автоматически подставлять eval/exec source.
- All output and diagnostics SHALL remain secret-redacted, and failures SHALL remain non-zero.

#### Scenario: Eval expression
- **WHEN** `odcli eval "1+1"` executes in a supported environment or project context
- **THEN** result `2` is returned separately from an empty captured user-output field and no RPC is used

#### Scenario: Print then fail
- **WHEN** eval runs `exec("print('before'); raise ValueError('failure')")`
- **THEN** captured user output contains `before`, the diagnostic identifies `ValueError: failure` with relevant context, and the command exits non-zero

### Requirement: `module` commands

```bash
odcli module list --state installed
odcli module update comerta_base --dry-run
odcli module update comerta_base --yes
odcli module test comerta_base --test-tags /comerta_base --reload-tests
```

- `list [MODULE...]` MUST читать `ir.module.module`.
- `update` MUST требовать `--yes`; внутри shell `button_immediate_upgrade()`.
- `update` SHALL operate from either a ready environment or initialized project through the shared context and configured database; explicit `--env` SHALL retain precedence.
- `test` MUST remain a backward-compatible alias to the top-level local Odoo test operation and SHALL support both context kinds without fabricating an environment.
- install/uninstall MUST NOT добавляться. Public `ModuleResource` or `TestResource` MUST NOT существовать.

#### Scenario: Module list
- **WHEN** `odcli module list --state installed` executes
- **THEN** installed modules are listed via local Odoo shell

#### Scenario: Project-context update
- **WHEN** `odcli module update sale --yes` runs from an initialized main checkout without an exact environment
- **THEN** it uses the project runtime and configured database through the existing update command path

#### Scenario: Module test compatibility
- **WHEN** an existing caller invokes `odcli module test comerta_base --test-tags /comerta_base`
- **THEN** the request reaches the same preflight, `OdooTestSpec`, runner, and result path as the top-level command

### Requirement: `vscode generate`

```bash
odcli vscode generate
odcli vscode generate --write
```

The command SHALL accept the shared `environment | project` context. In project context it SHALL derive Python, Odoo entry point, source config, repository root, and configured database from the initialized project; in environment context it SHALL preserve existing generated-config and recorded-runtime behavior. Default SHALL print one debugpy profile; `--write` SHALL atomically create a missing `.vscode/launch.json` and SHALL NOT rewrite existing JSONC. `--dry-run` SHALL report the intended write without creating directories or files. Rich, JSON, and TOON SHALL use the shared output contract.

#### Scenario: Print project profile
- **WHEN** `odcli vscode generate` runs from a complete initialized project with no exact environment
- **THEN** one profile derived from project values is printed and no file is written

#### Scenario: Existing JSONC is preserved
- **WHEN** `--write` targets an existing `.vscode/launch.json`
- **THEN** the command refuses to overwrite it in either context
