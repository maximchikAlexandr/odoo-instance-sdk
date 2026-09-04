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
