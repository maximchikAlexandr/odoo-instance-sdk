## 1. Catalog current-runtime record (schema v8 → v9)

- [ ] 1.1 Добавить `environment_runtime` table migration в `BackupCatalog._create_schema` (v8→v9, `CREATE TABLE IF NOT EXISTS`, FK на `environments(id)`); `CURRENT_SCHEMA_VERSION = 9`
- [ ] 1.2 Добавить `get_environment_runtime(environment_id) -> Row | None` и `list_environment_runtimes() -> list[Row]` (read-only)
- [ ] 1.3 Добавить `upsert_environment_runtime(environment_id, *, root_pid, create_time, started_at, checkout_branch, commit_sha, http_url, http_port, database_name) -> None` и `clear_environment_runtime(environment_id) -> None`
- [ ] 1.4 Обновить catalog migration tests (version 9, upsert/clear idempotency, FK constraint, read-only API)

## 2. `run_foreground` persist/clear runtime identity

- [ ] 2.1 В `OdooInstance.run_foreground()` (только для instance bound к environment через `from_environment()`) persist runtime identity в `environment_runtime` после spawn (root_pid, `psutil.Process(pid).create_time()` или `time.time()` fallback, started_at, branch/commit SHA из worktree, http_url/port, db_name)
- [ ] 2.2 В `finally` (normal/crash/Ctrl+C/exception) `clear_environment_runtime(environment_id)` best-effort (ошибка логируется в stderr, не маскирует exit code/exception)
- [ ] 2.3 Manual instance (`instance(base_url=...)`/`from_config()`) — без `environment_id`, runtime identity НЕ пишется
- [ ] 2.4 `shell()`/`run_shell_script()`/`start()`/`stop()` — без persist (только `run_foreground`)
- [ ] 2.5 `psutil` import ленивый; `run_foreground` сам не требует `psutil` (fallback `time.time()`)

## 3. Snapshot typed models

- [ ] 3.1 Добавить `RuntimeState`, `GitActivityState`, `PidScope` StrEnums в `models.py`
- [ ] 3.2 Добавить frozen `msgspec.Struct` models: `GitDiff`, `GitActivity`, `PythonEnvFootprint`, `DatabaseFootprint`, `StorageFootprint`, `ProcessTreeMetrics`, `RuntimeMetrics`, `ClusterContainer`, `ClusterMetrics`, `ClusterSnapshot`, `EnvironmentSnapshot`, `ProjectSummary`, `Snapshot` (все `frozen=True, forbid_unknown_fields=True, kw_only=True`)
- [ ] 3.3 Export snapshot models из `odoo_instance_sdk/__init__.py`
- [ ] 3.4 Typed errors в `exceptions.py`: `MonitorError` (base), `MonitorExtrasMissingError` (все redacted, наследники `OdooInstanceSdkError`); export из `__init__.py`

## 4. `EnvironmentMonitor` collector

- [ ] 4.1 Создать `resources/monitor.py` с `@dataclass(frozen=True, slots=True, kw_only=True) EnvironmentMonitor`; default constructor `EnvironmentMonitor()`; optional injection fields (`catalog_path`, `process_provider`, `git_provider`, `docker_provider`); cache TTLs hardcoded (15s expensive sections, 5s cluster `status()`)
- [ ] 4.2 `snapshot(project_id=None) -> Snapshot`: project discovery из `BackupCatalog.list_environments(include_removed=False)` по `git_common_dir`, `project_id = "project_" + repo_key(repository_root, git_common_dir)`; группировка, stable ordering
- [ ] 4.3 `watch(interval=2.0, project_id=None) -> AsyncIterator[Snapshot]` (async generator over `snapshot()` + `asyncio.sleep`); `interval >= 0.1` else `ValueError`; cancellation cleans up, no leaks
- [ ] 4.4 Component failure isolation: один environment/cluster failure → partial snapshot (`complete=False`/`unavailability_reason`), остальные продолжаются; catalog SQLite error → `MonitorError`; `psutil` missing → `MonitorExtrasMissingError` с actionable hint
- [ ] 4.5 Redaction: snapshot не содержит credentials/env vars/cmdline/absolute paths/raw Docker payload

## 5. Odoo process tree metrics (psutil)

- [ ] 5.1 Создать `internal/process_metrics.py` с `collect_process_tree(root_pid, create_time, *, prev_cpu_point) -> ProcessTreeMetrics`; `psutil` import ленивый, missing → `MonitorExtrasMissingError`
- [ ] 5.2 PID+`create_time` verification (`psutil.Process(pid).create_time() == recorded` и `psutil.pid_exists`); mismatch → `state="stopped"`
- [ ] 5.3 Recursive children (`proc.children(recursive=True)`), `child_pids`, `process_count = 1 + len(children)`, aggregated `rss_bytes`
- [ ] 5.4 CPU two-sample: first sample `cpu_percent=None`, subsequent numeric; collector хранит prev CPU point по `(pid, create_time)` in-memory
- [ ] 5.5 `NoSuchProcess`/`AccessDenied`/`ZombieProcess` изолируются на один environment

## 6. Git activity (three-dot diff)

- [ ] 6.1 Создать `internal/git_activity.py`; `default_branch` из manifest или fallback `main`; upstream tip если доступен, fallback локальная default branch
- [ ] 6.2 ahead/behind via `git rev-list --count <merge-base>..<HEAD>` / `HEAD..<merge-base>`; merge-base via `git merge-base`
- [ ] 6.3 added/deleted text lines via `git diff --numstat <merge-base>...HEAD`; binary files (`-`) пропускаются
- [ ] 6.4 no-common-ancestor → `state="orphan"`, counts `None`; `state`: clean/ahead/behind/diverged/orphan
- [ ] 6.5 Bounded cache по `(worktree_path, HEAD SHA, default-tip SHA)`, TTL 15s

## 7. Storage footprint (disk ownership)

- [ ] 7.1 Создать `internal/storage_footprint.py`; worktree size (`du -sb` где доступен, fallback `os.walk` без symlink-following, realpath dedup)
- [ ] 7.2 Owned venv size при `python_environment_owned=true`; reused/external — `owned=False`, `bytes=None`, excluded from total
- [ ] 7.3 Owned DB (`db_mode="copy"`, journal-owned `target_db`): `pg_database_size(target_db)` (через existing `DatabaseResource` read-only или `psql -c`) + filestore via existing containment checks; `shared` — `owned=False`, excluded
- [ ] 7.4 Other files (generated config/lock/local logs/cache/artifacts в environment root)
- [ ] 7.5 `total_bytes` сумма; `complete=False` если owned component недоступен; shared cluster volume только на cluster card
- [ ] 7.6 Bounded cache по `environment_id`, TTL 15s

## 8. Cluster container identity + resources (Docker inspect/stats)

- [ ] 8.1 Добавить `PostgresCluster.resource_snapshot() -> ClusterResourceSnapshot | None` (read-only, без lifecycle lock, без start/stop); external → `None`; compose → `docker inspect`/`docker stats --no-stream` через existing `ComposeRunner`
- [ ] 8.2 Container ID (12 hex short)/name/image, Docker-reported init PID, `pid_scope` (`host` Linux / `docker_vm` macOS Docker Desktop+Colima / `unavailable` stopped/missing)
- [ ] 8.3 CPU percent, memory usage/limit bytes, optional volume usage bytes (без privileged host traversal; иначе `None`); `sampled_at`
- [ ] 8.4 Stopped/missing compose → `ClusterResourceSnapshot(container=None, metrics=None, unavailability_reason="stopped"/"missing")` (не `None`); docker-unavailable → `unavailability_reason="docker_unavailable"` (не raise)
- [ ] 8.5 Batch для нескольких managed projects: один `docker stats --no-stream <id1> <id2> ...` / один `docker inspect <id1> <id2> ...`; один container failure не блокирует остальные (`unavailability_reason="stats_failed"/"inspect_failed"`)
- [ ] 8.6 No raw Docker payload, no `POSTGRES_PASSWORD_FILE` value, no individual PostgreSQL backend PIDs

## 9. Bounded caching

- [ ] 9.1 CPU/RAM — без кеша (свежая каждая итерация; first sample `null`)
- [ ] 9.2 Git activity cache по `(worktree, HEAD SHA, default-tip SHA)`, TTL 15s
- [ ] 9.3 Storage cache по `environment_id`, TTL 15s
- [ ] 9.4 Docker inspect cache по `container_id`, TTL 15s; Docker stats cache по `container_id`, TTL 15s
- [ ] 9.5 cluster `status()` (PostgresCluster.status) cache TTL 5s
- [ ] 9.6 In-memory only, не персистится; новый `EnvironmentMonitor()` — пустой кеш

## 10. FastAPI server + SPA mount

- [ ] 10.1 Создать `internal/serve.py` (или `resources/serve.py`) с FastAPI app: `GET /api/v1/snapshot` (calls `EnvironmentMonitor().snapshot()`, `msgspec.json.encode`), `GET /healthz` (`{"status":"ok"}` 200)
- [ ] 10.2 Default UI mode: mount собранный React SPA (`StaticFiles` из `odoo_instance_sdk/web/dist/`); loopback bind default; `--host`/`--port` override; port auto-select [8069,8099] если занят; browser open unless `--no-open`
- [ ] 10.3 `--headless`: only API routes, no static mount, no browser open
- [ ] 10.4 Non-loopback bind explicit opt-in via `--host`; CORS/auth/TLS out of scope
- [ ] 10.5 Import guard: `fastapi`/`uvicorn` missing → actionable hint (`pip install odoo-instance-sdk[dashboard]`)

## 11. React + Mantine UI

- [ ] 11.1 Создать `src/odoo_instance_sdk/web/` Vite + React + Mantine project; одна responsive страница, без router/Redux/query lib
- [ ] 11.2 Project selector (Mantine `Select`): "All projects" + each `ProjectSummary`; polling `fetch('/api/v1/snapshot')` ~раз в 2 секунды
- [ ] 11.3 Cluster card per displayed project (mode/state/container ID+PID+scope/CPU/RAM/volume); одна `Card` per `EnvironmentSnapshot` (project, name, branch+short SHA, database, port, badges; Git ahead/behind/`+added/-deleted`; Total disk + breakdown; Odoo PID/workers/CPU/RAM для ready, `—` для stopped; **Open Odoo** active только при `ready`)
- [ ] 11.4 Loading, API error, empty-catalog states
- [ ] 11.5 Mantine components: `Select`, `SimpleGrid`, `Card`, `Badge`, `Text`, `Button`, `Progress`; no chart/router/Redux/query lib
- [ ] 11.6 Build output `dist/` shipped in package (sdist + wheel) via `uv_build` data inclusion; Node.js не требуется для установленного пакета

## 12. CLI extensions

- [ ] 12.1 Рефакторить `internal/cli_env.py::env_list` на `EnvironmentMonitor.snapshot()` (или эквивалентный collector helper) для cluster summary + runtime columns; grouped human output с project header + cluster summary line + environment rows (Odoo PID `root (+N children)`, CPU/RAM, `main↕`, `main…±`, Size); preserve `--all`/`--all-projects`/`--json`
- [ ] 12.2 `odcli postgres status [--json]` расширить read-only container fields через `PostgresCluster.resource_snapshot()`; parity с monitor cluster snapshot; external/stopped/docker-unavailable явные reasons; exit 0 для diagnostic (docker-unavailable)
- [ ] 12.3 Добавить `odcli monitor [--headless] [--host HOST] [--port PORT] [--no-open]` в `cli.py`; запускает FastAPI через `internal/serve.py`; import guard для `dashboard` extra (exit 1 + hint если missing)
- [ ] 12.4 JSON envelope v1 остаётся; `env list --json` payload parity с monitor snapshot contract

## 13. Packaging: optional extras + built assets

- [ ] 13.1 Добавить `[project.optional-dependencies] metrics = ["psutil>=5.9,<7"]` и `dashboard = ["odoo-instance-sdk[metrics]", "fastapi>=0.115,<1.0", "uvicorn>=0.30,<1.0"]` в `pyproject.toml`
- [ ] 13.2 Настроить `uv_build` data inclusion для собранного React SPA (sdist + wheel); Node.js не требуется для установленного пакета
- [ ] 13.3 Coverage regexs: добавить `monitor` regex (`odoo_instance_sdk/(resources/monitor\\.py|internal/(process_metrics|git_activity|storage_footprint|cluster_resources|serve)\\.py)$`) + thresholds (80/70)

## 14. Tests

- [ ] 14.1 `tests/unit/test_monitor_snapshot.py`: multi-project discovery, stopped/running Odoo, PID reuse, compose/external/stopped cluster, Linux/VM PID scope, Docker stats errors, Git divergence, storage ownership, component failure isolation, redaction
- [ ] 14.2 `tests/unit/test_process_metrics.py`: fake `psutil.Process`, CPU two-sample, `AccessDenied` isolation
- [ ] 14.3 `tests/unit/test_git_activity.py`: clean/ahead/behind/diverged/orphan, binary files, stale-local-main fallback
- [ ] 14.4 `tests/unit/test_storage_footprint.py`: owned/reused venv, shared/copy DB, symlinks, incomplete
- [ ] 14.5 `tests/unit/test_cluster_resources.py`: fake Docker inspect/stats, PID scope, batch, external/stopped/docker-unavailable
- [ ] 14.6 `tests/unit/test_serve.py`: FastAPI routes (`/api/v1/snapshot`, `/healthz`), project filter, headless vs UI, missing extra guard
- [ ] 14.7 `tests/unit/test_cli_monitor.py`: `odcli monitor --headless`, port auto-select, missing extra hint
- [ ] 14.8 `tests/unit/test_cli_env_list_grouping.py`: project header, cluster summary, stopped row, JSON parity
- [ ] 14.9 `tests/unit/test_cli_postgres_status_resources.py`: container fields, external/stopped/docker-unavailable, parity
- [ ] 14.10 `tests/unit/test_catalog_runtime_record.py`: schema v9 migration, upsert/clear, read-only
- [ ] 14.11 `tests/unit/test_run_foreground_runtime_identity.py`: persist after spawn, clear in `finally` (normal/crash/Ctrl+C), manual instance no persist, `psutil` fallback
- [ ] 14.12 `tests/integration/test_monitor_smoke.py` (opt-in `integration` marker, skip без Docker/psutil): two projects, several environments, часть stopped; verify selector/cards/headless API и Open Odoo

## 15. Quality gates

- [ ] 15.1 `ruff check` clean
- [ ] 15.2 `mypy --strict src/odoo_instance_sdk` clean
- [ ] 15.3 `pytest -m "not real_odoo and not packaging"` green; coverage thresholds pass (новый `monitor` regex + thresholds)
- [ ] 15.4 `pytest -m integration` green locally (with Docker/psutil) или skips gracefully
- [ ] 15.5 Frontend production TypeScript build green (`npm run build` в `src/odoo_instance_sdk/web/`); собранные assets включены в package
- [ ] 15.6 `pyproject.toml::[tool.coverage.regexs]` + thresholds добавлены; core deps без `psutil`/`fastapi`/`uvicorn`/`pydantic`/`docker-py`