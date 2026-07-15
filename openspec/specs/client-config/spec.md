## ADDED Requirements

### Requirement: Публичная структура клиента

Публичный API MUST иметь следующую структуру:

```text
OdooClient
├── instance
│   ├── __call__(base_url, master_password=None)
│   └── from_config(path, base_url=None, master_password=None)
└── backups
    ├── list()
    ├── latest()
    ├── history()
    ├── validate()
    └── delete()

OdooInstance
├── base_url
├── configured_database_names
├── databases
│   ├── backup()
│   ├── restore()
│   ├── drop()
│   ├── list()
│   └── exists()
├── run()
├── start()
├── stop()
├── status()
└── wait_ready()
```

`client.instance(...)` MUST возвращать отдельный `OdooInstance`. `OdooInstance.databases` MUST быть единственным публичным входом к database manager конкретного instance. Server lifecycle и readiness методы (`run`, `start`, `stop`, `status`, `wait_ready`) MUST быть доступны напрямую на `OdooInstance`.

`client.database` и `client.server` MUST быть удалены. Compatibility alias или deprecation proxy MUST NOT добавляться.

`client.backups` MUST управлять локальной коллекцией скачанных backup files и MUST NOT выполнять HTTP operations Odoo.

Ресурсы MUST группировать явные процедуры и возвращать типизированные модели. Модели MUST NOT выполнять скрытые side effects.

Process registry (зарегистрированные процессы и subprocess handles) MUST храниться приватно на `OdooClient` и разделяться всеми instances. Публичный доступ к registry отсутствует.

#### Scenario: Явный remote instance

- **WHEN** пользователь вызывает `client.instance(base_url=..., master_password=...)`
- **THEN** он получает `OdooInstance`, database operations доступны через `instance.databases`, server lifecycle и readiness — напрямую через `instance.start()`, `instance.wait_ready()` и т.д.

#### Scenario: Старый API отсутствует

- **WHEN** пользователь обращается к `client.database` или `client.server`
- **THEN** атрибут отсутствует и SDK не перенаправляет вызов автоматически

### Requirement: Конфигурация клиента

`OdooClientConfig` MUST быть `@dataclass(frozen=True, slots=True, kw_only=True)` и MUST содержать только общие параметры:

- путь или имя Odoo executable;
- необязательный default backup directory;
- HTTP timeout по умолчанию.

Base URL и master password MUST NOT храниться в `OdooClientConfig`.

`InstanceConfig` MUST быть `@dataclass(frozen=True, slots=True, kw_only=True)` и MUST содержать normalized base URL, optional master password и informational tuple `configured_database_names`.

Поле master password MUST иметь `repr=False` и MUST NOT попадать в exception messages, stdout, stderr, SQLite или application logs SDK.

#### Scenario: Один client и несколько instances

- **WHEN** один `OdooClient` создаёт два instance с разными URL
- **THEN** каждый `DatabaseResource` использует только конфигурацию своего instance

#### Scenario: Read-only instance без password

- **WHEN** instance создан без master password
- **THEN** `list()` и `exists()` доступны, а privileged operations завершаются `MasterPasswordRequiredError` до HTTP request

### Requirement: Создание instance из явных параметров

`client.instance(base_url=..., master_password=None)` MUST нормализовать URL и вернуть новый `OdooInstance`.

Normalized URL MUST:

- использовать только HTTP/HTTPS;
- иметь lower-case scheme и hostname;
- не содержать credentials, query или fragment;
- не содержать path, кроме `/`;
- не содержать default port;
- не завершаться `/`.

Normalized URL MUST быть public `OdooInstance.base_url` и identity key backup catalog.

#### Scenario: Эквивалентные URL

- **WHEN** создаются instances из `HTTP://LOCALHOST:80/` и `http://localhost`
- **THEN** оба имеют identity `http://localhost`

#### Scenario: URL с path запрещён

- **WHEN** base URL содержит `/odoo`, query, fragment или embedded credentials
- **THEN** создание instance завершается `InvalidBaseUrlError`

### Requirement: Создание local instance из Odoo config

`client.instance.from_config(path, base_url=None, master_password=None)` MUST читать `[options]` через `configparser.RawConfigParser(interpolation=None)`.

Метод MUST читать `http_interface`, `http_port`, `admin_passwd`, `db_name`.

Явные arguments MUST иметь приоритет над config. При отсутствии config values MUST использовать Odoo 19.0 defaults:

- `http_port = 8069`;
- `admin_passwd = "admin"`;
- `db_name = ()`.

Base URL MUST автоматически строиться с scheme HTTP только если `http_interface` является literal loopback или `localhost`. Для absent, wildcard или non-loopback interface caller MUST явно передать local `base_url`.

Resulting URL MUST быть local; remote URL в `from_config()` MUST завершаться `InstanceConfigurationError`.

#### Scenario: Loopback config

- **WHEN** config содержит `http_interface = 127.0.0.1`, `http_port = 8070` и `admin_passwd`
- **THEN** instance получает URL `http://127.0.0.1:8070`, password из config и parsed `db_name`

#### Scenario: Wildcard interface

- **WHEN** config содержит `http_interface = 0.0.0.0` и base URL не передан
- **THEN** method завершается `InstanceConfigurationError`

#### Scenario: Explicit override

- **WHEN** method получает явные base URL и master password
- **THEN** они имеют приоритет над значениями config