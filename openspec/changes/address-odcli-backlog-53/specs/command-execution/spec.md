## ADDED Requirements

### Requirement: Plan preconditions survive preview

An inspectable command plan SHALL represent checked preconditions as typed JSON-safe observations with stable identity, status, and sanitized detail. A failed precondition SHALL prevent normal execution, but preview SHALL remain executable as a read-only inspection operation and SHALL return the complete plan. Preconditions SHALL not introduce a second executor or rerun process construction.

#### Scenario: Failed precondition blocks only mutation
- **WHEN** a captured command has a failed port or active-connection precondition
- **THEN** normal execution stops before mutation while preview returns the same captured plan with the failure recorded

### Requirement: Shared Rich plan summary

Execution plans SHALL expose enough typed goal, target, mutation, precondition, and warning information for the CLI output boundary to build one concise Rich summary. Exact private execution details and the existing redacted public snapshot SHALL remain the source of truth for execution and machine formats.

#### Scenario: Projection does not alter snapshot
- **WHEN** Rich and JSON previews render the same captured command
- **THEN** renderer selection does not mutate, rebuild, or omit fields from the underlying public plan

## MODIFIED Requirements

### Requirement: Planning is non-mutating and observable

Building or previewing a command SHALL start none of its planned processes and SHALL perform no filesystem, database, network, catalogue, lock, or process mutation. Bounded read-only probes required for resolution MAY execute through the shared process boundary and SHALL be recorded as observations with `read_only=true` and `executed_during_planning=true`. Resolving an initialized project, monitoring it, or producing any dry-run SHALL NOT register/upsert the project or runtime. Successful non-preview init and normal foreground execution/lifecycle MAY perform the registration writes explicitly authorized by their own execution requirements, after planning and outside resolution.

#### Scenario: Project preview does not mutate catalogue
- **WHEN** a command is built or dry-run from an unregistered initialized project with catalogue mutation sentinels
- **THEN** read-only observations may be returned but no project, environment, runtime, lifecycle, or migration write occurs

#### Scenario: Registration occurs only during authorized execution
- **WHEN** a successfully planned normal init or foreground lifecycle operation crosses its explicit execution boundary
- **THEN** its specified project registration write may occur without making ordinary resolution or command construction mutating
