## Purpose

Public OdooClient facades, client/instance configuration, and instance construction.

## Requirements

### Requirement: Публичная структура клиента

Публичный API MUST иметь следующую структуру:

```text
OdooClient
├── instance
│   ├── __call__(base_url, master_password=None)
│   ├── from_config(path, base_url=None, master_password=None)
│   └── from_environment(environment)
├── backups
│   ├── list()
│   ├── latest()
│   ├── history()
│   ├── validate()
│   └── delete()
└── environments
    ├── checkout(project, branch, *, options)
    ├── sync_python(selector, *, upgrade)
    ├── get(selector)
    ├── list(*, project, include_removed)
    └── remove(selector)
```

`client.instance(...)`, `client.instance.from_config(...)` и `client.instance.from_environment(...)` MUST возвращать отдельный `OdooInstance`. `OdooInstance.databases` MUST быть единственным публичным входом к database manager конкретного instance. Server lifecycle и readiness методы (`run`, `start`, `stop`, `status`, `wait_ready`, `run_foreground`, `shell`, `run_shell_script`) MUST быть доступны напрямую на `OdooInstance`.

`client.environments` MUST быть `EnvironmentResource` для provisioning lifecycle (checkout/sync/get/list/remove) development environments. `client.backups` MUST управлять локальной коллекцией скачанных backup files и MUST NOT выполнять HTTP operations Odoo.

`client.database`, `client.server`, `client.catalog` и `client.doctor` MUST быть удалены/отсутствовать. Compatibility alias или deprecation proxy MUST NOT добавляться.

Ресурсы MUST группировать явные процедуры и возвращать типизированные модели. Модели MUST NOT выполнять скрытые side effects.

Process registry (зарегистрированные процессы и subprocess handles) MUST храниться приватно на `OdooClient` и разделяться всеми instances. Публичный доступ к registry отсутствует.

Catalog открывается internally и не экспортируется как `client.catalog`.

#### Scenario: Три фасада

- **WHEN** `OdooClient` constructed
- **THEN** `client.instance`, `client.backups`, `client.environments` доступны; `client.catalog`, `client.doctor` отсутствуют

#### Scenario: Environments resource

- **WHEN** `client.environments.checkout(project, "feat/x")` вызывается
- **THEN** возвращается `DevelopmentEnvironment`; Git/uv/flock internal

#### Scenario: from_environment на instance factory

- **WHEN** `client.instance.from_environment(env)` вызывается для ready environment
- **THEN** возвращается `OdooInstance` с recorded command_prefix и default_cwd

#### Scenario: Старый API отсутствует

- **WHEN** пользователь обращается к `client.database`, `client.server`, `client.catalog` или `client.doctor`
- **THEN** атрибут отсутствует

### Requirement: Конфигурация клиента

`OdooClientConfig` MUST быть `@dataclass(frozen=True, slots=True, kw_only=True)` и MUST содержать только общие параметры:

- путь или имя Odoo executable (fallback для instance без `command_prefix`);
- необязательный default backup directory;
- HTTP timeout по умолчанию.

Base URL и master password MUST NOT храниться в `OdooClientConfig`.

`InstanceConfig` MUST быть `@dataclass(frozen=True, slots=True, kw_only=True)` и MUST содержать:

- normalized base URL;
- optional master password;
- informational tuple `configured_database_names`;
- `command_prefix: tuple[str, ...] | None = None` — resolved runtime executable prefix (instance-level, overrides `OdooClientConfig.executable` when set);
- `default_cwd: Path | None = None` — resolved runtime working directory;
- существующие cluster-key fields (`db_host`, `db_port`, `db_user`, `db_password`).

Поле master password MUST иметь `repr=False` и MUST NOT попадать в exception messages, stdout, stderr, SQLite или application logs SDK.

`command_prefix` и `default_cwd` MAY появляться в repr (non-secret).

#### Scenario: Instance prefix overrides client executable

- **WHEN** `InstanceConfig.command_prefix = ("/venv/bin/python", "/worktree/odoo-bin")` set
- **THEN** `run()`/`start()`/`run_foreground()`/`shell()`/`run_shell_script()` используют prefix, не `OdooClientConfig.executable`

#### Scenario: Один client и несколько instances с разным Python

- **WHEN** один `OdooClient` создаёт два instance через `from_environment()` с разным recorded Python
- **THEN** каждый instance имеет свой `command_prefix`; `run()` каждого использует свой prefix

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

Явные arguments MUST иметь приоритет над config. При отсутствии `admin_passwd` в config и без явного `master_password` аргумента, `master_password` MUST оставаться `None` (НЕ default `"admin"`). `MasterPasswordRequiredError` MUST NOT подниматься при construction — только на mutating DB-методах (`backup`/`restore`/`drop`).

При отсутствии config values MUST использовать Odoo 19.0 defaults:

- `http_port = 8069`;
- `db_name = ()`.

(Примечание: ранее `admin_passwd` default был `"admin"`; это revised — `master_password` больше не инвариант конструкции instance, см. AC7.)

Base URL MUST автоматически строиться с scheme HTTP только если `http_interface` является literal loopback или `localhost`. Для absent, wildcard или non-loopback interface caller MUST явно передать local `base_url`.

Resulting URL MUST быть local; remote URL в `from_config()` MUST завершаться `InstanceConfigurationError`.

`from_config()` MUST оставлять `command_prefix=None` (см. `instance-runtime-binding` spec).

#### Scenario: Loopback config без admin_passwd

- **WHEN** config содержит `http_interface = 127.0.0.1`, `http_port = 8070`, БЕЗ `admin_passwd`, и `master_password` не передан
- **THEN** instance получает URL `http://127.0.0.1:8070`, `master_password = None`; `list()`/`exists()` доступны; `backup()` поднимает `MasterPasswordRequiredError`

#### Scenario: Loopback config с admin_passwd

- **WHEN** config содержит `http_interface = 127.0.0.1`, `http_port = 8070` и `admin_passwd = secret`
- **THEN** instance получает URL `http://127.0.0.1:8070`, `master_password = "secret"`, parsed `db_name`

#### Scenario: Wildcard interface

- **WHEN** config содержит `http_interface = 0.0.0.0` и base URL не передан
- **THEN** method завершается `InstanceConfigurationError`

#### Scenario: Explicit override

- **WHEN** method получает явные base URL и master password
- **THEN** они имеют приоритет над значениями config

#### Scenario: Read-only instance без password

- **WHEN** instance создан без master password (через `from_config()` или `from_environment()`)
- **THEN** `list()` и `exists()` доступны, а privileged operations (`backup`/`restore`/`drop`) завершаются `MasterPasswordRequiredError` до HTTP request

