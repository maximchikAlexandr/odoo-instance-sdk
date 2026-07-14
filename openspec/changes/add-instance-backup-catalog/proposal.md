# Каталог резервных копий и instance-bound database API

## Why

Текущий API привязывает database manager к одному глобальному `base_url`, а скачанные резервные копии не имеют локального каталога, истории, поиска и проверки целостности. Из-за этого пользовательский код вручную хранит файлы, повторно скачивает свежие копии и не может безопасно восстановить контекст между запусками процесса.

## What Changes

- **BREAKING**: удалить `client.database`; операции database manager доступны только через `OdooInstance.databases`.
- **BREAKING**: удалить `client.server`; server lifecycle (`run`, `start`, `stop`, `status`) и readiness (`wait_ready`) доступны напрямую на `OdooInstance` без вложенного подресурса `instance.server`. Process registry остаётся приватным на `OdooClient` и разделяется всеми instances.
- **BREAKING**: переименовать `RemoteInstanceError` в `NonLocalInstanceError` без compatibility alias.
- Добавить фабрику `client.instance(...)` для явного подключения к локальному или удалённому Odoo.
- Добавить `client.instance.from_config(...)` для локального Odoo с чтением `[options]` из `odoo.conf`.
- Перенести `base_url` и master password из глобальной конфигурации клиента в конфигурацию конкретного экземпляра.
- Переименовать успешный артефакт `BackupArtifact` в `Backup`.
- Добавить ресурс `client.backups` с локальным SQLite-каталогом, полным аудитом скачиваний, поиском, удалением и проверкой целостности.
- Сохранять начало, успех и ошибку каждого скачивания, результаты каждой проверки и удаление файла.
- Добавить быстрые проверки: ZIP Odoo через CRC и обязательные файлы; PostgreSQL custom dump через `pg_restore --list`.
- Убрать HTTP Basic Auth из всех HTTP requests: Odoo 19.0 database endpoints имеют `auth="none"` и не проверяют Basic header; `master_pwd` передаётся как обычное form field. `/web/health` readiness endpoint также `auth="none"`.
- Сохранить запрет `restore()` и `drop()` для нелокальных экземпляров без unsafe override.
- Не добавлять автоматическое переиспользование свежего backup: пользовательский код сам принимает решение по результату `backups.latest()` и времени скачивания.

## Capabilities

### New Capabilities

- `backup-catalog`: локальная коллекция резервных копий, SQLite-аудит, поиск, удаление и проверка целостности.

### Modified Capabilities

- `odoo-instance-sdk`: новая структура instance-bound ресурсов database manager, создание instance из явных параметров или локального `odoo.conf`, обновлённые модели и исключения.

## Impact

- Публичный Python API несовместим с предыдущей версией: `client.database` и `BackupArtifact` удаляются без compatibility proxy.
- Добавляется runtime-зависимость `platformdirs`; SQLite используется из стандартной библиотеки.
- `pg_restore` остаётся необязательной внешней программой и нужен только для проверки backup формата `dump`.
- Создаётся пользовательское состояние в cache directory:
  - `backups/` — файлы по умолчанию;
  - `backups.sqlite3` — каталог и аудит.
- Удаляется Basic Auth из всех HTTP requests; `warn_if_cleartext_auth` переименовывается в `warn_if_cleartext_secret` (master password в form POST по HTTP остаётся cleartext риском).
- Затрагиваются client wiring (удаление `client.server`, перенос lifecycle на `OdooInstance`), конфигурация, database resource, server lifecycle, readiness, публичные модели, исключения, документация и тесты.
- Классы с логикой и зависимостями остаются dataclass-контейнерами; msgspec применяется только к моделям данных, `StartConfig` и HTTP DTO.
