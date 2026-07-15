## MODIFIED Requirements

### Requirement: Получение списка и проверка существования базы

`instance.databases.list()` MUST вызывать Odoo 19.0 JSON-RPC endpoint `/web/database/list` и возвращать tuple `Database` в порядке ответа Odoo.

SDK MUST NOT угадывать default database и MUST NOT предоставлять `resolve_default()`.

`list()` MUST populate `backup` для каждого `Database` в результате: если инстанс имеет cluster-ключ (`db_port is not None`), для каждого имени вызвать `catalog.latest_restore(db_host, db_port, name)` — non-None становится `backup`, `None` → `NoBackup()`. Для инстансов без cluster-ключа `backup` MUST быть `NoBackup()` для всех.

`instance.databases.exists(name)` MUST вызвать `list()` и вернуть точный membership result. После проверки, если `name` не существует, инстанс имеет cluster-ключ И есть restores row для (cluster, `name`), SDK MUST записать `database_events "dropped"` для `name` (с идемпотентностью — см. `database-restore-tracking` spec). `exists()` сверка MUST проверять ТОЛЬКО `name`, не все tracked databases.

Если `list()` raises `DatabaseManagerUnavailableError` (Odoo недоступен): `exists(name)` применяет psql fallback по тем же правилам, что `current()` (cluster-ключ + `db_user is not None`): psql confirms → True (reconciliation не пишется); psql absent → False + `dropped` event (с идемпотентностью); psql non-zero/timeout → inconclusive → propagate `DatabaseManagerUnavailableError`. Без cluster-ключа/`db_user` → propagate.

Если listing отключён или endpoint недоступен, методы MUST выбрасывать `DatabaseManagerUnavailableError`, а не возвращать пустой tuple.

#### Scenario: Несколько удалённых баз

- **WHEN** remote Odoo возвращает несколько database names
- **THEN** `list()` возвращает tuple `Database` для каждого имени без выбора одного default

#### Scenario: Listing недоступен

- **WHEN** Odoo не предоставляет database list
- **THEN** SDK сообщает явную typed error

#### Scenario: Сверка пропавшей базы

- **WHEN** `exists("staging")` возвращает False, инстанс имеет cluster-ключ, restores содержит строку для "staging"
- **THEN** catalog получает один `database_events "dropped"` для (cluster, "staging") с идемпотентностью

#### Scenario: list() populate backup для каждой базы

- **WHEN** `list()` возвращает ("prod", "staging") для from_config()-инстанса, restores содержит mapping для "prod", не для "staging"
- **THEN** результат: `(Database("prod", backup=<Backup>), Database("staging", backup=NoBackup()))`

#### Scenario: list() без cluster-ключа

- **WHEN** `list()` вызван на __call__()-инстансе без cluster-ключа
- **THEN** все `Database` имеют `backup=NoBackup()`, restores и database_events не затрагиваются

#### Scenario: Пустой list() с tracked restores

- **WHEN** `list()` возвращает `()` для from_config()-инстанса, restores содержит "staging" и "test"
- **THEN** catalog получает `dropped` для "staging" и "test" (оба отсутствуют в пустом списке, с идемпотентностью)

### Requirement: Удаление базы

`instance.databases.drop()` MUST отправлять `POST /web/database/drop` только после local guard и master password guard.

После ответа метод MUST подтвердить `exists(name) == False`. Redirect или HTTP 200 без postcondition MUST NOT считаться успехом.

После успешного drop, если инстанс имеет cluster-ключ (`db_port is not None`), метод MUST вызвать `catalog.record_database_dropped(db_host, db_port, name)` (с идемпотентностью). Для инстансов без cluster-ключа метод MUST NOT писать в `database_events`.

#### Scenario: Успешное удаление локальной базы с cluster-ключом

- **WHEN** local database существует и Odoo успешно удаляет её, инстанс имеет cluster-ключ
- **THEN** `drop()` возвращает `DropResult` после отрицательной проверки `exists()`, catalog получает `database_events "dropped"`

#### Scenario: Успешное удаление без cluster-ключа

- **WHEN** `drop()` вызван на __call__()-инстансе
- **THEN** HTTP drop выполняется, `DropResult` возвращается, `database_events` не пишется

## ADDED Requirements

### Requirement: Индексирование и current database

`DatabaseResource` MUST реализовать `__getitem__(self, index: int) -> Database` — делегирует в `list()` и возвращает `list()[index]`. MUST check `isinstance(index, int)`; иначе raises `TypeError`. Поддерживает negative indices (Python tuple semantics). Out-of-range MUST raise `IndexError`. Slices MUST NOT быть поддержаны (raise `TypeError` для `slice`).

`databases.current()` MUST возвращать `Database` для `InstanceConfig.configured_database_names[0]`. Точный flow:

1. Если `configured_database_names` is None или пустой tuple `()` → вернуть `Database(name="", backup=NoBackup())` БЕЗ network call.
2. Иначе `name = configured_database_names[0]`.
3. Вызвать `list()` (HTTP). On success:
   - Если `name` в результате: если cluster-ключ есть (`db_port is not None`) → `backup = catalog.latest_restore(db_host, db_port, name)` или `NoBackup()` если None; если cluster-ключа нет → `backup = NoBackup()`. Вернуть `Database(name, backup)`.
   - Если `name` НЕ в результате: если cluster-ключ есть → записать `dropped` event (идемпотентно). Вернуть `Database(name, backup=NoBackup())` UNCONDITIONALLY (без `latest_restore` — database gone, mapping moot).
4. On `DatabaseManagerUnavailableError`:
   - Если cluster-ключ есть (`db_port is not None`) AND `db_user is not None` → psql fallback. psql confirms (`stdout.strip()` non-empty) → `backup = catalog.latest_restore(...)` or `NoBackup()`. psql absent (`stdout.strip()` empty) → `NoBackup()` UNCONDITIONALLY (без `latest_restore`) + `dropped` event (идемпотентно). psql non-zero/timeout → `NoBackup()`, без `dropped` event.
   - Если cluster-ключа нет ИЛИ `db_user is None` → propagate `DatabaseManagerUnavailableError` (НЕ swallow).

`current()` не угадывает default database: он возвращает базу, явно указанную пользователем в `configured_database_names[0]` из odoo.conf (явная конфигурация, не эвристика). Это не противоречит запрету на `resolve_default()`, который относился к автоматическому выбору по эвристике.

#### Scenario: Доступ по индексу

- **WHEN** `list()` возвращает `(Database("prod"), Database("staging"))`
- **THEN** `databases[0]` возвращает `Database("prod")`, `databases[1]` возвращает `Database("staging")`, `databases[-1]` возвращает `Database("staging")`

#### Scenario: Индекс out-of-range

- **WHEN** `list()` возвращает `(Database("prod"),)` и вызывается `databases[5]`
- **THEN** raises `IndexError`

#### Scenario: current для from_config() инстанса с mapping

- **WHEN** `from_config()` заполнил `configured_database_names=("prod",)`, база "prod" существует в `list()`, restores содержит mapping
- **THEN** `current()` возвращает `Database(name="prod", backup=<Backup>)`

#### Scenario: current без configured_database_names (None)

- **WHEN** инстанс из `__call__()`, `configured_database_names` is None
- **THEN** `current()` возвращает `Database(name="", backup=NoBackup())` БЕЗ network call

#### Scenario: current с пустым configured_database_names

- **WHEN** `from_config()` с odoo.conf без `db_name`, `configured_database_names = ()`
- **THEN** `current()` возвращает `Database(name="", backup=NoBackup())` БЕЗ network call

#### Scenario: current когда база пропала

- **WHEN** `configured_database_names=("prod",)`, но `list()` не возвращает "prod", инстанс имеет cluster-ключ
- **THEN** `current()` возвращает `Database(name="prod", backup=NoBackup())` и catalog получает `dropped` event (идемпотентно)

#### Scenario: current когда Odoo лежит и cluster-ключ есть

- **WHEN** `configured_database_names=("prod",)`, `list()` raises `DatabaseManagerUnavailableError`, инстанс имеет cluster-ключ и `db_user`
- **THEN** SDK fallback на psql; psql confirms → `Database("prod", backup=latest_restore or NoBackup())`; psql absent → `Database("prod", NoBackup())` + `dropped` event; psql error → `Database("prod", NoBackup())` без `dropped`

#### Scenario: current когда Odoo лежит и нет cluster-ключа

- **WHEN** `configured_database_names=("prod",)`, `list()` raises `DatabaseManagerUnavailableError`, инстанс без cluster-ключа
- **THEN** `current()` propagates `DatabaseManagerUnavailableError`