## Why

Нет типизированного синхронного Python SDK для управления локальным процессом Odoo 19.0 и его базами через стандартные HTTP-методы. Разработчикам и тест-инфраструктуре приходится заново собирать subprocess-обвязку, health-polling и HTTP-клиент к `/web/database/*` на каждом проекте. SDK закрывает этот зазор одним типизированным `uv tool install`.

## What Changes

- Вводится пакет `odoo-instance-sdk` с публичным `OdooClient` и ресурсами `server`/`database`.
- `server.run()` — запуск одной CLI-команды Odoo как `[executable, *args]` без shell, с типизированным `CommandResult`.
- `server.start()/stop()/status()` — управление долгоживущим локальным процессом через идентификаторы `OdooProcess` (uuid4), с корректным `killpg` на POSIX и `taskkill /T` на Windows; реестр процессоров per-client.
- `server.wait_ready()` — опрос `GET /web/health?db_server_status=true` до `HTTP 200 + status=="pass"`, с прерыванием при раннем выходе связанного процесса и типизированным таймаутом.
- `database.backup()` — стандартный HTTP-метод Odoo 19.0, потоковая скачивание, дефолтный кэш-каталог через платформенный stdlib-резолв.
- `database.restore()/drop()` — стандартные HTTP-методы с неотключаемой local-only проверкой `client.config.base_url` (`localhost`, `127.0.0.0/8`, `::1`), выполняемой до HTTP-запроса.
- `database.list()/exists()` — list через HTTP; exists через `list.contains`, отдельного HTTP-метода нет.
- Все публичные модели — `msgspec.Struct`; запрещены `Any`, `dataclasses`, Pydantic; публичные исключения — отдельные типы.
- `OdooClientConfig.master_pwd` не попадает в `repr`, исключения, потоки и логи (кастомный `__repr__`).
- `server.start()` принимает типизированный `StartConfig` (msgspec.Struct) со сверкой полей по `odoo/tools/config.py` 19.0; SDK сам формирует `[executable, *args]`.
- Зависимости: `httpx` (HTTP-клиент, streaming + multipart); `msgspec` (модели). Без `platformdirs`, без `psutil` — stdlib-альтернативы.
- Проект под `uv` (окружение, сборка, публикация), `mypy --strict`, `ruff` (по `multica-py/ruff.toml` с адаптацией `known-first-party`).
- Публикация на PyPI: `wheel` + `sdist`, установка через `uv tool install odoo-instance-sdk` и `uv add odoo-instance-sdk`.
- Не входит (явный non-goal): Docker, Git, оркестрация, планировщик, управление модулями, XML-RPC/JSON-RPC бизнес-методы, отдельный ресурс тестов, чтение журналов, создание пустой базы, выбор модулей.

## Capabilities

### New Capabilities

- `client-config`: `OdooClient` и `OdooClientConfig` — структура, обязательные/необязательные поля, маскирование `master_pwd` в `repr`, инициализация ресурсов.
- `server-cli`: `server.run()` — разовая CLI-команда Odoo (passthrough args, env, cwd, timeout, типизированный `CommandResult`).
- `server-lifecycle`: `server.start()/stop()/status()` — долгоживущий локальный процесс, `StartConfig`, реестр процессов per-client, сигналы/убийство группы процессов, идемпотентный stop, типизированные статусы и ошибки.
- `readiness`: `server.wait_ready()` — опрос `/web/health`, критерий готовности, прерывание по смерти связанного процесса, типизированный таймаут.
- `database-backup`: `database.backup()` — стандартный HTTP-метод бэкапа Odoo 19.0, потоковая скачивание, `BackupArtifact`, дефолтный кэш-каталог через stdlib-резолв.
- `database-restore`: `database.restore()` — стандартный HTTP-метод восстановления, приём `BackupArtifact`, неотключаемая local-only проверка.
- `database-management`: `database.list()/exists()/drop()` — стандартные HTTP-методы Odoo 19.0; `exists` поверх `list`; local-only проверка для `drop`.
- `models-types`: публичные `msgspec.Struct`-модели (`OdooClientConfig`, `CommandResult`, `OdooProcess`, `ProcessStatus`, `ReadinessResult`, `BackupArtifact`, `RestoreResult`, `DropResult`, `StartConfig`) и иерархия типизированных исключений.
- `packaging`: `uv`-проект, `mypy --strict`, `ruff`-конфиг, метаданные PyPI, сборка `wheel`+`sdist`, точки установки.

### Modified Capabilities

(нет — первая версия SDK, существующих specs нет)

## Impact

- **Код**: новый репозиторий должен быть развёрнут с нуля; `src/odoo_instance_sdk/` — публичный API; `tests/` — минимальные `__main__`-style самопроверки (без фреймворков в первой версии).
- **Зависимости**: добавление `httpx` (HTTP-клиент) и `msgspec` (модели) в `pyproject.toml`. Dev-зависимости: `mypy`, `ruff`.
- **API**: новое публичное API `OdooClient` с подресурсами `server` и `database`. Все типы — публичные.
- **PyPI**: пакет `odoo-instance-sdk` публикуется с SemVer; первая версия `0.1.0` (pre-1.0 — API может меняться).
- **Инструментирование**: локальный кэш резервных копий в платформенном каталоге (`~/.cache/odoo-instance-sdk/backups/` на POSIX, `%LOCALAPPDATA%\odoo-instance-sdk\backups` на Windows).
- **Источники истины**: CLI/HTTP/health/backup-форматы сверяются с Odoo 19.0 (`odoo-bin`, `odoo/tools/config.py`, `addons/web/controllers/database.py`, `addons/web/controllers/home.py`, `odoo/service/db.py`).