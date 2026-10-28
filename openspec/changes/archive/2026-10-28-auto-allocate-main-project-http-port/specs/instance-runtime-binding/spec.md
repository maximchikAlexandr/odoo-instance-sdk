## ADDED Requirements

### Requirement: Main-project run selects and preserves an available HTTP port

Before constructing a non-preview foreground or detached main-project run command, OdCLI SHALL resolve the current project configuration under the existing project lock. If `preferred_http_port` is present, OdCLI SHALL validate that exact port and fail with an actionable `port-conflict` result when it is occupied or unavailable, without changing the preference, automatic assignment, generated configuration, or runtime record. Without an explicit preference, OdCLI SHALL reuse an available automatic assignment or use the existing bounded HTTP allocator to select a free candidate, persist it, and construct the command and runtime endpoint from that value. Task environments and auxiliary restore SHALL retain their existing allocation paths.

#### Scenario: Occupied default falls back

- **WHEN** port `8069` is occupied and a main project has neither an explicit preference nor an automatic assignment
- **THEN** a real foreground or detached run selects an available port through the existing HTTP allocator, persists it, and starts with that exact port in argv, base URL, and runtime identity
- **AND** output reports the selected port with an automatic-fallback reason

#### Scenario: Automatic assignment is reused

- **WHEN** a main project has a persisted automatic assignment and that port remains available
- **THEN** the next run reuses the same port and reports automatic reuse without rewriting the assignment

#### Scenario: Stale automatic assignment is replaced

- **WHEN** a persisted automatic assignment becomes occupied before a later main-project run
- **THEN** OdCLI selects and persists another available automatic port before command capture
- **AND** it does not signal or claim ownership of the process occupying the old port

#### Scenario: Occupied explicit pin fails closed

- **WHEN** a main project has an explicit preferred HTTP port that is occupied or unavailable
- **THEN** the run fails before spawn with the conflicting host and port plus guidance to change or remove the explicit preference
- **AND** no automatic replacement or project/runtime mutation occurs

#### Scenario: Selection is consistent across detached lifecycle

- **WHEN** a detached main-project run starts after automatic fallback
- **THEN** its typed result, readiness probe, persisted runtime identity, status, and stop operations address the same selected endpoint

#### Scenario: Publication uses selected runtime endpoint

- **WHEN** a ready main-project runtime started on an automatically selected port is published
- **THEN** publication routes to the local endpoint captured in that runtime and does not use the stale configured/default port

#### Scenario: Spawn race remains fail-closed

- **WHEN** an automatically selected port becomes unavailable after allocation but before spawn
- **THEN** the existing final port assertion rejects the run without spawning Odoo or publishing a runtime identity
- **AND** a later retry may replace only the automatic assignment
