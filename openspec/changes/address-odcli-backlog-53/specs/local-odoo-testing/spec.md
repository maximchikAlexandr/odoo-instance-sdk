## ADDED Requirements

### Requirement: Project-owned Odoo test execution

Top-level `odcli test` and compatibility `odcli module test` SHALL operate on either a ready environment or initialized project. Project context SHALL use the project repository root, resolved Python/Odoo/config runtime, configured database, HTTP interface, and preferred port, without creating or registering a development environment. Explicit `--env` SHALL retain precedence. Addon containment, installed-module preflight, single-runner execution, dry-run behavior, output formats, and failure semantics SHALL be identical across ownership kinds.

#### Scenario: Direct project test
- **WHEN** `odcli test sale` runs from an initialized main checkout without an exact environment
- **THEN** it selects the addon inside the project root and runs against the project-configured database and HTTP binding

#### Scenario: Project compatibility alias
- **WHEN** `odcli module test sale --test-tags /sale` runs in project context
- **THEN** it uses the same selection, preflight, runner, and result path as top-level test

### Requirement: Deterministic changed-test base for projects

In project context, `odcli test --changed --base REF` SHALL use the explicit ref. Without `--base`, it SHALL use the initialized project's configured effective checkout base when that value is non-empty and not `HEAD`; otherwise it SHALL fail with an actionable instruction to pass `--base`. It SHALL not guess `main`, contact a remote, mutate Git, or create environment metadata.

#### Scenario: Explicit project base
- **WHEN** project-context changed selection receives `--base origin/release`
- **THEN** that ref is resolved locally and recorded with explicit provenance

#### Scenario: Missing deterministic default
- **WHEN** project context has no usable configured checkout base and `--base` is omitted
- **THEN** selection fails before database preflight or Odoo execution with guidance to pass `--base`
