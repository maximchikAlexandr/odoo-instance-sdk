## ADDED Requirements

### Requirement: `server.wait_ready()` polls `/web/health`

`server.wait_ready(proc: OdooProcess, *, timeout: float = 60.0)` SHALL poll `GET <base_url>/web/health?db_server_status=true` on a fixed poll interval (default 1 second) until readiness or `timeout`.

The instance SHALL be considered ready only when:
- HTTP status code is `200`; AND
- JSON body contains `status == "pass"`.

Any other response (non-200, network error, body missing or `status != "pass"`) SHALL trigger another poll iteration, up to `timeout`.

#### Scenario: Instance becomes ready
- **WHEN** `/web/health` returns `{"status": "pass"}` with HTTP 200
- **THEN** `wait_ready()` returns a `ReadinessResult` with `ok == True`

#### Scenario: Non-pass status keeps polling
- **WHEN** `/web/health` returns `{"status": "init"}` with HTTP 200
- **THEN** polling continues

#### Scenario: Network error keeps polling
- **WHEN** the HTTP request fails (connection refused)
- **THEN** polling continues without raising

### Requirement: Linked process early exit breaks polling

If the linked `OdooProcess` exits before readiness is reached, `wait_ready()` SHALL raise `ProcessExitedBeforeReady` immediately without waiting for `timeout`.

#### Scenario: Process dies during wait
- **WHEN** the linked process is reported as exited by the registry
- **THEN** `ProcessExitedBeforeReady` is raised
- **AND** no further HTTP requests are made

### Requirement: Timeout raises typed error

When `timeout` elapses without reaching readiness and the linked process is still running, `wait_ready()` SHALL raise `ReadinessTimeoutError`. The error SHALL expose `timeout` and the last response (if any) as typed attributes.

#### Scenario: Timeout without readiness
- **WHEN** `wait_ready(proc, timeout=1.0)` is called and the instance does not become ready within 1 second
- **THEN** `ReadinessTimeoutError` is raised

### Requirement: `ReadinessResult` fields

`ReadinessResult` (msgspec.Struct) SHALL contain:
- `ok: bool`
- `elapsed: float` — total time spent polling in seconds
- `attempts: int` — number of HTTP requests made
- `final_status: str | None` — last observed `status` value, or `None` if never seen

#### Scenario: Successful readiness result content
- **WHEN** readiness is reached after 3 polls over 2 seconds ending in `status="pass"`
- **THEN** `result.ok is True`
- **AND** `result.attempts == 3`
- **AND** `result.elapsed >= 2.0`
- **AND** `result.final_status == "pass"`