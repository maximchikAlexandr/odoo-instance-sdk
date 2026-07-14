## ADDED Requirements

### Requirement: Public model-contracts are `msgspec.Struct`; internal containers may be `dataclass`

Every public model-contract (listed below) SHALL be a subclass of `msgspec.Struct`. The SDK SHALL NOT use `dataclasses`, `pydantic`, or `TypedDict` for public model-contracts. Internal runtime containers of logic (clients, resources, registry records, exceptions) MAY use `dataclasses` to avoid hand-written `__init__` and improve readability. The SDK SHALL NOT use `typing.Any` in any public signature; opaque payloads SHALL use `bytes` or `str` with a typed shape.

Public model-contracts (must be `msgspec.Struct`):
- `OdooClientConfig`, `StartConfig`, `CommandResult`, `OdooProcess`, `ProcessStatus`, `ReadinessResult`, `BackupArtifact`, `RestoreResult`, `DropResult`

#### Scenario: Model instantiation is typed
- **WHEN** the public API is imported
- **THEN** `OdooClientConfig`, `StartConfig`, `CommandResult`, `OdooProcess`, `ProcessStatus`, `ReadinessResult`, `BackupArtifact`, `RestoreResult`, `DropResult` are all `msgspec.Struct` subclasses

### Requirement: Minimum model set is exposed

The package's public namespace (`odoo_instance_sdk`) SHALL expose at minimum:
- `OdooClient`, `OdooClientConfig`, `StartConfig`
- `CommandResult`, `OdooProcess`, `ProcessStatus`, `ReadinessResult`
- `BackupArtifact`, `RestoreResult`, `DropResult`

#### Scenario: Public re-exports
- **WHEN** user does `from odoo_instance_sdk import OdooClient, BackupArtifact`
- **THEN** both names are defined in the `odoo_instance_sdk` namespace

### Requirement: Typed exception hierarchy

The SDK SHALL expose typed exceptions, each a subclass of `OdooInstanceSdkError` (root type). At minimum:
- `OdooInstanceSdkError` (root)
- `ConfigError` — invalid client or start configuration
- `CommandTimeoutError` — `server.run()` timeout
- `ProcessNotFoundError` — unregistered or unknown process handle
- `ProcessExitedBeforeReady` — linked process died during `wait_ready()`
- `ReadinessTimeoutError` — `wait_ready()` timeout
- `RemoteInstanceError` — local-only guard refused a remote `restore()`/`drop()`
- `DatabaseError` — Odoo database HTTP endpoint returned an error

No exception message SHALL include `master_pwd`.

#### Scenario: Exception hierarchy root
- **WHEN** any typed SDK exception is raised
- **THEN** it is an instance of `OdooInstanceSdkError`

#### Scenario: Exception messages do not leak password
- **WHEN** `DatabaseError(message="...")` is constructed while processing a request that included `master_pwd`
- **THEN** the `message` does not contain the value of `master_pwd`