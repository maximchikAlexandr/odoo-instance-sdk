## Purpose

Downloading Odoo database backups and storing them in the shared local catalog.
## Requirements
### Requirement: Создание backup

`instance.databases.backup()` MUST поддерживать:

- имя базы;
- `BackupFormat.ZIP` или `BackupFormat.DUMP`;
- параметр `filestore`, default `True`;
- необязательный destination directory;
- необязательный timeout.

Метод MUST:

1. создать audit entry до HTTP request;
2. отправить `POST /web/database/backup` с полями Odoo 19.0 `master_pwd`, `name`, `backup_format`, `filestore`;
3. потоково записать response в `.part` file;
4. атомарно переименовать успешно скачанный file;
5. записать успех или ошибку в catalog;
6. вернуть `Backup`.

`Backup` MUST напрямую приниматься `instance.databases.restore()` другого локального instance без ручного открытия или переупаковки file.

#### Scenario: Backup remote instance

- **WHEN** remote instance имеет master password и база существует
- **THEN** backup скачивается локально, audit содержит success и метод возвращает `Backup`

#### Scenario: Ошибка скачивания

- **WHEN** HTTP request или запись file завершается ошибкой
- **THEN** partial file удаляется, audit сохраняет failure и вызывающий получает типизированное исключение

### Requirement: Каталог хранения backups

`instance.databases.backup()` MUST сохранять file:

1. в явно переданный destination directory;
2. иначе в `OdooClientConfig.backups_directory`;
3. иначе в `platformdirs.user_cache_path("odoo-instance-sdk") / "backups"`.

Catalog database MUST всегда храниться в `platformdirs.user_cache_path("odoo-instance-sdk") / "backups.sqlite3"`.

SDK MUST использовать безопасный basename из `Content-Disposition`. Final filename MUST начинаться с backup UUID. Имя HTTP response MUST NOT позволять выйти за destination directory.

Успешный backup MUST NOT удаляться автоматически.

#### Scenario: Custom destination

- **WHEN** caller передал destination directory
- **THEN** file сохраняется там, а absolute path регистрируется в общем SQLite catalog

#### Scenario: Default cache

- **WHEN** destination и client default отсутствуют
- **THEN** file и catalog создаются в стандартном user cache layout

### Requirement: Declarative source Git branch provenance

`instance.databases.backup()` SHALL accept an optional keyword-only `source_git_branch: str | None = None`. It SHALL pass the caller-supplied value into the existing pre-request catalog audit row and return it on `Backup`. The method SHALL treat the value as declarative metadata and SHALL NOT run Git, call a remote provenance endpoint, or derive a branch from the database name.

Normalization SHALL trim surrounding whitespace, reject empty/non-empty-after-trim violations and control characters, and otherwise preserve the declared ref text. Omitting the option SHALL remain backward compatible and store `NULL`.

#### Scenario: Backup records configured branch

- **WHEN** backup is called with `source_git_branch="release/19"`
- **THEN** the catalog audit begins before HTTP with that branch and the returned backup exposes the same value

#### Scenario: Existing callers omit branch

- **WHEN** an existing caller invokes `backup(database_name)` without the new option
- **THEN** download behavior is unchanged and `Backup.source_git_branch is None`

#### Scenario: Invalid branch rejected before request

- **WHEN** `source_git_branch` is empty after trimming or contains a control character
- **THEN** backup fails before catalog or HTTP mutation

### Requirement: Streaming remote backup transfer

Remote database backup creation SHALL use the existing HTTPX streaming API and SHALL write response chunks directly to the existing exclusive `.part` artifact without buffering the complete response in memory. During the write it SHALL incrementally count bytes, calculate SHA-256, stop before accepting data beyond the configured size limit, and publish the final file atomically only after a successful complete transfer.

#### Scenario: Large response is not fully buffered

- **WHEN** a remote backup response is larger than the HTTP client's ordinary in-memory response body
- **THEN** the client consumes it as a stream and memory usage does not scale with the complete archive size
- **AND** the final catalogue size and SHA-256 match the published file

#### Scenario: Stream exceeds the limit

- **WHEN** the next response chunk would make received bytes exceed the configured maximum
- **THEN** the transfer fails, no final backup is published, and existing failed/download cleanup policy handles the `.part`

#### Scenario: Stream breaks

- **WHEN** the HTTP response terminates before successful completion
- **THEN** the catalogue does not mark the backup available and no partial file is renamed as final

### Requirement: Observable backup wait and transfer

Backup creation SHALL report waiting for response headers separately from transferring response bytes. Transfer progress SHALL report received bytes; it SHALL report a percentage only for a trustworthy `Content-Length` expressed in the same bytes and SHALL verify the final byte count against that length. Missing, encoded, invalid, or inconsistent length SHALL not be presented as a percentage.

#### Scenario: Reliable content length

- **WHEN** the response supplies a valid trustworthy `Content-Length`
- **THEN** Rich progress may show received bytes and percentage and completion requires the final byte count to match

#### Scenario: Unknown content length

- **WHEN** the response has no trustworthy byte total
- **THEN** progress shows waiting/status and received bytes without percentage

### Requirement: Interrupted backup retains truthful state

Interrupting local backup download SHALL close the HTTP response and file handle, return the known backup UUID and retained-artifact state, and SHALL not claim that closing the client stopped remote archive generation. A successfully published backup SHALL not be deleted merely because a later restore or preparation step is interrupted.

#### Scenario: Interrupt before transfer

- **WHEN** Ctrl-C occurs while the remote server is preparing the response
- **THEN** the local request closes, the catalogue does not report an available archive, and the CLI exits 130 with the known backup UUID

#### Scenario: Interrupt after publication

- **WHEN** Ctrl-C occurs after the archive was atomically published
- **THEN** the available backup remains in the catalogue and on disk and is reported as retained

### Requirement: Project ownership captured at download start
Project-backed backup operations SHALL pass the resolved canonical project identifier into `start_download` for local and remote sources; source type SHALL NOT determine project ownership. [Source: GH#64 §1]

#### Scenario: Start project download
- **WHEN** backup download is planned and executed from a resolved project owner
- **THEN** its catalogue row records that project before transfer state advances
