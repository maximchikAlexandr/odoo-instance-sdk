## Context

Issue #11 требует read-only observability surface поверх существующих primitives. Сегодня:

- `BackupCatalog` (`storage/backup_catalog.py`, schema v8) хранит environments всех проектов: `id`, `name`, `repository_root`, `git_common_dir`, `branch`, `worktree_path`, `generated_config_path`, `python_environment_path`, `python_environment_owned`, `dependency_lock_path`, `db_mode`, `source_db_name`, `target_db_name`, `backup_id`, `runtime_json` (JSON-encoded `odoo_bin`/`runtime_cwd`), `state`, `created_at`, `last_used_at`, `removed_at`, `last_error`. HTTP `http_interface`/`http_port` убраны из catalog в v8 и читаются из generated `odoo.conf` (single source of truth). `list_environments(git_common_dir, include_removed)` — read-only API.
- `OdooClient` хранит `Popen` handles в in-memory process registry одного Python-процесса; `odcli run` foreground, поэтому отдельный backend не может получить handle. Odoo PID не сохранён в catalog; PID reuse небезопасен.
- `PostgresCluster` (`resources/postgres.py`, frozen dataclass) знает project ownership/mode/state и deterministic Compose project (`odcli_pg_<project-id>`), `status()` (read-only, TCP probe для external, Compose `ps`/`pg_isready` для compose), `ensure_running()`, `stop()`, `resolve_image_digest`/`approve_image`. Не возвращает container identity, PID или resource snapshot. Compose runner (`internal/postgres_compose.py`) — `Protocol` + `SubprocessComposeRunner`, injection для тестов.
- `internal/repo_key.py::repo_key(repository_root, git_common_dir)` — стабильный project-id (slug + 8-hex из git_common_dir).
- `internal/git_worktree.py` — Git CLI wrapper (rev-parse, worktree list porcelain, worktree is dirty).
- `internal/address.py::probe_address` — loopback TCP probe.
- `internal/cli_output.py::emit_json_envelope`/`fail` — JSON envelope v1.
- `internal/cli_env.py` — `env list` command, `_reconcile_environment`, `_print_env_table`.
- `internal/paths.py` — platformdirs roots (`get_data_root`, `get_state_root`, `get_catalog_path`, `get_environments_root`, `get_project_postgres_dir`).
- `internal/port_allocation.py` — cross-project port allocation из manifests + generated configs.
- `models.py` — `msgspec.Struct` models, `StartConfig.from_odoo_config`, `PostgresClusterState` StrEnum, redacted `__repr__`.
- `pyproject.toml` — core deps `httpx`/`msgspec`/`platformdirs`/`click`/`json5`; build backend `uv_build`; coverage regexs/thresholds; mypy strict; no extras today.

Стек: Python >=3.12, stdlib + `subprocess` для Docker/Git, `msgspec` для typed models, `psutil` будет новой опциональной зависимостью. React+Mantine+Vite — build-time frontend. FastAPI+Uvicorn — опциональный backend.

## Goals / Non-Goals

**Goals:**
- Один `EnvironmentMonitor` collector без interfaces/factories, переиспользующий catalog + `PostgresCluster` + Docker CLI + `psutil` + Git CLI.
- Typed immutable `msgspec.Struct` snapshot, consumed by SDK / FastAPI / React UI без дублирования расчёта.
- PID-safe runtime identity: catalog `environment_runtime` table + PID+`create_time` verification.
- Read-only cluster container identity + resources (Docker inspect/stats, PID scope, CPU/RAM/volume).
- Bounded caching: CPU/RAM не кешируется, expensive Git/storage/Docker stats — TTL 15s.
- Component failure isolation: ошибка одного environment/cluster не роняет snapshot.
- No secrets/absolute paths/raw Docker payload в snapshot.
- Optional extras `metrics`/`dashboard`; built React assets shipped, no Node.js at runtime.

**Non-Goals:**
- Control operations (start/stop/delete); background supervisor; изменение foreground `odcli run` семантики (только persist/clear runtime identity).
- Historical metrics/charts/Prometheus/Grafana; SSE/WebSocket; event bus; persistent daemon.
- Распределение shared cluster CPU/RAM/volume между environments; individual PostgreSQL backend PIDs; inspection произвольного external PG.
- Generic provider/plugin architecture; второй catalog; docker-py; Pydantic DTO.
- Встроенные multi-user auth/TLS/control-plane; CORS; arbitrary external host inspection.

## Decisions

### D1: Один `EnvironmentMonitor`, frozen dataclass, internal dependencies

`EnvironmentMonitor` — `@dataclass(frozen=True, slots=True, kw_only=True)` в `resources/monitor.py` (re-exported из `__init__.py`). Зависимости resolved lazily из existing internal helpers; default constructor `EnvironmentMonitor()` работает без аргументов. Optional keyword fields для injection в тестах:

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentMonitor:
    catalog_path: Path | None = None              # default get_catalog_path()
    process_provider: ProcessProvider | None = None  # fake psutil for tests
    git_provider: GitProvider | None = None        # fake git for tests
    docker_provider: DockerProvider | None = None  # fake docker inspect/stats
```

Cache TTLs — hardcoded константы (15s для expensive sections, 5s для cluster `status()`); без настраиваемого поля (YAGNI — issue не требует configurable cache). Providers — small internal Protocols для тест-инъекции; не публичные, не generic plugin architecture (единственная реализация default). `EnvironmentMonitor` — единственная public реализация; не оборачивать в ABC/Protocol с одним impl.

`snapshot(project_id=None) -> Snapshot` синхронный (psutil/Docker/Git — blocking subprocess); `watch()` — async generator поверх `snapshot()` + `asyncio.sleep`. FastAPI endpoint вызывает `snapshot()` синхронно в handler (sufficient для MVP; async background refresh out of scope).

### D2: Snapshot models — frozen `msgspec.Struct`

Все snapshot models в `models.py` (file-organization deferral — см. D16):

```python
class RuntimeState(enum.StrEnum):
    STOPPED = "stopped"
    READY = "ready"
    NOT_READY = "not_ready"

class GitActivityState(enum.StrEnum):
    CLEAN = "clean"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    ORPHAN = "orphan"

class PidScope(enum.StrEnum):
    HOST = "host"
    DOCKER_VM = "docker_vm"
    UNAVAILABLE = "unavailable"

class GitDiff(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    added: int
    deleted: int

class GitActivity(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True): ...
class PythonEnvFootprint(msgspec.Struct, ...): ...
class DatabaseFootprint(msgspec.Struct, ...): ...
class StorageFootprint(msgspec.Struct, ...): ...
class ProcessTreeMetrics(msgspec.Struct, ...): ...   # child_pids/process_count
class RuntimeMetrics(msgspec.Struct, ...): ...        # state/root_pid/cpu_percent/rss_bytes/started_at/http_url/...
class ClusterContainer(msgspec.Struct, ...): ...     # id/name/image/pid/pid_scope
class ClusterMetrics(msgspec.Struct, ...): ...        # cpu_percent/memory_*/volume_usage_bytes/sampled_at
class ClusterEndpoint(msgspec.Struct, ...): ...       # host/port (redacted)
class ClusterSnapshot(msgspec.Struct, ...): ...      # mode/owned/state/endpoint/container/metrics/unavailability_reason/sampled_at
class EnvironmentSnapshot(msgspec.Struct, ...): ...  # id/name/branch/short_sha/db_mode/database/runtime/git/storage
class ProjectSummary(msgspec.Struct, ...): ...       # id/name/display_hint/environment_count/cluster
class Snapshot(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    schema_version: int
    generated_at: datetime
    projects: tuple[ProjectSummary, ...]
    environments: tuple[EnvironmentSnapshot, ...]
```

`kw_only=True` + `frozen=True` + `forbid_unknown_fields=True` (как existing `Backup`/`Database`). FastAPI encode через `msgspec.json.encode(snapshot)` (не Pydantic).

### D3: Catalog current-runtime record (schema v8 → v9)

Новая таблица `environment_runtime` (одна строка на `environment_id`, upsert). Migration v8→v9 additive (`CREATE TABLE IF NOT EXISTS`):

```sql
CREATE TABLE IF NOT EXISTS environment_runtime (
    environment_id TEXT PRIMARY KEY REFERENCES environments(id),
    root_pid INTEGER NOT NULL,
    create_time REAL NOT NULL,
    started_at TEXT NOT NULL,
    checkout_branch TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    http_url TEXT NOT NULL,
    http_port INTEGER NOT NULL,
    database_name TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

`CURRENT_SCHEMA_VERSION = 9`. `BackupCatalog` получает `get_environment_runtime(env_id)`, `list_environment_runtimes()`, `upsert_environment_runtime(...)`, `clear_environment_runtime(env_id)`. Collector и `env list` только читают; `run_foreground` пишет/чистит. PID reuse: collector проверяет `psutil.Process(pid).create_time() == recorded_create_time` и `psutil.pid_exists(pid)`; несовпадение → `stopped`, catalog row не трогается (cleanup — ответственность `run_foreground` `finally`).

### D4: `run_foreground` persist/clear runtime identity

`OdooInstance.run_foreground()` (только для instance bound к environment через `from_environment()`):

1. После spawn (после `Popen`) и до блокировки wait:
   - `root_pid = proc.pid`;
   - `create_time = psutil.Process(root_pid).create_time()` если `psutil` available, иначе `time.time()` (fallback; `run_foreground` сам не требует `psutil`);
   - `started_at = datetime.now(UTC).isoformat()`;
   - `checkout_branch`/`commit_sha` — из `git rev-parse --abbrev-ref HEAD` + `git rev-parse HEAD` в worktree (existing `internal/git_worktree.py`);
   - `http_url`/`http_port` — из `StartConfig.http_interface`/`http_port`;
   - `database_name` — `StartConfig.db_name` (target DB для copy, source DB для shared);
   - `catalog.upsert_environment_runtime(environment_id, ...)`.
2. В `finally` (normal/crash/Ctrl+C/exception): `catalog.clear_environment_runtime(environment_id)`, best-effort (ошибка логируется в stderr, не маскирует exit code/exception).

Manual instance (`instance(base_url=...)`/`from_config()`) — без `environment_id`, runtime identity НЕ пишется. `shell()`/`run_shell_script()`/`start()`/`stop()` — без persist (только `run_foreground` длительный foreground server).

### D5: Odoo process tree metrics (psutil)

`internal/process_metrics.py`:

```python
def collect_process_tree(root_pid: int, create_time: float, *, prev_cpu_point: dict | None) -> ProcessTreeMetrics:
    proc = psutil.Process(root_pid)
    if proc.create_time() != create_time:
        return ProcessTreeMetrics(state="stopped", ...)  # PID reuse
    children = proc.children(recursive=True)
    child_pids = tuple(c.pid for c in children)
    process_count = 1 + len(child_pids)
    rss = sum(p.memory_info().rss for p in [proc, *children] if p.status() not in ZOMBIE)
    cpu_percent = sum(p.cpu_percent() for p in [proc, *children])  # requires two samples
    ...
```

CPU requires two samples с интервалом; first `snapshot()` returns `cpu_percent=None`, subsequent `watch()` iterations produce numeric. Collector хранит prev CPU point в памяти по `(pid, create_time)`. `NoSuchProcess`/`AccessDenied`/`ZombieProcess` изолируются на один environment. `psutil` import ленивый; missing → `MonitorExtrasMissingError` с actionable hint.

### D6: Git activity (three-dot diff, Worktrunk semantics)

`internal/git_activity.py`:

- `default_branch` — из project manifest (если есть поле) или fallback `main` (явный, стабильный; не гадается из remote refs).
- upstream tip если доступен (`git rev-parse --verify <default>@{upstream}`), fallback локальная default branch.
- `git rev-list --count <merge-base>..<HEAD>` = ahead; `git rev-list --count HEAD..<merge-base>` = behind; merge-base via `git merge-base <default-tip> HEAD`.
- `git diff --numstat <merge-base>...HEAD` → sum added/deleted text lines; binary files (`-` в numstat) пропускаются.
- no-common-ancestor → `git merge-base` fails → `state="orphan"`, counts `None`.
- `state`: `clean` (0/0), `ahead` (>0/0), `behind` (0/>0), `diverged` (>0/>0), `orphan`.
- Bounded cache по `(worktree_path, HEAD SHA, default-branch SHA)`, TTL 15s.

### D7: Storage footprint (disk ownership)

`internal/storage_footprint.py`:

- worktree: `du -sb <worktree>` (или stdlib `os.walk` + `Path.stat().st_size`, без следования symlinks за owned roots; дедупликация по realpath).
- owned venv (`python_environment_owned=true`): size of `python_environment_path`.
- owned DB (`db_mode="copy"`, journal-owned `target_db`): PostgreSQL logical size via read-only `pg_database_size(target_db)` (через existing `DatabaseResource`/SQL connection, не general cluster/volume); filestore via existing containment checks (`<data_dir>/filestore/<db>`).
- other files: generated config, dependency lock, local logs/cache/artifacts в environment root, не вошедшие выше.
- `total_bytes` — сумма; `complete=False` если любой owned component недоступен.
- Shared/source DB, external venv, shared Git object store, shared cluster volume — исключены.
- Bounded cache по `environment_id`, TTL 15s.

### D8: Cluster container identity + resources (Docker inspect/stats)

`PostgresCluster.resource_snapshot() -> ClusterResourceSnapshot | None` — новый public read-only метод на `PostgresCluster`. Per-cluster single-container inspect делегирует в internal `internal/cluster_resources.py` batch helper (не отдельная public abstraction, а batch-оптимизация): helper выполняет batch `docker inspect`/`docker stats --no-stream` для нескольких containers одним call и кеширует результаты по `container_id` (shared между всеми `PostgresCluster` instances одного `EnvironmentMonitor` snapshot pass). `PostgresCluster.resource_snapshot()` для одного кластера (без collector context) вызывает helper с одним container — helper переиспользует existing `ComposeRunner` (`internal/postgres_compose.py`). Это выбранный design, не альтернатива: public surface — `PostgresCluster.resource_snapshot()`; internal batch helper существует и covered/tested через `test_cluster_resources.py`.

- Compose: `docker inspect <container-id>` (через Compose project name + service identity) → container ID/name/image/init PID. PID scope: detect platform (`sys.platform == "darwin"` → `docker_vm` для Docker Desktop/Colima; Linux → `host`; stopped/missing → `unavailable`).
- `docker stats --no-stream --format json <id>` → CPU percent, memory usage/limit. Volume usage: `docker inspect` `Mounts` + `docker system df -v` (только если Docker предоставляет без privileged host traversal; иначе `None`).
- External: `None` (не инспектируется).
- Stopped/missing compose: `ClusterResourceSnapshot(container=None, metrics=None, unavailability_reason="stopped"/"missing")` (не `None`, чтобы отличить от external).
- Batch: для нескольких managed projects — один `docker stats --no-stream <id1> <id2> ...` / один `docker inspect <id1> <id2> ...`, не один subprocess на карточку. Один container stats failure не блокирует остальные.

### D9: Bounded caching

`EnvironmentMonitor` хранит in-memory cache (не персистится):

- CPU/RAM: **без кеша** (свежая каждая итерация; first sample `null`).
- Git activity: cache по `(worktree, HEAD SHA, default-tip SHA)`, TTL 15s.
- Storage: cache по `environment_id`, TTL 15s.
- Docker inspect: cache по `container_id`, TTL 15s.
- Docker stats: cache по `container_id`, TTL 15s.
- cluster `status()` (PostgresCluster.status): TTL 5s (чтобы не вызывать Docker/pg_isready на каждом 2s tick).

Новый `EnvironmentMonitor()` — пустой кеш. Cache miss → синхронный пересчёт в `snapshot()` (достаточно для MVP; async background refresh out of scope).

### D10: FastAPI server + SPA mount

`internal/serve.py` (или `resources/serve.py`, не публичный):

- FastAPI app, `GET /api/v1/snapshot` (calls `EnvironmentMonitor().snapshot()`, returns `msgspec.json.encode`), `GET /healthz`.
- Default UI mode: mount собранный React SPA (`StaticFiles` из `odoo_instance_sdk/web/dist/` или equivalent data location).
- Headless: only API routes, no static mount.
- `uvicorn.run(app, host, port)`.
- Import guard: если `fastapi`/`uvicorn` не installed → `MonitorExtrasMissingError`-equivalent, CLI hint.
- Browser open: `webbrowser.open(url)` если не `--no-open` и не `--headless`.
- Port auto-select: если default 8069 занят, следующий free loopback в disjoint range `8100–8120` (не `[8069, 8099]`, зарезервированном для environment checkout — monitor не должен отбирать порт у будущего checkout).

### D11: React + Mantine UI

`src/odoo_instance_sdk/web/` — Vite + React + Mantine project. Build output `dist/` shipped in package (sdist + wheel) via `uv_build` data inclusion (`[tool.uv.build-backend]` data config или `package-data`). Одна responsive страница, без router/Redux/query lib:

- project selector (Mantine `Select`): "All projects" + each `ProjectSummary`.
- cluster card per displayed project (Mantine `Card` + `Badge`): mode/state/container ID+PID+scope/CPU/RAM/volume.
- one `Card` per `EnvironmentSnapshot`: project, name, branch+short SHA, database, port, lifecycle/runtime badges; Git ahead/behind/`+added/-deleted`; Total disk + breakdown (Worktree/Database/Filestore/Python env/Other); Odoo process root PID + optional worker PIDs + CPU/RAM для живого process tree, `—` для stopped; **Open Odoo** active только при `ready`, открывает `http_url`.
- loading, API error, empty-catalog states.
- polling `fetch('/api/v1/snapshot')` ~раз в 2 секунды.
- Mantine components: `Select`, `SimpleGrid`, `Card`, `Badge`, `Text`, `Button`, `Progress`. No chart dependency, no router, no Redux/query lib.

Build: `npm install && npm run build` (или `pnpm`) в `src/odoo_instance_sdk/web/`; output committed/shipped. CI step (out of code scope; build pipeline) builds assets; `uv build` includes them. Node.js не требуется для установленного пакета.

### D12: CLI `env list` grouping + `postgres status` extension + `monitor` command

`internal/cli_env.py::env_list` рефакторится на `EnvironmentMonitor.snapshot()` (или эквивалентный collector helper) для cluster summary + runtime columns; preserves existing `--all`/`--all-projects`/`--json`. `_print_env_table` → grouped output с project header.

`cli.py::postgres status` расширяется read-only container fields через `PostgresCluster.resource_snapshot()` (или collector helper); parity с monitor.

`cli.py::monitor` — новая команда (замена planned `dashboard`), `--headless`/`--host`/`--port`/`--no-open`, запускает FastAPI через `internal/serve.py`. Import guard для `dashboard` extra.

### D13: Optional extras + packaging

`pyproject.toml`:

```toml
[project.optional-dependencies]
metrics = ["psutil>=5.9,<7"]
dashboard = ["odoo-instance-sdk[metrics]", "fastapi>=0.115,<1.0", "uvicorn>=0.30,<1.0"]
```

Built React assets включаются в package via `uv_build` data inclusion (`[tool.uv.build-backend]` data config). Coverage regexs: добавить `monitor` regex (`odoo_instance_sdk/(resources/monitor\\.py|internal/(process_metrics|git_activity|storage_footprint|cluster_resources|serve)\\.py)$`) + thresholds (80/70, новый код).

### D14: Typed errors

`exceptions.py` (наследники `OdooInstanceSdkError`):

```python
class MonitorError(OdooInstanceSdkError): """Base."""
class MonitorExtrasMissingError(MonitorError): """psutil/fastapi/uvicorn not installed."""
```

Component failures изолируются в snapshot (`complete=False`/`unavailability_reason`), не отдельным exception; catalog SQLite error → `MonitorError`. `MonitorSnapshotError` не добавляется (нет concrete scenario, который его raises — Ponytail). Redacted messages (без secrets/absolute paths).

### D15: Tests

- `tests/unit/test_monitor_snapshot.py`: multi-project discovery, stopped/running Odoo, PID reuse, compose/external/stopped cluster, Linux/VM PID scope, Docker stats errors, Git divergence, storage ownership, component failure isolation, redaction.
- `tests/unit/test_process_metrics.py`: fake `psutil.Process`, CPU two-sample, `AccessDenied` isolation.
- `tests/unit/test_git_activity.py`: clean/ahead/behind/diverged/orphan, binary files, stale-local-main fallback.
- `tests/unit/test_storage_footprint.py`: owned/reused venv, shared/copy DB, symlinks, incomplete.
- `tests/unit/test_cluster_resources.py`: fake Docker inspect/stats, PID scope, batch, external/stopped.
- `tests/unit/test_serve.py`: FastAPI routes (`/api/v1/snapshot`, `/healthz`), project filter, headless vs UI, missing extra guard.
- `tests/unit/test_cli_monitor.py`: `odcli monitor --headless`, port auto-select, missing extra hint.
- `tests/unit/test_cli_env_list_grouping.py`: project header, cluster summary, stopped row, JSON parity.
- `tests/unit/test_cli_postgres_status_resources.py`: container fields, external/stopped/docker-unavailable, parity.
- `tests/unit/test_catalog_runtime_record.py`: schema v9 migration, upsert/clear, read-only.
- `tests/unit/test_run_foreground_runtime_identity.py`: persist after spawn, clear in `finally` (normal/crash/Ctrl+C), manual instance no persist, `psutil` fallback.
- `tests/integration/test_monitor_smoke.py` (opt-in `integration` marker, skip без Docker/psutil): two projects, several environments, часть stopped; verify selector/cards/headless API и Open Odoo.

Frontend production TypeScript build — `npm run build` в CI (no UI test framework in MVP).

### D16: Resolved placement/implementation choices

- `EnvironmentMonitor` живёт в `resources/monitor.py` (public primitive, как `resources/postgres.py`), re-exported из `__init__.py` (не `internal/`).
- Snapshot models живут в `models.py` (следуя существующему pattern — все public typed models там). Если `models.py` станет слишком большим, implementer MAY выделить `monitor_models.py` рядом, re-exported из `__init__.py` — это pure file-organization refactor, не behavioral change; spec не диктует имя файла.
- Filesystem size: `du -sb <path>` (native, fast) где доступен; fallback pure-Python `os.walk` + `Path.stat().st_size` на системах без `du`. Spec mandate: no symlink-following за owned roots, realpath dedup, no double counting.
- `pg_database_size(target_db)`: через existing `DatabaseResource` read-only query (без добавления `psycopg`). Если existing `DatabaseResource` не exposes read-only size query, implementer добавляет minimal read-only helper (через existing SQL connection path, не новую dependency); `psql -c` subprocess — last-resort fallback, не primary.

## Risks / Trade-offs

- **`psutil` новая зависимость (extra `metrics`)**: не core, opt-in. Risk: пользователь забывает extra. Mitigation: typed `MonitorExtrasMissingError` с actionable hint; `run_foreground` fallback на `time.time()` (не требует psutil).
- **PID+`create_time` not 100% safe**: extremely rare race если OS reuses PID с тем же `create_time` (clock resolution). Mitigation: documented limitation; `create_time` — best-effort; combined with `psutil.pid_exists` и catalog `finally` cleanup. Acceptable для local dev observability (не security boundary).
- **Docker stats один call для нескольких контейнеров может не вернуть данные по одному**: Mitigation: не ретраить per-container на этом tick; next polling tick соберёт заново; `unavailability_reason="stats_failed"` для affected.
- **Filesystem scan без symlinks может быть медленным на больших worktrees**: Mitigation: bounded cache 15s; `du -sb` (native, fast) preferred over pure-Python `os.walk` где возможно; `complete=False` если timeout.
- **macOS Docker VM PID не macOS PID**: Mitigation: `pid_scope="docker_vm"` явный; UI/CLI показывают scoped PID, не называют macOS PID.
- **React assets в package увеличивает size**: Mitigation: только собранный SPA (gzip-friendly); `uv_build` data inclusion; dashboard extra optional. Acceptable для self-contained local observability.
- **First CPU sample `null`**: документировано; `watch()` после первой итерации numeric. Risk: single `snapshot()` показывает `null` CPU. Mitigation: UI показывает `—` для first sample, numeric после polling.
- **`pg_database_size(target_db)` требует DB connection**: Mitigation: read-only query через existing `DatabaseResource`/SQL; `complete=False` если DB unreachable; не роняет environment.

## Migration Plan

- Catalog schema v8 → v9: additive `environment_runtime` table (`CREATE TABLE IF NOT EXISTS`); idempotent; существующие environments не имеют runtime row (collector = `stopped`). `CURRENT_SCHEMA_VERSION = 9`.
- `run_foreground` начинает persist/clear runtime identity; old catalogs без runtime rows работают (collector = `stopped` для всех, пока не запустят `odcli run`).
- `PostgresCluster.resource_snapshot()` — новый read-only метод; существующие `status`/`ensure_running`/`stop` без изменений.
- `odcli env list`/`postgres status` расширяются additively (новые columns/fields); JSON parity с monitor snapshot.
- Optional extras `metrics`/`dashboard` — новые; core install без изменений.
- Rollback: schema v9 → v8 drop `environment_runtime` (допустимо, runtime rows — transient). `run_foreground` без persist — safe (collector = `stopped`).

## Open Questions

None — все решения зафиксированы в Decisions выше.