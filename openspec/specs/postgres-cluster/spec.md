# postgres-cluster Specification

## Purpose
TBD - created by archiving change add-postgres-cluster-lifecycle. Update Purpose after archive.

## Requirements

### Requirement: `PostgresCluster` public abstraction

`PostgresCluster` MUST быть единственной operational abstraction для project-level PostgreSQL cluster. SDK MUST NOT предоставлять `Resource`-подобный интерфейс, factory hierarchy или `client.postgres` facade.

`PostgresCluster` MUST быть `@dataclass(frozen=True, slots=True, kw_only=True)` в `odoo_instance_sdk.resources.postgres` и публично импортируемым из `odoo_instance_sdk` top-level.

Public surface:

```python
from odoo_instance_sdk import PostgresCluster

cluster = PostgresCluster.from_project("[PROJECT_ROOT]")
state = cluster.status()
cluster.ensure_running(timeout=60.0)
cluster.stop(timeout=30.0)  # SDK-owned compose mode only
digest = cluster.resolve_image_digest(timeout=60.0)
cluster.approve_image(digest, timeout=60.0)
```

`resolve_image_digest` and `approve_image` are public compose-only consent operations; external mode raises `PostgresClusterNotOwnedError`. `status()` remains read-only and intentionally does not acquire the lifecycle lock; `ensure_running()` and `stop()` acquire the canonical lock for all state-changing transitions.

`mode` и `owned` MUST быть read-only properties. `mode: Literal["external", "compose"]`. `owned` — `True` iff `mode == "compose"`.

`__repr__` MUST быть redacted: никогда не содержит пароль, секретную строку подключения или содержимое `postgres-password` файла.

#### Scenario: Import from top-level

- **WHEN** `from odoo_instance_sdk import PostgresCluster`
- **THEN** `PostgresCluster` is importable and constructible via `from_project`

#### Scenario: Mode and owned are read-only

- **WHEN** code attempts `cluster.mode = "compose"` on a `PostgresCluster` instance
- **THEN** `FrozenInstanceError` (dataclass is frozen)

#### Scenario: Repr redacts secrets

- **WHEN** `repr(cluster)` is rendered for a compose-mode cluster
- **THEN** output contains `mode=`, `owned=` and `endpoint=` but never contains the password or any secret file content

### Requirement: `PostgresCluster.from_project()`

`PostgresCluster.from_project(project_path: str | Path) -> PostgresCluster` MUST:

- читать `.odcli/project.toml` через существующий `ProjectConfig.load`;
- для `mode="external"` (или отсутствующей `[postgres]` section) — `owned=False`, connection endpoint берётся из source `odoo.conf` (`db_host`/`db_port` из `StartConfig.from_odoo_config(source_config)`; `db_host` default `127.0.0.1`, `db_port` default `5432`);
- для `mode="compose"` — `owned=True`, endpoint `127.0.0.1:<port>` из manifest;
- не запускать Docker, не создавать файлы, не аллоцировать порт;
- не требовать master password или source config для compose mode (compose self-contained в manifest).

Если source config отсутствует для external mode — `PostgresClusterError` с stable message.

#### Scenario: External mode reads source config

- **WHEN** `PostgresCluster.from_project("/repo")` on a project with `[postgres] mode="external"` and a source config with `db_host=db`, `db_port=5433`
- **THEN** endpoint is `db:5433`, `owned=False`

#### Scenario: Compose mode self-contained

- **WHEN** `PostgresCluster.from_project("/repo")` on a project with `[postgres] mode="compose", port=5468`
- **THEN** endpoint is `127.0.0.1:5468`, `owned=True`, no source config read

#### Scenario: Legacy manifest without postgres section

- **WHEN** `PostgresCluster.from_project("/repo")` on a manifest without `[postgres]`
- **THEN** treated as external mode, endpoint from source config

#### Scenario: External mode without source config

- **WHEN** `PostgresCluster.from_project("/repo")` on external mode without `source_config`
- **THEN** raises `PostgresClusterError` with a stable message

### Requirement: `PostgresClusterState` enum

`PostgresClusterState` MUST быть `enum.StrEnum` со значениями:

- `UNKNOWN` — not probed (initial)
- `UNREACHABLE` — endpoint not reachable
- `STARTING` — compose up issued, not yet healthy
- `HEALTHY` — ready
- `STOPPED` — compose stopped
- `UNHEALTHY` — running but healthcheck failing

`PostgresClusterState` MUST быть публично импортируемым из `odoo_instance_sdk`.

#### Scenario: Enum values are stable strings

- **WHEN** `PostgresClusterState.HEALTHY.value` is read
- **THEN** equals `"healthy"`

### Requirement: `status()` is read-only

`PostgresCluster.status() -> PostgresClusterState` MUST:

- никогда не изменять cluster state (no Docker start/stop, no file creation);
- для `external` — TCP probe endpoint (через `internal.address.probe_address` или эквивалент); `HEALTHY` если reachable, `UNREACHABLE` иначе; никогда не вызывает Docker;
- для `compose` — через Compose CLI (`ps`/`pg_isready`); `STOPPED` если контейнеров нет, `STARTING`/`HEALTHY`/`UNHEALTHY` по healthcheck; `UNKNOWN` если Docker unavailable (без raise);
- не поднимать и не останавливать cluster;
- не логировать пароль;
- возвращать `PostgresClusterState`, не падать при transient Docker errors (возвращает `UNKNOWN`).

`status()` не дублирует preflight — это отдельная read-only operation.

#### Scenario: External status probes without Docker

- **WHEN** `cluster.status()` on external mode with reachable endpoint
- **THEN** returns `HEALTHY` and does not call Docker

#### Scenario: Compose status detects stopped

- **WHEN** `cluster.status()` on compose mode after `stop()`
- **THEN** returns `STOPPED`

#### Scenario: Docker unavailable returns UNKNOWN

- **WHEN** `cluster.status()` on compose mode and `docker` not in PATH
- **THEN** returns `UNKNOWN` without raising

### Requirement: `ensure_running()` is idempotent

`PostgresCluster.ensure_running(timeout: float = 60.0) -> None` MUST:

- для `external` — вызывать `status()`; если `HEALTHY` — return; иначе raise `PostgresClusterUnreachableError` (typed, redacted);
- для `compose` — если `HEALTHY` — return; если `STOPPED`/`UNREACHABLE`/`STARTING` — `compose up --detach --wait` с timeout, затем poll `status()` до `HEALTHY` или timeout; `UNHEALTHY` → raise `PostgresClusterUnhealthyError`; timeout → `PostgresClusterTimeoutError`;
- никогда не вызывать Docker в external mode;
- быть retry-safe (повторный вызов после успеха — no-op);
- не логировать и не поднимать пароль в исключениях.

#### Scenario: External ensure_running probes only

- **WHEN** `cluster.ensure_running()` on external mode with reachable endpoint
- **THEN** returns normally, no Docker invocation

#### Scenario: External unreachable raises typed error

- **WHEN** `cluster.ensure_running()` on external mode with unreachable endpoint
- **THEN** raises `PostgresClusterUnreachableError` with redacted message

#### Scenario: Compose ensure_running starts and waits

- **WHEN** `cluster.ensure_running(timeout=60.0)` on compose mode in `STOPPED`
- **THEN** runs `docker compose up --detach --wait`, polls until `HEALTHY` or timeout, returns normally when healthy

#### Scenario: Compose ensure_running is retry-safe

- **WHEN** `cluster.ensure_running()` is called twice in a row on a healthy compose cluster
- **THEN** second call is a no-op

#### Scenario: Compose unhealthy raises typed error

- **WHEN** `cluster.ensure_running()` on a compose cluster that is `UNHEALTHY`
- **THEN** raises `PostgresClusterUnhealthyError`

### Requirement: Compose image trust and serialized lifecycle

For compose mode, a manifest image is only a mutable selector. `approve_image(image_digest)` MUST pull and inspect it, accept only an exactly matching OCI `repository@sha256:<64-hex>` RepoDigest, and save a `reference -> digest` approval in user data outside the repository with mode `0600`. Each `ensure_running()` MUST resolve again, fail closed if it differs or approval is corrupt, and render Compose with the immutable approved digest. Standalone public `status()` remains read-only and does not acquire the lifecycle lock. The canonical exclusive project lock MUST cover lifecycle-internal status/rechecks, reconciliation, `up`, polling to terminal health, and `stop`; a concurrent caller rechecks status after acquiring the lock.

#### Scenario: Compose startup uses an approved immutable digest

- **WHEN** a compose cluster image has been explicitly approved and `ensure_running()` starts the cluster
- **THEN** the image is resolved again and Compose uses the matching immutable RepoDigest

#### Scenario: Concurrent lifecycle calls are serialized

- **WHEN** concurrent callers attempt compose lifecycle transitions for the same project
- **THEN** each transition holds the canonical project lifecycle lock and rechecks status after acquiring it

### Requirement: `stop()` rejects externally owned clusters

`PostgresCluster.stop(timeout: float = 30.0) -> None` MUST:

- для `external` — raise `PostgresClusterNotOwnedError` (typed);
- для `compose` — `docker compose stop --timeout <timeout>`; preserves volume (never `down -v`);
- быть idempotent (stop уже остановленного — no-op, не raise).

#### Scenario: Stop external raises

- **WHEN** `cluster.stop()` on external mode
- **THEN** raises `PostgresClusterNotOwnedError`

#### Scenario: Stop compose preserves volume

- **WHEN** `cluster.stop()` on a running compose cluster
- **THEN** runs `docker compose stop`, the named volume persists, `status()` returns `STOPPED`

#### Scenario: Stop already stopped is no-op

- **WHEN** `cluster.stop()` on a compose cluster already in `STOPPED`
- **THEN** returns normally without raising

### Requirement: No separate `start()` method

SDK MUST NOT предоставлять `PostgresCluster.start()`. Idempotent operation — `ensure_running()`. Это the required idempotent operation per issue #8.

#### Scenario: No start method

- **WHEN** introspection on `PostgresCluster`
- **THEN** no `start` method exists; lifecycle uses `status`, `ensure_running`, `stop`, `from_project`, `mode`, `owned`, plus the explicit consent operations `resolve_image_digest` and `approve_image`

### Requirement: Compose runtime artifacts layout

SDK-owned compose mode MUST хранить runtime artifacts в platformdirs user data directory:

```
<platformdirs-data>/odoo-instance-sdk/projects/<project-id>/postgres/
  compose.yaml
  postgres-password      # mode 0600
```

`project-id` MUST быть deterministic и стабильный между запусками — использует существующий `repo_key(repository_root)` как идентификатор. Directory создаётся lazily при первом `up` (не при `init`).

`postgres-password` файл MUST быть `0600`, written atomically (`tempfile.mkstemp` + `os.replace`), и не перезаписываться если уже существует (idempotent `up`).

#### Scenario: Artifacts created on first up

- **WHEN** `cluster.ensure_running()` on a compose cluster with no existing artifacts
- **THEN** directory is created, `compose.yaml` and `postgres-password` (mode 0600) are written atomically

#### Scenario: Existing password is preserved

- **WHEN** `cluster.ensure_running()` on a compose cluster with existing `postgres-password`
- **THEN** the existing password file is not overwritten

#### Scenario: Project id is deterministic

- **WHEN** `PostgresCluster.from_project("/repo")` is called twice on the same repository
- **THEN** both instances resolve the same `project-id` (same `repo_key`)

### Requirement: Generated Compose service is minimal and fixed

Generated `compose.yaml` MUST содержать ровно one service со следующими свойствами:

- image — exact requested PostgreSQL-compatible image из manifest;
- ports — loopback-only publishing `127.0.0.1:<port>:5432`;
- one named volume для `PGDATA`;
- `pg_isready` healthcheck (interval 2s, timeout 3s, retries 30, start_period 5s);
- file-backed Compose secret `postgres_password` и `POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password`;
- deterministic Compose project name `odcli_pg_<project-id>`;
- NO `container_name`, NO `build`, NO `extends`, NO custom networks, NO server configuration mounts (no `postgresql.conf` mount).

SDK MUST validate generated `compose.yaml` через `docker compose -f <file> config --quiet` перед atomic publish; invalid → `PostgresComposeInvalidError`.

SDK MUST start managed cluster через `docker compose -p <project> -f <file> up --detach --wait`.

#### Scenario: Compose file is minimal

- **WHEN** `compose.yaml` is generated for `mode="compose", image="pgvector/pgvector:pg16", port=5468, user="odoo"`
- **THEN** file contains one service, one volume, one secret, loopback port binding, healthcheck, and none of `container_name`/`build`/`extends`/custom networks

#### Scenario: Invalid compose is rejected

- **WHEN** generated `compose.yaml` fails `docker compose config --quiet`
- **THEN** raises `PostgresComposeInvalidError` and does not publish the file

### Requirement: Secrets absent from manifest, args, logs, JSON, repr, exceptions

Passwords MUST отсутствовать в:

- `.odcli/project.toml` (через существующий `assert_no_secrets`);
- process arguments (используется `POSTGRES_PASSWORD_FILE`, не `POSTGRES_PASSWORD=...`);
- logs (SDK не логирует пароль);
- JSON envelope output;
- `__repr__` of `PostgresCluster`, `StartConfig`-like;
- exception messages (`PostgresClusterUnreachableError`/etc. содержат только `host:port`, `mode`, `owned`, `state`).

Generated secret/config files MUST быть `0600`.

#### Scenario: Manifest contains no password

- **WHEN** `ProjectConfig.to_manifest()` is rendered for a compose-mode project
- **THEN** output contains `[postgres]` section but no `password` key (asserted by `assert_no_secrets`)

#### Scenario: Process args use file-backed secret

- **WHEN** `docker compose up` is invoked for a managed cluster
- **THEN** command line contains `-f <file>` and project name but no `POSTGRES_PASSWORD=<value>`; compose file references `POSTGRES_PASSWORD_FILE`

#### Scenario: Exception message is redacted

- **WHEN** `PostgresClusterUnreachableError` is raised
- **THEN** message contains `host:port` and `mode` but no password

### Requirement: Typed redacted errors

SDK MUST предоставлять следующие typed errors (все наследники `PostgresClusterError`, который наследует `OdooInstanceSdkError`):

- `PostgresClusterError` — base
- `PostgresClusterNotOwnedError` — `stop()` on external
- `PostgresClusterUnreachableError` — external `ensure_running` fail
- `PostgresClusterUnhealthyError` — compose `ensure_running` unhealthy
- `PostgresClusterStartError` — `compose up` non-timeout failure
- `PostgresClusterTimeoutError` — `ensure_running` timeout
- `PostgresComposeUnavailableError` — Docker/Compose CLI not in PATH
- `PostgresComposeInvalidError` — generated `compose.yaml` invalid
- `PostgresPortCollisionError` — allocated/persisted port not free at `up`

Все error messages MUST быть redacted (без пароля, без полной строки подключения).

#### Scenario: Stop external raises typed error

- **WHEN** `cluster.stop()` on external mode
- **THEN** raises `PostgresClusterNotOwnedError`, not generic `Exception`

#### Scenario: Compose unavailable raises typed error

- **WHEN** `cluster.ensure_running()` on compose mode and `docker` not in PATH
- **THEN** raises `PostgresComposeUnavailableError`

### Requirement: No new Python dependency

Implementation MUST NOT добавлять новые Python зависимости. Используется stdlib + установленный `docker compose` CLI. Никаких `docker-py`, `PyYAML`, `psycopg`, второй SQLite registry, daemon или generic service manager.

Compose file генерируется как text (без PyYAML). Compose CLI вызывается через `subprocess`.

#### Scenario: No new deps in pyproject

- **WHEN** `pyproject.toml` is inspected after implementation
- **THEN** `[project.dependencies]` does not include `docker`, `pyyaml`, `psycopg`, or any new package

### Requirement: Cross-project centralized port allocation

Port allocation MUST быть централизованной через единый helper `internal.port_allocation.find_free_port(kind, catalog, exclude_project)`, который итерирует по существующим источникам (catalog.environments + project manifests) — **не** отдельный registry портов, который может разъехаться после ручных правок конфигов.

`find_free_port` MUST:

1. Итерировать `catalog.list_environments()` → для каждого environment читать generated `odoo.conf` по `generated_config_path` и собирать `http_port` (Odoo HTTP). Catalog MUST NOT хранить `http_port`/`http_interface`.
2. Для каждого `repository_root` из catalog environments читать `.odcli/project.toml` через `ProjectConfig.load` → собирать `postgres.port` (compose mode) и `preferred_http_port`.
3. Live `probe_address` на кандидата.
4. Возвращать первый свободный порт в kind-специфичном диапазоне, не занятый ни в одном источнике.

`kind` — `"postgres"` (range `[5468, 65535)`) или `"http"` (range `[8069, 8099]`).

`exclude_project` (optional `Path`) — пропускает порты собственного проекта (его manifest), чтобы re-init / checkout не видел собственные `preferred_http_port` / `postgres.port` как collision.

Single source of truth — manifest'ы и generated `odoo.conf`, не отдельный state file и не колонки catalog. Ручные правки конфигов отражаются автоматически при следующей аллокации.

`EnvironmentResource._allocate_port` MUST делегировать в `find_free_port("http", ...)`, сохраняя existing behavior. `cli.py` postgres port allocation MUST делегировать в `find_free_port("postgres", ...)`.

#### Scenario: Postgres port allocation checks other projects

- **WHEN** `find_free_port("postgres", catalog)` is called and project A has `[postgres] port = 5468` and project B is being initialized
- **THEN** 5468 is excluded from candidates even if `probe_address` reports it free (container stopped)

#### Scenario: HTTP port allocation checks other projects

- **WHEN** `find_free_port("http", catalog)` is called and project A's manifest has `preferred_http_port = 8070` and project B is being initialized
- **THEN** 8070 is excluded from candidates even if no environment is currently running on it

#### Scenario: Manual config edit reflected

- **WHEN** a user manually edits `.odcli/project.toml` to change `postgres.port` to 5500, then runs `init` for a different project
- **THEN** 5500 is excluded from candidates (manifest is the source of truth, not a stale registry)

#### Scenario: Manual generated odoo.conf edit reflected

- **WHEN** a user manually edits an environment's generated `odoo.conf` `http_port` to 8077, then another environment is checked out
- **THEN** 8077 is excluded from candidates (generated config is the source of truth, not a catalog column)

#### Scenario: exclude_project skips own ports

- **WHEN** `find_free_port("postgres", catalog, exclude_project="/repo")` is called and `/repo` has `[postgres] port = 5468`
- **THEN** 5468 is NOT excluded (it's the current project's own port); re-init doesn't collide with itself

#### Scenario: No new state file

- **WHEN** port allocation runs
- **THEN** no new JSON/SQLite/file is written; only existing catalog + manifests are read

### Requirement: Unit and integration tests

Unit tests MUST использовать fake command runner (injected `ComposeRunner` Protocol) — без Docker. Все offline, marked `unit`.

One opt-in disposable integration test MUST доказывать init → up/healthy → instance preflight → stop while preserving the volume. Marked `integration`, skip если `docker` unavailable.

#### Scenario: Unit tests use fake runner

- **WHEN** unit tests run
- **THEN** `ComposeRunner` is injected, no Docker invoked

#### Scenario: Integration test skips without Docker

- **WHEN** integration test runs and `docker` not in PATH
- **THEN** test is skipped, not failed

#### Scenario: Integration test preserves volume

- **WHEN** integration test runs `init` → `up` → `instance.run_foreground()` (preflight) → `stop`
- **THEN** the named volume persists after `stop`
