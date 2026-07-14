## ADDED Requirements

### Requirement: Public model-contracts are `msgspec.Struct`; internal containers may be `dataclass`

Every public model-contract (listed below) SHALL be a subclass of `msgspec.Struct`. Internal runtime containers of logic (clients, resources, registry records, exceptions) MAY use `dataclasses` to avoid hand-written `__init__` and improve readability. The SDK SHALL NOT use `typing.Any` in any public signature; opaque payloads SHALL use `bytes` or `str` with a typed shape.

Public model-contracts (must be `msgspec.Struct`):
- `StartConfig`, `CommandResult`, `OdooProcess`, `ProcessStatus`, `ReadinessResult`, `Backup`, `BackupEvent`, `BackupValidationResult`, `BackupDeletionResult`, `RestoreResult`, `DropResult`

Configuration objects `OdooClientConfig` and `InstanceConfig` SHALL be `@dataclass(frozen=True, slots=True, kw_only=True)`. Secret fields MUST use `repr=False`.

#### Scenario: Model instantiation is typed
- **WHEN** the public API is imported
- **THEN** `StartConfig`, `CommandResult`, `OdooProcess`, `ProcessStatus`, `ReadinessResult`, `Backup`, `RestoreResult`, `DropResult` are all `msgspec.Struct` subclasses
- **AND** `OdooClientConfig` and `InstanceConfig` are frozen dataclasses

### Requirement: Two-config design

`OdooClientConfig` SHALL contain only shared client parameters:
- `executable: str` — path or name of the Odoo executable
- `http_timeout_seconds: float = 30.0` — default HTTP timeout
- `backups_directory: Path | None = None` — optional default backup directory

Base URL and master password MUST NOT be stored in `OdooClientConfig`.

`InstanceConfig` SHALL contain:
- `base_url: str` — normalized base URL
- `master_password: str | None` (with `repr=False`)
- `configured_database_names: tuple[str, ...] = ()`

The `InstanceConfig.__repr__` SHALL display `master_pwd=<redacted>` rather than the actual password value. The `master_password` field SHALL use `repr=False`.

#### Scenario: One client and several instances
- **WHEN** one `OdooClient` creates two instances with different URLs
- **THEN** each `DatabaseResource` uses only its own instance configuration

### Requirement: Minimum model set is exposed

The package's public namespace (`odoo_instance_sdk`) SHALL expose at minimum:
- `OdooClient`, `OdooClientConfig`, `InstanceConfig`, `StartConfig`
- `CommandResult`, `OdooProcess`, `ProcessStatus`, `ReadinessResult`
- `Backup`, `RestoreResult`, `DropResult`

#### Scenario: Public re-exports
- **WHEN** user does `from odoo_instance_sdk import OdooClient, Backup`
- **THEN** both names are defined in the `odoo_instance_sdk` namespace

### Requirement: Typed exception hierarchy

The SDK SHALL expose typed exceptions, each a subclass of `OdooInstanceSdkError` (root type). At minimum:
- `OdooInstanceSdkError` (root)
- `ConfigError` — invalid client or start configuration
- `CommandTimeoutError` — `server.run()` timeout
- `ProcessNotFoundError` — unregistered or unknown process handle
- `ProcessExitedBeforeReady` — linked process died during `wait_ready()`
- `ReadinessTimeoutError` — `wait_ready()` timeout
- `NonLocalInstanceError` — local-only guard refused a remote `restore()`/`drop()`
- `DatabaseError` — Odoo database HTTP endpoint returned an error
- `BackupCatalogError`, `BackupNotFoundError`, `BackupNotAvailableError`, `BackupDownloadError`
- `DatabaseManagerUnavailableError`, `DatabaseAlreadyExistsError`, `RestoreFailedError`, `DropFailedError`
- `MasterPasswordRequiredError`, `InstanceConfigurationError`, `InvalidBaseUrlError`
- `BackupValidationUnavailableError`

`RemoteInstanceError` SHALL be renamed to `NonLocalInstanceError`. The name `RemoteInstanceError` MUST NOT exist in the codebase. Compatibility alias MUST NOT be added.

No exception message SHALL include `master_pwd`.

#### Scenario: Exception hierarchy root
- **WHEN** any typed SDK exception is raised
- **THEN** it is an instance of `OdooInstanceSdkError`

#### Scenario: Exception messages do not leak password
- **WHEN** `DatabaseError(message="...")` is constructed while processing a request that included `master_pwd`
- **THEN** the `message` does not contain the value of `master_pwd`