## ADDED Requirements

### Requirement: `instance.databases.list()` uses standard Odoo 19.0 HTTP method

`instance.databases.list()` SHALL return a tuple of database names by invoking the standard Odoo 19.0 list endpoint at `<base_url>/web/database/list` via JSON-RPC POST. SDK MUST NOT use HTTP Basic Auth: Odoo 19.0 database endpoints have `auth="none"` and do not validate the Basic header.

#### Scenario: List returns database names
- **WHEN** the Odoo instance has databases `["db1", "db2"]` and `instance.databases.list()` is called
- **THEN** the return is `("db1", "db2")` (a `tuple[str, ...]`, not a dict)

### Requirement: `instance.databases.exists()` is derived from `list()`

`instance.databases.exists(db: str) -> bool` SHALL return `<db> in instance.databases.list()`. No separate HTTP method SHALL be added for existence checks.

#### Scenario: Existing database
- **WHEN** `db1` is in `instance.databases.list()` and `instance.databases.exists("db1")` is called
- **THEN** the result is `True`

#### Scenario: Missing database
- **WHEN** `dbX` is not in `instance.databases.list()` and `instance.databases.exists("dbX")` is called
- **THEN** the result is `False`

### Requirement: `instance.databases.drop()` uses standard Odoo 19.0 HTTP method

`instance.databases.drop(database_name: str, *, timeout: float | None = None)` SHALL delete the database by invoking the standard Odoo 19.0 drop endpoint at `<base_url>/web/database/drop` via POST with form field `master_pwd` (the instance master password) and `name`. SDK MUST NOT use HTTP Basic Auth: Odoo 19.0 database endpoints have `auth="none"` and do not validate the Basic header.

#### Scenario: Drop call shape
- **WHEN** `instance.databases.drop("mydb")` is called
- **THEN** a request is sent to `<base_url>/web/database/drop`
- **AND** the request body includes `master_pwd` as a form field and `name="mydb"`
- **AND** no HTTP Basic Auth header is sent

### Requirement: Drop returns typed `DropResult`

A successful drop SHALL return a `DropResult` (frozen `msgspec.Struct`) containing:
- `db: str` — the name of the dropped database

#### Scenario: Successful drop
- **WHEN** `instance.databases.drop("mydb")` succeeds and `exists("mydb")` is `False`
- **THEN** `result.db == "mydb"`

### Requirement: Local-only guard applies to `drop()` before HTTP

`instance.databases.drop()` SHALL refuse to issue any HTTP request when `instance.base_url` does not resolve to a local instance. The check SHALL be identical to the one used by `instance.databases.restore()` (hostname `localhost`, IPv4 in `127.0.0.0/8`, IPv6 `::1`, via `urllib.parse` and `ipaddress` stdlib). The check SHALL NOT be disabled by any parameter.

#### Scenario: Localhost allowed
- **WHEN** `base_url == "http://localhost:8069"` and `drop("mydb")` is called
- **THEN** the HTTP request is issued

#### Scenario: Remote refused
- **WHEN** `base_url == "http://odoo.example.com:8069"` and `drop("mydb")` is called
- **THEN** `NonLocalInstanceError` is raised
- **AND** no HTTP request is sent

### Requirement: Odoo-level failures raise typed `DatabaseError`

If `list()` or `drop()` returns an HTTP error, the SDK SHALL raise `DatabaseError` populated from the response (same shape used by `instance.databases.restore()`). Existence checks on a nonexistent database SHALL NOT raise — they SHALL return `False` (because `list()` simply will not contain the name; an HTTP error from `list()` SHOULD still propagate as `DatabaseError`).

#### Scenario: Drop nonexistent database
- **WHEN** `drop("nonexistent")` is called and Odoo returns an HTTP error
- **THEN** `DatabaseError` is raised

#### Scenario: Exists on nonexistent database does not raise
- **WHEN** `exists("nonexistent")` is called and `list()` returns `("other_db",)` without HTTP error
- **THEN** the result is `False`
- **AND** no exception is raised