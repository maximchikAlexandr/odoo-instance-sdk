## MODIFIED Requirements

### Requirement: Self-update preserves and verifies installed companion composition

Canonical `odcli update` SHALL inspect whether `odcli-multica` is installed as a supported uv tool. An absent companion SHALL preserve the existing core-only update plan and SHALL NOT install a companion. A present companion SHALL require unambiguous protected receipt, launcher, package revision, and embedded `odoo-instance-sdk` revision evidence before mutation.

For a present companion, the immutable update plan SHALL warn before mutation that both utilities will change, preserve the existing core install step, and capture an additional companion install step that installs `odcli-multica` from the exact `packages/odcli-multica` monorepo subdirectory and pins both that distribution and its isolated SDK dependency to the same target VCS revision. The versioned snapshot, journal, rollback, recovery argv, and incomplete-state result SHALL cover both uv-tool environments. Post-install verification SHALL invoke the captured companion launcher with a supported non-mutating command and SHALL read protected distribution metadata from its environment to prove the actual VCS revision of `odcli-multica` and its embedded SDK; it SHALL NOT require an unsupported companion option or accept matching display versions as revision evidence. Commit/cleanup and aggregate `updated` success SHALL occur only after the core launcher, companion launcher, companion distribution revision, and embedded SDK revision all verify. A partial failure SHALL roll back both compositions when safe or return `update_incomplete` with retained recovery evidence.

#### Scenario: Core-only installation remains core-only

- **WHEN** supported core uv-tool provenance is present and no `odcli-multica` launcher or receipt exists
- **THEN** update uses the existing core-only install, migrate, verify, rollback, and recovery flow and does not install the companion

#### Scenario: Installed companion and embedded SDK advance together

- **WHEN** protected provenance identifies installed core and companion uv tools and update targets an immutable revision
- **THEN** the frozen plan installs the companion from `packages/odcli-multica`, pins its embedded SDK to the same revision, and verifies both installed distribution revisions before success

#### Scenario: Companion launcher uses a supported readiness command

- **WHEN** a coordinated update verifies the captured `odcli-multica` launcher
- **THEN** it invokes a supported non-mutating command and does not require the unsupported `--version` option

#### Scenario: Users are warned before coordinated replacement

- **WHEN** a present companion makes update a coordinated two-tool mutation
- **THEN** Rich output identifies both affected utilities before the first install step and dry-run exposes both secret-free frozen install and verify steps

#### Scenario: Companion SDK remains old

- **WHEN** the companion launcher starts but its installed SDK distribution does not match the target revision
- **THEN** verification fails and update does not return `updated` or remove journal and snapshot evidence

#### Scenario: Companion package remains old

- **WHEN** the companion launcher starts but the installed `odcli-multica` distribution metadata does not match the target revision
- **THEN** verification fails and update does not return `updated` or remove journal and snapshot evidence

#### Scenario: Companion revision evidence is invalid

- **WHEN** the companion launcher succeeds but protected metadata is missing, malformed, ambiguous, or unreadable
- **THEN** verification fails closed and update retains the journal, snapshot, and recovery command

#### Scenario: Partial coordinated update is recoverable

- **WHEN** core replacement succeeds but companion install, launcher readiness, or revision verification fails
- **THEN** update restores and verifies both prior compositions when safe or returns `update_incomplete` with retained two-tool journal, snapshot, and recovery commands that retain the companion subdirectory requirement

#### Scenario: Present companion provenance is ambiguous

- **WHEN** an `odcli-multica` launcher exists but its receipt, package revision, tool environment, or embedded SDK revision cannot be proven uniquely
- **THEN** preflight fails before either tool is changed
