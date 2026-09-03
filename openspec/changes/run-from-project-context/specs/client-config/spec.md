## MODIFIED Requirements

### Requirement: Публичная структура клиента

Публичный API MUST иметь следующую структуру:

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
    ├── sync_python(selector, *, upgrade)
    ├── get(selector)
    ├── list(*, project, include_removed)
    └── remove(selector)
```

Each instance factory method MUST return a separate `OdooInstance`. `from_environment()` MUST bind recorded environment runtime state. `from_project()` MUST bind runtime state declared by an initialized `ProjectConfig` directly and MUST NOT create, select, or modify a `DevelopmentEnvironment` or environment catalogue record. `OdooInstance.databases` remains the sole public database-manager entry point for an instance, while server lifecycle and readiness methods remain directly available on `OdooInstance`.

`client.environments` MUST remain the environment provisioning lifecycle facade, and `client.backups` MUST remain the local downloaded-backup collection facade. Models MUST NOT perform hidden side effects. The private process registry MUST remain shared by instances created from either context.

#### Scenario: Instance from ready environment

- **WHEN** `client.instance.from_environment(env)` is called for a ready environment
- **THEN** it returns an instance with the recorded command prefix and working directory

#### Scenario: Instance from initialized project

- **WHEN** `client.instance.from_project(project)` is called with complete valid project runtime configuration
- **THEN** it returns an instance whose command prefix, working directory, Odoo config, URL, database binding, and defaults come from that project

#### Scenario: Project construction does not mutate environments

- **WHEN** an instance is constructed from a project
- **THEN** no environment catalogue record or lifecycle event is created or changed

#### Scenario: Invalid project runtime fails before execution

- **WHEN** required project runtime fields or referenced files are missing or invalid
- **THEN** construction fails with a sanitized configuration error before subprocess creation

#### Scenario: Три фасада

- **WHEN** `OdooClient` is constructed
- **THEN** `client.instance`, `client.backups`, and `client.environments` are available while `client.catalog` and `client.doctor` are absent

#### Scenario: Environments resource

- **WHEN** `client.environments.checkout(project, "feat/x")` is called
- **THEN** it returns a `DevelopmentEnvironment` while Git, uv, and locking remain internal

#### Scenario: from_environment на instance factory

- **WHEN** `client.instance.from_environment(env)` is called for a ready environment
- **THEN** it returns an `OdooInstance` with the recorded command prefix and default working directory

#### Scenario: Старый API отсутствует

- **WHEN** пользователь обращается к `client.database`, `client.server`, `client.catalog` или `client.doctor`
- **THEN** атрибут отсутствует
