## MODIFIED Requirements

### Requirement: Click entry point

SDK MUST добавлять один Click entry point:

```toml
[project.scripts]
odcli = "odoo_instance_sdk.cli:cli"
```

CLI — тонкий adapter над SDK, не оркестратор процессов.

Help и synopsis MUST показывать полный command surface:

```text
odcli [--project PATH] COMMAND
odcli [--project PATH] [--env SELECTOR] <instance-command>

odcli init [OPTIONS]
odcli remove|rm (--project PATH | --project-id ID) [--purge] [OPTIONS]
odcli env checkout BRANCH [OPTIONS]
odcli env sync [ENVIRONMENT] [OPTIONS]
odcli env list [OPTIONS]
odcli env remove [ENVIRONMENT]... [OPTIONS]
odcli run [OPTIONS]
odcli logs [-n|--tail N] [-f|--follow]
odcli shell [-- ODOO_ARGS...]
odcli doctor [OPTIONS]
odcli monitor [--headless] [--host HOST] [--port PORT] [--no-open]
odcli eval EXPRESSION [OPTIONS]
odcli exec SCRIPT [-- SCRIPT_ARGS...]
odcli test [TARGET] [OPTIONS]
odcli module list [MODULE...] [OPTIONS]
odcli module update MODULE... [OPTIONS]
odcli module test MODULE... [OPTIONS]
odcli translations export --module MODULE... [OPTIONS]
odcli deps verify [OPTIONS]
odcli vscode generate [OPTIONS]
odcli ps [OPTIONS]
```

#### Scenario: Help shows full command surface

- **WHEN** `odcli --help` runs
- **THEN** shows init, canonical remove with alternate spelling rm, env, run, logs, shell, doctor, monitor, eval, exec, test, module, translations, deps, vscode, ps

### Requirement: Stable machine output

The exact bounded structured leaf inventory is: `init`, `remove`, `doctor`, `env checkout`, `env list`, `env remove`, `env sync`, `backup list`, `backup show`, `backup validate`, `backup delete`, `db refresh`, `db reset-admin-password`, `db list`, `db restore`, `db drop`, `resource list`, `resource doctor`, `eval`, `exec`, `test`, `module list`, `module update`, `module test`, `translations export`, `deps verify`, `vscode generate`, `ps`, `db locks`, `db stats`, `db bloat`, `db init-monitoring`, `postgres approve-image`, `postgres status`, `postgres up`, and `postgres stop`. Each SHALL accept command-local `--format rich|json|toon`; `rich` SHALL be the default. Removed `--json` SHALL be an ordinary Click usage error with exit code `2`. `--format json` SHALL be the only JSON selector. Supplying `--json` with `--format` SHALL also be a Click usage error with exit code `2`. During normal execution, `run`, interactive `shell`, `psql`, and `logs --follow` SHALL remain raw-streaming and SHALL not emit document output or use a Rich live wrapper. Eligible spawning `run` and `shell` SHALL accept document-format options only together with `--dry-run`; those dry-run paths SHALL suppress native execution and emit one bounded plan document in Rich, JSON, or TOON. `psql --dry-run` SHALL remain an explicit plan-only exception that emits the shared sanitized native command plan without spawning; normal `psql` remains raw passthrough and SHALL continue to reject `--format` and `--json`.

The CLI SHALL define one CLI-only `OutputMode` with values `rich`, `json`, and `toon`. The mode and envelope types SHALL NOT become public SDK models or FastAPI response models. Each successful or failed bounded operation SHALL first build one JSON-safe CLI envelope v1 containing `schema_version`, `ok`, `command`, `context`, `provenance`, `dry_run`, and `warnings`; success SHALL contain equal `result` and `data`, while failure SHALL omit top-level `result` and `data` and SHALL contain stable `error.code` and sanitized `error.message`. `error` SHALL additionally contain an operation-specific JSON-safe `details` field when the capability contract requires structured diagnostics; failures without structured details SHALL omit it and retain their existing v1 shape.

JSON and TOON SHALL serialize that exact envelope without building format-specific result graphs. Decoding a TOON document with the selected strict decoder SHALL yield the same JSON value as decoding JSON output for the same operation. Machine modes SHALL emit exactly one UTF-8 document to stdout with no ANSI, prompt, status, progress, or external log text; diagnostics SHALL go to stderr. Renderer selection SHALL NOT change operation execution, exception mapping, or exit code. Native Click parse failures that occur before output-mode resolution SHALL retain Click's stderr usage output and exit code `2`.

For `remove`, `env remove`, `backup delete`, `db restore`, and `db drop`, JSON and TOON document modes SHALL never call `click.confirm`. Without the operation's required explicit confirmation, they SHALL NOT execute mutation and SHALL emit exactly one sanitized failure envelope with `error.code="confirmation_required"` and exit code `1`. `remove --purge` SHALL require the exact `--confirm-project` token and SHALL NOT accept `--yes` as a substitute. Other listed lifecycle commands retain their existing `--yes` contract. Dry-run SHALL never prompt and SHALL remain non-mutating.

Rich renderers SHALL remain adjacent to the concrete commands whose typed results they render. They SHALL use `Table`, `Status`, `Progress`, and `Live` only when appropriate to the operation and SHALL NOT introduce a generic renderer interface, registry, or DSL. `db stats` and `db bloat` SHALL render separate tables and indexes tables rather than one sparse combined table.

#### Scenario: JSON envelope

- **WHEN** `odcli env list --format json` executes
- **THEN** stdout contains exactly one versioned envelope and no progress or log text

#### Scenario: JSON alias preserves envelope v1

- **WHEN** `odcli env list --json` is invoked
- **THEN** Click exits `2` before the operation runs
- **AND** `odcli env list --format json` against the same frozen result emits envelope v1 with no ANSI or diagnostic text

#### Scenario: TOON is semantically equal to JSON

- **WHEN** a bounded command succeeds or fails once and its envelope is emitted as JSON and TOON
- **THEN** strict TOON decoding and JSON decoding produce equal Python builtins including `result`/`data`, context, provenance, warnings, and error fields

#### Scenario: Conflicting alias is usage error

- **WHEN** a caller supplies `--json --format toon`
- **THEN** Click exits `2`, does not execute the operation, and does not emit a partial machine document

#### Scenario: Machine diagnostics stay on stderr

- **WHEN** a bounded machine-mode operation reports a sanitized diagnostic in addition to its result
- **THEN** stdout contains one JSON or TOON envelope and the diagnostic is written only to stderr

#### Scenario: Structured failure details preserve the failure variant

- **WHEN** a bounded operation has structured diagnostics required by its capability contract
- **THEN** the machine document has `ok=false`, omits top-level `result` and `data`, retains `error.code` and sanitized `error.message`, and stores those diagnostics only in JSON-safe `error.details`
- **AND** a failure without such diagnostics omits `error.details`

#### Scenario: Machine mutation requires explicit confirmation

- **WHEN** `remove`, `env remove`, `backup delete`, `db restore`, or `db drop` runs in JSON/TOON mode without its required explicit confirmation and without `--dry-run`
- **THEN** no prompt or mutation occurs, stdout contains one `confirmation_required` failure envelope, and the command exits `1`

#### Scenario: Explicit machine mutation executes

- **WHEN** a supported mutating lifecycle leaf is invoked with its required explicit confirmation in JSON/TOON mode
- **THEN** the same operation runs once and its result is emitted under the normal renderer-independent exit mapping

#### Scenario: Machine remove requires exact project confirmation

- **WHEN** `odcli remove --purge --project-id ID --format json|toon` is invoked without the exact `--confirm-project` token
- **THEN** no prompt is rendered, removal is not called, stdout contains one failure envelope with `error.code="confirmation_required"`, and the command exits `1`

#### Scenario: Explicit machine project purge executes

- **WHEN** `odcli remove --purge --project-id ID --confirm-project TOKEN --format json|toon` receives the exact token
- **THEN** the same captured removal command runs once and its result is emitted as one document under the normal renderer-independent exit mapping

#### Scenario: Secrets redacted

- **WHEN** an error occurs during checkout or resource lifecycle execution
- **THEN** every machine or Rich error message redacts passwords, config bodies, sensitive environment values, and captured secrets before emission

#### Scenario: Diagnostic machine formats share one result graph

- **WHEN** one frozen diagnostic or resource result is projected as JSON and TOON
- **THEN** both decoded envelopes contain equal typed fields, numeric byte fields, warnings, and completeness state

#### Scenario: Native command dry-run supports every bounded format

- **WHEN** `odcli run --dry-run` or spawning `odcli shell --dry-run` is requested with `--format rich|json|toon`
- **THEN** output contains exactly one bounded plan with `dry_run=true` in the selected format
- **AND** `--json` is a Click usage error with exit code `2`
- **AND** no native child stream starts

#### Scenario: Normal native command stays raw

- **WHEN** `odcli run` or interactive `odcli shell` executes without `--dry-run`
- **THEN** its inherited stream is not wrapped in a bounded document or Rich live view

#### Scenario: Normal native command rejects machine options

- **WHEN** `odcli run` or spawning `odcli shell` is invoked with `--format` or `--json` but without `--dry-run`
- **THEN** Click exits `2` before invoking SDK code or starting a process

#### Scenario: Canonical bounded inventory remains single-source

- **WHEN** the stable machine-output characterization gate compares the documented normal-execution leaves
- **THEN** they equal test-owned `PUBLIC_LEAF_CASES`, including `remove` and every backup, database, resource, and `ps` leaf
- **AND** no second bounded-leaf table is introduced

#### Scenario: Mutating lifecycle dry-runs are canonical leaves

- **WHEN** the characterization gate exercises `remove --purge`, `backup delete`, `db restore`, or `db drop` with `--dry-run` in every shared format
- **THEN** each appears exactly once in test-owned `PUBLIC_LEAF_CASES` as `mutating-or-spawning` with required dry-run support
- **AND** no file, database, configuration, process, route, container, volume, or catalogue mutation occurs

#### Scenario: Database drop is a canonical bounded leaf

- **WHEN** the stable machine-output characterization gate exercises `db drop DATABASE --dry-run` in every shared format
- **THEN** `db drop` appears exactly once in test-owned `PUBLIC_LEAF_CASES` as `mutating-or-spawning` with required dry-run support
- **AND** no database, session, or catalogue mutation occurs

## ADDED Requirements

### Requirement: Root project removal CLI

Canonical `odcli remove` and its short alias `odcli rm` SHALL resolve to the same Click command object, callback and parameters. They SHALL require exactly one command-local `--project PATH` or `--project-id ID`, delegate to `OdooClient.projects.remove_command()`, and share the sole `PUBLIC_LEAF_CASES` row whose stable command identifier is `remove` and whose `sdk_primitive` is `projects.remove_command`. Both spellings SHALL accept `--purge`, `--confirm-project`, `--dry-run`, and `--format rich|json|toon`. They SHALL accept `--yes` for shared lifecycle compatibility, but `--yes` SHALL NOT satisfy purge confirmation. Help SHALL present one operation with the alternate spelling; completion, validation, prompt, plan, Rich/JSON/TOON output, stderr and exit behavior SHALL be identical. The CLI SHALL NOT enumerate, stop, unpublish, or delete resources itself, and SHALL NOT add a second callback, renderer, SDK method, inventory row, or machine command id for `rm`.

#### Scenario: Short remove alias preserves one command

- **WHEN** equivalent `odcli remove` and `odcli rm` invocations receive the same arguments and input, including help, completion, dry-run, refusal, forget, or purge paths
- **THEN** they resolve one callback and captured SDK command, produce semantically identical output, prompts, errors and exit codes, and machine envelopes keep `command="remove"`
- **AND** help exposes `rm` only as the alternate spelling of `remove` and `PUBLIC_LEAF_CASES` contains no `rm` row

#### Scenario: Selector exclusivity

- **WHEN** both selectors or neither selector is supplied
- **THEN** Click exits `2` before SDK resolution, prompt, or mutation

#### Scenario: Forget output

- **WHEN** non-purge removal succeeds in any bounded format
- **THEN** one envelope reports the forgotten state and retained resource summary from the typed SDK result

#### Scenario: Rich purge confirmation

- **WHEN** an interactive Rich purge omits `--confirm-project`
- **THEN** CLI displays the captured plan and exact token, reads one token, and passes it to the already captured SDK command without rebuilding the plan
