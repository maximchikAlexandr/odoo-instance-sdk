## ADDED Requirements

### Requirement: Public `OdooClient` structure

`OdooClient` SHALL expose two resources: `instance` (an `InstanceFactory`) and `backups` (a `BackupResource`). `OdooClient` SHALL be constructible from a single `OdooClientConfig` instance. Resources SHALL share the same config and process registry from the parent client.

#### Scenario: Construct client and access resources
- **WHEN** user constructs `OdooClient(config)` with a valid `OdooClientConfig`
- **THEN** `client.instance` is an `InstanceFactory` bound to `config`
- **AND** `client.backups` is a `BackupResource` bound to `config`

### Requirement: `OdooClientConfig` fields

`OdooClientConfig` (frozen dataclass) SHALL contain:
- `executable: str` — name or path of the Odoo executable
- `http_timeout_seconds: float = 30.0` — default HTTP request timeout in seconds
- `backups_directory: Path | None = None` — optional default backup directory

Base URL and master password MUST NOT be stored in `OdooClientConfig`. They belong to `InstanceConfig`.

#### Scenario: Construct config with required fields
- **WHEN** user constructs `OdooClientConfig(executable="odoo")`
- **THEN** `config.http_timeout_seconds == 30.0`
- **AND** `config.backups_directory is None`

#### Scenario: Construct config with all fields
- **WHEN** user constructs `OdooClientConfig` with all three fields set
- **THEN** all fields are accessible as attributes

### Requirement: `InstanceConfig` fields

`InstanceConfig` (frozen dataclass) SHALL contain:
- `base_url: str` — normalized base URL of the Odoo instance
- `master_password: str | None` — master database manager password (with `repr=False`)
- `configured_database_names: tuple[str, ...] = ()` — databases from config file

#### Scenario: Construct instance config
- **WHEN** user calls `client.instance(base_url="http://localhost:8069", master_password="secret")`
- **THEN** the returned `OdooInstance.config.base_url == "http://localhost:8069"`
- **AND** `OdooInstance.config.master_password == "secret"`

### Requirement: `master_password` never leaks through `repr`

`InstanceConfig.master_password` SHALL use `repr=False`. `InstanceConfig.__repr__` SHALL display `master_pwd=<redacted>` rather than the actual password value. The real value SHALL NOT appear in `repr`, exception messages, stdio, or logs produced by the SDK. `OdooClient.__repr__` SHALL NOT include the config object either directly or as a substring that exposes `master_password`.

#### Scenario: repr masks master password
- **WHEN** `repr(InstanceConfig(base_url="http://x", master_password="hunter2"))` is called
- **THEN** the returned string does not contain the substring `"hunter2"`
- **AND** the returned string contains the literal `master_pwd=<redacted>`

#### Scenario: client repr does not leak password
- **WHEN** `repr(client)` is called on a client built from a config with `master_password="hunter2"`
- **THEN** the returned string does not contain the substring `"hunter2"`