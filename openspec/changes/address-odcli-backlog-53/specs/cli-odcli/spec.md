## ADDED Requirements

### Requirement: Rich Click help and validation errors

The root command and every nested group and command SHALL use `rich-click>=1.9,<2` for Click-generated help, usage, and validation errors. Every visible entry SHALL have a useful one-line description; existing option types, metavars, defaults, required markers, choices, ranges, command names, parsing, exit codes, completion behavior, and `--help` behavior SHALL remain intact. Help MAY use at most four stable task-oriented panels and SHALL leave small pages ungrouped. Command results, output envelopes, passthrough streams, logs, and progress SHALL continue through their existing boundaries and SHALL NOT be rendered by `rich-click`.

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

Rich `odcli db refresh --restore` SHALL show logical step progress. On an interactive TTY it SHALL use live current/completed-step rendering; on non-TTY Rich output it SHALL emit deterministic step-prefixed sanitized lines without `Live` or cursor control. An explicit `--show-command-output` SHALL stream sanitized, step-prefixed stdout/stderr only in Rich mode. Combining `--show-command-output` with `--format json`, `--format toon`, or `--json` SHALL be a Click usage error with exit code `2` before SDK work. JSON and TOON without the flag SHALL emit one deterministic final document without Rich rendering or raw stream injection. Existing execution exit codes, captured subprocess results, and redaction SHALL remain unchanged.

#### Scenario: Interactive restore shows plan progress
- **WHEN** restore runs in an interactive Rich terminal without the stream flag
- **THEN** current and completed logical steps are displayed without dumping subprocess output

#### Scenario: Machine restore remains bounded
- **WHEN** restore runs in JSON or TOON mode
- **THEN** stdout contains exactly one parseable document and no live progress or raw command stream

#### Scenario: Stream flag is Rich-only
- **WHEN** `--show-command-output` is combined with JSON, TOON, or the JSON alias
- **THEN** Click exits `2` before restore planning/execution and emits no partial machine document

#### Scenario: Redirected Rich output is line-oriented
- **WHEN** Rich restore output is not attached to a TTY
- **THEN** progress and enabled command streams use sanitized step-prefixed lines without `Live`, ANSI cursor control, or unassociated chunks

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

The exact bounded structured leaf inventory is: `init`, `doctor`, `env checkout`, `env list`, `env remove`, `env sync`, `db refresh`, `db reset-admin-password`, `db drop`, `eval`, `exec`, `test`, `module list`, `module update`, `module test`, `translations export`, `deps verify`, `vscode generate`, `db locks`, `db stats`, `db bloat`, `db init-monitoring`, `postgres approve-image`, `postgres status`, `postgres up`, and `postgres stop`. Each SHALL accept command-local `--format rich|json|toon`; `rich` SHALL be the default. Existing `--json` SHALL remain a backward-compatible alias for `--format json`. Supplying `--json` with `--format toon` or `--format rich` SHALL be a Click usage error with exit code `2`; supplying `--json --format json` SHALL be accepted. During normal execution, `run`, interactive `shell`, `psql`, and `logs --follow` SHALL remain raw-streaming and SHALL not emit document output or use a Rich live wrapper. Eligible spawning `run` and `shell` SHALL accept document-format options only together with `--dry-run`; those dry-run paths SHALL suppress native execution and emit one bounded plan document in Rich, JSON, or TOON, with `--json` equivalent to `--format json`. `psql --dry-run` SHALL remain an explicit plan-only exception that emits the shared sanitized native command plan without spawning; normal `psql` remains raw passthrough and SHALL continue to reject `--format` and `--json`.

The CLI SHALL define one CLI-only `OutputMode` with values `rich`, `json`, and `toon`. The mode and envelope types SHALL NOT become public SDK models or FastAPI response models. Each successful or failed bounded operation SHALL first build one JSON-safe CLI envelope v1 containing `schema_version`, `ok`, `command`, `context`, `provenance`, `dry_run`, and `warnings`; success SHALL contain equal `result` and `data`, and failure SHALL contain stable `error.code` and sanitized `error.message`.

JSON and TOON SHALL serialize that exact envelope without building format-specific result graphs. Decoding a TOON document with the selected strict decoder SHALL yield the same JSON value as decoding JSON output for the same operation. Machine modes SHALL emit exactly one UTF-8 document to stdout with no ANSI, prompt, status, progress, or external log text; diagnostics SHALL go to stderr. Renderer selection SHALL NOT change operation execution, exception mapping, or exit code. Native Click parse failures that occur before output-mode resolution SHALL retain Click's stderr usage output and exit code `2`.

For `env remove`, JSON and TOON document modes (including the `--json` alias) SHALL never call `click.confirm`. Without `--yes`, they SHALL NOT execute removal and SHALL emit exactly one sanitized failure envelope with `error.code="confirmation_required"` and exit code `1`. With `--yes`, JSON and TOON SHALL execute the same removal operation and normal success/failure mapping. Interactive Rich mode SHALL retain its existing confirmation behavior.

Rich renderers SHALL remain adjacent to the concrete commands whose typed results they render. They MAY use `Table`, `Status`, `Progress`, and `Live` only when appropriate to the operation; they SHALL NOT introduce a generic renderer interface, registry, or DSL. `db stats` and `db bloat` SHALL render separate tables and indexes tables rather than one sparse combined table.

#### Scenario: JSON envelope

- **WHEN** `odcli env list --json` executes
- **THEN** stdout contains exactly one versioned envelope and no progress or log text

#### Scenario: JSON alias preserves envelope v1

- **WHEN** `odcli env list --json` and `odcli env list --format json` run against the same frozen result
- **THEN** each stdout document decodes to the same envelope v1 and contains no ANSI or diagnostic text

#### Scenario: TOON is semantically equal to JSON

- **WHEN** a bounded command succeeds or fails once and its envelope is emitted as JSON and TOON
- **THEN** strict TOON decoding and JSON decoding produce equal Python builtins including `result`/`data`, context, provenance, warnings, and error fields

#### Scenario: Conflicting alias is usage error

- **WHEN** a caller supplies `--json --format toon`
- **THEN** Click exits `2`, does not execute the operation, and does not emit a partial machine document

#### Scenario: Machine diagnostics stay on stderr

- **WHEN** a bounded machine-mode operation reports a sanitized diagnostic in addition to its result
- **THEN** stdout contains exactly one JSON or TOON envelope and the diagnostic is written only to stderr

#### Scenario: Machine remove requires explicit confirmation

- **WHEN** `odcli env remove ENV --format json`, `--format toon`, or `--json` is invoked without `--yes`
- **THEN** no prompt is rendered, removal is not called, stdout contains one failure envelope with `error.code="confirmation_required"`, and the command exits `1`

#### Scenario: Explicit machine remove executes

- **WHEN** `odcli env remove ENV --yes --format json`, `--format toon`, or `--json` is invoked
- **THEN** the same removal operation runs once and its result is emitted as one document under the normal renderer-independent exit mapping

#### Scenario: Secrets redacted

- **WHEN** error occurs during checkout
- **THEN** every machine or Rich error message redacts passwords, config body, and environment variables before emission

#### Scenario: Diagnostic machine formats share one result graph

- **WHEN** one frozen `db stats` result is projected as JSON and TOON
- **THEN** both decoded envelopes contain equal summary, tables, indexes, capabilities, and warnings with numeric byte fields

#### Scenario: Native command dry-run supports every bounded format

- **WHEN** `odcli run --dry-run` or spawning `odcli shell --dry-run` is requested with `--format rich|json|toon` or `--json`
- **THEN** output contains exactly one bounded plan with `dry_run=true` in the selected format
- **AND** `--json` and `--format json` produce equivalent JSON documents
- **AND** no native child stream starts

#### Scenario: Normal native command stays raw

- **WHEN** `odcli run` or interactive `odcli shell` executes without `--dry-run`
- **THEN** its inherited stream is not wrapped in a bounded document or Rich live view

#### Scenario: Normal native command rejects machine options

- **WHEN** `odcli run` or spawning `odcli shell` is invoked with `--format` or `--json` but without `--dry-run`
- **THEN** Click exits `2` before invoking SDK code or starting a process

#### Scenario: Canonical bounded inventory remains single-source

- **WHEN** the stable machine-output characterization gate compares the documented normal-execution leaves
- **THEN** they equal canonical `PUBLIC_LEAF_CASES`, including `test`, `db refresh`, `db reset-admin-password`, and `db drop`
- **AND** no second bounded-leaf table is introduced


#### Scenario: Database drop is a canonical bounded leaf

- **WHEN** the stable machine-output characterization gate exercises `db drop DATABASE --dry-run` in every shared format
- **THEN** `db drop` appears exactly once in canonical `PUBLIC_LEAF_CASES` as `mutating-or-spawning` with required dry-run support
- **AND** no database, session, or catalogue mutation occurs

### Requirement: Test output uses the shared CLI contract

Both `odcli test` and `odcli module test` SHALL be bounded structured leaves under the MYL-55 `OutputMode` and CLI envelope v1 contract. They SHALL accept command-local `--format rich|json|toon`, keep `--json` as the alias for `--format json`, reject conflicting format flags through the shared option resolver, and use the shared sanitized error/exit mapping. The command name in new-path envelopes SHALL be `test`; the compatibility path SHALL retain `module.test` while `result` and execution semantics remain equal for equivalent inputs.

Every success machine result SHALL contain `owner_kind: "environment" | "project"`, canonical `project_id`, nullable `environment_id` and `environment_name`, `worktree_root`, `database`, `http_url`, `command_prefix: list[str]`, selector kind/value and provenance, modules, and exit code. For an environment owner both environment fields SHALL identify the resolved environment; for a project owner both SHALL be null. All common worktree/runtime fields SHALL describe the same resolved owner and SHALL NOT fabricate an environment. An executed result SHALL additionally contain effective native test tags, `reload_tests`, `allow_empty`, counts, and failure/zero-tests flags from `OdooTestResult`. A successful `--changed --dry-run` result SHALL instead contain `dry_run=true` plus complete base/Git provenance and SHALL omit `test_tags`, `reload_tests`, `allow_empty`, counts, and failure/zero-tests flags. A successful changed selection with no addons SHALL contain `reason="no_addon_changes"`, complete base/Git provenance, empty modules, and `exit_code=0`, and SHALL omit those same execution-only fields; if it is also a dry-run it MAY additionally contain `dry_run=true`. Neither non-executed state SHALL construct or imply an `OdooTestResult`, fabricate zero counts/false flags, or emit execution progress. JSON and strict-decoded TOON SHALL be semantically equal in all three states. Raw sanitized Odoo diagnostics SHALL be written only to stderr; machine stdout SHALL contain exactly one document without ANSI, prompts, progress, or embedded raw logs.

Rich output SHALL show the same owner identity, project, nullable environment identity, common worktree/runtime context, selection/modules, and exit status. It SHALL show final counts only for executed results; `--changed --dry-run` and `no_addon_changes` MAY use a Rich table but SHALL not display fabricated counts or execution progress.

#### Scenario: JSON and TOON test parity

- **WHEN** equivalent frozen test results are emitted with `--format json` and `--format toon`
- **THEN** decoded envelope-v1 values are equal, stdout contains one document, diagnostics are only on stderr, and both commands return the typed result's exit code

#### Scenario: Changed no-op is a successful document

- **WHEN** `odcli test --changed --format json` finds only docs/non-addon paths
- **THEN** stdout contains one success envelope with `reason="no_addon_changes"`, selected modules is empty, and no Odoo diagnostics/process are produced

#### Scenario: Dry-run machine shape has no execution fields

- **WHEN** `odcli test --changed --dry-run --format json` safely selects one or more addons
- **THEN** stdout contains selection/base provenance, modules, `dry_run=true`, and `exit_code=0`, omits tags/options/counts/failure/zero flags, and produces no execution progress or Odoo diagnostics


#### Scenario: Environment and project owner shapes are explicit

- **WHEN** equivalent executed tests resolve once to an environment and once to its initialized project
- **THEN** every format reports `owner_kind`, the same canonical project/common runtime fields, non-null environment identity only for the environment owner, and null environment identity only for the project owner

#### Scenario: Owner fields have parity in non-executed states

- **WHEN** changed selection returns a no-addon no-op or dry-run for either owner kind
- **THEN** JSON, strict-decoded TOON, and Rich expose the same owner/common context while execution-only fields and progress remain absent

### Requirement: `odcli db` command group

The Click adapter SHALL add:

```text
odcli db refresh [--restore] [--reset-admin-password] [--source-branch BRANCH]
odcli db reset-admin-password
odcli db drop DATABASE [--force-default] [--force-connections] [--yes] [--dry-run]
```

`commands/db.py` SHALL parse options, resolve project/environment context, call existing public resources for refresh/password reset, call the CLI-private cluster-bound PostgreSQL operation for guarded drop, render typed results, and map typed exceptions. The guarded drop path SHALL NOT call, replace, or change the public Odoo HTTP `DatabaseResource.drop/drop_command` methods. It SHALL not download, restore, acquire locks, run ORM scripts, edit manifests, or construct alternate result dictionaries itself. The group SHALL be registered through the stable `odoo_instance_sdk.cli:cli` entry point after rebasing the MYL-55 CLI foundation.

#### Scenario: Help exposes database commands

- **WHEN** `odcli db --help` runs
- **THEN** it lists `refresh`, `reset-admin-password`, and `drop` with the documented options

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

#### Scenario: Print profile
- **WHEN** `odcli vscode generate` runs from a complete initialized project with no exact environment
- **THEN** one profile derived from project values is printed and no file is written

#### Scenario: Existing JSONC is preserved
- **WHEN** `--write` targets an existing `.vscode/launch.json`
- **THEN** the command refuses to overwrite it in either context
