## Context

Репозиторий пуст, активных openspec-изменений нет. Есть спецификация в `.devlocal/tickets/001/odoo-instance-sdk-spec-ru-clean.md` — единственный источник требований. Источник истины по поведению — Odoo 19.0 (`odoo-bin`, `odoo/tools/config.py`, `addons/web/controllers/database.py`, `addons/web/controllers/home.py`, `odoo/service/db.py`). Проект не проиндексирован в codebase-memory (нечего индексировать), graph-tools при реализации не используются.

В explore-режиме согласованы 12 зазоров спецификации. Этот документ фиксирует их как принятые решения, чтобы `tasks.md` и реализация не переоткрывали их повторно.

## Goals / Non-Goals

**Goals:**
- Единый типизированный синхронный Python API для управления локальным процессом Odoo 19.0 и его базами через стандартные HTTP-методы.
- Публикуемый на PyPI пакет под `uv`, с `mypy --strict` и `ruff`.
- Минимум зависимостей: `httpx`, `msgspec` + stdlib для всего остального.
- Public API, в котором нельзя ошибиться типами (особенно `StartConfig` с `Literal`-полями).

**Non-Goals:**
- Docker, Git, оркестрация, планировщик, управление модулями.
- XML-RPC/JSON-RPC бизнес-методы Odoo.
- Создание пустой базы, выбор/установка модулей.
- Отдельный ресурс тестов, публичный метод чтения журналов.
- Управление удалёнными экземплярами сверх `backup()` и `list()` для remote (restore/drop — только локально).
- Идемпотентность `drop`/`restore` — пробрасываем ошибки Odoo без переинтерпретации.

## Decisions

### D1. Handle процессов — `OdooProcess`, не `str` id

`start()` возвращает `OdooProcess`. `stop()`, `status()`, `wait_ready()` принимают `OdooProcess`. Идентификатор (`uuid4().hex`) хранится внутри объекта; реестр процессов keyed-by-id живёт на `OdooClient`.

- **Почему**: эргономика + типобезопасность. `ProcessNotFoundError` кидается, если процесс не зарегистрирован в этом client'е (по id).
- **Альтернатива** (Handles as `str`): отвергнута — заставляет пользователя тащить лишнее состояние и теряет тип.
- **Реестр**: `dict[str, OdooProcess]` в `OdooClient`, не глобальный. Несколько одновременных `start()` в одном client'е разрешены (каждый со своим uuid, отдельной registry-записью).

### D2. `server.start()` принимает типизированный `StartConfig` (msgspec.Struct)

SDK сам формирует `[executable, *args]` по полям `StartConfig`. Поля сверяются по `odoo/tools/config.py` 19.0 на этапе tasks.

- **Почему**: требование пользователя — "нельзя ошибиться при вводе". `Literal`-типы где применимо (`log_level`, `http_interface`-constraint), `list[str]` для путей, `int` для портов.
- **Альтернатива** (Passthrough `list[str]` как у `run()`): отвергнута пользователем — `run()` остаётся raw passthrough для произвольных CLI-команд, `start()` — высоуровневый с типизированными knobs.
- **Минимальный набор полей** (precise list — в tasks после сверки с `config.py`): `http_port`, `http_interface`, `config_path`, `addons_path`, `data_dir`, `dbfilter`, `workers`, `max_cron_threads`, `log_level`, `load_language`, `dev_mode`. Лишние поля не добавляем (YAGNI).
- `cwd` и `env` передаются как отдельные kwargs `start(config, *, cwd=None, env=None)`, **не** в `StartConfig` — они surround запуск процесса, не Odoo CLI.

### D3. Маскирование `master_pwd` — кастомный `__repr__` на `OdooClientConfig`

`msgspec.Struct` автогенерирует `__repr__`; мы переопределяем его на `OdooClientConfig`, выводя `master_pwd=<redacted>`. Пароль хранится как обычное поле (нужно для HTTP-запросов), но никогда не попадает в `repr`/исключения/логи. Дополнительно: исключения SDK никогда не включают `master_pwd` в свои сообщения; `__repr__` `OdooClient` также не выводит config.

- **Почему**: одна точка контроля, проще чем `SecretStr`-обёртка.
- **Альтернатива** (SecretStr-аналог msgspec-struct): отвергнута — лишний тип, лишняя间接енность.

### D4. HTTP-клиент — `httpx`

Единственная внешняя зависимость кроме `msgspec`. Используется sync API (`httpx.Client`): `stream()` для `backup()`, `post(..., files=...)` для `restore()`, `post(json=...)` для `list()/drop()`. Basic auth `admin:master_pwd` для database endpoints.

- **Почему**: ~30 строк потокового бэкапа + multipart restore вместо ~150 на `urllib`. Streaming + multipart + basic auth одна библиотека покрывает лаконично.
- **Альтернатива** (`urllib` stdlib): отвергнута — кодтаскаль, особенно multipart upload файла на диск.
- **Timeout**: httpx `Timeout` per-request, дефолт из `OdooClientConfig.http_timeout`.

### D5. Платформенный кэш-каталог — stdlib branching, без `platformdirs`

```python
def _default_backup_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "odoo-instance-sdk" / "backups"
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "odoo-instance-sdk" / "backups"
```

- **Почему**: ~5 строк, нет новой зависимости. Покрывает POSIX+Windows для первой версии.
- **Альтернатива** (`platformdirs`): отвергнута — избыточно для SDK этого диаметра; edge-кейсы (`HOME` нету, *BSD) можно добавить потом без breaking change функции.
- Каталог создаётся (`mkdir(parents=True, exist_ok=True)`) при первом `backup()` без `dest`.

### D6. Local-only guard по `client.config.base_url`

Проверка выполняется **до** HTTP-запроса для `restore()` и `drop()`. Парсим `base_url` через `urllib.parse.urlsplit`, берём `hostname`; если это IP — `ipaddress.ip_address(hostname)` и проверка `127.0.0.0/8` + `::1`; если DNS — сравнение с `localhost` (case-insensitive). Неотключаемая — нет параметра `allow_remote`.

- **Почему**: spec требует "до отправки HTTP-запроса и не должна отключаться". Источник URL — клиент (а не `BackupArtifact.source_base_url`), подтверждено пользователем в explore.
- **Альтернатива** (проверять оба URL — клиента и артефакта): отвергнута — артефакт может приехать с remote (`backup()` на remote разрешён), мы защищаем target восстановления.

```
Local-only guard
┌─────────────────────────────────────────────┐
│  base_url.hostname ∈ {localhost, 127.0.0.0/8, ::1}
├─────────────────────────────────────────────┤
│  backup() ✓  list() ✓  restore() ✗  drop() ✗ │
└─────────────────────────────────────────────┘
При нарушении → RemoteInstanceError (typed)
```

### D7. `BackupArtifact` — объект с абсолютным путём; имя файла `<dbname>.<format>`

`BackupArtifact` (msgspec.Struct): `path: Path` (абсолютный, резолвится через `.resolve()`), `source_db: str`, `format: Literal["zip","dump"]`, `has_filestore: bool`, `source_base_url: str`.

- Имя файла: `<dbname>.<format>`. Если в каталоге назначения уже есть такой файл — перезаписываем (Odoo-tooling конвенция).
- **Почему** Content-Disposition был бы магией; `<dbname>.<format>` — детерминированно и читаемо. Content-Disposition — опциональное улучшение в будущей версии.
- **Альтернатива** (Content-Disposition): отвергнута для v0.1 — лишняя недетерминированность.

### D8. Поведение `drop`/`restore` при ошибке Odoo — проброс типизированной `DatabaseError`

Odoo 19.0 `drop` на несуществующую базу и `restore` на существующую возвращают HTTP-ошибку с сообщением. SDK не переинтерпретирует: парсит тело ответа, кидает `DatabaseError(status_code, message, body)`. Идемпотентность не делается.

### D9. Группа процессов — POSIX primary, Windows fallback

`start()` запускает процесс с `start_new_session=True` (POSIX). `stop()`:
1. `os.killpg(os.getpgid(pid), signal.SIGTERM)`.
2. `proc.wait(timeout=...)`.
3. При таймауте — `os.killpg(..., SIGKILL)` + повторный wait.

Windows (`start_new_session` no-op): `stop()` использует `subprocess.run(["taskkill", "/T", "/PID", str(pid)])` для древовидного убийства. Basic wait на самом `proc` объект.

- **Почему**: avoids добавления `psutil`. POSIX — primary (большинство dev-машин Linux/macOS), Windows — best-effort для первой версии.
- **Альтернатива** (`psutil`): отвергнута — новая зависимость ради Windows cleanup; spec не требует полной proc-tree эквивалентности.
- **Идемпотентный stop**: повторный `stop()` на уже завершённом процессе не кидает ошибку (spec требует). Если PID уже не существует (ESRCH) — тихо возвращаемся.

### D10. `wait_ready()` flow

```
loop until overall_timeout:
  if linked process exited → raise ProcessExitedBeforeReady
  GET /web/health?db_server_status=true
  ├─ HTTP 200 + body.status == "pass" → return ReadinessResult(ok=True, ...)
  ├─ HTTP 200 + status != "pass" → sleep poll_interval, retry
  ├─ HTTP non-200 / network err      → sleep poll_interval, retry
                                   (Odoo еще грузится / endpoint ещё не открыт)
overall_timeout expired → raise ReadinessTimeoutError
```

- `poll_interval` = 1s по умолчанию (константа пакета, не настраивается в v0.1).
- `overall_timeout` параметр метода, отдельный от `http_timeout` в `OdooClientConfig` (более длинный, ~30s дефолт).
- Связанный процесс: в каждой итерации проверяем `proc.status() == exited` через реестр client'а. Если dead — немедленно `ProcessExitedBeforeReady(proc)`.

### D11. `exists()` через `list()`

`database.exists(name)` → `name in database.list()`. Отдельного HTTP-метода нет (spec). `list()` уже кэшировать в v0.1 не будем — каждый `exists()` делает один HTTP-запрос. YAGNI кэша.

### D12. Структура репозитория

```
odoo-instance-sdk/
├── pyproject.toml         (uv, mypy strict, ruff,deps,entry-points)
├── ruff.toml              (по multica-py, known-first-party=["odoo_instance_sdk"])
├── README.md
├── LICENSE
├── src/odoo_instance_sdk/
│   ├── __init__.py        (public exports: OdooClient, all models, exceptions)
│   ├── client.py          (OdooClient, OdooClientConfig)
│   ├── _registry.py       (process registry, per-client)
│   ├── server.py          (ServerResource, StartConfig, run/start/stop/status/wait_ready)
│   ├── database.py        (DatabaseResource, backup/restore/drop/list/exists)
│   ├── _health.py         (readiness polling; isolate /web/health logic)
│   ├── _local_guard.py    (local-only URL guard)
│   ├── _platform_cache.py (default backup dir; stdlib branching)
│   ├── models.py          (all msgspec.Struct public types)
│   └── exceptions.py      (typed exception hierarchy)
└── tests/
    └── (минимальные __main__-style self-checks; без фреймворков в v0.1)
```

- `_`-префикс на внутренних модулях — контракт, что они не public API.
- Все модели и исключения в отдельных модулях для re-export через `__init__.py`.
- `tests/` — без фреймворка в v0.1 (assert-based `__main__` самопроверки); фреймворк добавим когда появится réal need (YAGNI).

### D13. Зависимости и dev-зависимости

Runtime: `httpx`, `msgspec`.
Dev: `mypy`, `ruff`.
Никаких `platformdirs`, `psutil`, `pytest` в v0.1.

### D14. Ruff-конфиг

Cкопирован `multica-py/ruff.toml` (target-version py312, line-length 100, набор lint-правил и ignores). Адаптировано:

```toml
[lint.isort]
known-first-party = ["odoo_instance_sdk"]
```

Odoo-модульных правил в исходном ruff.toml нет — ничего больше отрезать не нужно.

### D15. Версионирование и публикация

Первая версия `0.1.0` (pre-1.0 — API может меняться). PyPI: `wheel` + `sdist`. `uv tool install odoo-instance-sdk` — основная установка. `uv add odoo-instance-sdk` — как библиотечная зависимость. Метаданные: license MIT (как в репо), README, версия в `pyproject.toml` (`version = "0.1.0"`; static, не dynamic).

## Risks / Trade-offs

- **[Risk] `StartConfig` покроет не все нужные поля Odoo 19.0** → Mitigation: сверка с `odoo/tools/config.py` 19.0 в первой задаче tasks; список полей frozen в design-обсуждении, не "延伸 по запросу".
- **[Risk] Windows `taskkill /T` не убивает properly** → Mitigation: документировать POSIX-primary в README; Windows поддерживается best-effort в v0.1, full-equivalence proc-tree kill — future work (с `psutil`, если потребуется).
- **[Risk] `_default_backup_dir()` падает на системах без `HOME`** → Mitigation: env-fallbacks уже в коде; tail-случаи (контейнер с `HOME` unset) — документируем. В крайнем кидаем typed `CacheDirError`.
- **[Risk] Формат ответа `/web/health` изменился в 19.0** → Mitigation: сверка с `addons/web/controllers/home.py` 19.0 в tasks; парсим `{"status": "pass"}`; неизвестные поля игнорируем.
- **[Risk] httpx может ввести breaking change** → Mitigation: pin major в `pyproject.toml` (`httpx>=0.27,<1.0`).
- **[Trade-off] no-test-framework в v0.1** → быстро шипим; переключение на pytest потом — отдельный change, не breaking.

## Migration Plan

Это первая версия — миграции нет. После выпуска `0.1.0` любые breaking-change будут через major bump (когда появится 1.0) или pre-1.0 через minor bump с заметным changelog.

## Open Questions

(нет — все 12 зазоров из explore закрыты и приняты пользователем)