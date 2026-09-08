## MODIFIED Requirements

### Requirement: Logical progress corresponds to completed effects

The existing command run context SHALL emit start, progress, completion, and failure events for planned process and action steps. Every emitted event SHALL carry the stable non-empty `step_id` of the planned step plus concise sanitized operation and target context sufficient to distinguish concurrent or adjacent work; a renderer SHALL NOT emit anonymous bare `started` or `completed` lines. Process completion and failure SHALL retain elapsed time and exit status when known. An action completion event SHALL be emitted only after that action's effect and required postcondition finish; consuming an action through `RunContext.action()` alone SHALL NOT mark it completed. Progress events MAY report completed units and a total only when the total is reliable, and SHALL NOT imply effect completion.

#### Scenario: Action fails after it starts

- **WHEN** a planned action starts and its effect raises before its postcondition
- **THEN** observers receive identified started then failed events for that step and never completed

#### Scenario: Process completion is attributable

- **WHEN** a captured process step completes with an exit status
- **THEN** its event identifies the stable step and concise target and includes elapsed time and exit status

#### Scenario: Reliable byte total

- **WHEN** an action reports received bytes and a trustworthy total byte count
- **THEN** the observer may derive a percentage from those byte units

#### Scenario: Unknown duration

- **WHEN** a long action has no trustworthy total
- **THEN** its identified events expose status and elapsed time without a percentage or synthetic time estimate

#### Scenario: Progress inventory remains identified

- **WHEN** progress-producing flows including database refresh, database restore, environment lifecycle, tests, module update, eval, exec, translations, and PostgreSQL startup execute
- **THEN** every rendered event is associated with its planned step and no anonymous started/completed line is emitted

## ADDED Requirements

### Requirement: Rich plans retain real process commands

The shared human plan projection SHALL include the sanitized `display` of every planned `ProcessStep` in execution order while preserving argument boundaries through the existing redacted public projection. Semantic goal, action, target, mutation, precondition, and warning text MAY supplement those commands but SHALL NOT replace, hide, or fabricate them. `ActionStep` SHALL remain a description of real in-process work and SHALL NOT be converted into an invented shell command.

#### Scenario: Semantic summary does not hide a process

- **WHEN** a plan contains both a semantic observation and one or more `ProcessStep` values
- **THEN** Rich output contains every sanitized process display in order in addition to the semantic summary

#### Scenario: Blocked precondition preserves later command visibility

- **WHEN** dry-run captures a process command but a runtime precondition is failed
- **THEN** Rich output shows both the failed precondition and the captured sanitized command without executing it

#### Scenario: In-process action has no fake command

- **WHEN** a plan contains an `ActionStep` for filesystem, HTTP, database API, locking, cleanup, or another Python effect
- **THEN** Rich describes the action without synthesizing Bash or Python argv
