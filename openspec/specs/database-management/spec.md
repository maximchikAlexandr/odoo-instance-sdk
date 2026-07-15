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

`instance.databases.list()` MUST вызывать Odoo 19.0 JSON-RPC endpoint `/web/database/list` и возвращать tuple имён баз в порядке ответа Odoo.

SDK MUST NOT угадывать default database и MUST NOT предоставлять `resolve_default()`.

`instance.databases.exists(name)` MUST вызвать `list()` и вернуть точный membership result.

Если listing отключён или endpoint недоступен, методы MUST выбрасывать `DatabaseManagerUnavailableError`, а не возвращать пустой tuple.

#### Scenario: Несколько удалённых баз

- **WHEN** remote Odoo возвращает несколько database names
- **THEN** `list()` возвращает все names без выбора одного default

#### Scenario: Listing недоступен

- **WHEN** Odoo не предоставляет database list
- **THEN** SDK сообщает явную typed error

### Requirement: Удаление базы

`instance.databases.drop()` MUST отправлять `POST /web/database/drop` только после local guard и master password guard.

После ответа метод MUST подтвердить `exists(name) == False`. Redirect или HTTP 200 без postcondition MUST NOT считаться успехом.

#### Scenario: Успешное удаление локальной базы

- **WHEN** local database существует и Odoo успешно удаляет её
- **THEN** `drop()` возвращает `DropResult` после отрицательной проверки `exists()`

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