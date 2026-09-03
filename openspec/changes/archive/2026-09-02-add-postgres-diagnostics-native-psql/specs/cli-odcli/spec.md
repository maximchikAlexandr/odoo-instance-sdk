## MODIFIED Requirements

### Requirement: Stable machine output

The exact bounded structured leaf inventory is: `init`, `doctor`, `env checkout`, `env list`, `env remove`, `env sync`, `db refresh`, `db reset-admin-password`, `eval`, `exec`, `test`, `module list`, `module update`, `module test`, `translations export`, `deps verify`, `vscode generate`, `db locks`, `db stats`, `db bloat`, `db init-monitoring`, `postgres approve-image`, `postgres status`, `postgres up`, and `postgres stop`. Each SHALL accept command-local `--format rich|json|toon`; `rich` SHALL be the default. Existing `--json` SHALL remain a backward-compatible alias for `--format json`. Supplying `--json` with `--format toon` or `--format rich` SHALL be a Click usage error with exit code `2`; supplying `--json --format json` SHALL be accepted. During normal execution, `run`, interactive `shell`, `psql`, and `logs --follow` SHALL remain raw-streaming and SHALL not emit document output or use a Rich live wrapper. Eligible spawning `run` and `shell` SHALL accept document-format options only together with `--dry-run`; those dry-run paths SHALL suppress native execution and emit one bounded plan document in Rich, JSON, or TOON, with `--json` equivalent to `--format json`. `psql --dry-run` SHALL remain an explicit plan-only exception that emits the shared sanitized native command plan without spawning; normal `psql` remains raw passthrough and SHALL continue to reject `--format` and `--json`.

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
- **THEN** they equal canonical `PUBLIC_LEAF_CASES`, including `test`, `db refresh`, and `db reset-admin-password`
- **AND** no second bounded-leaf table is introduced

## ADDED Requirements

### Requirement: PostgreSQL CLI leaves use current project and database context

The CLI SHALL expose `odcli db locks [DATABASE]`, `odcli db stats [DATABASE]`, `odcli db bloat [DATABASE]`, `odcli db init-monitoring [DATABASE] --yes`, root `odcli psql [PSQL_ARGS...]`, and the enriched existing `odcli postgres status`. These leaves SHALL reuse current cwd/project/environment resolution and SHALL NOT add a PostgreSQL-specific `--env`, host, port, user, password, or database-connection selector.

In a registered worktree, omitted `DATABASE` SHALL use the generated environment database. In a project root, omission SHALL work only for an unambiguous project default. An explicit database SHALL stay within the resolved cluster. Diagnostics SHALL work while Odoo is stopped if PostgreSQL is available.

#### Scenario: Worktree omission uses generated database

- **WHEN** `odcli db locks` runs in a registered worktree whose generated config binds database `feature_42`
- **THEN** the command queries `feature_42` on that worktree's resolved project cluster

#### Scenario: No PostgreSQL environment selector

- **WHEN** help is rendered for diagnostics and `psql`
- **THEN** no new PostgreSQL-specific environment or connection-identity selector is offered

### Requirement: Native psql is a raw passthrough command

`odcli psql` SHALL accept, without a mandatory `--`, exactly the closed zero-value and one-value non-identity option set specified by `database-management`, pass those tokens with exact boundaries to `DatabaseResource.psql_command()`, inherit stdin/stdout/stderr and TTY, and return the native exit code. It is a bounded native-option passthrough: protected connection aliases, positional database/user/connection strings, unknown options, missing option values, and operands after `--` SHALL fail before spawn. It SHALL accept neither `--format` nor `--json`.

Running `odcli psql` is the explicit trust boundary for potentially mutating SQL; the CLI SHALL NOT add a per-statement confirmation, custom REPL, Rich wrapper, or machine envelope. Missing binary, unreachable cluster, missing/ambiguous database, and protected connection flags SHALL produce short sanitized actionable errors before spawn.

#### Scenario: Interactive psql inherits the terminal

- **WHEN** `odcli psql` runs with no native arguments in a valid TTY context
- **THEN** native psql owns the terminal streams, completion/history/signals work, and its exit code becomes the CLI exit code

#### Scenario: One-shot native query passes through

- **WHEN** `odcli psql -c 'SELECT current_database();'` runs
- **THEN** psql receives the exact `-c` argument plus SDK-bound connection identity and its native stdout/stderr/exit code are preserved

#### Scenario: Connection override fails before spawn

- **WHEN** `odcli psql --dbname other` or an equivalent protected option is supplied
- **THEN** Click exits with an actionable usage error and no psql process starts

#### Scenario: Positional identity fails before spawn

- **WHEN** `odcli psql other_db`, `odcli psql postgresql://other/db`, `odcli psql -- other_db`, or a second native positional username is supplied
- **THEN** Click exits with an actionable usage error and no psql process starts

#### Scenario: Declared value-taking options do not become positional identity

- **WHEN** `odcli psql -c 'SELECT 1' -f query.sql -v ON_ERROR_STOP=1 -F '|' -Pborder=2 --record-separator=:: -T class=compact` is supplied
- **THEN** token-aware validation consumes each option value at its native arity and preserves every boundary in the planned argv

#### Scenario: Unknown native option fails closed

- **WHEN** `odcli psql` receives an option not listed in the supported grammar
- **THEN** Click exits with an actionable usage error and no psql process starts

### Requirement: Monitoring initialization uses shared confirmation and dry-run

`odcli db init-monitoring [DATABASE]` SHALL be mutating and SHALL require `--yes` for execution in every noninteractive/machine context. Without `--yes`, machine modes SHALL emit one `confirmation_required` failure document without prompting or mutation; Rich interactive mode MAY use the existing confirmation behavior. `--dry-run` SHALL build and render the exact shared command plan without prompt, process spawn, or database mutation, and SHALL not require `--yes`.

Pure read-only `db locks`, `db stats`, `db bloat`, and `postgres status` SHALL NOT add a redundant `--dry-run`. `odcli psql --dry-run ...` SHALL be supported by the shared native command-plan option while normal `psql` remains raw passthrough.

#### Scenario: Machine initialization requires yes

- **WHEN** `odcli db init-monitoring --format json` is invoked without `--yes`
- **THEN** no prompt or PostgreSQL process occurs and one failure envelope reports `confirmation_required`

#### Scenario: Initialization dry-run is exact and inert

- **WHEN** `odcli db init-monitoring --dry-run` is invoked for a valid SDK-owned database
- **THEN** the exact sanitized process/action plan is rendered and no extension is created

#### Scenario: Psql dry-run preserves planned native argv

- **WHEN** `odcli psql --dry-run -c 'SELECT 1'` is invoked
- **THEN** the shared plan shows the exact sanitized native psql step that normal execution would consume and does not spawn it
