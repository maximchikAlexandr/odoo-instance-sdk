## 1. SDK: `PostgresCluster` core

- [x] 1.1 Добавить `PostgresClusterState` enum в `models.py` (UNKNOWN/UNREACHABLE/STARTING/HEALTHY/STOPPED/UNHEALTHY)
- [x] 1.2 Добавить typed exceptions в `exceptions.py` (`PostgresClusterError` + 8 subclasses), все redacted
- [x] 1.3 Создать `resources/postgres.py` с `PostgresCluster` dataclass (frozen/slots/kw_only), `from_project()`, `mode`/`owned` properties, `status()`/`ensure_running()`/`stop()`, redacted `__repr__`
- [x] 1.4 External mode: `status()` TCP probe через `internal/address.py::probe_address`; `ensure_running()` probe only, raise `PostgresClusterUnreachableError`
- [x] 1.5 Compose mode: `status()` через `ComposeRunner`; `ensure_running()` compose up --detach --wait + poll; `stop()` compose stop preserve volume; reject external
- [x] 1.6 Export `PostgresCluster`/`PostgresClusterState`/`PostgresClusterError`/subclass из `__init__.py`

## 2. Compose runner + artifacts

- [x] 2.1 Создать `internal/postgres_compose.py` с `ComposeRunner` Protocol (для fake в тестах) и default `SubprocessComposeRunner`
- [x] 2.2 `compose_config`/`compose_up`/`compose_stop`/`compose_ps`/`compose_exec_health` через `docker compose` CLI; `shutil.which("docker")` → `PostgresComposeUnavailableError`
- [x] 2.3 Compose file text generation (minimal: one service, loopback port, named volume, `pg_isready` healthcheck, file-backed secret, deterministic project name); validation через `docker compose config --quiet` перед atomic publish
- [x] 2.4 `internal/paths.py::get_project_postgres_dir(project_id)` через `repo_key(repository_root)`; lazy directory creation
- [x] 2.5 `postgres-password` генерация `secrets.token_urlsafe(32)`, atomic write `0600`, не перезаписывать если существует

## 3. `ProjectConfig` extension

- [x] 3.1 Добавить `PostgresProjectConfig` (`msgspec.Struct`, frozen, kw_only) с `mode`/`image`/`port`/`user`
- [x] 3.2 Добавить `postgres: PostgresProjectConfig | None = None` в `ProjectConfig`; `_from_mapping` парсит `[postgres]`; `to_manifest()` пишет section только если не-default
- [x] 3.3 Backward compat: old manifest без `[postgres]` → `postgres=None`; `assert_no_secrets` покрывает `password`

## 4. CLI `init` `--postgres*`

- [x] 4.1 Добавить `--postgres`/`--postgres-image`/`--postgres-port`/`--postgres-user` в `cli.py::init`
- [x] 4.2 Interactive prompts только для unresolved mode-specific values; `--no-input` forbids + requires `--postgres-image` для compose
- [x] 4.3 Port allocation через `probe_address` для compose без `--postgres-port`; persist в manifest
- [x] 4.4 `--postgres-user` default = source `db_user` (from `--config` via `StartConfig.from_odoo_config`) или `"odoo"`
- [x] 4.5 `--dry-run --json` отчёт postgres plan (no secrets, no file write)
- [x] 4.6 Idempotency comparison учитывает `[postgres]`; existing init flow (vscode, prompts) не сломан

## 5. CLI `odcli postgres` group

- [x] 5.1 Создать `postgres` command group в `cli.py` с `status`/`up`/`stop`
- [x] 5.2 `status --json`: envelope v1 с `state`/`mode`/`owned`/`endpoint` (redacted); read-only; external без Docker
- [x] 5.3 `up [--wait-timeout SECONDS]`: compose → `ensure_running`; external → `status()` only
- [x] 5.4 `stop [--timeout SECONDS]`: compose only; external → `PostgresClusterNotOwnedError`, exit 1
- [x] 5.5 Используют `resolve_project_path(ctx)`, без project arg

## 6. `InstanceFactory.from_environment` + preflight

- [x] 6.1 `InstanceFactory.from_environment()` привязывает `PostgresCluster.from_project(environment.repository_root)` к `OdooInstance._postgres_cluster`
- [x] 6.2 `OdooInstance._ensure_dependencies_ready()` вызывает `_postgres_cluster.ensure_running(timeout=60.0)` если не None
- [x] 6.3 `run_foreground()`/`shell()`/`run_shell_script()`/`_run_shell_script_exclusive()` вызывают preflight ровно один раз до artifact lock
- [x] 6.4 Manual instance (`instance(base_url=...)`/`from_config()`) → `_postgres_cluster=None` → no preflight
- [x] 6.5 `run_foreground` exit не вызывает `cluster.stop()`; `EnvironmentResource.remove` не трогает cluster

## 7. Doctor cluster checks

- [x] 7.1 Добавить `_check_postgres(report, project_root)` в `internal/doctor.py`
- [x] 7.2 Конструирует `PostgresCluster.from_project`, для compose + missing Docker → `STATUS_WARN`
- [x] 7.3 `cluster.status()` read-only; `_state_to_status` mapping; `postgres.cluster` check с `mode`/`owned`/`state`/`endpoint` (redacted)
- [x] 7.4 Без project → skip postgres check; existing checks не сломаны

## 8. Tests

- [x] 8.1 `tests/unit/test_postgres_cluster.py`: external/compose `status`/`ensure_running`/`stop`, ownership, redacted errors через fake `ComposeRunner`
- [x] 8.2 `tests/unit/test_project_config_postgres.py`: round-trip `[postgres]`, secret-absent, backward compat
- [x] 8.3 `tests/unit/test_cli_init_postgres.py`: `--postgres*` options, `--no-input` errors, port allocation, dry-run JSON, idempotency
- [x] 8.4 `tests/unit/test_cli_postgres_group.py`: `status`/`up`/`stop` JSON envelope, ownership errors, external без Docker
- [x] 8.5 `tests/unit/test_instance_preflight.py`: `run_foreground`/`shell`/`run_shell_script` вызывают preflight ровно один раз; manual instance без preflight; cluster stays running after exit
- [x] 8.6 `tests/unit/test_doctor_postgres.py`: read-only checks, missing Docker warn, external reachability
- [x] 8.7 `tests/integration/test_postgres_lifecycle.py` (opt-in `integration` marker, skip без `docker`): init → up/healthy → instance preflight → stop preserving volume

## 10. Centralized port allocation (cross-project)

- [x] 10.1 Создать `internal/port_allocation.py` с `find_free_port(kind, catalog, exclude_project)` — итерирует catalog.environments + project manifests + generated odoo.conf для HTTP ports; `probe_address` live check
- [x] 10.2 Catalog schema migration v7→v8: убрать `http_port`/`http_interface` из `environments` (table recreate with `legacy_alter_table`); убрать `active_environment_for_port`
- [x] 10.3 `EnvironmentResource._allocate_port` делегирует в `find_free_port("http", ...)`
- [x] 10.4 `_row_to_env` читает `http_interface`/`http_port` из generated odoo.conf (single source of truth)
- [x] 10.5 `cli.py` postgres port allocation делегирует в `find_free_port("postgres", ...)`
- [x] 10.6 Обновить catalog migration tests (version 8, убрать `active_environment_for_port`)
- [x] 10.7 Unit + integration tests green

## 9. Quality gates

- [x] 9.1 `ruff check` clean
- [x] 9.2 `mypy --strict` clean
- [x] 9.3 `pytest -m "not real_odoo and not packaging"` green; coverage thresholds pass (новый `postgres` regex + thresholds)
- [x] 9.4 `pytest -m integration` green locally (with Docker) или skips gracefully
- [x] 9.5 `pyproject.toml::[tool.coverage.regexs]` добавлен `postgres` regex; thresholds заданы