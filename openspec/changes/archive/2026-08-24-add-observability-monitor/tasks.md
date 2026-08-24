## 1. Catalog current-runtime record (schema v8 → v9)

- [x] 1.1 Добавить `environment_runtime` table migration в `BackupCatalog._create_schema` (v8→v9, `CREATE TABLE IF NOT EXISTS`, FK на `environments(id)`); `CURRENT_SCHEMA_VERSION = 9`
- [x] 1.2 Добавить `get_environment_runtime(environment_id) -> Row | None` и `list_environment_runtimes() -> list[Row]` (read-only)
- [x] 1.3 Добавить `upsert_environment_runtime(environment_id, *, root_pid, create_time, started_at, checkout_branch, commit_sha, http_url, http_port, database_name) -> None` и `clear_environment_runtime(environment_id) -> None`
- [x] 1.4 Обновить catalog migration tests (version 9, upsert/clear idempotency, FK constraint, read-only API)

## 2. `run_foreground` persist/clear runtime identity

- [x] 2.1 В `OdooInstance.run_foreground()` (только для instance bound к environment через `from_environment()`) persist runtime identity в `environment_runtime` после spawn (root_pid, `psutil.Process(pid).create_time()` или `time.time()` fallback, started_at, branch/commit SHA из worktree, http_url/port, db_name)
- [x] 2.2 В `finally` (normal/crash/Ctrl+C/exception) `clear_environment_runtime(environment_id)` best-effort (ошибка логируется в stderr, не маскирует exit code/exception)
- [x] 2.3 Manual instance (`instance(base_url=...)`/`from_config()`) — без `environment_id`, runtime identity НЕ пишется
- [x] 2.4 `shell()`/`run_shell_script()`/`start()`/`stop()` — без persist (только `run_foreground`)
- [x] 2.5 `psutil` import ленивый; `run_foreground` сам не требует `psutil` (fallback `time.time()`)

## 3. Snapshot typed models

- [x] 3.1 Добавить `RuntimeState`, `GitActivityState`, `PidScope` StrEnums в `models.py`
- [x] 3.2 Добавить frozen `msgspec.Struct` models exactly as Canonical snapshot types: `GitDiff`, `GitActivity`, `PythonEnvFootprint`, `DatabaseFootprint`, `StorageFootprint`, `RuntimeMetrics`, `ClusterContainer`, `ClusterMetrics`, `ClusterEndpoint`, `ClusterResourceSnapshot`, `ClusterSnapshot`, `EnvironmentSnapshot` (includes `project_id`, `lifecycle_state`, `allocated_http_port`), `ProjectSummary`, `Snapshot`. Do not add public `ProcessTreeMetrics`.
- [x] 3.3 Export snapshot models из `odoo_instance_sdk/__init__.py`
- [x] 3.4 Typed errors в `exceptions.py`: `MonitorError` (base), `MonitorExtrasMissingError` (все redacted, наследники `OdooInstanceSdkError`); export из `__init__.py`

## 4. `EnvironmentMonitor` collector

- [x] 4.1 Создать `resources/monitor.py` с `@dataclass(frozen=True, slots=True, kw_only=True) EnvironmentMonitor`; default constructor `EnvironmentMonitor()`; optional injection fields (`catalog_path`, `process_provider`, `git_provider`, `docker_provider`); cache TTLs hardcoded (15s expensive sections, 5s cluster `status()`)
- [x] 4.2 `snapshot(project_id=None) -> Snapshot`: discovery from `BackupCatalog.list_environments(include_removed=False)` grouped by `git_common_dir`; monitor id `"project_" + repo_key(...)`; cluster via `PostgresCluster.from_project(repository_root)` (unprefixed `repo_key` compose name); unknown `project_id` → empty tuples; populate `EnvironmentSnapshot` mapping table (`branch` catalog, `short_sha` first 7 of `git.head_sha`, `database` target/source, `lifecycle_state`, `allocated_http_port` from generated `odoo.conf`)
- [x] 4.3 `watch(interval=2.0, project_id=None) -> AsyncIterator[Snapshot]` (async generator over `snapshot()` + `asyncio.sleep`); `interval >= 0.1` else `ValueError`; cancellation cleans up, no leaks
- [x] 4.4 Component failure isolation: один environment/cluster failure → partial snapshot (`complete=False`/`unavailability_reason`), остальные продолжаются; catalog SQLite error → `MonitorError`; `psutil` missing → `MonitorExtrasMissingError` с actionable hint
- [x] 4.5 Redaction: snapshot не содержит credentials/env vars/cmdline/absolute paths/raw Docker payload

## 5. Odoo process tree metrics (psutil)

- [x] 5.1 Создать `internal/process_metrics.py` с internal `ProcessTreeResult` и `collect_process_tree(...) -> ProcessTreeResult | None`; `psutil` import ленивый, missing → `MonitorExtrasMissingError`
- [x] 5.2 PID+`create_time` verification (`==` exact float and `pid_exists`); mismatch / root `NoSuchProcess`/`AccessDenied`/`ZombieProcess` → `None` → collector `runtime.state="stopped"`
- [x] 5.3 Recursive children (`proc.children(recursive=True)`), `child_pids`, `process_count = 1 + len(children)`, aggregated `rss_bytes`
- [x] 5.4 CPU two-sample: first sample `cpu_percent=None`, subsequent numeric; collector хранит prev CPU point по `(pid, create_time)` in-memory
- [x] 5.5 After a live tree, one `httpx.get("{http_url}/web/health?db_server_status=true", timeout=2.0)`; HTTP 200 and JSON `status=="pass"` → `ready`, else `not_ready` with metrics kept

## 6. Git activity (three-dot diff)

- [x] 6.1 Создать `internal/git_activity.py`; `default_branch` always `"main"`; tip = `main@{upstream}` if `rev-parse --verify` succeeds else `refs/heads/main`; Git CLI failure → orphan shape
- [x] 6.2 ahead/behind via `git rev-list --count <merge-base>..<HEAD>` / `HEAD..<merge-base>`; merge-base via `git merge-base`
- [x] 6.3 added/deleted text lines via `git diff --numstat <merge-base>...HEAD`; binary files (`-`) пропускаются
- [x] 6.4 no-common-ancestor → `state="orphan"`, counts `None`; `state`: clean/ahead/behind/diverged/orphan
- [x] 6.5 Bounded cache по `(worktree_path, HEAD SHA, default-tip SHA)`, TTL 15s

## 7. Storage footprint (disk ownership)

- [x] 7.1 Создать `internal/storage_footprint.py`; directory size: `du -sb` iff `shutil.which("du")` and exit 0 with parseable int (timeout 10s), else `os.walk(followlinks=False)` + realpath dedup
- [x] 7.2 Owned venv size при `python_environment_owned=true`; reused/external — `owned=False`, `bytes=None`, excluded from total
- [x] 7.3 Owned DB (`db_mode="copy"`): `internal/postgres_size.py::database_size_bytes` via `psql -c SELECT pg_database_size(...)` (same pattern as `_verify_database_via_psql`); filestore via `validate_filestore_containment`; `shared` — `owned=False`, excluded
- [x] 7.4 Other files (generated config/lock/local logs/cache/artifacts в environment root)
- [x] 7.5 `total_bytes` сумма; `complete=False` если owned component недоступен; shared cluster volume только на cluster card
- [x] 7.6 Bounded cache по `environment_id`, TTL 15s

## 8. Cluster container identity + resources (Docker inspect/stats)

- [x] 8.1 Добавить `PostgresCluster.resource_snapshot() -> ClusterResourceSnapshot | None` (read-only, без lifecycle lock, без start/stop); external → `None`; compose → `docker inspect`/`docker stats --no-stream` через existing `ComposeRunner`
- [x] 8.2 Container ID (12 hex short)/name/image, Docker-reported init PID, `pid_scope` (`host` Linux / `docker_vm` macOS Docker Desktop+Colima / `unavailable` stopped/missing)
- [x] 8.3 CPU percent, memory usage/limit bytes, optional volume usage bytes (без privileged host traversal; иначе `None`); `sampled_at`
- [x] 8.4 `unavailability_reason="stopped"` iff `status()==STOPPED`; `"missing"` iff not STOPPED and container ID unresolved; docker-unavailable → `"docker_unavailable"` (не raise); `sampled_at` copied to metrics/resource/snapshot or all None
- [x] 8.5 Batch для нескольких managed projects: один `docker stats --no-stream <id1> <id2> ...` / один `docker inspect <id1> <id2> ...`; один container failure не блокирует остальные (`unavailability_reason="stats_failed"/"inspect_failed"`)
- [x] 8.6 No raw Docker payload, no `POSTGRES_PASSWORD_FILE` value, no individual PostgreSQL backend PIDs

## 9. Bounded caching

- [x] 9.1 CPU/RAM — без кеша (свежая каждая итерация; first sample `null`)
- [x] 9.2 Git activity cache по `(worktree, HEAD SHA, default-tip SHA)`, TTL 15s
- [x] 9.3 Storage cache по `environment_id`, TTL 15s
- [x] 9.4 Docker inspect cache по `container_id`, TTL 15s; Docker stats cache по `container_id`, TTL 15s
- [x] 9.5 cluster `status()` (PostgresCluster.status) cache TTL 5s
- [x] 9.6 In-memory only, не персистится; новый `EnvironmentMonitor()` — пустой кеш

## 10. FastAPI server + SPA mount

- [x] 10.1 Создать `internal/serve.py` (only this path) with FastAPI: `GET /api/v1/snapshot` (`msgspec.json.encode`, HTTP 200), `GET /healthz` (`{"status":"ok"}` 200); catalog error HTTP 500 `{"error":"<redacted>"}`
- [x] 10.2 Default UI mode: mount `StaticFiles` from `odoo_instance_sdk/web/dist/`; bind `127.0.0.1`; `--port` exact or auto: try 8069 then scan 8100–8120 inclusive; never auto-select 8070–8099; browser open unless `--no-open`
- [x] 10.3 `--headless`: only API routes, no static mount, no browser open
- [x] 10.4 Loopback-only bind (`localhost`, `127.0.0.1`, `::1`); unauthenticated non-loopback bind is rejected. CORS/auth/TLS are out of scope.
- [x] 10.5 Import guard: `fastapi`/`uvicorn` missing → actionable hint (`pip install odoo-instance-sdk[dashboard]`)

## 11. React + Mantine UI

- [x] 11.1 Создать `src/odoo_instance_sdk/web/` Vite + React + Mantine project; одна responsive страница, без router/Redux/query lib
- [x] 11.2 Project selector (Mantine `Select`): "All projects" + each `ProjectSummary`; polling `fetch('/api/v1/snapshot')` every 2000 ms
- [x] 11.3 Cluster card per displayed project (mode/state/container ID+PID+scope/CPU/RAM/volume); одна `Card` per `EnvironmentSnapshot` (project, name, branch+short SHA, database, port from runtime or `allocated_http_port`, `lifecycle_state`+runtime badges; Git; disk; Odoo PID/workers/CPU/RAM для live, `—` для stopped; **Open Odoo** active только при `runtime.state=="ready"`)
- [x] 11.4 Loading, API error, empty-catalog states
- [x] 11.5 Mantine components: `Select`, `SimpleGrid`, `Card`, `Badge`, `Text`, `Button`, `Progress`; no chart/router/Redux/query lib
- [x] 11.6 Build output `dist/` shipped in package (sdist + wheel) via `uv_build` data inclusion; Node.js не требуется для установленного пакета  # ponytail: uv_build auto-includes web/dist under module root; verified via uv build (Block L)

## 12. CLI extensions

- [x] 12.1 Рефакторить `internal/cli_env.py::env_list` to call `EnvironmentMonitor.snapshot()`; grouped human table with exact columns from cli-odcli spec; `--all` human-only catalog merge of removed rows; `--json` always non-removed Snapshot; preserve `--all-projects`
- [x] 12.2 `odcli postgres status [--json]` MUST call `cluster.status()` and `cluster.resource_snapshot()`, assemble `ClusterSnapshot` fields; external/stopped/missing/docker-unavailable reasons; exit 0 для diagnostic (docker-unavailable)
- [x] 12.3 Добавить `odcli monitor [--headless] [--host HOST] [--port PORT] [--no-open]` в `cli.py`; запускает FastAPI через `internal/serve.py`; import guard для `dashboard` extra (exit 1 + hint если missing)
- [x] 12.4 JSON envelope v1 остаётся; `env list --json` payload parity с monitor snapshot contract

## 13. Packaging: optional extras + built assets

- [x] 13.1 Добавить `[project.optional-dependencies] metrics = ["psutil>=5.9,<7"]` и secure `dashboard` extra (`fastapi>=0.141,<1.0`, `starlette>=1.3.1,<2.0`, `uvicorn>=0.30,<1.0`) в `pyproject.toml`
- [x] 13.2 Настроить `uv_build` data inclusion для собранного React SPA (sdist + wheel); Node.js не требуется для установленного пакета
- [x] 13.3 Coverage regexs: добавить `monitor` regex (`odoo_instance_sdk/(resources/monitor\\.py|internal/(process_metrics|git_activity|storage_footprint|cluster_resources|postgres_size|serve)\\.py)$`) + thresholds (80 line / 70 branch)

## 14. Tests

- [x] 14.1 `tests/unit/test_monitor_snapshot.py`: multi-project discovery, stopped/running Odoo, PID reuse, compose/external/stopped cluster, Linux/VM PID scope, Docker stats errors, Git divergence, storage ownership, component failure isolation, redaction
- [x] 14.2 `tests/unit/test_process_metrics.py`: fake `psutil.Process`, CPU two-sample, `AccessDenied` isolation
- [x] 14.3 `tests/unit/test_git_activity.py`: clean/ahead/behind/diverged/orphan, binary files, stale-local-main fallback
- [x] 14.4 `tests/unit/test_storage_footprint.py`: owned/reused venv, shared/copy DB, `du` vs walk fallback, psql size failure → complete=False, symlinks
- [x] 14.5 `tests/unit/test_cluster_resources.py`: fake Docker inspect/stats, PID scope, batch, external/stopped/docker-unavailable
- [x] 14.6 `tests/unit/test_serve.py`: FastAPI routes (`/api/v1/snapshot`, `/healthz`), project filter, headless vs UI, missing extra guard
- [x] 14.7 `tests/unit/test_cli_monitor.py`: `odcli monitor --headless`, port auto-select, missing extra hint
- [x] 14.8 `tests/unit/test_cli_env_list_grouping.py`: project header, cluster summary, stopped row, `--all` human removed vs JSON non-removed, JSON parity
- [x] 14.9 `tests/unit/test_cli_postgres_status_resources.py`: container fields, external/stopped/docker-unavailable, parity
- [x] 14.10 `tests/unit/test_catalog_runtime_record.py`: schema v9 migration, upsert/clear, read-only
- [x] 14.11 `tests/unit/test_run_foreground_runtime_identity.py`: persist after spawn, clear in `finally` (normal/crash/Ctrl+C), manual instance no persist, `psutil` fallback
- [x] 14.12 Two-part smoke proof: `tests/integration/test_monitor_smoke.py` validates the real Python/headless API across multiple projects and stopped environments; `web/src/App.test.tsx` validates selector, cluster/environment cards and Open Odoo. This is not a Docker/browser E2E.

## 15. Quality gates

- [x] 15.1 `ruff check` clean
- [x] 15.2 `mypy --strict src/odoo_instance_sdk` clean
- [x] 15.3 `pytest -m "not real_odoo and not packaging"` green; coverage thresholds pass (новый `monitor` regex + thresholds)
- [x] 15.4 `pytest -m integration` green locally (with Docker/psutil) или skips gracefully
- [x] 15.5 Frontend production TypeScript build green (`npm ci && npm run build` в `src/odoo_instance_sdk/web/` with `package-lock.json`); собранные assets включены в package  # ponytail: build verified green (tsc+vite), package-lock.json committed; package data inclusion handled by Block L
- [x] 15.6 `pyproject.toml::[tool.coverage.regexs]` + thresholds добавлены; core deps без `psutil`/`fastapi`/`uvicorn`/`pydantic`/`docker-py`
