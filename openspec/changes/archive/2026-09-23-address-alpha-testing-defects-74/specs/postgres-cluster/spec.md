## MODIFIED Requirements

### Requirement: `ensure_running()` is idempotent

`PostgresCluster.ensure_running_command()` SHALL remain idempotent. `odcli postgres up` SHALL build its diagnostic result from the captured cluster even when `ensure_running_command()` returns `None` (success). The result SHALL show actual `mode`, `owned`, `state`, and `endpoint` in Rich/JSON/TOON, not `unknown`/`false`/`—`. A failed, empty, or unparseable Docker metrics snapshot SHALL NOT imply `STOPPED`; `stopped` SHALL follow only from a successful `PostgresCluster.status_command()`.

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

#### Scenario: successful postgres up shows real state

- **WHEN** `odcli postgres up` succeeds and `ensure_running_command()` returns `None`
- **THEN** the diagnostic result is built from the captured cluster and shows actual `mode`, `owned`, `state`, and `endpoint`

#### Scenario: failed metrics do not imply stopped

- **WHEN** a running cluster has an empty or unparseable Docker metrics snapshot
- **THEN** the cluster state comes from `PostgresCluster.status_command()`, not from the metrics snapshot
