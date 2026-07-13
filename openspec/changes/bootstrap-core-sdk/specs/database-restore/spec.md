## ADDED Requirements

### Requirement: `database.restore()` uses standard Odoo 19.0 HTTP method

`database.restore(artifact: BackupArtifact, new_db: str, *, timeout: float | None = None)` SHALL upload the backup to the standard Odoo 19.0 restore endpoint at `<base_url>/web/database/restore` via multipart POST with HTTP basic auth (`admin`, `master_pwd`). The exact multipart shape SHALL be verified against `odoo/service/db.py` 19.0 during implementation.

#### Scenario: Restore call shape
- **WHEN** `database.restore(artifact, "newdb")` is called
- **THEN** a multipart POST is sent to `<base_url>/web/database/restore`
- **AND** the request includes basic auth with `master_pwd`
- **AND** the request includes the backup file contents and the new database name

### Requirement: Returns typed `RestoreResult`

A successful restore SHALL return a `RestoreResult` (msgspec.Struct) containing at minimum:
- `new_db: str` — the name of the restored database
- `source: BackupArtifact` — the artifact that was restored

#### Scenario: Successful restore
- **WHEN** the restore HTTP request succeeds
- **THEN** `result.new_db == "newdb"`
- **AND** `result.source is artifact`

### Requirement: Local-only guard applies before HTTP

`database.restore()` SHALL refuse to issue any HTTP request when `client.config.base_url` does not resolve to a local instance. The check SHALL consider the following as local:
- hostname `localhost` (case-insensitive)
- IPv4 address in `127.0.0.0/8`
- IPv6 address `::1`

The check SHALL be performed by parsing the `base_url` hostname via `urllib.parse.urlsplit` and inspecting via `ipaddress` stdlib. The check SHALL NOT be disable by any parameter.

#### Scenario: Localhost allowed
- **WHEN** `base_url == "http://localhost:8069"` and `restore(artifact, "newdb")` is called
- **THEN** the HTTP request is issued (no guard error)

#### Scenario: Remote refused
- **WHEN** `base_url == "http://odoo.example.com:8069"` and `restore(artifact, "newdb")` is called
- **THEN** `RemoteInstanceError` is raised
- **AND** no HTTP request is sent

#### Scenario: IPv4 in 127.0.0.0/8 allowed
- **WHEN** `base_url == "http://127.0.0.1:8069"` and `restore(artifact, "newdb")` is called
- **THEN** the HTTP request is issued (no guard error)

#### Scenario: IPv6 loopback allowed
- **WHEN** `base_url == "http://[::1]:8069"` and `restore(artifact, "newdb")` is called
- **THEN** the HTTP request is issued (no guard error)

### Requirement: Odoo-level failures raise typed `DatabaseError`

If the Odoo server returns an HTTP error (e.g. restoring into an existing database, restore failure, server-side error), the SDK SHALL raise `DatabaseError` populated from the response. The error SHALL expose `status_code: int`, `message: str` (parsed from response body; never including `master_pwd`), and `body: bytes | str` for diagnostics.

#### Scenario: Existing database
- **WHEN** `restore(artifact, "existing_db")` is called on a target where the database already exists
- **THEN** `DatabaseError` is raised with the response's status code and message