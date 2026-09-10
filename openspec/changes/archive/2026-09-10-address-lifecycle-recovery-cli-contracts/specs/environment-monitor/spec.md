## ADDED Requirements

### Requirement: Platform-correct process-tree memory

Monitor schema v4 SHALL expose one nullable platform-neutral `memory_bytes` value for an Odoo process tree and SHALL label it as `Memory` in Rich output. On Darwin, the value SHALL be the sum of `ri_phys_footprint` for the validated root and each readable validated child, collected through the standard-library binding to `proc_pid_rusage(RUSAGE_INFO_V4)`. On other supported platforms, it SHALL remain the psutil RSS sum for the same process tree. Rich and v4 machine output SHALL use the identical selected value. Failed or unavailable measurement SHALL remain `null`/unavailable and SHALL NOT fall back to a differently defined metric. V4 SHALL NOT call physical footprint `rss_bytes`; older schema documents retain their historical meaning only as compatibility data.

#### Scenario: Darwin uses physical footprint

- **WHEN** a validated Darwin root and children return physical-footprint values
- **THEN** `memory_bytes` equals their sum and Rich `Memory` renders that exact total

#### Scenario: Non-Darwin retains RSS

- **WHEN** the same process tree is collected on another supported platform
- **THEN** `memory_bytes` equals the existing psutil RSS process-tree total

#### Scenario: Measurement is unavailable

- **WHEN** the selected platform adapter cannot read a required root measurement or detects PID reuse
- **THEN** machine output reports unavailable memory and Rich shows an explicit unavailable value rather than zero or a substituted metric

#### Scenario: Schema labels remain truthful

- **WHEN** v4 is exported through JSON, TOON, OpenAPI or generated dashboard types
- **THEN** the selected value is named `memory_bytes`, no physical-footprint value is emitted under `rss_bytes`, and no duplicate memory column is added
