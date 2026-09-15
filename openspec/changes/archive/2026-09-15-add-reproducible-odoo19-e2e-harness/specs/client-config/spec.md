## MODIFIED Requirements

### Requirement: Публичная структура клиента

The public API MUST have this structure:

```text
OdooClient
├── instance
│   ├── __call__(base_url, master_password=None)
│   ├── from_config(path, base_url=None, master_password=None)
│   ├── from_environment(environment)
│   └── from_project(project)
├── backups
│   ├── list()
│   ├── latest()
│   ├── history()
│   ├── validate()
│   └── delete()
└── environments
    ├── checkout(project, branch, *, options)
    ├── sync_python(selector, *, upgrade=False, hash_lock=None, hash_lock_sha256=None)
    ├── sync_python_command(selector, *, upgrade=False, hash_lock=None, hash_lock_sha256=None)
    ├── get(selector)
    ├── list(*, project, include_removed)
    └── remove(selector)
```

Each instance factory method MUST return a separate `OdooInstance`. `from_environment()` MUST bind recorded environment runtime state. `from_project()` MUST bind runtime state declared by an initialized `ProjectConfig` directly and MUST NOT create, select, or modify a `DevelopmentEnvironment` or environment catalogue record. `OdooInstance.databases` SHALL remain the sole public database-manager entry point for an instance, while server lifecycle and readiness methods SHALL remain directly available on `OdooInstance`.

`client.environments` MUST remain the environment provisioning lifecycle facade, and `client.backups` MUST remain the local downloaded-backup collection facade. Models MUST NOT perform hidden side effects. The private process registry MUST remain shared by instances created from either context. The additive hash-lock keywords SHALL retain `None` defaults; `sync_python()` SHALL delegate to the sibling immutable command and neither the facade nor a model SHALL implement lock parsing or process execution independently.

#### Scenario: Instance from ready environment

- **WHEN** `client.instance.from_environment(env)` is called for a ready environment
- **THEN** it SHALL return an instance with the recorded command prefix and working directory

#### Scenario: Instance from initialized project

- **WHEN** `client.instance.from_project(project)` is called with complete valid project runtime configuration
- **THEN** it SHALL return an instance whose command prefix, working directory, Odoo config, URL, database binding, and defaults come from that project

#### Scenario: Project construction does not mutate environments

- **WHEN** an instance is constructed from a project
- **THEN** no environment catalogue record or lifecycle event SHALL be created or changed

#### Scenario: Invalid project runtime fails before execution

- **WHEN** required project runtime fields or referenced files are missing or invalid
- **THEN** construction SHALL fail with a sanitized configuration error before subprocess creation

#### Scenario: Три фасада

- **WHEN** `OdooClient` is constructed
- **THEN** `client.instance`, `client.backups`, and `client.environments` SHALL be available while `client.catalog` and `client.doctor` SHALL be absent

#### Scenario: Environments resource

- **WHEN** `client.environments.checkout(project, "feat/x")` is called
- **THEN** it SHALL return a `DevelopmentEnvironment` while Git, uv, hash-lock validation, and locking remain internal

#### Scenario: from_environment на instance factory

- **WHEN** `client.instance.from_environment(env)` is called for a ready environment
- **THEN** it SHALL return an `OdooInstance` with the recorded command prefix and default working directory

#### Scenario: Старый API отсутствует

- **WHEN** a caller accesses `client.database`, `client.server`, `client.catalog`, or `client.doctor`
- **THEN** the attribute SHALL be absent

#### Scenario: Environment facade exposes immutable audited sync

- **WHEN** a caller supplies a valid hash-lock pair through `client.environments.sync_python_command()` or `sync_python()`
- **THEN** both entry points SHALL expose the same owned-environment operation and the convenience method SHALL execute the captured command without rebuilding it
