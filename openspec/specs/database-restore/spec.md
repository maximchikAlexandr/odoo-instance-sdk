## Purpose

Restoring a catalog Backup onto a local Odoo instance with postcondition checks and restore tracking.

## Requirements

### Requirement: Восстановление базы

`instance.databases.restore()` MUST принимать существующий доступный `Backup`, target database name и параметры Odoo 19.0 `copy` и `neutralize_database`.

Перед HTTP request метод MUST проверить:

- local instance guard;
- наличие master password (raised `MasterPasswordRequiredError` if `None` — см. ADDED requirement "Mutating DB methods require password at call time" ниже);
- наличие соответствующей catalog row;
- state `available`;
- совпадение metadata объекта с catalog;
- существование и читаемость file;
- отсутствие target database.

Метод MUST отправлять multipart request в `POST /web/database/restore` и MUST NOT автоматически удалять существующую target database.

POST body MUST содержать `"name": target_database_name`.

Для copy checkout (`copy=True`) restore MUST использовать `neutralize_database=True`.

Target DB NEVER перезаписывается и не удаляется для повторной попытки автоматически.

После ответа Odoo метод MUST подтвердить `exists(target_name) == True`. HTTP 200 или redirect сам по себе MUST NOT считаться успехом.

Mapping write (шаги 1-2 ниже) MUST выполняться ТОЛЬКО после успешного `exists(target_name) == True` postcondition. Если postcondition fails (база не создалась), mapping MUST NOT быть записан.

После успешного postcondition, если инстанс имеет cluster-ключ (`db_port is not None`), метод MUST:
1. вызвать `catalog.record_restore(db_host, db_port, target_database_name, str(backup.id))`;
2. (record_restore вставляет restores row и database_events "restored" row атомарно — см. `database-restore-tracking` spec).

Для инстансов без cluster-ключа (`db_port is None`) метод MUST NOT писать в `restores` или `database_events`.

`restore()` вызывает `exists()` дважды (pre-guard и postcondition); каждый вызов MAY запускать reconciliation (через `list()`). Это приемлемо (идемпотентно). SDK MUST NOT оптимизировать, пропуская reconciliation.

#### Scenario: Restore catalog backup

- **WHEN** target instance локальный, target database отсутствует и передан доступный `Backup`
- **THEN** SDK восстанавливает database и возвращает result только после подтверждения через list endpoint

#### Scenario: Restore sends target name in POST body

- **WHEN** `restore(backup, target_database_name="comerta_x")`
- **THEN** POST body содержит `"name": "comerta_x"`

#### Scenario: Copy checkout restore neutralizes

- **WHEN** copy checkout вызывает `restore(..., copy=True, neutralize_database=True)`
- **THEN** target DB restored and neutralized

#### Scenario: Forged или stale Backup

- **WHEN** metadata объекта не совпадает с catalog либо file отсутствует
- **THEN** restore не отправляет HTTP request и выбрасывает типизированную backup error

#### Scenario: Restore с cluster-ключом пишет mapping

- **WHEN** `restore()` успешно выполнен на from_config()-инстансе с `db_host="localhost"`, `db_port=5432`, postcondition `exists()` подтверждён
- **THEN** catalog содержит restores row и database_events "restored" для target database

#### Scenario: Restore HTTP success но postcondition fail

- **WHEN** HTTP restore вернул 200, но `exists(target_name)` возвращает False
- **THEN** `RestoreFailedError` raises, restores и database_events НЕ пишутся

#### Scenario: Restore без cluster-ключа

- **WHEN** `restore()` успешно выполнен на __call__()-инстансе, postcondition подтверждён
- **THEN** HTTP restore завершён, restores и database_events не содержат новых строк

### Requirement: Модель запуска из готового backup

Поддерживаемый flow MUST начинаться с `Backup`, скачанного через `instance.databases.backup()` или найденного через `client.backups`.

SDK MUST NOT предоставлять создание пустой базы, module-selection resource, отдельный test resource или автоматическую политику повторного скачивания.

Решение использовать найденный backup или скачать новый MUST принимать вызывающий код по `Backup.downloaded_at`.

#### Scenario: Переиспользование свежего backup

- **WHEN** `client.backups.latest()` вернул существующий file
- **THEN** вызывающий код может сравнить `downloaded_at` со своим threshold и передать тот же `Backup` в restore

### Requirement: Mutating DB methods require password at call time

`backup()`, `restore()`, `drop()` MUST требовать master password в момент mutating DB call и поднимать `MasterPasswordRequiredError`, если `master_password is None`.

`MasterPasswordRequiredError` MUST NOT подниматься при construction instance (`from_config()`, `from_environment()`, `__call__()`).

`list()` и `exists()` MUST NOT требовать master password.

#### Scenario: Backup without password

- **WHEN** `instance.databases.backup()` и `master_password is None`
- **THEN** `MasterPasswordRequiredError` raised before HTTP request

#### Scenario: Restore without password

- **WHEN** `instance.databases.restore(backup, "target")` и `master_password is None`
- **THEN** `MasterPasswordRequiredError` raised before HTTP request

#### Scenario: List without password

- **WHEN** `instance.databases.list()` и `master_password is None`
- **THEN** list succeeds (no password needed for read)
