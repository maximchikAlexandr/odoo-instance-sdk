## MODIFIED Requirements

### Requirement: Port auto-allocation during checkout

When `--http-port` is absent, checkout MUST choose a free HTTP port from 8000 through 9000 inclusive. If `preferred_http_port` is inside that range it MUST be checked first; when preference is absent, 8069 MUST be checked first; an out-of-range preference MUST be ignored. If that candidate is unavailable or already allocated, checkout MUST scan the full range in ascending order without checking the first candidate twice. Each candidate MUST be free in the catalog, project manifests, generated configurations, and the live bind probe. Only after exhaustion SHALL checkout raise `EnvironmentConflictError` with code `no_free_port` and message `No available HTTP port in automatic allocation range 8000–9000; all candidates are allocated or unavailable. Pass --http-port explicitly.`

An explicit `--http-port` MUST remain an exact request, including outside the automatic range. Checkout MUST check only that value and return `port_in_use` on conflict without fallback. Automatic selection occurs only during checkout before first run. The registry does not reserve the OS port, so the existing live bind recheck before process start SHALL remain. PostgreSQL allocation SHALL remain unchanged.

#### Scenario: Default candidate is free

- **WHEN** checkout omits `--http-port`, has no preferred port, and 8069 is free
- **THEN** `http_port = 8069` SHALL be recorded

#### Scenario: Full-range fallback finds a lower port

- **WHEN** the first candidate is occupied or allocated and 8000 is the first free port in ascending range order
- **THEN** checkout SHALL record 8000

#### Scenario: In-range preferred port is tried first

- **WHEN** preferred port 8168 is configured and free
- **THEN** checkout SHALL select 8168 before scanning the range
- **WHEN** 8168 is unavailable and 8000 is free
- **THEN** checkout SHALL select 8000

#### Scenario: Out-of-range preference is ignored

- **WHEN** preferred port is outside 8000–9000 and `--http-port` is absent
- **THEN** checkout SHALL scan only the automatic range from 8000

#### Scenario: Boundaries are inclusive

- **WHEN** the only free automatic candidate is 8000 or 9000
- **THEN** checkout SHALL select that boundary value

#### Scenario: Automatic range is exhausted

- **WHEN** every candidate from 8000 through 9000 is allocated or unavailable
- **THEN** checkout SHALL return the specified `no_free_port` failure after checking the full range

#### Scenario: Explicit out-of-range request is exact

- **WHEN** the caller supplies `--http-port 19069`
- **THEN** checkout SHALL select 19069 if free or return `port_in_use` if unavailable, without fallback
