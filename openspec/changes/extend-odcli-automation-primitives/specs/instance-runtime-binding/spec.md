## ADDED Requirements

### Requirement: Detached project launch owns one persisted runtime identity
Project `run --detach` SHALL capture the resolved project binding, database, filestore, argv, cwd, endpoint, process group, creation identity, and runtime owner before spawn and SHALL persist one project-owned runtime record only after the child is proven alive. `--wait-ready` SHALL wait for that exact captured process and endpoint; success returns `ready`, while early exit, timeout, health failure, or cleanup failure returns a stable reason and preserves only evidence required for safe retry. Status and stop SHALL address the same persisted owner/runtime identity rather than infer ownership from a port or current checkout contents.

#### Scenario: Detached project becomes ready
- **WHEN** `run --detach --wait-ready --project PATH` starts the selected project and its captured health endpoint becomes ready
- **THEN** the typed result reports project identity, runtime identity, endpoint, database, readiness, and log identity
- **AND** later status and stop target that same runtime record

#### Scenario: Readiness times out
- **WHEN** the exact captured child remains alive but does not become ready before the bounded timeout
- **THEN** OdCLI terminates only that captured process group, verifies exit, clears only its matching record, and returns the readiness and cleanup outcome

#### Scenario: Persisted identity no longer matches
- **WHEN** PID reuse, creation-time mismatch, executable mismatch, or owner-snapshot mismatch is observed
- **THEN** status reports stale/unavailable and stop refuses to signal the unrelated process

### Requirement: Project runtime consumes the published database binding
Project run planning SHALL resolve the current atomic main-checkout binding and generate the effective Odoo configuration from its target database and managed filestore. A missing, incomplete, unsafe, or stale binding SHALL fail before spawn; it SHALL NOT silently fall back to the source config database or a machine-wide default.

#### Scenario: Restored project is started
- **WHEN** preparation published a new project binding and project run is planned
- **THEN** the immutable run plan names that bound database and filestore identity and does not mutate the project source config
