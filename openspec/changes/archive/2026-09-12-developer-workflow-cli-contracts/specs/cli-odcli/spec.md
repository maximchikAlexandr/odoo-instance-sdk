## ADDED Requirements

### Requirement: Single format selector and typed field projection
Format-aware commands SHALL expose only `--format rich|json|toon`, default to Rich, and reject removed `--json` as an ordinary Click usage error. Eligible bounded reads (`env list/show`, `backup list/show`, `resource list/doctor`, `db list/locks/stats/bloat`, and `module list`) SHALL accept comma-separated documented dotted `--fields` only with explicit JSON or TOON, recursively derive every allowed root and nested path from that leaf's concrete typed result schema without a command/field registry, project only successful `result`/`data`, preserve repeated-row order and all envelope/structural metadata, and fail before execution for unknown or ineligible fields. [Source: GH#62]

#### Scenario: Project repeated fields
- **WHEN** an eligible bounded read uses valid dotted fields in JSON and TOON
- **THEN** decoded documents are equal, every repeated row retains order with only selected data, and envelope, pagination, completeness, capabilities, timestamps, warnings, context, provenance, dry-run, and errors remain unfiltered

#### Scenario: Reject invalid projection
- **WHEN** `--fields` is used with Rich, unsupported output, an unknown path, mutation, error, dry-run plan, native passthrough, interactive, or streaming command
- **THEN** Click exits `2` before the operation runs

#### Scenario: Typed schema is the field authority
- **WHEN** an eligible leaf's concrete typed result adds or removes a nested field
- **THEN** accepted dotted paths change from that schema alone, including paths through repeated structures, without updating any projection allowlist or command-name field table

### Requirement: Structured doctor remediations
Doctor findings SHALL retain stable codes and zero or more typed secret-free remediations containing description, argv array, mutation flag, and actual dry-run support; Rich SHALL summarize them and JSON/TOON SHALL preserve equivalent objects. Doctor SHALL remain read-only and SHALL not fabricate actions or authorize execution. [Source: GH#62]

#### Scenario: Known dependency drift remediation
- **WHEN** doctor finds a condition with one safe supported corrective command
- **THEN** it returns that exact argv and safety metadata without executing it

### Requirement: Focused environment details command
`env show [ENVIRONMENT]` SHALL resolve cwd's registered worktree or require the existing positional selector elsewhere, select project/cluster/environment records from exactly one `EnvironmentMonitor.snapshot()`, show stopped environments and explicit unavailable metrics, start no process, and render compact Rich sections plus equivalent typed JSON/TOON. [Source: GH#43]

#### Scenario: Show stopped current environment
- **WHEN** `env show` runs inside a registered stopped worktree
- **THEN** one snapshot supplies all three sections, unavailable metrics remain null/warnings, and Odoo/PostgreSQL are not started

### Requirement: Human-oriented bounded presentation
Every bounded Rich leaf SHALL render a meaningful terminal-oriented summary rather than raw JSON, concatenated machine documents, unstructured mappings, or placeholder-only tables; long paths/commands/diagnostics SHALL not destroy primary content at widths 80, 120, or 180, while machine modes retain exact full values. `env list` SHALL keep identity, branch, lifecycle/runtime, port, database, Git ahead/diff, and artifact status visible and use a path-aware `~` form only in Rich. `backup validate` SHALL render validity, database/version when present, and readable errors from its existing typed result. [Source: GH#64 §4, §5, §7, §9]

#### Scenario: Presentation contract audits real leaves
- **WHEN** CLI-level presentation tests invoke every bounded leaf with realistic long and nested values at supported widths
- **THEN** output is non-empty, contentful, not raw machine syntax, and does not collapse headers or values into character columns
- **AND** JSON/TOON semantic payloads remain unchanged

### Requirement: Canonical short resource commands
The canonical Click names SHALL be `env create|ls|rm`, `backup ls|inspect|rm`, `db ls|rm`, `postgres ps`, `resource ls`, and `module ls`; existing long names SHALL remain aliases of the same command objects and callbacks. Group help SHALL list one operation with short name first, leaf help SHALL list only the alternate spelling, and behavior, parameters, confirmation, completion, exit codes, and stable machine `command` fields SHALL remain identical. [Source: GH#64 §10]

#### Scenario: Alias help and behavior parity
- **WHEN** each short or long spelling is invoked for help, completion, Rich, JSON, or TOON
- **THEN** help never aliases the invoked spelling to itself and both spellings resolve to identical behavior

### Requirement: Tracker-neutral Ticket Allocation CLI
Environment creation SHALL use positional metavariable `TICKET`, neutral Ticket/Ticket Allocation help, errors, identifiers, tests, and Rich language, and one `provenance.ticket_allocation` object with ticket, resolved branch, base ref, and bounded local/catalogue/origin evidence. It SHALL retain the existing ticket regex and iteration allocation semantics without calling an external tracker or retaining parallel Jira-specific provenance. [Source: GH#64 §11]

#### Scenario: Allocate a repeated ticket
- **WHEN** local, catalogue, and origin evidence contains prior `PROJ-123` iterations
- **THEN** allocation chooses the next non-reused `_N` branch, revalidates late collision fail-closed, and emits only neutral terminology
- **AND** every pre-existing checkout/allocation/lifecycle regression remains covered under tracker-neutral test identifiers
