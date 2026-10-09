## ADDED Requirements

### Requirement: CLI paths bind to stable operation IDs

Every canonical CLI leaf and alias SHALL bind to exactly one operation ID from production `PUBLIC_LEAF_CASES`. Existing friendly paths, Rich/JSON/TOON behavior and exit codes SHALL remain compatible. Renaming or adding an alias SHALL NOT change the operation ID or create another implementation binding.

#### Scenario: Existing alias shares contract

- **WHEN** canonical and legacy alias paths are inspected or invoked
- **THEN** discovery reports one operation identity and both adapters delegate to the same SDK primitive

### Requirement: Operation contract commands are read-only

The CLI SHALL expose bounded `contract export` and operation discovery commands that inspect the validated registry without resolving project/environment context, opening the catalogue, probing Git/Docker/PostgreSQL, importing unselected operation implementations or mutating state.

#### Scenario: Contract export has no domain I/O

- **WHEN** contract export runs outside an initialized project
- **THEN** it succeeds from metadata alone and domain-I/O spies observe no catalogue, filesystem discovery, Git, Docker, PostgreSQL or Odoo operation

### Requirement: Local operation invoke preserves output boundary

Finite `operation invoke` SHALL use the existing envelope-v1 construction, sanitizer and exit mapping. JSONL session records SHALL use a separate declared session transport and SHALL NOT be emitted by existing document-mode aliases. Machine invocation SHALL never prompt; approval-required mutations SHALL require the bounded session protocol.

#### Scenario: Machine invoke has clean stdout

- **WHEN** a finite operation succeeds, fails or reports a negative domain result
- **THEN** stdout contains only the selected document/session transport, diagnostics are separate and no Rich prompt, status or ANSI bytes appear

#### Scenario: Approval-required document call is rejected

- **WHEN** a caller selects finite document transport for a mutation requiring same-process approval
- **THEN** no preview or execution occurs and a typed `session_transport_required` failure is returned

### Requirement: Native and streaming CLI transports remain native

Existing native TTY, interactive, Rich live and JSONL streaming leaves SHALL retain their direct CLI behavior and cancellation semantics. Discovery SHALL describe those transports, while finite invoke SHALL reject them rather than buffering or fabricating a terminal JSON document.

#### Scenario: Interactive transport is unchanged

- **WHEN** an existing interactive or native command runs through its friendly CLI path
- **THEN** it retains inherited stdin/stdout/stderr, TTY and child exit behavior without a document wrapper
