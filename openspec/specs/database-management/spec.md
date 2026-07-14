## ADDED Requirements

### Requirement: `database.list()` uses standard Odoo 19.0 HTTP method

`database.list()` SHALL return a typed list of database names by invoking the standard Odoo 19.0 list endpoint at `<base_url>/web/database/list` via HTTP basic auth (`admin`, `master_pwd`).

#### Scenario: List returns database names
- **WHEN** the Odoo instance has databases `["db1", "db2"]` and `database.list()` is called
- **THEN** the return is `["db1", "db2"]` (a `list[str]`, not a dict)

### Requirement: `database.exists()` is derived from `list()`

`database.exists(db: str) -> bool` SHALL return `<db> in database.list()`. No separate HTTP method SHALL be added for existence checks.

#### Scenario: Existing database
- **WHEN** `db1` is in `database.list()` and `database.exists("db1")` is called
- **THEN** the result is `True`

#### Scenario: Missing database
- **WHEN** `dbX` is not in `database.list()` and `database.exists("dbX")` is called
- **THEN** the result is `False`

### Requirement: `database.drop()` uses standard Odoo 19.0 HTTP method

`database.drop(db: str, *, timeout: float | None = None)` SHALL delete the database by invoking the standard Odoo 19.0 drop endpoint at `<base_url>/web/database/drop` via HTTP basic auth (`admin`, `master_pwd`). The exact request shape SHALL be verified against `odoo/service/db.py` 19.0 during implementation.

#### Scenario: Drop call shape
- **WHEN** `database.drop("mydb")` is called
- **THEN** a request is sent to `<base_url>/web/database/drop`
- **AND** the request includes basic auth with `master_pwd` and the database name `mydb`

### Requirement: Drop returns typed `DropResult`

A successful drop SHALL return a `DropResult` (msgspec.Struct) containing at minimum:
- `db: str` — the name of the dropped database

#### Scenario: Successful drop
- **WHEN** `database.drop("mydb")` succeeds
- **THEN** `result.db == "mydb"`

### Requirement: Local-only guard applies to `drop()` before HTTP

`database.drop()` SHALL refuse to issue any HTTP request when `client.config.base_url` does not resolve to a local instance. The check SHALL be identical to the one used by `database.restore()` (hostname `localhost`, IPv4 in `127.0.0.0/8`, IPv6 `::1`, via `urllib.parse` and `ipaddress` stdlib). The check SHALL NOT be disabled by any parameter.

#### Scenario: Localhost allowed
- **WHEN** `base_url == "http://localhost:8069"` and `drop("mydb")` is called
- **THEN** the HTTP request is issued

#### Scenario: Remote refused
- **WHEN** `base_url == "http://odoo.example.com:8069"` and `drop("mydb")` is called
- **THEN** `RemoteInstanceError` is raised
- **AND** no HTTP request is sent

### Requirement: Odoo-level failures raise typed `DatabaseError`

If `list()` or `drop()` returns an HTTP error, the SDK SHALL raise `DatabaseError` populated from the response (same shape used by `database.restore()`). Existence checks on a nonexistent database SHALL NOT raise — they SHALL return `False` (because `list()` simply will not contain the name; an HTTP error from `list()` SHOULD still propagate as `DatabaseError`).

#### Scenario: Drop nonexistent database
- **WHEN** `drop("nonexistent")` is called and Odoo returns an HTTP error
- **THEN** `DatabaseError` is raised

#### Scenario: Exists on nonexistent database does not raise
- **WHEN** `exists("nonexistent")` is called and `list()` returns `["other_db"]` without HTTP error
- **THEN** the result is `False`
- **AND** no exception is raised