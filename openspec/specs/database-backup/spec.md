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
