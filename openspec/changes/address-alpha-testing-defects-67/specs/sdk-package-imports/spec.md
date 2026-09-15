## ADDED Requirements

### Requirement: SDK-first rule for CLI domain operations

A new or changed CLI domain read, mutation, or spawn operation SHALL be built on a public typed SDK primitive. The CLI SHALL retain Click parsing, context resolution, confirmation, and Rich/JSON/TOON rendering. A CLI-only operation SHALL be allowed only with a concrete transport or presentation `cli_only_reason` recorded in the canonical `PUBLIC_LEAF_CASES`. A generic formulation SHALL NOT be accepted.

The existing `PUBLIC_LEAF_CASES` SHALL remain the single inventory. A contract test SHALL reject a leaf without `sdk_primitive` or `cli_only_reason`. An architecture gate SHALL protect the boundary from new self-contained domain execution in a Click callback without introducing a separate command bus or a second manual allowlist.

#### Scenario: New CLI domain operation has an SDK primitive

- **WHEN** a new CLI leaf that performs a domain read, mutation, or spawn is added
- **THEN** its `PUBLIC_LEAF_CASES` entry records a public `sdk_primitive`

#### Scenario: CLI-only reason is concrete

- **WHEN** a leaf is marked CLI-only
- **THEN** its `cli_only_reason` names a specific transport or presentation boundary

#### Scenario: Architecture gate rejects parallel domain execution

- **WHEN** a Click callback builds a self-contained domain operation through `internal.*` where a public SDK primitive applies
- **THEN** the architecture gate fails