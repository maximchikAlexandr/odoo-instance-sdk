## Context

Issue #8 требует project-level PostgreSQL cluster ownership и lifecycle. Сегодня `OdooInstance.run_foreground()`/`shell()`/`run_shell_script()` (resources/instance.py) вызывают `run_foreground_process` напрямую, предполагая, что БД доступна. `ProjectConfig` (project.py) хранит discovery defaults в `.odcli/project.toml`, `internal/project_manifest.py` уже запрещает secret-like keys. `internal/paths.py` даёт platformdirs roots (`user_data_dir`, `user_cache_dir`, `user_state_dir`), `internal/doctor.py` — read-only coordinator pattern, `internal/context.py::resolve_project_path` — two-rule project resolution, `internal/cli_output.py::emit_json_envelope`/`fail` — JSON envelope, `internal/locks.py` — flock primitives, `StartConfig.from_odoo_config` — читает `db_host`/`db_port`/`db_user`/`db_password` из `odoo.conf`.

Стек уже не имеет docker-py/PyYAML/psycopg; constraint — не добавлять. Compose file пишется вручную как текст (YAML без кавычек/escape'ов минимален). Compose CLI запускается через `subprocess`.

## Goals / Non-Goals

**Goals:**
- Один `PostgresCluster` public abstraction без Resource/factory/`client.postgres` facade.
- Ownership determines lifecycle permission, not Docker presence.
- Один internal dependency preflight на `OdooInstance`, вызываемый ровно один раз перед spawn.
- `--no-input`, `--dry-run --json`, idempotency, read-only `status`/`doctor`.
- Secrets absent everywhere except `0600` file.

**Non-Goals:**
- Не предоставлять `start()` (существует `ensure_running()`).
- Не строить persisted status/history model.
- Не создавать/копировать Odoo business database.
- Не генерировать/монтировать `postgresql.conf`.
- Не добавлять новый Python dependency.

## Decisions

### D1: Один `PostgresCluster`, без Resource/factory

`PostgresCluster` — обычный `@dataclass(frozen=True, slots=True, kw_only=True)` (как `InstanceFactory`/`OdooInstance`) в `resources/postgres.py`. Public:

```python
from odoo_instance_sdk import PostgresCluster
cluster = PostgresCluster.from_project("[PROJECT_ROOT]")
state: PostgresClusterState = cluster.status()
cluster.ensure_running(timeout=60.0)
cluster.stop(timeout=30.0)  # SDK-owned compose only
```

`mode`/`owned` — read-only properties (через `property` на dataclass; dataclass frozen). `PostgresClusterState` — `enum.StrEnum` (`UNKNOWN`/`UNREACHABLE`/`STARTING`/`HEALTHY`/`STOPPED`/`UNHEALTHY`). `from_project(project_path: str | Path) -> PostgresCluster` читает manifest и source config (для external). Не фабрика, не `client.postgres`, не вложенный `Resource`.

`__repr__` без secrets (как `StartConfig.__repr__`).

### D2: Ownership — атрибут manifest, не Docker presence

`mode` (`external`/`compose`) и `owned` (bool: `mode == "compose"`) — из manifest `[postgres]`. `external` — SDK никогда не start/stop/adopt/remove; только reachability. `compose` — SDK owns cluster, может `up`/`stop`, но никогда `down -v`. Docker presence влияет только на выполнимость, не на разрешение. `stop()` для external — typed error `PostgresClusterNotOwnedError`.

### D3: Manifest extension — backward compatible

`ProjectConfig` получает optional field:

```python
postgres: PostgresProjectConfig | None = None
```

`PostgresProjectConfig(msgspec.Struct, frozen=True, kw_only=True)`:

```python
mode: Literal["external", "compose"] = "external"
image: str | None = None        # compose only
port: int | None = None         # compose only; None = allocate free loopback port at init
user: str | None = None          # compose only; default = source db_user or "odoo"
```

Manifest:

```toml
[postgres]
mode = "compose"
image = "pgvector/pgvector:pg16"
port = 5468
user = "odoo"
```

`external` mode — `[postgres]` section опциональна (или `mode = "external"` без других полей); connection берётся из source `odoo.conf` через существующий `StartConfig.from_odoo_config`. Старые manifests без `[postgres]` → `postgres=None` → treated as `external` via source config.

`ProjectConfig.to_manifest()` пишет `[postgres]` только если поля не-default. `_from_mapping` парсит `[postgres]` sub-section.

`assert_no_secrets` уже запрещает `password`/`db_password`/etc. — `postgres-password` файл хранится в platformdirs, не в manifest.

### D4: Compose runtime artifacts в platformdirs

Layout (новая функция `internal/paths.py::get_project_postgres_dir(project_id: str)`):

```
<platformdirs-data>/projects/<project-id>/postgres/
  compose.yaml
  postgres-password      # 0600
```

`project_id` — стабильный идентификатор проекта. Сегодня project идентифицируется через canonical Git common dir (`repo_key.py::repo_key`). Используем тот же `repo_key(repository_root)` как `project_id` (hex digest), чтобы artifacts были детерминированы между запусками. Директория создаётся lazily при первом `up`.

`compose.yaml` — минимальный, генерируется из template (text, не PyYAML):

```yaml
services:
  postgres:
    image: {image}
    ports:
      - "127.0.0.1:{port}:5432"
    environment:
      POSTGRES_USER: {user}
      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
      PGDATA: /var/lib/postgresql/data
    volumes:
      - {volume_name}:/var/lib/postgresql/data
    secrets:
      - postgres_password
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U {user} -d postgres"]
      interval: 2s
      timeout: 3s
      retries: 30
      start_period: 5s
secrets:
  postgres_password:
    file: {password_file}
volumes:
  {volume_name}:
```

`volume_name` = `pgdata_{project_id}` (deterministic). Compose project name = `odcli_pg_{project_id}` (deterministic). No `container_name`, no `build`, no `extends`, no custom networks, no server config mounts.

`postgres-password` генерируется `secrets.token_urlsafe(32)`, пишется `0600` atomic (`tempfile.mkstemp` + `os.replace`, `os.chmod(0o600)`). Если файл существует — не перезаписывается (idempotent `up`).

`compose.yaml` пишется atomic в `get_project_postgres_dir / "compose.yaml"`.

### D5: Compose runner — `docker compose` CLI, injection для тестов

`internal/postgres_compose.py`:

```python
class ComposeRunner(Protocol):
    def run(self, args: Sequence[str], *, cwd: Path | None, timeout: float | None) -> CommandResult: ...
```

Default impl: `subprocess.run([...], capture_output=True, text=True, timeout=...)`. Tests inject fake.

Operations:
- `compose_config(compose_file) -> None` — `docker compose -f <file> config --quiet`, validate before publish; raises `PostgresComposeInvalidError` on non-zero.
- `compose_up(compose_file, project_name, timeout) -> None` — `docker compose -p <project> -f <file> up --detach --wait`, timeout; raises `PostgresClusterStartError`/`PostgresClusterTimeoutError`.
- `compose_stop(compose_file, project_name, timeout) -> None` — `docker compose -p <project> -f <file> stop --timeout <timeout>`, preserves volume (no `-v`).
- `compose_ps(compose_file, project_name) -> str` — `docker compose -p <project> -f <file> ps --format json`, parse stdout (json5 не нужен — `json.loads` на одной строке).
- `compose_exec_health(compose_file, project_name, user) -> tuple[int, str]` — `docker compose ... exec -T postgres pg_isready -U {user} -d postgres`, return (rc, output).

Все команды имеют `docker` в начале; проверяем `shutil.which("docker")` → `PostgresComposeUnavailableError` если отсутствует.

### D6: `status()` — read-only, без Docker в external

`PostgresClusterState`:
- `UNKNOWN` — не проверялось (initial).
- `UNREACHABLE` — не достучаться.
- `STARTING` — compose up ещё не healthy.
- `HEALTHY` — ready.
- `STOPPED` — compose stopped.
- `UNHEALTHY` — запущен, но healthcheck fail.

`status()` логика:
- `external`: TCP probe на `(db_host, db_port)` из source config (loopback-only через существующий `internal/address.py::probe_address`); `HEALTHY`/`UNREACHABLE`. Никогда не вызывает Docker.
- `compose`: через `compose_ps` + `compose_exec_health`; `STOPPED` если нет контейнеров, `STARTING`/`HEALTHY`/`UNHEALTHY` если есть. `UNKNOWN` если Docker unavailable (но `status()` не падает — возвращает `UNKNOWN` с diagnostic).

`status()` не меняет состояние. Возвращает `PostgresClusterState`. Diagnostic detail доступен через отдельный `to_dict()`/repr (не в str()).

### D7: `ensure_running()` — idempotent, без Docker в external

- `external`: вызывает `status()`. Если `HEALTHY` — return. Иначе raise `PostgresClusterUnreachableError` (typed, redacted — без пароля, без полной строки подключения; только `host:port`).
- `compose`: вызывает `status()`. Если `HEALTHY` — return. Если `STOPPED`/`UNREACHABLE`/`STARTING` — `compose_up(timeout)`, затем poll `status()` до `HEALTHY` или `timeout`. `UNHEALTHY` → raise `PostgresClusterUnhealthyError`. Retry-safe: повторный вызов не падает.

Never spawn Docker в external. Никогда не логирует пароль. Timeout — `ReadinessTimeoutError`-like.

### D8: `stop()` — SDK-owned compose only

- `external`: raise `PostgresClusterNotOwnedError`.
- `compose`: `compose_stop(timeout)`. Не удаляет volume. Idempotent (stop уже остановленного — no-op).

### D9: Preflight на `OdooInstance`

`OdooInstance` получает optional field:

```python
_postgres_cluster: PostgresCluster | None = field(default=None, repr=False)
```

Один internal method:

```python
def _ensure_dependencies_ready(self) -> None:
    if self._postgres_cluster is not None:
        self._postgres_cluster.ensure_running(timeout=60.0)
```

Вызывается в начале `run_foreground()`, `shell()`, `run_shell_script()` (включая `_run_shell_script_exclusive`), до acquire artifact lock, ровно один раз per call.

`InstanceFactory.from_environment()` привязывает cluster:

```python
cluster = PostgresCluster.from_project(Path(environment.repository_root))
# или из manifest repository root если доступен
return OdooInstance(..., _postgres_cluster=cluster)
```

CLI `run`/`shell`/`eval`/`exec`/`module`/`translations` — не дублируют preflight; они идут через `instance.run_foreground()`/`shell()`/`run_shell_script()`, которые уже вызывают preflight.

При выходе Odoo project cluster остаётся running (`run_foreground` не вызывает `stop`). Удаление environment (`EnvironmentResource.remove`) не трогает cluster.

### D10: `init` — `--postgres*` опции

Расширение `init` в `cli.py`:

```text
--postgres [external|compose]   default: external
--postgres-image IMAGE          compose only; required with --no-input
--postgres-port PORT            compose only; omitted = allocate free loopback port
--postgres-user USER            compose only; default: source db_user or "odoo"
```

Interactive init prompts только для unresolved mode-specific values (`--no-input` forbids):
- compose без `--postgres-image` → prompt (TTY) / fail (`--no-input`).
- compose без `--postgres-port` → allocate free loopback port (`probe_address` per existing pattern) и persist.
- compose без `--postgres-user` → default = source `db_user` из `--config` или `"odoo"`.

`--dry-run --json` отчёт:

```json
{
  "postgres": {
    "mode": "compose",
    "image": "...",
    "port": 5468,
    "user": "odoo",
    "allocated_port": false
  }
}
```

Secrets не пишутся. `init` не запускает `up` (только writes manifest + compose artifacts directory NOT created until first `up`). Manifest `[postgres]` пишется через `ProjectConfig.to_manifest()` extension.

Idempotency: existing manifest comparison включает `[postgres]` section.

### D11: `odcli postgres` group

```text
odcli postgres status [--json]
odcli postgres up [--wait-timeout SECONDS]
odcli postgres stop [--timeout SECONDS]
```

- Все используют `resolve_project_path(ctx)` (no project arg inside initialized project).
- `status` — read-only, не меняет состояние. `--json`: envelope с `state`/`mode`/`owned`/`endpoint` (redacted). Human: `mode=compose owned=true state=healthy endpoint=127.0.0.1:5468`.
- `up` — idempotent. compose → `cluster.ensure_running(timeout)`. external → `cluster.status()` проверка (no Docker). `--wait-timeout SECONDS` → `ensure_running(timeout=...)`.
- `stop` — `cluster.stop(timeout)`. external → typed error, exit 1.
- JSON envelope v1 (existing `emit_json_envelope`/`fail`).

### D12: `doctor` — cluster checks, read-only

В `internal/doctor.py::run_doctor` добавляется `_check_postgres(report, project_root)` после `_check_manifest`:

```python
def _check_postgres(report, project_root):
    if project_root is None:
        return
    try:
        cluster = PostgresCluster.from_project(project_root)
    except ProjectManifestNotFoundError:
        return
    docker = shutil.which("docker")
    if docker is None and cluster.owned:
        report.checks.append(CheckResult("postgres.compose", STATUS_WARN, "docker not found in PATH"))
    state = cluster.status()  # read-only
    report.checks.append(CheckResult(
        "postgres.cluster",
        _state_to_status(state),
        f"mode={cluster.mode} owned={cluster.owned} state={state.value} endpoint={redacted_endpoint}",
    ))
```

`doctor` никогда не поднимает/не останавливает cluster. `_state_to_status`: `HEALTHY`→`ok`, `STARTING`/`STOPPED`→`info`, `UNHEALTHY`/`UNREACHABLE`→`warn`, `UNKNOWN`→`warn`.

### D13: Errors — typed, redacted

Новые exceptions в `exceptions.py` (всё наследники `OdooInstanceSdkError`):

```python
class PostgresClusterError(OdooInstanceSdkError): """Base for PostgresCluster errors."""
class PostgresClusterNotOwnedError(PostgresClusterError): ...
class PostgresClusterUnreachableError(PostgresClusterError): ...
class PostgresClusterUnhealthyError(PostgresClusterError): ...
class PostgresClusterStartError(PostgresClusterError): ...
class PostgresClusterTimeoutError(PostgresClusterError): ...
class PostgresComposeUnavailableError(PostgresClusterError): ...
class PostgresComposeInvalidError(PostgresClusterError): ...
class PostgresPortCollisionError(PostgresClusterError): ...
```

Все redacted — никогда не включают пароль. Сообщения содержат только `host:port`, `mode`, `owned`. `__repr__` `PostgresCluster` без пароля.

### D14: Tests — fake command runner, opt-in integration

- Unit tests (большинство): `tests/unit/test_postgres_cluster.py`, `test_project_config_postgres.py`, `test_cli_init_postgres.py`, `test_cli_postgres_group.py`, `test_instance_preflight.py`, `test_doctor_postgres.py`. Fake `ComposeRunner` через dependency injection. Все offline, marked `unit`.
- Opt-in integration: `tests/integration/test_postgres_lifecycle.py` marked `real_odoo`-style (`integration` marker, opt-in via `--m integration` или env var). Доказывает init → up/healthy → instance preflight → stop с сохранением volume. Требует локальный Docker; skip если `docker` отсутствует.
- Manifest tests: `[postgres]` round-trip, secret-absent assertion, backward compat (old manifest без `[postgres]`).

### D15: Coverage thresholds

Добавить `postgres` regex в `pyproject.toml::[tool.coverage.regexs]`:

```toml
postgres = "odoo_instance_sdk/(resources/postgres\\.py|internal/postgres_compose\\.py)$"
```

Thresholds: line/branch средние (80/70 — новый код, не security-critical, но spec-mandated).

## Risks / Trade-offs

- **Compose file как text, не PyYAML**: message of constraint — нет PyYAML в deps. Risk: экранирование image tag / user. Mitigation: validation regex на `image`/`user` (limited charset), `docker compose config` валидирует перед publish.
- **`project_id` = `repo_key`**: deterministic, но зависит от Git common dir. Worktree-registered projects без `.odcli` уже резолвятся через catalog (existing pattern). Risk: rebuild repo меняет `repo_key`. Mitigation: artifacts regenerируются при первом `up` (idempotent); data volume с тем же `project_id` остаётся, но compose project name меняется → orphan volume. Document: `repo_key` migrations out of scope.
- **Preflight в `from_environment()`**: добавляет один cluster read per spawn. Risk: latency в external-режиме с remote DB. Mitigation: TCP probe с timeout; `status()` кешируется на коротком TTL внутри `PostgresCluster` (optional, only if measurable).
- **Port allocation при `init`**: free port сейчас может стать занятым к моменту `up`. Mitigation: `up` проверяет заново; `compose up` fail → typed `PostgresPortCollisionError`, пользователь перезапускает `init` с другим `--postgres-port`. Persisted port не меняется автоматически.
- **External cluster с non-loopback host**: `internal/address.py::probe_address` уже умеет в loopback. External DB на удалённом хосте — spec говорит "local development"; `from_environment` уже `assert_local`. Для external cluster `ensure_running` всё же probe external endpoint (non-loopback allowed для external, т.к. кластер не наш).

## Migration

Старые manifests без `[postgres]` → `postgres=None` → treated as `external`. `PostgresCluster.from_project` на старом проекте: `mode=external`, `owned=False`, endpoint из source `odoo.conf`. Preflight в `from_environment` вызывает `ensure_running` для external → reachability probe. Если DB недоступен → typed error. Поведение: old projects получают preflight, который раньше отсутствовал; это видимое изменение, но строго safer (fail-fast вместо странного падения Odoo).

## Open Questions

- Q: Должен ли `init --postgres compose` сразу аллоцировать порт и писать его в manifest, или только при первом `up`? Решение: писать в manifest при `init` (детерминированность, `--dry-run` показывает allocated), `up` использует persisted. Если collision при `up` → error с инструкцией перезапустить `init --postgres-port`.
- Q: `PostgresCluster` как `@dataclass` или `msgspec.Struct`? Решение: `@dataclass(frozen=True, slots=True, kw_only=True)` (как `InstanceFactory`/`OdooInstance` — существующий pattern для ресурсов). Properties для `mode`/`owned`.
- Q: Хранить ли `postgres-password` в `user_state_dir` или `user_data_dir`? Решение: `user_data_dir` (durable; `user_state_dir` для locks; data важна для preserve-volume constraint). `get_data_root() / "projects" / project_id / "postgres"`.

## Centralized port allocation (cross-project)

Single source of truth for port usage is the existing config files, **not** a separate registry that can drift after manual edits. `internal/port_allocation.py` provides `find_free_port(kind, catalog, exclude_project)` which:

1. Iterates `catalog.list_environments()` → collects `repository_root` for all environments.
2. For each `repository_root`, reads `.odcli/project.toml` → collects `postgres.port` (compose mode) and `preferred_http_port`.
3. For each environment's `generated_config_path`, reads the generated `odoo.conf` → collects `http_port` + `http_interface` (per-env, since each env has its own generated config with a potentially different port).
4. Live `probe_address` check on the candidate.
5. Returns the first port in the kind-specific range that is free in all checks.

No new state file, no new SQLite table. Port usage is derived from the manifests + generated configs every time — manual edits to configs are reflected automatically.

### Catalog schema change: remove `http_port`/`http_interface` from `environments`

`catalog.environments.http_port` and `http_interface` are removed (schema v7→v8 migration). These were a second copy of data already in the generated `odoo.conf`; they drifted from the source of truth after manual config edits. `DevelopmentEnvironment` keeps `http_interface`/`http_port` fields, but `_row_to_env` now reads them from the generated `odoo.conf` (via `parse_odoo_config(generated_config_path)`) instead of the catalog row. `catalog.active_environment_for_port` is removed — `find_free_port("http", ...)` subsumes it.

```
find_free_port("postgres", catalog, exclude_project="/repo")
   │
   ├─ for repo_root in catalog.environments.repository_root:
   │     ProjectConfig.load(repo_root) ──→ postgres.port + preferred_http_port
   ├─ for env in catalog.environments:
   │     parse_odoo_config(env.generated_config_path) ──→ http_port + http_interface
   ├─ probe_address("127.0.0.1", candidate)
   └─ return first free candidate in [5468, 65535) for postgres / [8069, 8099] for HTTP
```

`EnvironmentResource._allocate_port` delegates to `find_free_port("http", catalog, exclude_project, requested=...)`. `cli.py` postgres port allocation delegates to `find_free_port("postgres", catalog, exclude_project)`.

`exclude_project` skips the current project's own manifest ports so re-init doesn't see its own existing manifest as a collision.

## Implementer Choices (made explicit)

- **Port allocation range**: `[5468, 65535)`; 5468 chosen to match the issue example and avoid common 5432/5500 collisions. First free loopback port is persisted to the manifest.
- **Probe timeout**: external `status()` uses `internal.address.probe_address` with a 0.2s socket bind timeout (inherited). This is a read-only reachability probe, not a connection test.
- **`--json` implies non-interactive**: `odcli init --json` (without `--no-input`) still forbids prompts, consistent with the existing `--dry-run --json` contract. `--no-input` is the explicit flag; `--json` suppresses prompts as a side effect of emitting a machine-readable envelope.
- **Image/user charset restriction**: `image` matches `^[A-Za-z0-9._/:@+-]+$`, `user` matches `^[A-Za-z_][A-Za-z0-9_]*$`. This avoids YAML escaping without PyYAML; invalid values raise `PostgresComposeInvalidError` before the compose file is written.
- **Password entropy**: `secrets.token_urlsafe(32)` (~43 chars, ~256 bits of entropy), written `0600`, never overwritten if the file exists (idempotent `up`).
- **Healthcheck params**: `interval: 2s, timeout: 3s, retries: 30, start_period: 5s`. 30 retries × 2s covers slow first-init of `pgvector` images; `start_period: 5s` absorbs container startup. Exact values mandated by spec.
- **Non-git `project_id` fallback**: when `git rev-parse` fails (non-git project), `project_id` falls back to `hashlib.sha256(resolved_path)[:8]` with the directory name. This is a defensive extension: artifacts remain usable but are **not shared across worktrees** for non-git projects (no common dir to derive a stable key). Marked `ponytail:` in code.
- **Stop failure error type**: `PostgresClusterStopError` (distinct from `PostgresClusterStartError`); best-effort stop, reported as a typed error on non-zero compose result.
- **`from_environment` cluster bind error handling**: only `ProjectManifestNotFoundError` and `PostgresClusterError` disable preflight (cluster set to `None`); unexpected errors propagate so a corrupt environment doesn't silently disable fail-fast.
- **External-mode explicit `--postgres external`**: provenance records `postgres` only when `postgres_cfg is not None` (compose mode). Explicit `--postgres external` produces no `[postgres]` section (backward compat) and no provenance entry; this matches the "external is the default" semantics.