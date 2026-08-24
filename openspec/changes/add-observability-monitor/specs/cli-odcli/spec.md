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

Environment row:

```text
  Environment       Branch          State    Odoo PID       CPU    RAM       main↕    main…±      Size
  comerta-CMRT-376  feat/CMRT-376   ready    43120 (+2)    37.4%  1.2 GiB   ↑4 ↓1    +234 -24    3.2 GiB
  comerta-main      main            stopped  —              —      —         —         —           2.6 GiB
```

- environment row показывает Odoo root PID и количество child PIDs (`43120 (+2)` = root + 2 children), но не повторяет PostgreSQL PID/resources (те только в project header);
- сохраняются `main↕` (ahead/behind) и `main…±` (added/deleted) Git columns и одна environment **Size** column (total disk);
- при узком terminal действуют существующие правила compact output (truncate columns, не wrap);
- `OBSERVED` column (port-free/occupied) остаётся для `ready` environments; для stopped — `—`;
- `failed`/`cleanup_failed` видны (как раньше); `removed` скрыты (как раньше), `--all` их включает.

Cluster summary и environment rows берутся из одного `EnvironmentMonitor.snapshot()` (или эквивалентного collector helper), чтобы CLI не дублировал расчёт metrics. Ошибка Docker/psutil/Git для одного project/environment не роняет весь list; affected project/environment показывает partial с индикатором ошибки.

`--json` использует тот же top-level `projects[].cluster` и `environments[].runtime` contract, что Python SDK/FastAPI monitor snapshot (JSON envelope v1 оборачивает `Snapshot` в `result`/`data`; envelope shape CLI-specific, payload contract — общий с monitor). `schema_version` snapshot внутри envelope — `1`.

Existing `--all` (include removed) и `--all-projects` flags сохраняются. Default `env list` без project context работает как `--all-projects`.

Существующая quick reconciliation (worktree/config/Python/lock/backup existence, `OBSERVED` port state) MUST сохраняться — `env list` выполняет её в дополнение к потреблению `EnvironmentMonitor.snapshot()` (snapshot даёт cluster/runtime/Git/Size; reconciliation даёт per-artifact existence indicators и `OBSERVED` port probe, которые не входят в snapshot contract). `OBSERVED` вычисляется CLI через существующий `socket.bind((http_interface, http_port))` (не collector); для stopped/non-ready — `—`. Reconciliation индикаторы отображаются как и раньше (existing compact artifacts column или эквивалент). Ошибка одной компоненты reconciliation не роняет row.

#### Scenario: Default hides removed

- **WHEN** `env list` без `--all`
- **THEN** `removed` environments скрыты, `failed`/`cleanup_failed` видны

#### Scenario: Grouped by project with cluster header

- **WHEN** `env list` runs with two projects
- **THEN** output has two `Project <name>` headers each followed by a `PostgreSQL ...` cluster summary line, then that project's environment rows

#### Scenario: Stopped environment row

- **WHEN** an environment has no running Odoo
- **THEN** its row shows `State=stopped`, `Odoo PID=—`, `CPU=—`, `RAM=—`, Git/Size still populated, `OBSERVED=—`

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

#### Scenario: OBSERVED reflects live socket.bind

- **WHEN** `env list` для environment с allocated port и `socket.bind((http_interface, http_port))` succeeds
- **THEN** `OBSERVED` = `port-free`; если fails → `port-occupied`; для stopped/non-ready → `—`

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

`postgres status` переиспользует `PostgresCluster.resource_snapshot()` (или эквивалентный collector helper) — CLI не дублирует Docker inspect/stats расчёт. Ошибка Docker для одного project (multi-project case) не роняет status; affected project получает `unavailability_reason`.

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

Default UI mode (без `--headless`): обслуживает `/api/v1/snapshot`, `/healthz` и собранный React SPA (static assets); default bind `127.0.0.1:8069` (loopback); `--host` переопределяет; `--port` переопределяет (default auto-select free loopback port в disjoint range `8100–8120` если 8069 занят, чтобы не конфликтовать с environment port allocation range `[8069, 8099]` — monitor не должен отбирать порт у будущего environment checkout); по умолчанию открывает браузер на `http://<host>:<port>/`; `--no-open` suppress.

Headless mode (`--headless`): обслуживает только versioned JSON API (`/api/v1/snapshot`, `/healthz`); static assets НЕ монтируются, браузер НЕ открывается; предназначен для server-to-server интеграции или размещения за внешним control-plane proxy. CORS, authentication и TLS не изобретать внутри MVP; non-loopback bind (`--host 0.0.0.0` или non-loopback) — explicit opt-in (команда требует `--host` явно для non-loopback; default loopback не требует подтверждения); production boundary обеспечивает вызывающая система/reverse proxy. API не возвращает credentials, environment variables, command line, secret paths, absolute local paths или raw Docker inspect payload.

Обычный `GET /api/v1/snapshot` достаточен для polling; frontend polls ~раз в 2 секунды. SSE/WebSocket и persistent sampler не добавляются. Server переиспользует `EnvironmentMonitor.snapshot()` (один collector); FastAPI только сериализует typed snapshot через `msgspec.json.encode`.

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

#### Scenario: Non-loopback requires explicit host

- **WHEN** `odcli monitor --host 0.0.0.0` runs
- **THEN** binds `0.0.0.0` (explicit opt-in); no additional confirmation prompt (CORS/auth/TLS out of scope, boundary is caller responsibility)

#### Scenario: Missing dashboard extra actionable hint

- **WHEN** `odcli monitor` runs and `fastapi`/`uvicorn` not installed
- **THEN** exits 1 with message containing `pip install odoo-instance-sdk[dashboard]`

#### Scenario: Port auto-select when 8069 occupied

- **WHEN** `odcli monitor` runs without `--port` and `127.0.0.1:8069` is already in use
- **THEN** server binds the next free loopback port in disjoint range `8100–8120` (not `[8069, 8099]`, which is reserved for environment checkout) and the opened browser URL uses the selected port

#### Scenario: No secrets in API response

- **WHEN** `GET /api/v1/snapshot` returns JSON
- **THEN** no field contains a password, env var, command line, absolute local path or raw Docker inspect payload