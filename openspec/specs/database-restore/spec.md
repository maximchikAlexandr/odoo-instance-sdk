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

#### Scenario: Restore catalog backup

- **WHEN** target instance локальный, target database отсутствует и передан доступный `Backup`
- **THEN** SDK восстанавливает database и возвращает result только после подтверждения через list endpoint

#### Scenario: Forged или stale Backup

- **WHEN** metadata объекта не совпадает с catalog либо file отсутствует
- **THEN** restore не отправляет HTTP request и выбрасывает типизированную backup error

### Requirement: Модель запуска из готового backup

Поддерживаемый flow MUST начинаться с `Backup`, скачанного через `instance.databases.backup()` или найденного через `client.backups`.

SDK MUST NOT предоставлять создание пустой базы, module-selection resource, отдельный test resource или автоматическую политику повторного скачивания.

Решение использовать найденный backup или скачать новый MUST принимать вызывающий код по `Backup.downloaded_at`.

#### Scenario: Переиспользование свежего backup

- **WHEN** `client.backups.latest()` вернул существующий file
- **THEN** вызывающий код может сравнить `downloaded_at` со своим threshold и передать тот же `Backup` в restore