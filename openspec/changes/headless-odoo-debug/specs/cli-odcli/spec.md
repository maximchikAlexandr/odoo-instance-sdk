# Spec Delta

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
odcli debug start [OPTIONS]
odcli debug continue [OPTIONS] [-- COMMAND...]
odcli debug context [OPTIONS]
odcli debug eval EXPR [OPTIONS]
odcli debug step [over|in|out]
odcli debug pause
odcli debug restart
odcli debug stop
odcli ps [OPTIONS]
```

#### Scenario: Help shows full command surface

- **WHEN** `odcli --help` runs
- **THEN** shows init, canonical remove with alternate spelling rm, env, run, logs, shell, doctor, monitor, eval, exec, test, module, translations, deps, vscode, debug, ps

### Requirement: Environment resolution for instance commands

Instance commands (`run`, `logs`, `shell`, `eval`, `exec`, `test`, `module`, `translations`, `deps verify`, `vscode generate`, and `debug`) MUST consume the shared `environment | project` resolver. Commands whose required state is available from either context MUST operate on both. A command that requires environment-owned state or lifecycle metadata MUST reject project context with an actionable error and MUST NOT fabricate an environment. `debug` requires a ready environment UUID and MUST reject project-only context before creating a session.

Test target, working-directory, and addon resolution MUST begin only after runtime context is resolved and MUST NOT select a different environment or project.

#### Scenario: Explicit environment precedes addon selection

- **WHEN** `odcli --env <uuid> test sale` runs
- **THEN** environment resolution completes before addon selection

#### Scenario: Project-capable command accepts main checkout

- **WHEN** a project-capable instance command runs under an initialized main checkout without `--env`
- **THEN** it uses the project runtime configuration

#### Scenario: Environment-only command rejects project context

- **WHEN** a command requiring environment-owned artifacts resolves only a project context
- **THEN** it returns an actionable error without catalog mutation or subprocess launch

#### Scenario: Explicit --env

- **WHEN** `odcli --env <uuid> test sale` runs
- **THEN** the environment is resolved from the explicit selector before addon selection

#### Scenario: Ambiguous name

- **WHEN** `odcli --env "feat" test sale` matches two environments
- **THEN** it fails with the candidate list and performs no addon, Git, preflight, project fallback, or Odoo work

#### Scenario: Debug rejects a project-only checkout

- **WHEN** `odcli debug start` resolves only an initialized project
- **THEN** it fails before daemon or Odoo spawn and does not invent an environment id

### Requirement: Stable machine output

The exact bounded structured leaf inventory is: `init`, `remove`, `doctor`, `env checkout`, `env list`, `env remove`, `env sync`, `backup list`, `backup show`, `backup validate`, `backup delete`, `db refresh`, `db reset-admin-password`, `db list`, `db restore`, `db drop`, `resource list`, `resource doctor`, `eval`, `exec`, `test`, `module list`, `module update`, `module test`, `translations export`, `deps verify`, `vscode generate`, `debug start`, `debug continue`, `debug context`, `debug eval`, `debug step`, `debug pause`, `debug restart`, `debug stop`, `ps`, `db locks`, `db stats`, `db bloat`, `db init-monitoring`, `postgres approve-image`, `postgres status`, `postgres up`, and `postgres stop`. Each SHALL accept command-local `--format rich|json|toon`; `rich` SHALL be the default. Removed `--json` SHALL be an ordinary Click usage error with exit code `2`. `--format json` SHALL be the only JSON selector. Supplying `--json` with `--format` SHALL also be a Click usage error with exit code `2`. During normal execution, `run`, interactive `shell`, `psql`, and `logs --follow` SHALL remain raw-streaming and SHALL not emit document output or use a Rich live wrapper. Eligible spawning `run` and `shell` SHALL accept document-format options only together with `--dry-run`; those dry-run paths SHALL suppress native execution and emit one bounded plan document in Rich, JSON, or TOON. `psql --dry-run` SHALL remain an explicit plan-only exception that emits the shared sanitized native command plan without spawning; normal `psql` remains raw passthrough and SHALL continue to reject `--format` and `--json`.

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

#### Scenario: Machine remove requires explicit confirmation

- **WHEN** `odcli env remove ENV --format json` or `--format toon` is invoked without `--yes`
- **THEN** no prompt is rendered, removal is not called, stdout contains one failure envelope with `error.code="confirmation_required"`, and the command exits `1`

#### Scenario: Explicit machine remove executes

- **WHEN** `odcli env remove ENV --yes --format json` or `--format toon` is invoked
- **THEN** the same removal operation runs once and its result is emitted as one document under the normal renderer-independent exit mapping

#### Scenario: Debug leaves are bounded

- **WHEN** the characterization gate exercises every `debug` verb with `--dry-run` in Rich, JSON, and TOON
- **THEN** each verb appears once in `PUBLIC_LEAF_CASES` with a public debug SDK primitive and required dry-run support
- **AND** `--json` on a debug verb exits `2` before any session work

### Requirement: Automation commands

`eval`, `exec`, `test`, `module`, `translations export`, `deps verify` и `vscode generate` MUST входить в CLI. Those shell-automation commands MUST NOT add a new public resource. The headless debug capability MAY add one public debug resource, and that resource MUST NOT replace or wrap `eval` or `exec`. RPC fallback MUST NOT использоваться. `eval`/`exec`/`test`/`module`/`translations` MUST использовать `run_shell_script()` или существующий exclusive variant того же Odoo shell primitive.

#### Scenario: Help lists automation commands

- **WHEN** `odcli --help` runs
- **THEN** shows `eval`, `exec`, `test`, `module`, `translations`, `deps`, `vscode`, `debug`

### Requirement: `vscode generate`

```bash
odcli vscode generate
odcli vscode generate --write
```

The command SHALL accept the shared `environment | project` context. In project context it SHALL derive Python, Odoo entry point, source config, repository root, and configured database from the initialized project; in environment context it SHALL preserve existing generated-config and recorded-runtime behavior. The generated profile SHALL reuse the same resolved runtime/argv source as `odcli run`, including safe `default_run_args` from the resolved project manifest, which SHALL appear in `args` exactly once. An empty `default_run_args` list SHALL add no arguments. Validation of disallowed managed-override families SHALL be preserved. The profile SHALL NOT include automatic module update/install arguments or secrets. Default SHALL print one debugpy profile. Rich, JSON, and TOON SHALL use the shared output contract.

`--write` SHALL create a missing `.vscode/launch.json`. When the file exists and has no SDK-owned Odoo profile, `--write` SHALL insert one while preserving unrelated configurations, compounds, JSONC comments, and other settings. Ownership SHALL be a stable SDK identity bound to the project or environment. A display name SHALL NOT prove ownership, and an unrelated profile with the same name SHALL remain untouched.

The file SHALL retain enough information about the last successfully generated profile to distinguish a resolved-runtime change from a manual edit. An unchanged managed profile SHALL be updated. A profile that already matches the resolved runtime SHALL produce no write. A manual edit SHALL be reported as a field-level conflict and SHALL NOT be overwritten. Invalid or ambiguous JSONC SHALL fail without modification. The write SHALL be atomic and SHALL recheck that the source bytes have not changed between planning and replacement.

`--dry-run` SHALL report create, update, no-op, or conflict, including the exact changed fields, and SHALL use the same captured change and resolved runtime as apply. The implementation SHALL edit only the SDK-owned profile and SHALL NOT serialize the whole document as plain JSON.

#### Scenario: Print profile

- **WHEN** `odcli vscode generate` runs from a complete initialized project with no exact environment
- **THEN** one profile derived from project values is printed and no file is written

#### Scenario: Existing JSONC is preserved

- **WHEN** `--write` targets an existing `.vscode/launch.json`
- **THEN** comments, compounds, unrelated profiles, and a same-name profile without the SDK identity stay byte-stable except for the inserted or updated SDK-owned profile

#### Scenario: Missing file is created

- **WHEN** `--write` targets a project whose `.vscode/launch.json` is absent
- **THEN** the file is created atomically with one SDK-owned profile

#### Scenario: User profiles stay beside the managed profile

- **WHEN** `--write` finds an existing JSONC file with comments, compounds, and profiles that lack the SDK identity
- **THEN** those parts are unchanged and one owned profile is inserted

#### Scenario: Manual edit conflicts

- **WHEN** the SDK-owned profile differs from the last generated profile by a manual field edit
- **THEN** the command reports the conflicting fields and does not modify the file

#### Scenario: Current profile is a no-op

- **WHEN** the SDK-owned profile already matches the resolved runtime
- **THEN** `--write` reports a no-op and does not change the file bytes

#### Scenario: Project default_run_args appear in profile

- **WHEN** `odcli vscode generate` runs from a project with `default_run_args = ["--dev=qweb,xml"]`
- **THEN** the generated profile `args` contain `--dev=qweb,xml` exactly once

#### Scenario: Empty default_run_args add nothing

- **WHEN** `odcli vscode generate` runs from a project with an empty `default_run_args` list
- **THEN** the generated profile `args` contain no extra arguments beyond the required runtime arguments

#### Scenario: Disallowed overrides are rejected

- **WHEN** `default_run_args` contains a managed-override family such as `--config`
- **THEN** the command fails before profile generation with a sanitized actionable error

#### Scenario: Dry-run and apply share one change

- **WHEN** `odcli vscode generate --write --dry-run` reports an update
- **THEN** no file is written, and a later apply of that same captured change writes only the reported fields

### Requirement: Instance commands share one ready path

Project-capable instance commands MUST obtain the client, resolved `environment | project` context, and `OdooInstance` through one shared internal path. Command bodies MUST NOT duplicate context precedence, runtime verification, instance construction, or client construction. Environment-only commands MUST narrow the shared result explicitly and reject project context.

HTTP port preflight applies to `run` and to `debug start`. Both MUST use the existing free-port check before spawn.

#### Scenario: Eval and run share resolve

- **WHEN** `odcli eval 1` and `odcli run` execute under the same supported context
- **THEN** both resolve that context through the shared path rather than command-specific helpers

#### Scenario: Lifecycle remains environment-only

- **WHEN** an environment lifecycle operation is invoked from only the main project checkout without an environment selector
- **THEN** it does not treat the project as a development environment

#### Scenario: Debug start checks the HTTP port

- **WHEN** `odcli debug start` finds the resolved HTTP port occupied
- **THEN** it fails with the existing port conflict before Odoo spawn

### Requirement: CLI does not open the catalog

CLI command bodies, printers и env-list rendering MUST NOT вызывать `get_catalog()` и MUST NOT писать `last_used_at` или environment events напрямую.

`odcli run` MUST вызвать `EnvironmentResource.record_use()` после free-port preflight и MUST NOT вызывать его при `port-conflict`. `odcli debug start` MUST call the same `EnvironmentResource.record_use()` after its free-port preflight succeeds and MUST NOT call it when preflight fails. Other instance commands MUST NOT record `use`.

JSON envelope v1 MUST остаться: `schema_version`, `ok`, `command`, `context`, `provenance`, `dry_run`, `warnings`; success — одинаковые `result` и `data`; error — `error.code` + sanitized `error.message`. Один shared emit path.

Entry point MUST остаться `odoo_instance_sdk.cli:cli`. Имена команд и `from odoo_instance_sdk.cli import cli` MUST сохраниться.

#### Scenario: List JSON does not open catalog

- **WHEN** `odcli env list --format json` prints the envelope
- **THEN** the command does not call `get_catalog()` and does not write environment events

#### Scenario: Port conflict skips use

- **WHEN** `odcli run` hits an occupied port
- **THEN** output is `port-conflict` / ownership-unknown and `record_use` is not called

#### Scenario: Successful run records use on the environment resource

- **WHEN** `odcli run` finds a free port
- **THEN** `EnvironmentResource.record_use()` writes `last_used_at` and `use/succeeded` before `run_foreground()`

#### Scenario: Successful debug start records use

- **WHEN** `odcli debug start` passes the free-port preflight
- **THEN** `EnvironmentResource.record_use()` writes `last_used_at` and `use/succeeded` before the daemon spawn

#### Scenario: Help still lists full command surface

- **WHEN** `odcli --help` runs
- **THEN** shows init, env, run, logs, shell, doctor, monitor, eval, exec, test, module, translations, deps, vscode, debug, ps
