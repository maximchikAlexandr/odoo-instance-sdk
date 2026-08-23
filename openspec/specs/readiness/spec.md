## Purpose

Define how an Odoo process is probed until it becomes ready or reaches a terminal failure.

## Requirements

### Requirement: Readiness check

`instance.wait_ready(proc, *, timeout=60.0)` MUST выполнять GET `/web/health?db_server_status=true` на normalized `OdooInstance.base_url` без HTTP Basic Auth и без master password.

Odoo 19.0 endpoint `/web/health` имеет `auth="none"` и возвращает JSON `{"status": "pass"}` с HTTP 200. Метод MUST:

1. периодически опрашивать endpoint в течение `timeout`;
2. на каждый poll проверять, что процесс ещё alive;
3. вернуть `ReadinessResult` с `ok=True` при `status == "pass"`;
4. выбросить `ProcessExitedBeforeReady`, если процесс завершился до readiness;
5. выбросить `ReadinessTimeoutError`, если readiness не достигнут за `timeout`.

Метод MUST NOT использовать Basic Auth и MUST NOT читать `master_pwd` из `InstanceConfig`.

#### Scenario: Ready сервер

- **WHEN** процесс запущен и `/web/health` возвращает `{"status": "pass"}`
- **THEN** `wait_ready` возвращает `ReadinessResult(ok=True)`

#### Scenario: Процесс упал до readiness

- **WHEN** процесс завершился до того, как `/web/health` ответил `pass`
- **THEN** `wait_ready` выбрасывает `ProcessExitedBeforeReady`

#### Scenario: Readiness timeout

- **WHEN** `/web/health` не отвечает `pass` в течение `timeout`
- **THEN** `wait_ready` выбрасывает `ReadinessTimeoutError`

### Requirement: `doctor` reports cluster state read-only

`odcli doctor` MUST сообщать cluster mode, ownership, endpoint, health и Docker Compose availability без изменения состояния (без `up`/`stop`/`config`).

Doctor MUST:

- после `_check_manifest` добавлять `_check_postgres(report, project_root)`;
- конструировать `PostgresCluster.from_project(project_root)` если manifest загружен;
- для compose mode и недоступного Docker (`shutil.which("docker") is None`) — `STATUS_WARN` с деталями;
- вызывать `cluster.status()` (read-only, не поднимает cluster);
- эмиттить `CheckResult("postgres.cluster", status, detail)` где detail содержит `mode`, `owned`, `state`, `endpoint` (redacted);
- `_state_to_status`: `HEALTHY`→`ok`, `STARTING`/`STOPPED`→`info`, `UNHEALTHY`/`UNREACHABLE`/`UNKNOWN`→`warn`;
- никогда не запускать и не останавливать cluster.

`doctor` JSON envelope MUST оставаться v1. Existing checks (manifest, uv, catalog, environment-specific) MUST оставаться без breaking changes.

#### Scenario: Doctor reports healthy compose cluster

- **WHEN** `odcli doctor` runs on a project with `[postgres] mode="compose"` and a healthy running cluster
- **THEN** a `postgres.cluster` check with `STATUS_OK` and `mode=compose owned=true state=healthy endpoint=127.0.0.1:<port>` is emitted

#### Scenario: Doctor warns on missing Docker for compose

- **WHEN** `odcli doctor` runs on a compose-mode project and `docker` not in PATH
- **THEN** a `postgres.compose` check with `STATUS_WARN` "docker not found in PATH" is emitted

#### Scenario: Doctor does not start cluster

- **WHEN** `odcli doctor` runs on a project with a stopped compose cluster
- **THEN** `status()` returns `STOPPED`, `doctor` reports `STATUS_INFO`, no `docker compose up` is invoked

#### Scenario: Doctor external mode reports reachability

- **WHEN** `odcli doctor` runs on an external-mode project with reachable endpoint
- **THEN** `postgres.cluster` check with `STATUS_OK` and `mode=external owned=false state=healthy endpoint=<host:port>` is emitted

#### Scenario: Doctor without project skips postgres check

- **WHEN** `odcli doctor` runs outside any project (no manifest found)
- **THEN** no `postgres.cluster` check is emitted; existing manifest error check remains
