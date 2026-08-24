## MODIFIED Requirements

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
snapshot = cluster.resource_snapshot()  # NEW: read-only container identity + metrics
```

`resolve_image_digest` and `approve_image` are public compose-only consent operations; external mode raises `PostgresClusterNotOwnedError`. `status()` remains read-only and intentionally does not acquire the lifecycle lock; `ensure_running()` and `stop()` acquire the canonical lock for all state-changing transitions. `resource_snapshot()` (new) is read-only, does not acquire the lifecycle lock, does not start/stop the cluster, и возвращает typed `ClusterResourceSnapshot` или `None` для external cluster. `EnvironmentMonitor` потребляет `PostgresCluster.resource_snapshot()` напрямую. Collector MUST call `PostgresCluster.from_project(repository_root)` so compose naming stays `odcli_pg_{repo_key}` (unprefixed). Batch `docker inspect`/`stats` for several projects goes through `internal/cluster_resources.py` (not a public abstraction).

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

#### Scenario: resource_snapshot is read-only

- **WHEN** `cluster.resource_snapshot()` is called on a compose cluster
- **THEN** it returns a `ClusterResourceSnapshot` without acquiring the lifecycle lock and without starting/stopping the cluster

### Requirement: `status()` is read-only

`PostgresCluster.status() -> PostgresClusterState` MUST:

- никогда не изменять cluster state (no Docker start/stop, no file creation);
- для `external` — TCP probe endpoint (через `internal.address.probe_address` или эквивалент); `HEALTHY` если reachable, `UNREACHABLE` иначе; никогда не вызывает Docker;
- для `compose` — через Compose CLI (`ps`/`pg_isready`); `STOPPED` если контейнеров нет, `STARTING`/`HEALTHY`/`UNHEALTHY` по healthcheck; `UNKNOWN` если Docker unavailable (без raise);
- не поднимать и не останавливать cluster;
- не логировать пароль;
- возвращать `PostgresClusterState`, не падать при transient Docker errors (возвращает `UNKNOWN`).

`status()` не дублирует preflight — это отдельная read-only operation. `resource_snapshot()` (новый) — отдельная read-only operation для container identity + resource metrics; `status()` возвращает только lifecycle state.

#### Scenario: External status probes without Docker

- **WHEN** `cluster.status()` on external mode with reachable endpoint
- **THEN** returns `HEALTHY` and does not call Docker

#### Scenario: Compose status detects stopped

- **WHEN** `cluster.status()` on compose mode after `stop()`
- **THEN** returns `STOPPED`

#### Scenario: Docker unavailable returns UNKNOWN

- **WHEN** `cluster.status()` on compose mode and `docker` not in PATH
- **THEN** returns `UNKNOWN` without raising

### Requirement: `resource_snapshot()` — read-only container identity and metrics

`PostgresCluster.resource_snapshot() -> ClusterResourceSnapshot | None` MUST:

- быть read-only: не запускать/останавливать cluster, не вызывать `compose up`/`stop`, не создавать файлы, не acquire lifecycle lock;
- для `external` — возвращать `None` (external cluster не инспектируется SDK);
- для `compose` — через read-only `docker inspect`/`docker stats --no-stream` (existing Compose runner / `subprocess`, не docker-py) resolve container via `compose_project_name(repo_key)` + service `postgres`, и возвращать:
  - container ID (12 hex short), name, image;
  - Docker-reported init PID + `PidScope` (`host` on native Linux, `docker_vm` on macOS, `unavailable` when stopped/missing). Docker Desktop on Linux is reported as `host` (`sys.platform`); that limitation is accepted.
  - container CPU percent, memory usage/limit bytes, optional managed volume usage bytes (from `docker inspect` Mounts named volume + `docker system df -v` JSON; if the command fails or the volume is absent → `None`; no host bind-mount traversal);
  - `sampled_at` (one UTC datetime copied to `ClusterMetrics.sampled_at`; both None when metrics is None);
  - `unavailability_reason="stopped"` iff `status() == STOPPED`; `"missing"` iff `status()` is not `STOPPED` and the `postgres` container ID cannot be resolved after `compose ps`; also `docker_unavailable` / `inspect_failed` / `stats_failed`;
- для stopped/missing compose — `ClusterResourceSnapshot` with `container=None`, `metrics=None` and the reason above (не `None`);
- не падать при transient Docker errors;
- не логировать пароль и не возвращать raw Docker inspect payload;
- не отображать individual PostgreSQL backend PIDs; container init PID only.

`ClusterResourceSnapshot` fields: `container: ClusterContainer | None`, `metrics: ClusterMetrics | None`, `unavailability_reason: str | None`, `sampled_at: datetime | None`. Collector maps this plus `status()` plus endpoint into `ClusterSnapshot`.

`resource_snapshot()` MUST NOT cache between calls. `EnvironmentMonitor` owns the 15s inspect/stats cache and the 5s `status()` cache.

#### Scenario: External resource_snapshot returns None

- **WHEN** `cluster.resource_snapshot()` on external mode
- **THEN** returns `None` (no Docker invocation)

#### Scenario: Compose healthy returns container metrics

- **WHEN** `cluster.resource_snapshot()` on a healthy compose cluster
- **THEN** returns `ClusterResourceSnapshot` with non-null `container` (id/name/image/pid/pid_scope) and `metrics` (cpu_percent/memory_usage_bytes/memory_limit_bytes)

#### Scenario: Linux host PID scope

- **WHEN** `resource_snapshot()` runs on native Linux with Docker daemon host PID namespace
- **THEN** `container.pid_scope == "host"`, `container.pid` is a host PID

#### Scenario: macOS Docker VM PID scope

- **WHEN** `resource_snapshot()` runs on macOS Docker Desktop/Colima
- **THEN** `container.pid_scope == "docker_vm"`, `container.pid` is a Linux-VM PID (not a macOS PID)

#### Scenario: Missing compose container

- **WHEN** `resource_snapshot()` runs on compose mode, `status()` is not `STOPPED`, and `compose ps` does not yield a `postgres` container ID
- **THEN** returns `ClusterResourceSnapshot` with `container=None`, `metrics=None`, `unavailability_reason="missing"`

- **WHEN** `cluster.resource_snapshot()` on a stopped compose cluster
- **THEN** returns `ClusterResourceSnapshot` with `container=None`, `metrics=None`, `unavailability_reason="stopped"` (not `None`)

#### Scenario: Docker unavailable does not raise

- **WHEN** `docker` is not in PATH and `resource_snapshot()` runs on compose mode
- **THEN** returns `ClusterResourceSnapshot` with `unavailability_reason="docker_unavailable"`, no exception

#### Scenario: No raw Docker payload in snapshot

- **WHEN** `resource_snapshot()` returns for a compose cluster
- **THEN** `ClusterResourceSnapshot` exposes only the redacted fields (id/name/image/pid/pid_scope/cpu/mem/volume/sampled_at/reason); no env vars, no `POSTGRES_PASSWORD_FILE` value, no raw inspect JSON

#### Scenario: No backend PIDs exposed

- **WHEN** `resource_snapshot()` runs on a healthy compose cluster with active client connections
- **THEN** only the container init PID is returned; individual PostgreSQL backend PIDs are not exposed

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