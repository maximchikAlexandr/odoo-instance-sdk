## MODIFIED Requirements

### Requirement: Проверка Odoo ZIP backup

`client.backups.validate()` для `BackupFormat.ZIP` MUST:

1. подтвердить catalog identity и file availability;
2. подтвердить ZIP signature;
3. подтвердить root entries `dump.sql` и `manifest.json`;
4. выполнить `ZipFile.testzip()` и требовать result `None`;
5. прочитать `manifest.json` и декодировать его как JSON object.

Метод MUST NOT распаковывать archive в temporary directory и MUST NOT восстанавливать database.

Success MUST вернуть `BackupValidationStatus.VALID` и записать `validation_succeeded`. Любая structural/CRC/JSON ошибка MUST вернуть `INVALID` и записать `validation_failed`.

Backup validation SHALL NOT reject a valid Odoo archive solely because a single ZIP member exceeds 512 MiB or the total uncompressed size exceeds 2 GiB. Those ceilings SHALL be removed as unconditional invalidity criteria. Validation SHALL remain fail-safe against path traversal, malformed ZIP, encrypted/unsupported/duplicate members, and CRC errors. Entry count SHALL stay `_MAX_ZIP_ENTRIES = 4096`. Streaming SHALL use a 65536-byte buffer; `dump.sql` and filestore SHALL NOT be read fully into memory. `_MAX_ZIP_COMPRESSION_RATIO = 100` SHALL NOT invalidate `dump.sql` by itself.

Before restore, declared uncompressed sizes SHALL be summed with overflow-safe arithmetic and compared to `shutil.disk_usage(Path(data_dir).resolve()).free` when `data_dir` is set, else `shutil.disk_usage(get_backups_dir()).free` minus reserve `max(1 GiB, 10% of that free space)`; insufficient space SHALL yield `backup_insufficient_disk` with measured, available, and reserve bytes, not a structural invalid-archive error. Optional operator maximum SHALL be `backup.max_uncompressed_bytes` in `get_config_root()/user.toml`; absent key means no ceiling; overflow SHALL yield `backup_operator_limit` with measured/allowed bytes. Corrupt ZIP/CRC SHALL be `backup_corrupt`. Path traversal/duplicate/encrypted/unsupported members, and a non-`dump.sql` member whose compression ratio exceeds 100, SHALL be `backup_unsafe`. The Odoo manifest version SHALL be `str(manifest["version"])` if present, else `f"{manifest['major_version']}.0"`. Rich/JSON/TOON SHALL distinguish those four error codes.

#### Scenario: Валидный Odoo ZIP

- **WHEN** archive содержит читаемые `dump.sql`, `manifest.json` и корректные CRC
- **THEN** validation result имеет status `valid`

#### Scenario: Повреждённый ZIP

- **WHEN** CRC повреждён или обязательный root entry отсутствует
- **THEN** validation result имеет status `invalid` и audit содержит failure

#### Scenario: large dump.sql passes validation

- **WHEN** `odcli backup validate <UUID>` runs on an archive with `dump.sql` of 1.21 GB uncompressed and total uncompressed 1.53 GB
- **THEN** structural validation passes without weakening CRC or path-safety checks

#### Scenario: very large dump modelled by metadata

- **WHEN** a test backup with a single `dump.sql` larger than 100 GB is modelled via ZIP metadata/streaming fixture
- **THEN** it is not rejected by an arbitrary byte ceiling and does not allocate comparable RAM or disk

#### Scenario: insufficient disk blocks restore with a resource error

- **WHEN** the summed uncompressed sizes exceed available disk space minus the reserve
- **THEN** restore is blocked before writes with `backup_insufficient_disk` showing measured bytes, available bytes, and the required reserve

#### Scenario: operator maximum is separate from structural validity

- **WHEN** `backup.max_uncompressed_bytes` is set and an archive exceeds it
- **THEN** `backup_operator_limit` with measured/allowed bytes is returned and structural validity is not affected

#### Scenario: ZIP bomb and traversal regressions remain blocked

- **WHEN** a ZIP bomb, path traversal, duplicate, encrypted, or unsupported-compression archive is validated
- **THEN** it is rejected as `backup_unsafe` or `backup_corrupt` and covered by tests

#### Scenario: bounded-memory streaming

- **WHEN** validation and restore process a large member
- **THEN** they use 65536-byte streaming and do not read the member wholly into memory

#### Scenario: Odoo 13 version is read correctly

- **WHEN** a standard Odoo 13 manifest with `version`, `major_version`, and `version_info` is validated
- **THEN** the result contains `13.0`, not `null`

#### Scenario: Rich/JSON/TOON distinguish error kinds

- **WHEN** a validation or restore failure is rendered
- **THEN** `backup_corrupt`, `backup_unsafe`, `backup_operator_limit`, and `backup_insufficient_disk` are distinct
