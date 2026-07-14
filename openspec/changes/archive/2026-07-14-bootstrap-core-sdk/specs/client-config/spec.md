## ADDED Requirements

### Requirement: Public `OdooClient` structure

`OdooClient` SHALL expose two resources: `server` and `database`. `OdooClient` SHALL be constructible from a single `OdooClientConfig` instance. Resources SHALL share the same config and process registry from the parent client.

#### Scenario: Construct client and access resources
- **WHEN** user constructs `OdooClient(config)` with a valid `OdooClientConfig`
- **THEN** `client.server` is a `ServerResource` instance bound to `config`
- **AND** `client.database` is a `DatabaseResource` instance bound to `config`

### Requirement: `OdooClientConfig` fields

`OdooClientConfig` (msgspec.Struct) SHALL contain:
- `executable: str | Path` — name or path of the Odoo executable
- `base_url: str` — base URL of the Odoo instance
- `master_pwd: str` — master database manager password
- `backup_dir: str | Path | None = None` — optional backup directory
- `http_timeout: float = 30.0` — default HTTP request timeout in seconds

All fields SHALL be required except `backup_dir` and `http_timeout`, which SHALL have defaults listed above.

#### Scenario: Construct config with required fields
- **WHEN** user constructs `OdooClientConfig(executable="odoo", base_url="http://localhost:8069", master_pwd="secret")`
- **THEN** `config.backup_dir is None`
- **AND** `config.http_timeout == 30.0`

#### Scenario: Construct config with all fields
- **WHEN** user constructs `OdooClientConfig` with all five fields set
- **THEN** all fields are accessible as attributes
- **AND** `backup_dir` is stored as given (no eager resolution)

### Requirement: `master_pwd` never leaks through `repr`

`OdooClientConfig.__repr__` SHALL render `master_pwd` as the literal string `<redacted>`. The real value SHALL NOT appear in `repr`, exception messages, stdio, or logs produced by the SDK. `OdooClient.__repr__` SHALL NOT include the config object either directly or as a substring that exposes `master_pwd`.

#### Scenario: repr masks master password
- **WHEN** `repr(OdooClientConfig(executable="odoo", base_url="http://x", master_pwd="hunter2"))` is called
- **THEN** the returned string does not contain the substring `"hunter2"`
- **AND** the returned string contains the literal `master_pwd=<redacted>`

#### Scenario: client repr does not leak password
- **WHEN** `repr(client)` is called on a client built from a config with `master_pwd="hunter2"`
- **THEN** the returned string does not contain the substring `"hunter2"`