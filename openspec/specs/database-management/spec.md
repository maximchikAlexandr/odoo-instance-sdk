## Purpose

Instance-bound Odoo database management operations and their local catalog contracts.
## Requirements
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

### Requirement: DatabaseResource exposes native psql through shared command plans

The existing instance-bound `DatabaseResource` SHALL expose:

```python
psql_command(args: tuple[str, ...] = ()) -> Command[int]
psql(args: tuple[str, ...] = ()) -> int
execute_sql_command(sql: str, *, timeout: float = 30.0) -> Command[SqlExecutionResult]
execute_sql(sql: str, *, timeout: float = 30.0) -> SqlExecutionResult
```

Each convenience method SHALL build its sibling command exactly once and delegate to `Command.run()`. The command SHALL capture the bound database, cluster host/port/user, sanitized private environment, exact native argv/stdin, timeout, and captured or inherited-TTY mode. Planning, dry-run, and execution SHALL use the shared immutable process contract; no PostgreSQL-specific executor or preview reconstruction SHALL exist.

For an SDK-owned cluster the plan SHALL include the shared `ensure_running` action before `psql`. For an external cluster it SHALL validate reachability without Docker lifecycle operations.

#### Scenario: Convenience method delegates to one command

- **WHEN** `instance.databases.psql(args=("-c", "SELECT 1"))` is called
- **THEN** it runs the exact process specification captured by `psql_command()` and returns the native exit code

#### Scenario: Interactive command preserves TTY mode

- **WHEN** `psql_command()` is built with no native arguments and run from a TTY
- **THEN** its process step inherits stdin/stdout/stderr and preserves native completion, history, signals, and exit code

#### Scenario: Owned cluster readiness is shared

- **WHEN** `psql_command()` targets a stopped SDK-owned cluster
- **THEN** its plan uses the accepted shared lifecycle/action boundary to ensure readiness before the psql process and does not hide an unplanned Docker launch

### Requirement: Native psql cannot override bound connection identity

The shared PostgreSQL builder SHALL add database connection identity from the bound instance/cluster and implement this closed native-option grammar:

- protected identity options `-d/--dbname`, `-h/--host`, `-p/--port`, and `-U/--username` SHALL be rejected in split, attached-short, and long-`=` forms;
- allowed one-value options SHALL be exactly `-c/--command`, `-f/--file`, `-F/--field-separator`, `-L/--log-file`, `-o/--output`, `-P/--pset`, `-R/--record-separator`, `-T/--table-attr`, and `-v/--set/--variable`; each short form SHALL accept one split or attached value and each long form one split or `=` value, and a missing value SHALL be rejected;
- allowed zero-value options SHALL be exactly `-a/--echo-all`, `-b/--echo-errors`, `-e/--echo-queries`, `-E/--echo-hidden`, `-H/--html`, `-l/--list`, `-n/--no-readline`, `-q/--quiet`, `-s/--single-step`, `-S/--single-line`, `-t/--tuples-only`, `-x/--expanded`, `-X/--no-psqlrc`, `-w/--no-password`, `-W/--password`, `-z/--field-separator-zero`, `-0/--record-separator-zero`, `-1/--single-transaction`, and `--csv`;
- every other short/long option and every unconsumed positional operand, including database/user and URI/keyword connection strings, SHALL be rejected before spawn; `--` SHALL end option recognition but SHALL NOT make following positional operands valid.

Validation SHALL scan left-to-right without reordering or combining tokens. This closed set is the supported native-option passthrough contract; adding a future PostgreSQL option requires an accepted spec update with its arity and identity effect.

All launches SHALL use `shell=False`. The builder SHALL remove ambient libpq identity/service overrides, `PSQLRC`, and ambient `PGOPTIONS` before adding any SDK-owned statement-timeout option; it MAY preserve an explicitly allowed `PGPASSFILE`. An explicit password SHALL exist only in the private child environment and SHALL NOT appear in argv, public plan, fingerprint, repr, stdout, exception text, or logs.

#### Scenario: Connection override is rejected

- **WHEN** native arguments contain `-h other-host`, `-d other-db`, `--username=other`, or any equivalent protected alias
- **THEN** command construction fails before spawn with an actionable usage error

#### Scenario: Query and file options pass through

- **WHEN** native arguments are `("-v", "ON_ERROR_STOP=1", "-f", "query.sql")`
- **THEN** those argument boundaries are preserved in the captured psql argv alongside the SDK-owned connection identity

#### Scenario: Presentation value options preserve arity

- **WHEN** native arguments contain split and attached forms such as `-F "|"`, `-Pborder=2`, `--record-separator=::`, `-T`, and `class=compact`
- **THEN** each declared value is consumed exactly once and the original token boundaries reach the planned argv

#### Scenario: Unknown option is rejected

- **WHEN** native arguments contain an option outside the closed zero-value/one-value sets
- **THEN** command construction fails before spawn even if a later token could look like its value

#### Scenario: Positional connection identity is rejected

- **WHEN** native arguments contain `other_db`, `postgresql://other/db`, `host=other dbname=other`, or any such operand after `--`
- **THEN** command construction fails before spawn rather than allowing native positional identity to override the binding

#### Scenario: Ambient PGOPTIONS is replaced, not inherited

- **WHEN** the parent environment defines `PGOPTIONS` that changes query behavior and a bounded SQL command is planned
- **THEN** that value affects neither execution nor the public plan, and only the SDK-owned statement timeout is present in the private child environment

#### Scenario: Password is private

- **WHEN** the bound configuration contains a password and the command is previewed, executed, fails, and is represented
- **THEN** the password appears only in the private child environment and nowhere in user-visible or fingerprinted data

### Requirement: execute_sql is a narrow captured transport operation

`execute_sql()` SHALL execute exactly one caller-provided SQL string in captured mode against the bound database and return a frozen `SqlExecutionResult` containing only `returncode`, `stdout`, and sanitized `stderr`. The SQL SHALL be captured as exact stdin or command input in the shared plan, and a positive finite timeout SHALL govern both server statement execution and the subprocess.

The method SHALL NOT promise parameter binding, map arbitrary rows into typed models, present itself as an application query layer, or silently modify the caller SQL. Callers remain responsible for using a safe parameterized API for untrusted input.

#### Scenario: Captured result preserves process outcome

- **WHEN** caller SQL writes `value\n` to stdout and psql exits 0
- **THEN** `SqlExecutionResult(returncode=0, stdout="value\n", stderr="")` is returned

#### Scenario: Invalid timeout fails before spawn

- **WHEN** `timeout` is non-finite or not greater than zero
- **THEN** command construction fails and no process is spawned

### Requirement: Guarded project-cluster database deletion

Database deletion SHALL be planned and executed by a CLI-private/internal operation only through the resolved project's bound PostgreSQL cluster and existing PostgreSQL transport. It SHALL NOT add a public `DatabaseResource` or `PostgresCluster` method and SHALL preserve the existing public `DatabaseResource.drop/drop_command` Odoo HTTP manager semantics and call-time master-password requirement. The operation SHALL accept one exact normalized database name, reject empty/wildcard names and the exact denylist `postgres`, `template0`, and `template1`, query `pg_database.datistemplate` read-only for the exact target, and reject every database for which it is true. It SHALL display cluster identity without credentials and verify the target exists. It SHALL refuse the configured project default unless explicitly forced. It SHALL report active connection count and identities in sanitized form and SHALL refuse to terminate them unless explicitly forced; forced termination and `DROP DATABASE` SHALL be separate inspectable steps. It SHALL connect to the exact maintenance database `postgres`; because `postgres` is denied as a target, the target and maintenance database cannot coincide. Immediately before any session termination or drop, execution SHALL revalidate existence, exact denylist, `datistemplate`, configured-default, and active-session preconditions and SHALL fail closed without mutation if any safety value changed or cannot be read. Passwords SHALL never appear in argv, plans, errors, or logs.

#### Scenario: Target cannot escape the cluster
- **WHEN** deletion is requested for a database name on a resolved project
- **THEN** all inspection, termination, and drop actions use that project's PostgreSQL transport and no caller-supplied host/user/password is accepted

#### Scenario: Active connections require force
- **WHEN** the target has active sessions and connection force is absent
- **THEN** the operation reports the sessions and performs neither termination nor drop

#### Scenario: Custom template database is refused
- **WHEN** the exact target is not in the name denylist but `pg_database.datistemplate` is true during planning or execution revalidation
- **THEN** the operation fails closed and performs no session termination, drop, or catalogue write

#### Scenario: Forced drop is ordered
- **WHEN** active sessions exist and all required confirmations and force flags are present
- **THEN** the command terminates only target-database sessions, drops that exact database, and verifies absence

#### Scenario: Public SDK drop remains unchanged
- **WHEN** public SDK methods and their behavior are characterized after the CLI drop is added
- **THEN** `DatabaseResource.drop/drop_command` still use the Odoo HTTP database manager with a call-time master password and `test_discovered_public_methods` reports the unchanged public method set

### Requirement: Drop reconciles the audit catalogue

After verified successful deletion, the operation SHALL invoke the existing canonical `record_database_dropped` reconciliation helper exactly once for the bound cluster key and database. The helper SHALL preserve its existing idempotency rule: it inserts a sanitized `dropped` event only when the latest event is not already `dropped`; otherwise it performs its canonical no-op. A failed, refused, or dry-run deletion SHALL not invoke successful reconciliation and SHALL write no catalogue event or mapping change.

#### Scenario: Successful deletion is audited
- **WHEN** the database is absent after the drop postcondition
- **THEN** `record_database_dropped` is called exactly once and inserts a new `dropped` row only when the latest event for that cluster/database is not already `dropped`

#### Scenario: Existing dropped event remains idempotent
- **WHEN** the database was recreated outside the catalogue, successfully dropped by the CLI, and its latest catalogue event is already `dropped`
- **THEN** reconciliation is invoked once and the canonical helper inserts no duplicate event

#### Scenario: Failure leaves catalogue unchanged
- **WHEN** termination, drop, or the absence postcondition fails
- **THEN** no successful dropped event or mapping reconciliation is committed
