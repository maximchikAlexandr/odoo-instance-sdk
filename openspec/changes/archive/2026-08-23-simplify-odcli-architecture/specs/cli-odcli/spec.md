## ADDED Requirements

### Requirement: Instance commands share one ready path

`run`, `shell`, `eval`, `exec`, `module`, `translations`, `deps verify` и `vscode generate` MUST получать `OdooClient`, ready environment и `OdooInstance` через один internal `ready_instance` path. Command bodies MUST NOT копировать resolve/verify/`from_environment()` и MUST NOT конструировать `OdooClient` сами.

`ready_instance` MUST использовать already-specified two-rule context. Port preflight остаётся только у `run`.

#### Scenario: Eval and run share resolve

- **WHEN** `odcli eval 1` and `odcli run` execute inside a registered worktree
- **THEN** both resolve the same environment through `ready_instance`, not through per-command helpers

### Requirement: CLI does not open the catalog

CLI command bodies, printers и env-list rendering MUST NOT вызывать `get_catalog()` и MUST NOT писать `last_used_at` или environment events напрямую.

`odcli run` MUST вызвать `EnvironmentResource.record_use()` после free-port preflight и MUST NOT вызывать его при `port-conflict`. Other instance commands MUST NOT record `use`.

JSON envelope v1 MUST остаться: `schema_version`, `ok`, `command`, `context`, `provenance`, `dry_run`, `warnings`; success — одинаковые `result` и `data`; error — `error.code` + sanitized `error.message`. Один shared emit path.

Entry point MUST остаться `odoo_instance_sdk.cli:cli`. Имена команд и `from odoo_instance_sdk.cli import cli` MUST сохраниться.

#### Scenario: List JSON does not open catalog

- **WHEN** `odcli env list --json` prints the envelope
- **THEN** the command does not call `get_catalog()` and does not write environment events

#### Scenario: Port conflict skips use

- **WHEN** `odcli run` hits an occupied port
- **THEN** output is `port-conflict` / ownership-unknown and `record_use` is not called

#### Scenario: Successful run records use on the environment resource

- **WHEN** `odcli run` finds a free port
- **THEN** `EnvironmentResource.record_use()` writes `last_used_at` and `use/succeeded` before `run_foreground()`

#### Scenario: Help still lists full command surface

- **WHEN** `odcli --help` runs
- **THEN** shows init, env, run, shell, doctor, eval, exec, module, translations, deps, vscode
