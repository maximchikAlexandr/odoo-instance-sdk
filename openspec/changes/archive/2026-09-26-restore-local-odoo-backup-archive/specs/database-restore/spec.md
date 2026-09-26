## MODIFIED Requirements

### Requirement: Модель запуска из готового backup

Поддерживаемый flow MUST начинаться либо с `Backup`, скачанного через `instance.databases.backup()` или найденного через `client.backups`, либо с типизированного `LocalArchiveRestoreSource`, переданного в существующий `EnvironmentResource.refresh_database_command()`.

SDK MUST NOT предоставлять создание пустой базы, module-selection resource, отдельный test resource, remote-URL source, импорт локального архива в backup catalogue или автоматическую политику повторного скачивания.

Решение использовать найденный backup или скачать новый MUST принимать вызывающий код по `Backup.downloaded_at`.

#### Scenario: Переиспользование свежего backup

- **WHEN** `client.backups.latest()` вернул существующий file
- **THEN** вызывающий код может сравнить `downloaded_at` со своим threshold и передать тот же `Backup` в restore

#### Scenario: Локальный архив не импортируется

- **WHEN** вызывающий код передаёт `LocalArchiveRestoreSource` в `refresh_database_command()`
- **THEN** SDK восстанавливает из проверенного snapshot и не создаёт `Backup` или retained catalogue file

## ADDED Requirements

### Requirement: Restore from a caller-owned local Odoo ZIP

The environment restore command SHALL accept public frozen typed `LocalArchiveRestoreSource(path: str)` through its existing restore-source parameter. It SHALL support Odoo ZIP archives containing `dump.sql`, a compatible manifest database name, and safe filestore content. It SHALL preserve the caller-owned archive, restore into a new target, verify database and filestore postconditions, apply the existing neutralization and optional administrator reset, switch the project default only after full success, and return `DatabasePreparationResult` without fabricating a `Backup`. It SHALL NOT persist the source path, expose it in plan/output/error projections, add a public restore method, or support native dump/remote URL/format conversion through this source.

#### Scenario: Valid local ZIP restores through the public boundary

- **WHEN** `LocalArchiveRestoreSource` identifies a supported Odoo ZIP and the target is absent
- **THEN** `EnvironmentResource.refresh_database_command()` restores `dump.sql` and filestore through the existing guarded stages and returns the confirmed target

#### Scenario: Source archive is preserved

- **WHEN** local-archive restore succeeds or fails
- **THEN** the caller-owned archive remains unchanged and only private staging artifacts are cleaned

#### Scenario: Result and plans are path-redacted

- **WHEN** a local-archive command is previewed, succeeds, fails, or is interrupted
- **THEN** bounded Rich, JSON, and TOON projections identify the source kind and sanitized digest evidence without exposing the source or private snapshot path

#### Scenario: Unsupported local format is rejected

- **WHEN** `LocalArchiveRestoreSource` identifies a native dump, remote URL, or archive requiring conversion
- **THEN** restore fails before database mutation with a typed validation/configuration error
