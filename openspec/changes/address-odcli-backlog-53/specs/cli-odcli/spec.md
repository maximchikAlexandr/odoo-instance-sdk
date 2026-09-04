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
