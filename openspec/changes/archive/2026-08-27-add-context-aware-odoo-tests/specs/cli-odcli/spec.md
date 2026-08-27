## ADDED Requirements

### Requirement: Top-level Odoo test command and shared adapter

The Click command tree SHALL add this bounded structured command:

```text
odcli test [TARGET] [--tags TAGS] [--reload-tests] [--allow-empty]
           [--changed [--base REF] [--dry-run]] [--format rich|json|toon] [--json]
```

`odcli test` SHALL resolve the typed MYL-55 CLI project/environment context, delegate selection and execution to the `local-odoo-testing` capability, and render through the shared MYL-55 output adapter. `TARGET` SHALL be optional and singular. `--changed` with `TARGET`, `--base` without `--changed`, `--dry-run` without `--changed`, and a test-file target with `--tags` SHALL be Click usage errors with exit code `2` before selection or execution.

The command SHALL be added beside the existing command groups through the stable `odoo_instance_sdk.cli:cli` registration/composition entry point. The test adapter SHALL live in focused `commands/test.py`; reusable selection/preflight/execution helpers SHALL not import Click, Rich, or the CLI envelope.

#### Scenario: Root help exposes test command

- **WHEN** `odcli --help` and `odcli test --help` run
- **THEN** the root lists `test` and its help exposes target, native tags, changed/base/dry-run, reload, empty-result, and shared format options

#### Scenario: Invalid changed combination is a usage error

- **WHEN** `odcli test sale --changed` is invoked
- **THEN** Click exits `2` before environment selection, Git collection, preflight, or Odoo execution

### Requirement: Test output uses the shared CLI contract

Both `odcli test` and `odcli module test` SHALL be bounded structured leaves under the MYL-55 `OutputMode` and CLI envelope v1 contract. They SHALL accept command-local `--format rich|json|toon`, keep `--json` as the alias for `--format json`, reject conflicting format flags through the shared option resolver, and use the shared sanitized error/exit mapping. The command name in new-path envelopes SHALL be `test`; the compatibility path SHALL retain `module.test` while `result` and execution semantics remain equal for equivalent inputs.

Every success machine result SHALL contain resolved environment identity, selector kind/value and provenance, modules, and exit code. An executed result SHALL additionally contain effective native test tags, `reload_tests`, `allow_empty`, counts, and failure/zero-tests flags from `OdooTestResult`. A successful `--changed --dry-run` result SHALL instead contain `dry_run=true` plus complete base/Git provenance and SHALL omit `test_tags`, `reload_tests`, `allow_empty`, counts, and failure/zero-tests flags. A successful changed selection with no addons SHALL contain `reason="no_addon_changes"`, complete base/Git provenance, empty modules, and `exit_code=0`, and SHALL omit those same execution-only fields; if it is also a dry-run it MAY additionally contain `dry_run=true`. Neither non-executed state SHALL construct or imply an `OdooTestResult`, fabricate zero counts/false flags, or emit execution progress. JSON and strict-decoded TOON SHALL be semantically equal in all three states. Raw sanitized Odoo diagnostics SHALL be written only to stderr; machine stdout SHALL contain exactly one document without ANSI, prompts, progress, or embedded raw logs.

Rich output SHALL show the same resolved environment, selection/modules, and exit status. It SHALL show final counts only for executed results; `--changed --dry-run` and `no_addon_changes` MAY use a Rich table but SHALL not display fabricated counts or execution progress.

#### Scenario: JSON and TOON test parity

- **WHEN** equivalent frozen test results are emitted with `--format json` and `--format toon`
- **THEN** decoded envelope-v1 values are equal, stdout contains one document, diagnostics are only on stderr, and both commands return the typed result's exit code

#### Scenario: Changed no-op is a successful document

- **WHEN** `odcli test --changed --format json` finds only docs/non-addon paths
- **THEN** stdout contains one success envelope with `reason="no_addon_changes"`, selected modules is empty, and no Odoo diagnostics/process are produced

#### Scenario: Dry-run machine shape has no execution fields

- **WHEN** `odcli test --changed --dry-run --format json` safely selects one or more addons
- **THEN** stdout contains selection/base provenance, modules, `dry_run=true`, and `exit_code=0`, omits tags/options/counts/failure/zero flags, and produces no execution progress or Odoo diagnostics

### Requirement: `module test` is a compatibility alias

`odcli module test MODULE...` SHALL remain available with its existing plural positional module form and existing `--test-tags`, `--reload-tests`, `--allow-empty`, `--json`, and MYL-55 `--format` options. It SHALL validate each module through the same eligible-addon boundary, build the same `OdooTestSpec`, use the same installed-state preflight and single runner, and return the same `OdooTestResult` as `odcli test MODULE --tags ...` for an equivalent one-module request.

The alias SHALL continue to require at least one module and `--test-tags`. It SHALL not accept cwd/file inference, `--changed`, `--base`, or `--dry-run`, and SHALL not retain a second `run_module_tests` behavior branch after migration.

#### Scenario: Legacy command delegates to the same path

- **WHEN** `odcli module test sale --test-tags /sale --reload-tests` and `odcli test sale --tags /sale --reload-tests` run against the same frozen environment/runner
- **THEN** they perform the same selection validation, preflight, and one runner call and produce equivalent typed results and exit codes

#### Scenario: Legacy plural modules remain supported

- **WHEN** `odcli module test sale stock --test-tags standard` is invoked
- **THEN** both exact eligible addons are sorted/deduplicated into one `OdooTestSpec` and one runner call

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
odcli env checkout BRANCH [OPTIONS]
odcli env sync [ENVIRONMENT] [OPTIONS]
odcli env list [OPTIONS]
odcli env remove [ENVIRONMENT] [OPTIONS]
odcli run
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
```

#### Scenario: Help shows full command surface

- **WHEN** `odcli --help` runs
- **THEN** shows init, env, run, logs, shell, doctor, monitor, eval, exec, test, module, translations, deps, vscode

### Requirement: Environment resolution for instance commands

Для instance commands (`run`, `logs`, `shell`, `eval`, `exec`, `test`, `module`, `translations`, `deps verify`):

1. Explicit root `--env SELECTOR` — UUID либо однозначное имя; option допустим только для instance commands.
2. Exact registered worktree containing current directory.
3. Иначе — ошибка: либо `cd` в worktree, либо `--env`, со списком candidates если их несколько.

Никогда не выбирать единственный `ready` молча и никогда не выбирать по recency. Test target/cwd/addon resolution begins only after this environment is resolved and SHALL NOT select a different environment.

#### Scenario: Explicit --env

- **WHEN** `odcli --env <uuid> test sale` runs
- **THEN** environment is resolved from the explicit selector before addon selection

#### Scenario: Ambiguous name

- **WHEN** `odcli --env "feat" test sale` matches 2 environments
- **THEN** error with candidate list and no addon/Git/preflight/Odoo work

### Requirement: Automation commands

`eval`, `exec`, `test`, `module`, `translations export`, `deps verify` и `vscode generate` MUST входить в CLI. Новых public resources MUST NOT добавляться. RPC fallback MUST NOT использоваться. `eval`/`exec`/`test`/`module`/`translations` MUST использовать `run_shell_script()` или существующий exclusive variant того же Odoo shell primitive.

#### Scenario: Help lists automation commands

- **WHEN** `odcli --help` runs
- **THEN** shows `eval`, `exec`, `test`, `module`, `translations`, `deps`, `vscode`

### Requirement: `module` commands

```bash
odcli module list --state installed
odcli module update comerta_base --dry-run
odcli module update comerta_base --yes
odcli module test comerta_base --test-tags /comerta_base --reload-tests
```

- `list [MODULE...]` MUST читать `ir.module.module`.
- `update` MUST требовать `--yes`; внутри shell `button_immediate_upgrade()`.
- `test` MUST remain a backward-compatible alias to the top-level local Odoo test operation, call Odoo 19 `odoo.tests.shell.run_tests(...)` through the same single runner path, enforce workers=0 and the free bound HTTP port, and exit non-zero for failed/error tests and zero tests unless `--allow-empty`.
- install/uninstall MUST NOT добавляться. Public `ModuleResource` or `TestResource` MUST NOT существовать.

#### Scenario: Module list

- **WHEN** `odcli module list --state installed` executes
- **THEN** installed modules are listed via local Odoo shell

#### Scenario: Module test compatibility

- **WHEN** an existing caller invokes `odcli module test comerta_base --test-tags /comerta_base`
- **THEN** the request reaches the same preflight, `OdooTestSpec`, runner, and result path as the top-level command without a duplicate implementation
