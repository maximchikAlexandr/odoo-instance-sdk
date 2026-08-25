## MODIFIED Requirements

### Requirement: `odcli env list`

```bash
odcli env list
odcli env list --all
odcli env list --json
```

Default table (новое в этом change): human output MUST группироваться по project — один project header (имя + cluster summary) на project, затем environment rows этого project. `--all-projects` MUST печатать по одной секции на project (поведение по умолчанию для CLI без project context эквивалентно `--all-projects`).

Project header:

```text
Project comerta
  PostgreSQL  healthy  container=4fc83d  pid=vm:9124  cpu=4.2%  ram=512 MiB  disk=12 GiB
```

Для external/stopped/missing cluster — явные `external`/`stopped`/`—` в соответствующих полях (без падения).

Exact environment-row columns, left to right (space-separated, compact truncation, no wrap):

`NAME  BRANCH  STATE  RUNTIME  OBSERVED  ODOO_PID  CPU  RAM  GIT_AHEAD  GIT_DIFF  SIZE  DB_MODE  DATABASE  PORT  ARTIFACTS`

```text
  NAME              BRANCH          STATE  RUNTIME   OBSERVED      ODOO_PID     CPU    RAM      GIT_AHEAD  GIT_DIFF   SIZE     DB_MODE  DATABASE     PORT  ARTIFACTS
  comerta-CMRT-376  feat/CMRT-376   ready  ready     port-occupied 43120 (+2)   37.4%  1.2 GiB  ↑4 ↓1      +234 -24   3.2 GiB  copy     comerta_376  8070  ok
  comerta-main      main            ready  stopped   —             —            —      —        ↑0 ↓0      +0 -0      2.6 GiB  shared   comerta      8069  ok
```

- `PORT` = `runtime.http_port` when `RUNTIME` is `ready` or `not_ready`, else `allocated_http_port` (or `—` if both None);
- `STATE` = `lifecycle_state` from snapshot when the row is in snapshot, else catalog `state` for `--all` removed rows;
- `RUNTIME` = `RuntimeState` (`stopped`/`ready`/`not_ready`);
- `OBSERVED` = `port-free`/`port-occupied` iff `lifecycle_state=="ready"` and `runtime.state=="ready"` and `allocated_http_port` is not None; probe via existing `probe_address`/`_check_port_free` on `(StartConfig.http_interface, allocated_http_port)` from `StartConfig.from_odoo_config(generated_config_path)` (same source as snapshot `allocated_http_port`). Otherwise `—` (`runtime.state` in `{stopped, not_ready}`, any non-`ready` lifecycle, missing config, `--all` removed rows).
- `ODOO_PID` = `{root} (+{n})` where n = `len(child_pids)`, or `—` when `RUNTIME=stopped`;
- `GIT_AHEAD` = `↑{ahead} ↓{behind}` or `—` when orphan;
- `GIT_DIFF` = `+{added} -{deleted}` or `—` when orphan;
- `SIZE` = environment `storage.total_bytes` humanized; prefix `>=` when `complete=False`;
- `ARTIFACTS` = existing reconciliation compact list (`ok` or comma-separated missing names: `worktree,registered,config,python,python-contained,lock,backup`);
- Drop from human output: `ID`, `PYTHON_MODE`, `LAST_USED`, `WORKTREE` (absolute paths). IDs remain in `--json` snapshot payload as `environments[].id`.
- PostgreSQL PID/resources appear only on the project header, never on environment rows.
- `failed`/`cleanup_failed` remain visible; `removed` hidden unless `--all`.

Cluster summary and metric columns for non-removed rows MUST come from `EnvironmentMonitor.snapshot()` (no second collector). `--all` is human-only: enumerate `BackupCatalog.list_environments(include_removed=True)`, merge snapshot metrics by `environment.id`. Removed rows (absent from snapshot) print `RUNTIME=—`, `ODOO_PID=—`, `CPU=—`, `RAM=—`, `GIT_AHEAD=—`, `GIT_DIFF=—`, `SIZE=—`, `OBSERVED=—`; `STATE=removed`; `PORT` from generated config if readable else `—`; `ARTIFACTS` from reconciliation. `--json` always wraps non-removed `Snapshot` only; `--all` MUST NOT change the JSON payload.

`--json` wraps that `Snapshot` in CLI envelope v1 `result`/`data` (`command="env.list"`). `schema_version` inside the snapshot is `1`.

Existing `--all` (include removed) и `--all-projects` flags сохраняются. Default `env list` без project context работает как `--all-projects`.

Reconciliation (`ARTIFACTS`) MUST still run in the CLI after `snapshot()`: worktree/config/Python/lock/backup existence using the existing `_reconcile_environment` checks. `OBSERVED` MUST reuse existing `probe_address`/`_check_port_free` on `(StartConfig.http_interface, allocated_http_port)` from the generated `odoo.conf`; do not probe `runtime.http_port` and do not add a new bind path. One reconciliation failure MUST NOT drop the row (`ARTIFACTS` lists the failed check).

#### Scenario: --all human includes removed, JSON does not

- **WHEN** `odcli env list --all` prints human table and `odcli env list --json --all` emits JSON
- **THEN** human table includes `STATE=removed` rows; JSON `result.environments` contains only non-removed snapshot rows

#### Scenario: Default hides removed

- **WHEN** `env list` без `--all`
- **THEN** `removed` environments скрыты, `failed`/`cleanup_failed` видны

#### Scenario: Grouped by project with cluster header

- **WHEN** `env list` runs with two projects
- **THEN** output has two `Project <name>` headers each followed by a `PostgreSQL ...` cluster summary line, then that project's environment rows

#### Scenario: Stopped environment row

- **WHEN** an environment has no running Odoo
- **THEN** its row shows `RUNTIME=stopped`, `ODOO_PID=—`, `CPU=—`, `RAM=—`, Git/Size still populated, `OBSERVED=—`; `STATE` remains the catalog lifecycle value

#### Scenario: External cluster header

- **WHEN** `env list` runs for a project with `[postgres] mode="external"`
- **THEN** cluster header shows `external` (no container/pid/cpu/ram/disk fields)

#### Scenario: JSON parity with monitor snapshot

- **WHEN** `odcli env list --json --all-projects` runs
- **THEN** `result`/`data` payload uses the same `projects[].cluster` and `environments[].runtime` contract as `EnvironmentMonitor.snapshot()` and `GET /api/v1/snapshot`

#### Scenario: One environment failure does not crash list

- **WHEN** Git CLI fails for one environment's worktree
- **THEN** that environment's row shows an error indicator, other projects/environments still listed

#### Scenario: Reconciliation detects missing worktree

- **WHEN** `env list` (с reconciliation) для environment где worktree отсутствует в `git worktree list --porcelain -z`
- **THEN** environment listed с indicator missing worktree

#### Scenario: OBSERVED reflects allocated port probe

- **WHEN** `env list` for an environment with `lifecycle_state=="ready"`, `runtime.state=="ready"`, and `_check_port_free(http_interface, allocated_http_port)` succeeds
- **THEN** `OBSERVED` = `port-free`; if that probe fails → `port-occupied`; if `runtime.state` is `stopped` or `not_ready`, or `lifecycle_state` is not `ready`, or `allocated_http_port` is None → `—`

#### Scenario: Reconciliation detects missing generated config

- **WHEN** `env list` для environment где generated `odoo.conf` missing
- **THEN** environment listed с indicator missing config

#### Scenario: Reconciliation detects missing owned backup

- **WHEN** `env list` для copy environment где owned backup file missing
- **THEN** environment listed с indicator missing backup

#### Scenario: Reconciliation detects missing Python or lock

- **WHEN** `env list` для environment где recorded Python path не существует OR `requirements.lock` missing
- **THEN** environment listed с indicator missing Python/lock

### Requirement: `odcli postgres status`

```bash
odcli postgres status [--json]
```

`status` MUST быть read-only (не меняет cluster state). `status` MUST NOT вызывать Docker в external mode (только TCP probe).

Расширение (новое в этом change): human и `--json` output дополнительно возвращают read-only cluster container fields (parity с monitor cluster snapshot):

- container ID (short), name, image;
- Docker-reported init PID + PID scope (`host`/`docker_vm`/`unavailable`);
- container CPU percent, memory usage/limit bytes, optional volume usage bytes;
- `sampled_at` timestamp;
- `unavailability_reason` для stopped/missing/external/docker-unavailable.

Для external mode — container/resource fields `null` с `unavailability_reason="external_not_owned"`. Для stopped/missing compose — container/resource fields `null` с `unavailability_reason="stopped"`/`"missing"`. Для docker-unavailable — `unavailability_reason="docker_unavailable"`, exit 0 (не error; diagnostic).

Human и `--json` значения MUST совпадать с project cluster snapshot из `odcli monitor` / `EnvironmentMonitor` (один collector, parity). `--json`: JSON envelope v1 с `result` содержащим `state`, `mode`, `owned`, `endpoint` (redacted) + новые container/resource fields.

`postgres status` MUST call both `cluster.status()` and `cluster.resource_snapshot()`, then emit a `ClusterSnapshot`-shaped object:

- `mode` / `owned` / `endpoint` from `PostgresCluster` properties;
- `state` from `status()`;
- `container` / `metrics` / `unavailability_reason` / `sampled_at` from `resource_snapshot()` when not `None`;
- when `resource_snapshot()` is `None` (external): `container=None`, `metrics=None`, `unavailability_reason="external_not_owned"`, `sampled_at=None`.

Do not invent a third Docker inspect path. Docker error MUST NOT crash the command.

#### Scenario: Status inside initialized project

- **WHEN** `odcli postgres status` runs inside a project with `[postgres] mode="compose"`
- **THEN** output reports `state`, `mode`, `owned`, `endpoint`, container ID/name/image/PID+scope, CPU, memory, optional volume without starting/stopping cluster

#### Scenario: Status JSON envelope with container fields

- **WHEN** `odcli postgres status --json` runs on a healthy compose cluster
- **THEN** JSON envelope v1 `result` contains `state`, `mode`, `owned`, `endpoint`, `container` (id/name/image/pid/pid_scope), `metrics` (cpu_percent/memory_usage_bytes/memory_limit_bytes/volume_usage_bytes), `sampled_at`

#### Scenario: Status external does not invoke Docker

- **WHEN** `odcli postgres status` on external mode
- **THEN** only TCP probe is performed, container/resource fields `null` with `unavailability_reason="external_not_owned"`, no `docker compose`/`docker inspect` invocation

#### Scenario: Status stopped compose

- **WHEN** `odcli postgres status` on a stopped compose cluster
- **THEN** `state=stopped`, container/resource fields `null`, `unavailability_reason="stopped"`, exit 0

#### Scenario: Docker unavailable is diagnostic not error

- **WHEN** `odcli postgres status` on compose mode and `docker` not in PATH
- **THEN** `unavailability_reason="docker_unavailable"`, exit 0 (not 1); human output shows diagnostic

#### Scenario: Parity with monitor cluster snapshot

- **WHEN** `odcli postgres status --json` and `odcli monitor --headless` `GET /api/v1/snapshot` run in the same instant for the same project
- **THEN** container PID/resource values match between the two outputs

## ADDED Requirements

### Requirement: `odcli monitor` command

```bash
odcli monitor [--headless] [--host HOST] [--port PORT] [--no-open]
```

`odcli monitor` MUST запускать FastAPI server, обслуживающий:

- `GET /api/v1/snapshot` — возвращает один `Snapshot` JSON (контракт `EnvironmentMonitor.snapshot()`, `schema_version=1`); supports optional `?project_id=<opaque>` query;
- `GET /healthz` — возвращает `{"status":"ok"}` HTTP 200 (liveness, без cluster/catalog probe).

Default UI mode (без `--headless`): serves `/api/v1/snapshot`, `/healthz`, and the React SPA. Default bind `127.0.0.1`. `--host` overrides bind address. `--port` binds that exact port (exit 1 if occupied). Without `--port`: try `8069`, then scan `8100`–`8120` inclusive; never auto-select `8070`–`8099`. If all of those ports are occupied, exit 1. Default opens `http://<host>:<port>/`; `--no-open` suppresses.

Headless mode (`--headless`): обслуживает только versioned JSON API (`/api/v1/snapshot`, `/healthz`); static assets НЕ монтируются, браузер НЕ открывается. Built-in server binds loopback hosts only (`127.0.0.1`, `localhost`, `::1`) and rejects every non-loopback `--host`, because it has no authentication. It also accepts only loopback HTTP Host headers. API не возвращает credentials, environment variables, command line, secret paths, absolute local paths или raw Docker inspect payload.

Обычный `GET /api/v1/snapshot` достаточен для polling; frontend polls every 2000 ms.

Требуемый extra: `dashboard` (FastAPI + Uvicorn + `metrics`). Без extra команда завершается с короткой actionable install hint (`pip install odoo-instance-sdk[dashboard]`), exit 1. `odcli monitor` не добавляется, если extra `dashboard` не установлен (runtime import guard).

#### Scenario: Default UI mode serves SPA and API

- **WHEN** `odcli monitor` runs without `--headless`
- **THEN** FastAPI serves `/api/v1/snapshot`, `/healthz` and the React SPA; browser opens on `http://127.0.0.1:<port>/`

#### Scenario: Headless serves API only

- **WHEN** `odcli monitor --headless --no-open` runs
- **THEN** `/api/v1/snapshot` and `/healthz` respond; static assets not mounted; browser not opened

#### Scenario: Healthz liveness

- **WHEN** `GET /healthz` is requested
- **THEN** returns `{"status":"ok"}` HTTP 200 without probing catalog/cluster

#### Scenario: Snapshot with project filter

- **WHEN** `GET /api/v1/snapshot?project_id=project_comerta_7e3d` runs
- **THEN** response contains only the matching project's `ProjectSummary` and `EnvironmentSnapshot`s

#### Scenario: Default loopback bind

- **WHEN** `odcli monitor` runs without `--host`
- **THEN** server binds `127.0.0.1`, not `0.0.0.0`

#### Scenario: Non-loopback is rejected

- **WHEN** `odcli monitor --host 0.0.0.0` runs
- **THEN** exits non-zero before binding and explains that unauthenticated network binds are refused

#### Scenario: Missing dashboard extra actionable hint

- **WHEN** `odcli monitor` runs and `fastapi`/`uvicorn` not installed
- **THEN** exits 1 with message containing `pip install odoo-instance-sdk[dashboard]`

#### Scenario: Port auto-select when 8069 occupied

- **WHEN** `odcli monitor` runs without `--port` and `127.0.0.1:8069` is already in use
- **THEN** server binds the first free loopback port in `8100–8120` inclusive and the opened browser URL uses that port

#### Scenario: No secrets in API response

- **WHEN** `GET /api/v1/snapshot` returns JSON
- **THEN** no field contains a password, env var, command line, absolute local path or raw Docker inspect payload
