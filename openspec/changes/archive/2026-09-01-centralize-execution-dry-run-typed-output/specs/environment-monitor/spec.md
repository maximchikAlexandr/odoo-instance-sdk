## ADDED Requirements

### Requirement: Inspectable finite monitor snapshots

`EnvironmentMonitor` SHALL expose `snapshot_command(project_id: str | None = None, *, include_removed: bool = False) -> Command[Snapshot]`. The existing `snapshot()` operation SHALL build that command exactly once and return its `.run()` result. The command SHALL capture every process-backed Git, storage, Docker, and PostgreSQL probe in one immutable plan, and every child launch SHALL execute through `internal/proc`.

#### Scenario: One-shot snapshot is inspected

- **WHEN** a caller constructs `snapshot_command()` and inspects its plan
- **THEN** every finite process-backed probe for that collection appears as a redacted captured step
- **AND** calling `.run()` consumes the same snapshot without reconstructing probe inputs

### Requirement: Watch is an explicit unbounded command coordinator

`EnvironmentMonitor.watch()` SHALL remain a thin unbounded async streaming coordinator rather than return one finite `Command`. For each emitted tick it SHALL construct one fresh immutable `snapshot_command()` with the original selection arguments and run it exactly once. It SHALL NOT directly launch a child, reuse a command or consumption ledger across ticks, or precompute an unbounded set of future probes; all tick launches SHALL pass through `internal/proc`.

#### Scenario: Watch emits multiple fresh snapshots

- **WHEN** a recording executor observes three `watch()` ticks
- **THEN** three distinct `snapshot_command()` instances are built and run in order with the original arguments
- **AND** no process step or consumption ledger is shared across ticks

#### Scenario: Watch is cancelled between ticks

- **WHEN** the consumer cancels or closes `watch()` after one emitted snapshot
- **THEN** no later snapshot command is built or run
- **AND** the existing no-background-task cleanup contract remains intact
