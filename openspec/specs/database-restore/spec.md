## ADDED Requirements

### Requirement: Восстановление базы

`instance.databases.restore()` MUST принимать существующий доступный `Backup`, target database name и параметры Odoo 19.0 `copy` и `neutralize_database`.

Перед HTTP request метод MUST проверить:

- local instance guard;
- наличие master password;
- наличие соответствующей catalog row;
- state `available`;
- совпадение metadata объекта с catalog;
- существование и читаемость file;
- отсутствие target database.

Метод MUST отправлять multipart request в `POST /web/database/restore` и MUST NOT автоматически удалять существующую target database.

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