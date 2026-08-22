## ADDED Requirements

### Requirement: One catalog owner with cohesive internal persistence

SDK MUST сохранить ровно один SQLite file, один connection/schema owner и одну `PRAGMA user_version` migration chain.

Catalog owner MUST открывать connection и применять существующую migration chain. Второй SQLite file, второй migration owner, public repository interface, factory и persistence framework MUST NOT появляться.

Environment persistence operations (environments, environment_events, copy journal) MUST жить в cohesive internal component, отдельном от backup/restore history (backups, backup_events, restores, database_events). Оба компонента MUST использовать тот же connection и тот же schema owner.

Pass-through wrappers, которые только переименовывают существующие catalog methods без собственной логики, MUST NOT добавляться.

Internal catalog type MAY быть переименован. Public API остаётся `client.backups` и `client.environments`. Catalog MUST NOT экспортироваться как `client.catalog`.

Schema version, tables и row contracts MUST NOT меняться этим change.

#### Scenario: Single SQLite after the split

- **WHEN** `OdooClient` opens persistence
- **THEN** exactly one durable `catalog.sqlite3` is used for backups, restores, environments and events

#### Scenario: Environment write uses the same owner as backup write

- **WHEN** checkout records an environment row and backup() records a backup row
- **THEN** both writes go through the same connection/schema owner and the same migration chain

#### Scenario: No public repository surface

- **WHEN** a caller imports `odoo_instance_sdk`
- **THEN** no public repository, unit-of-work or catalog factory type is available; `client.catalog` remains absent

### Requirement: Catalog callers stay behind existing facades

Public SDK callers MUST продолжать ходить в persistence только через `client.backups` и `client.environments`. Internal resources MAY use the catalog owner.

CLI output/rendering MUST NOT получать catalog owner и MUST NOT вызывать mutating catalog methods. Read-only `doctor` MAY читать catalog events через существующий internal coordinator; это не rendering и не mutation.

#### Scenario: CLI list does not open catalog writes

- **WHEN** `odcli env list` renders the table
- **THEN** it does not call catalog mutating APIs; reads go through `EnvironmentResource.list()` / application context

#### Scenario: Doctor may read catalog events

- **WHEN** `odcli doctor` runs
- **THEN** the read-only coordinator MAY read catalog events; output rendering still MUST NOT write `last_used_at` or environment events