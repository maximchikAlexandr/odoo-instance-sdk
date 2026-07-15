## ADDED Requirements

### Requirement: REST-контракт database manager

Instance-bound `DatabaseResource` MUST использовать только стандартные endpoints Odoo 19.0:

- `POST /web/database/backup`;
- `POST /web/database/restore`;
- `POST /web/database/drop`;
- JSON-RPC `/web/database/list`.

Каждый request MUST строиться от normalized `OdooInstance.base_url`.

`backup()`, `restore()` и `drop()` MUST использовать master password instance как обычное form field `master_pwd` в POST body. SDK MUST NOT использовать HTTP Basic Auth: Odoo 19.0 database endpoints имеют `auth="none"` и не проверяют Basic header.

SDK MUST NOT выдавать прямой публичный доступ к произвольным Odoo HTTP endpoints.

#### Scenario: Изоляция instance

- **WHEN** database method вызван у `instance_a.databases`
- **THEN** request отправляется только на normalized base URL `instance_a`

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

### Requirement: Запрет destructive операций на нелокальных инстансах

`instance.databases.restore()` и `instance.databases.drop()` MUST быть запрещены для нелокального normalized base URL.

Local URL MUST определяться без DNS resolution:

- hostname ровно `localhost`; или
- literal IPv4/IPv6 address, для которого `ipaddress.ip_address(host).is_loopback` равно `True`.

Private network address, public address, любой иной DNS hostname и malformed URL MUST считаться нелокальными. Guard MUST выполняться до открытия HTTP connection. Override, force или unsafe flag MUST NOT существовать.

`backup()`, `list()` и `exists()` MUST поддерживать удалённые instances.

#### Scenario: Loopback разрешён

- **WHEN** instance URL использует `localhost`, `127.0.0.0/8` или `::1`
- **THEN** restore и drop проходят local guard

#### Scenario: Private network запрещена

- **WHEN** instance URL использует `10.0.0.0/8`, `172.16.0.0/12` или `192.168.0.0/16`
- **THEN** restore и drop завершаются `NonLocalInstanceError` до network request

### Requirement: HTTP transport без Basic Auth

SDK MUST NOT использовать HTTP Basic Auth для запросов к Odoo 19.0. Database endpoints имеют `auth="none"` и не проверяют Basic header. `master_pwd` передаётся как обычное form field в POST body.

HTTP client для database operations MUST создаваться без `auth=`. Health endpoint опрашивается без auth.

SDK MUST предупреждать о передаче секрета по cleartext HTTP через `warn_if_cleartext_secret`: warning срабатывает при HTTP-запросе к нелокальному host, потому что `master_pwd` в form POST передаётся в cleartext.

#### Scenario: Database request без Basic Auth

- **WHEN** `backup()`, `restore()` или `drop()` отправляет POST
- **THEN** HTTP client не имеет `auth=` и `master_pwd` находится только в form body

#### Scenario: Cleartext warning для нелокального HTTP

- **WHEN** database operation выполняется к нелокальному host по HTTP (не HTTPS)
- **THEN** SDK warns о передаче секрета в cleartext один раз за процесс

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