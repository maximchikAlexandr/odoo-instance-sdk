## ADDED Requirements

### Requirement: `database.backup()` uses standard Odoo 19.0 HTTP method

`instance.databases.backup(database_name: str, *, format: BackupFormat = BackupFormat.ZIP, filestore: bool = True, destination: str | Path | None = None, timeout: float | None = None)` SHALL invoke the standard Odoo 19.0 HTTP backup endpoint at `<base_url>/web/database/backup` with multipart form data containing the field `master_pwd` (the instance master password) and the parameters `name`, `backup_format`, `filestore`. SDK MUST NOT use HTTP Basic Auth: Odoo 19.0 database endpoints have `auth="none"` and do not validate the Basic header.

#### Scenario: Default backup call shape
- **WHEN** `instance.databases.backup("mydb")` is called
- **THEN** the HTTP request targets `<base_url>/web/database/backup`
- **AND** the request body includes `master_pwd`, `name="mydb"` and `backup_format="zip"`
- **AND** no HTTP Basic Auth header is sent

### Requirement: Response is streamed to disk

The response body SHALL be streamed to a file on disk, not buffered entirely in memory.

#### Scenario: Large backup does not fully load memory
- **WHEN** a backup response body is many megabytes
- **THEN** the file is written incrementally
- **AND** the SDK does not hold the entire body in a single bytes object

### Requirement: Default destination is a platform cache directory

If `destination` is `None`, the file SHALL be saved under `OdooClientConfig.backups_directory` if set, otherwise under `platformdirs.user_cache_path("odoo-instance-sdk") / "backups"`.

The directory SHALL be created with `mkdir(parents=True, exist_ok=True)` if missing.

#### Scenario: Custom destination
- **WHEN** caller passes a destination directory
- **THEN** the file is saved there, and the absolute path is registered in the shared SQLite catalog

#### Scenario: Default cache
- **WHEN** destination and client default are absent
- **THEN** the file and catalog are created in the standard user cache layout

### Requirement: UUID-prefixed filename for collision avoidance

The backup file SHALL be named with a UUID prefix for collision avoidance, format `{backup_id}_{server_filename}` where `backup_id` is a generated UUID and `server_filename` is derived safely from the `Content-Disposition` header. The HTTP response filename SHALL NOT allow escaping the destination directory.

#### Scenario: Filename shape
- **WHEN** `instance.databases.backup("mydb", format=BackupFormat.ZIP, destination="/tmp")` is called and the server returns `Content-Disposition: attachment; filename=mydb.zip`
- **THEN** the saved file path starts with the backup UUID prefix followed by the server filename

### Requirement: `Backup` is a typed object

`instance.databases.backup()` SHALL return a `Backup` (frozen `msgspec.Struct`) with:
- `id: uuid.UUID` — unique backup identifier
- `source_base_url: str` — the normalized base URL of the instance that produced the backup
- `database_name: str` — original database name
- `format: BackupFormat`
- `filestore_requested: bool`
- `path: str` — absolute local path
- `filename: str`
- `size_bytes: int`
- `sha256: str`
- `downloaded_at: datetime`

The returned `Backup` SHALL be directly passable to `instance.databases.restore()` of another local instance without manual file opening or repacking.

#### Scenario: Backup content
- **WHEN** `backup("mydb", format=BackupFormat.DUMP, filestore=False, destination="/tmp")` returns successfully
- **THEN** `backup.database_name == "mydb"`
- **AND** `backup.format == BackupFormat.DUMP`
- **AND** `backup.filestore_requested is False`
- **AND** `backup.source_base_url == <instance base_url>`