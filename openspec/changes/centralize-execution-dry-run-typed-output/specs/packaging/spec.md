## ADDED Requirements

### Requirement: Bounded Expression dependency

The project SHALL add Expression using the repository's bounded runtime dependency policy and lock it reproducibly. Production imports SHALL be limited to pure internal planning modules; package metadata paths, Click registration, public execution models, process effects, cleanup, and serializers SHALL not import Expression.

#### Scenario: Checkout pipeline payoff is measured

- **WHEN** the first checkout planning slice is complete
- **THEN** the change records before/after planning branches and Expression adapter/unwrap count
- **AND** the result is recorded as a preliminary checkout assessment only
- **AND** a positive checkout result does not waive the mandatory reassessment after the #35 vertical slice

#### Scenario: Issue #35 vertical slice is complete

- **WHEN** the #35 vertical slice has implemented its planning pipeline
- **THEN** contributors repeat the branch-versus-adapter/unwrap measurement under the repository-local rule
- **AND** Expression is removed before broader adoption if adapters/unwraps exceed the branching removed

#### Scenario: Metadata startup runs

- **WHEN** fresh interpreters import `odoo_instance_sdk`, run `odcli --help`, or run `odcli --version`
- **THEN** Expression and `odoo_instance_sdk.internal.proc` remain absent from `sys.modules`

### Requirement: Architecture regression gates

CI and `make pr` SHALL run source-level gates that reject direct production subprocess launches outside `internal/proc`, bounded output writes outside the output boundary/native allowlist, and explicit `Any` or bare `object` production annotations. Violations SHALL report file and line, and allowlists SHALL be minimal, documented beside the test, and limited to cases the protected boundary cannot represent.

#### Scenario: Direct process launch is added

- **WHEN** production code adds `subprocess.run` or `subprocess.Popen` outside `internal/proc`
- **THEN** CI fails and identifies the launch site

#### Scenario: Output boundary is bypassed

- **WHEN** production code adds `print`, `click.echo`/`secho`, direct stdout/stderr writes, or `Console().print` outside the documented boundary
- **THEN** CI fails and identifies the output site

#### Scenario: Imprecise annotation is added

- **WHEN** a production annotation contains explicit `Any` or bare `object`, including quoted or qualified forms
- **THEN** CI fails and identifies the annotation

### Requirement: Architecture rules are repository-local

`AGENTS.md` SHALL state the process, immutable preview/execution, public command sibling, bounded output, typed pipeline, no-`Any`/`object`, third-party narrowing, and no vague/single-use abstraction rules from GitHub #45 so future changes consume these boundaries.

#### Scenario: Future agent reads repository rules

- **WHEN** a contributor opens repository-local `AGENTS.md`
- **THEN** the required execution, output, typing, and minimal-abstraction invariants are explicit and match the enforced gates
