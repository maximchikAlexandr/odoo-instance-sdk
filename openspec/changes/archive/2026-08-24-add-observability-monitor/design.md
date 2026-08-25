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
    catalog_path: Path | None = None
    process_provider: ProcessProvider | None = None
    git_provider: GitProvider | None = None
    docker_provider: DockerProvider | None = None
```

In-memory cache живёт в mutable `dict` field (`field(default_factory=dict, hash=False, compare=False)`); frozen dataclass не мешает мутировать содержимое dict. Cache TTLs — hardcoded константы `EXPENSIVE_TTL_SECONDS = 15.0`, `CLUSTER_STATUS_TTL_SECONDS = 5.0`; без настраиваемого поля.

Internal test Protocols (не публичные, не generic plugin architecture):

```python
class ProcessProvider(Protocol):
    def collect(
        self, root_pid: int, create_time: float, *, prev_cpu_point: object | None
    ) -> ProcessTreeResult | None: ...

class GitProvider(Protocol):
    def collect(self, worktree: Path) -> GitActivity: ...

class DockerProvider(Protocol):
    def inspect_stats(self, container_ids: tuple[str, ...]) -> dict[str, ClusterResourceSnapshot]: ...
```

Default `None` → collector использует `internal/process_metrics.py`, `internal/git_activity.py`, `internal/cluster_resources.py`. `EnvironmentMonitor` — единственная public реализация; не оборачивать в ABC/Protocol с одним impl.

`snapshot(project_id: str | None = None) -> Snapshot` синхронный. `watch(interval: float = 2.0, project_id: str | None = None)` — async generator поверх `snapshot()` + `asyncio.sleep(interval)`. FastAPI `GET /api/v1/snapshot` вызывает `snapshot()` синхронно в handler.

### D2: Snapshot models — frozen `msgspec.Struct`

Все public snapshot models живут только в `models.py` (не выделять `monitor_models.py`). Полные `field: type` списки — единственный канон; см. `specs/environment-monitor/spec.md` Requirement "Canonical snapshot types". `ProcessTreeResult` — internal dataclass в `internal/process_metrics.py`, не export, не часть snapshot JSON. `EnvironmentSnapshot.project_id: str` связывает flat `environments` с `projects`. FastAPI encode только через `msgspec.json.encode(snapshot)` (не Pydantic).
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

`internal/process_metrics.py` — internal only:

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessTreeResult:
    child_pids: tuple[int, ...]
    process_count: int
    cpu_percent: float | None
    rss_bytes: int | None

def collect_process_tree(
    root_pid: int, create_time: float, *, prev_cpu_point: object | None
) -> ProcessTreeResult | None:
    ...
```

Returns `None` (collector maps to `RuntimeMetrics.state=STOPPED`, all resource fields null, `child_pids=()`, `process_count=0`) when: PID missing, `create_time` mismatch (`!=` exact float), `NoSuchProcess`, `AccessDenied` on the **root** process, or `ZombieProcess` on the root. Child `AccessDenied`/`ZombieProcess`: skip that child, still return root metrics.

CPU: first call with no `prev_cpu_point` MUST set `cpu_percent=None`; collector stores prev CPU point in-memory keyed by `(pid, create_time)`; subsequent `watch()` iterations produce numeric. `cpu_percent(interval=None)` — non-blocking second sample using stored point, no extra sleep.

Readiness (collector, not `wait_ready`/`poll_health`): after a live `ProcessTreeResult`, one `httpx.get(f"{http_url}/web/health?db_server_status=true", timeout=2.0)`; `ready` iff HTTP 200 and JSON `status == "pass"`; timeout/connect/HTTP/JSON error → `not_ready` with process metrics still populated. Budget per environment is that one 2.0s request.

`psutil` import ленивый; missing → `MonitorExtrasMissingError` with `pip install odoo-instance-sdk[metrics]`.

### D6: Git activity (three-dot diff, Worktrunk semantics)

`internal/git_activity.py`:

- `default_branch` is always `"main"`. Do not read `ProjectConfig` (no such field exists) and do not guess from remotes/`origin/HEAD`.
- Default-branch tip: `git rev-parse --verify main@{upstream}` if that succeeds; else `git rev-parse --verify refs/heads/main`. Git subprocess timeout 10s.
- `git rev-list --count <merge-base>..<HEAD>` = ahead; `git rev-list --count HEAD..<merge-base>` = behind; merge-base via `git merge-base <default-tip> HEAD`.
- `git diff --numstat <merge-base>...HEAD` → sum added/deleted text lines; binary files (`-` in numstat) skipped (contribute 0).
- no-common-ancestor (`git merge-base` exit ≠ 0) → `state="orphan"`, `ahead=behind=diff=None`.
- Any other Git CLI failure (timeout, not a repo, missing worktree) → same orphan shape: `GitActivity(default_branch="main", head_sha=None, short_sha=None, branch="unknown", ahead=None, behind=None, diff=None, state=ORPHAN)`. `complete` is not a GitActivity field.
- `state`: `clean` (0/0), `ahead` (>0/0), `behind` (0/>0), `diverged` (>0/>0), `orphan`.
- Bounded cache по `(worktree_path, HEAD SHA, default-branch SHA)`, TTL 15s.

### D7: Storage footprint (disk ownership)

`internal/storage_footprint.py`:

- Directory size: run `du -sb <path>` iff `shutil.which("du")` is not None and the subprocess exits 0 with stdout that parses as a single integer (timeout 10s). Otherwise walk with `os.walk(followlinks=False)` + `Path.stat().st_size`, skip symlink directories, dedup by `Path.resolve()`. No third path. On timeout or OSError for an owned component: that field `None`, `complete=False`.
- owned venv (`python_environment_owned=true`): size of `python_environment_path` via the same directory-size rule.
- owned DB (`db_mode="copy"`, journal-owned `target_db`): `internal/postgres_size.py::database_size_bytes(*, host, port, user, password, database_name, timeout=10.0) -> int | None` using the same `psql -c` subprocess pattern as `DatabaseResource._verify_database_via_psql` (`PGPASSWORD` in env, never argv; `SELECT pg_database_size('escaped')`; `-t -A`; timeout 10s). Never Odoo HTTP, never `DatabaseResource` public API, never psycopg. Connection params MUST come from the environment generated `odoo.conf` via `StartConfig.from_odoo_config` (`db_host` default `127.0.0.1`, `db_port` default `5432`, `db_user`, `db_password`). Failure → `postgres_bytes=None`, `complete=False`. Filestore via `validate_filestore_containment(data_dir, db_name)` then directory-size on that path.
- other files: generated config, dependency lock, local logs/cache/artifacts in environment root not counted above.
- `total_bytes` — sum of known components (0 for missing optional); `complete=False` if any **owned** component is unavailable.
- Shared/source DB, external venv, shared Git object store, shared cluster volume — excluded.
- Bounded cache по `environment_id`, TTL 15s.
### D8: Cluster container identity + resources (Docker inspect/stats)

`PostgresCluster.resource_snapshot() -> ClusterResourceSnapshot | None` — новый public read-only метод на `PostgresCluster`. Per-cluster single-container inspect делегирует в internal `internal/cluster_resources.py` batch helper (не отдельная public abstraction, а batch-оптимизация): helper выполняет batch `docker inspect`/`docker stats --no-stream` для нескольких containers одним call и кеширует результаты по `container_id` (shared между всеми `PostgresCluster` instances одного `EnvironmentMonitor` snapshot pass). `PostgresCluster.resource_snapshot()` для одного кластера (без collector context) вызывает helper с одним container — helper переиспользует existing `ComposeRunner` (`internal/postgres_compose.py`). Это выбранный design, не альтернатива: public surface — `PostgresCluster.resource_snapshot()`; internal batch helper существует и covered/tested через `test_cluster_resources.py`.

Collector never passes the monitor opaque id (`project_{repo_key}`) into compose naming. Compose project name stays `odcli_pg_{repo_key}` via `PostgresCluster.from_project(repository_root)` / existing `compose_project_name(_project_id)` where `_project_id` is unprefixed `repo_key`. Service name is `postgres`.

- Compose: `docker inspect <container-id>` (Compose project name + service `postgres`) → container ID/name/image/init PID. PID scope: `sys.platform == "darwin"` → `docker_vm`; else `host`; stopped/missing → `unavailable`.
- `docker stats --no-stream --format json <id>` → CPU percent, memory usage/limit. Volume usage: `docker inspect` `Mounts` + `docker system df -v` (только если Docker предоставляет без privileged host traversal; иначе `None`).
- External: `None` (не инспектируется).
- Stopped vs missing compose: `unavailability_reason="stopped"` iff `status() == STOPPED`; `"missing"` iff `status()` is not `STOPPED` and container ID cannot be resolved. `sampled_at` is one UTC datetime copied to metrics/resource/snapshot; all None when metrics is None.
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

`internal/serve.py` only (not public, not `resources/serve.py`):

- FastAPI app, `GET /api/v1/snapshot` (HTTP 200, `Content-Type: application/json`, body `msgspec.json.encode(snapshot)`), `GET /healthz` (`{"status":"ok"}` 200). Catalog `MonitorError` → HTTP 500 with JSON `{"error": "<redacted>"}`. Query `?project_id=` forwarded to `snapshot(project_id=...)`.
- Default UI mode: mount `StaticFiles` from `importlib.resources` path `odoo_instance_sdk/web/dist/`.
- Headless: only API routes, no static mount.
- `uvicorn.run(app, host, port)`.
- Import guard: missing `fastapi`/`uvicorn` → CLI `odcli monitor` exits 1 with `pip install odoo-instance-sdk[dashboard]` (do not construct the app).
- Browser open: `webbrowser.open(url)` unless `--no-open` or `--headless`.
- Bind default `127.0.0.1`. Port: if `--port` set, bind that port (fail exit 1 if occupied). If `--port` omitted: try `8069`; if occupied, scan `8100` through `8120` inclusive for the first free loopback port; never bind `8070–8099` via auto-select. If `8069` and `8100–8120` are all occupied, exit 1 with a message naming that range.

### D11: React + Mantine UI

`src/odoo_instance_sdk/web/` — Vite + React + Mantine. Build output `dist/` shipped via `uv_build` including `odoo_instance_sdk/web/dist/**` as package data. One responsive page, no router/Redux/query lib:

- project selector (Mantine `Select`): "All projects" + each `ProjectSummary`.
- cluster card per displayed project (Mantine `Card` + `Badge`): mode/state/container ID+PID+scope/CPU/RAM/volume.
- one `Card` per `EnvironmentSnapshot`: project, name, branch+short SHA, database, port (`runtime.http_port` if live else `allocated_http_port`), `lifecycle_state` + `runtime.state` badges; Git ahead/behind/`+added/-deleted`; Total disk + breakdown; Odoo process root PID + optional worker PIDs + CPU/RAM for a live tree, `—` for stopped; **Open Odoo** active only when `runtime.state=="ready"`, opens `http_url`.
- loading, API error, empty-catalog states.
- polling `fetch('/api/v1/snapshot')` every 2000 ms (`setInterval(..., 2000)`).
- Mantine components: `Select`, `SimpleGrid`, `Card`, `Badge`, `Text`, `Button`, `Progress`. No chart dependency, no router, no Redux/query lib.

Build: `npm ci && npm run build` in `src/odoo_instance_sdk/web/` (commit `package-lock.json`; do not use pnpm/yarn). CI builds assets; `uv build` includes `dist/`. Node.js is not required for the installed package.

### D12: CLI `env list` grouping + `postgres status` extension + `monitor` command

`internal/cli_env.py::env_list` MUST call `EnvironmentMonitor.snapshot()` (no parallel collector helper). `_print_env_table` becomes grouped output with a project header. Flags `--all` / `--all-projects` / `--json` stay.

`cli.py::postgres status` MUST call `cluster.status()` and `cluster.resource_snapshot()`, then emit `ClusterSnapshot` fields (no parallel Docker helper).

`cli.py::monitor` — `--headless`/`--host`/`--port`/`--no-open`, starts FastAPI via `internal/serve.py`. Import guard for `dashboard` extra.

### D13: Optional extras + packaging

`pyproject.toml`:

```toml
[project.optional-dependencies]
metrics = ["psutil>=5.9,<7"]
dashboard = ["odoo-instance-sdk[metrics]", "fastapi>=0.141,<1.0", "starlette>=1.3.1,<2.0", "uvicorn>=0.30,<1.0"]
```

Built React assets включаются в package via `uv_build` including `src/odoo_instance_sdk/web/dist/**`. Coverage regexs: добавить `monitor` regex (`odoo_instance_sdk/(resources/monitor\\.py|internal/(process_metrics|git_activity|storage_footprint|cluster_resources|postgres_size|serve)\\.py)$`) + thresholds (80 line / 70 branch).

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
- `tests/unit/test_storage_footprint.py`: owned/reused venv, shared/copy DB, `du` vs walk fallback, `postgres_size` psql failure → complete=False, symlinks, incomplete.
- `tests/unit/test_cluster_resources.py`: fake Docker inspect/stats, PID scope, batch, external/stopped.
- `tests/unit/test_serve.py`: FastAPI routes (`/api/v1/snapshot`, `/healthz`), project filter, headless vs UI, missing extra guard.
- `tests/unit/test_cli_monitor.py`: `odcli monitor --headless`, port auto-select, missing extra hint.
- `tests/unit/test_cli_env_list_grouping.py`: project header, cluster summary, stopped row, JSON parity.
- `tests/unit/test_cli_postgres_status_resources.py`: container fields, external/stopped/docker-unavailable, parity.
- `tests/unit/test_catalog_runtime_record.py`: schema v9 migration, upsert/clear, read-only.
- `tests/unit/test_run_foreground_runtime_identity.py`: persist after spawn, clear in `finally` (normal/crash/Ctrl+C), manual instance no persist, `psutil` fallback.
- `tests/integration/test_monitor_smoke.py` (opt-in `integration` marker, skip без Docker/psutil): two projects, several environments, часть stopped; verify selector/cards/headless API и Open Odoo.

Frontend production TypeScript build — `npm run build` в CI (no UI test framework in MVP).

### D16: Resolved placement

- `EnvironmentMonitor` — `resources/monitor.py`, re-exported from `__init__.py`.
- Public snapshot models — `models.py` only. Do not add `monitor_models.py`.
- FastAPI app — `internal/serve.py` only.
- Directory size — D7 (`du -sb` then `os.walk`).
- DB size — `internal/postgres_size.py` via `psql` (D7).
- Monitor opaque id — `project_{repo_key(...)}`. Compose/PostgresCluster id — unprefixed `repo_key` (D8).

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
