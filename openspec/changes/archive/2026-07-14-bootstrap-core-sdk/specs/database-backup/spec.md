## ADDED Requirements

### Requirement: `database.backup()` uses standard Odoo 19.0 HTTP method

`database.backup(db: str, *, format: Literal["zip", "dump"] = "zip", include_filestore: bool = True, dest: str | Path | None = None, timeout: float | None = None)` SHALL invoke the standard Odoo 19.0 HTTP backup endpoint at `<base_url>/web/database/backup` with HTTP basic auth (`admin`, `master_pwd`) and the parameters `db`, `backup_format`, `t` (token handling per Odoo 19.0 controller).

#### Scenario: Default backup call shape
- **WHEN** `database.backup("mydb")` is called
- **THEN** the HTTP request targets `<base_url>/web/database/backup`
- **AND** the request includes basic auth with `master_pwd`
- **AND** the request body includes `db="mydb"` and `backup_format="zip"`

### Requirement: Response is streamed to disk

The response body SHALL be streamed to a file on disk, not buffered entirely in memory.

#### Scenario: Large backup does not fully load memory
- **WHEN** a backup response body is many megabytes
- **THEN** the file is written incrementally
- **AND** the SDK does not hold the entire body in a single bytes object

### Requirement: Default destination is a platform cache directory

If `dest` is `None`, the file SHALL be saved under a platform-appropriate cache directory resolved via stdlib only (no `platformdirs` dependency):
- POSIX: `$XDG_CACHE_HOME/odoo-instance-sdk/backups/` (or `~/.cache/odoo-instance-sdk/backups/` when `XDG_CACHE_HOME` is unset).
- Windows: `%LOCALAPPDATA%\odoo-instance-sdk\backups\` (or `~\AppData\Local\odoo-instance-sdk\backups\` when `LOCALAPPDATA` is unset).

The directory SHALL be created with `mkdir(parents=True, exist_ok=True)` if missing.

#### Scenario: Missing XDG_CACHE_HOME uses ~/.cache
- **WHEN** `dest=None` and `XDG_CACHE_HOME` is unset on POSIX
- **THEN** the destination directory resolves to `~/.cache/odoo-instance-sdk/backups/`

#### Scenario: Windows uses LOCALAPPDATA
- **WHEN** `dest=None` on Windows with `LOCALAPPDATA` set
- **THEN** the destination directory resolves to `%LOCALAPPDATA%\odoo-instance-sdk\backups\`

### Requirement: Filename is `<db>.<format>`

The backup file SHALL be named `<db>.<format>` (e.g. `mydb.zip`). If a file with the same name already exists in the destination directory, it SHALL be overwritten.

#### Scenario: Filename shape
- **WHEN** `database.backup("mydb", format="zip", dest="/tmp")` is called
- **THEN** the saved file path is `/tmp/mydb.zip`

#### Scenario: Existing file is overwritten
- **WHEN** `/tmp/mydb.zip` already exists and `backup("mydb", format="zip", dest="/tmp")` is called
- **THEN** the file is replaced with the new backup content

### Requirement: `BackupArtifact` is a typed object

`database.backup()` SHALL return a `BackupArtifact` (msgspec.Struct) with:
- `path: Path` — absolute local path (resolved via `Path.resolve()`)
- `source_db: str` — original database name
- `format: Literal["zip", "dump"]`
- `has_filestore: bool`
- `source_base_url: str` — the `base_url` of the instance that produced the backup

The returned artifact SHALL be directly passable to `database.restore()`.

#### Scenario: Artifact content
- **WHEN** `backup("mydb", format="dump", include_filestore=False, dest="/tmp")` returns successfully
- **THEN** `artifact.path` is an absolute `Path` ending in `mydb.dump`
- **AND** `artifact.source_db == "mydb"`
- **AND** `artifact.format == "dump"`
- **AND** `artifact.has_filestore is False`
- **AND** `artifact.source_base_url == <client base_url>`