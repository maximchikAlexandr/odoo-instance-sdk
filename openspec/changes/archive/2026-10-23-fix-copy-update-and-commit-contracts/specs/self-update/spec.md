## ADDED Requirements

### Requirement: Self-update preserves and verifies installed companion composition

Canonical `odcli update` SHALL inspect whether `odcli-multica` is installed as a supported uv tool. An absent companion SHALL preserve the existing core-only update plan and SHALL NOT install a companion. A present companion SHALL require unambiguous protected receipt, launcher, package revision, and embedded `odoo-instance-sdk` revision evidence before mutation.

For a present companion, the immutable update plan SHALL warn before mutation that both utilities will change, preserve the existing core install step, and capture an additional companion install step that pins both `odcli-multica` and its isolated SDK dependency to the same target VCS revision. The versioned snapshot, journal, rollback, recovery argv, and incomplete-state result SHALL cover both uv-tool environments. Post-install verification SHALL invoke the captured core and companion launchers and prove the actual VCS revision of core, companion, and the SDK distribution inside the companion environment; matching display versions alone SHALL NOT be sufficient. Commit/cleanup and aggregate `updated` success SHALL occur only after every installed component verifies. A partial failure SHALL roll back both compositions when safe or return `update_incomplete` with retained recovery evidence.

#### Scenario: Core-only installation remains core-only

- **WHEN** supported core uv-tool provenance is present and no `odcli-multica` launcher or receipt exists
- **THEN** update uses the existing core-only install, migrate, verify, rollback, and recovery flow and does not install the companion

#### Scenario: Installed companion and embedded SDK advance together

- **WHEN** protected provenance identifies installed core and companion uv tools and update targets an immutable revision
- **THEN** the frozen plan updates core, companion, and the companion environment's SDK to that revision and verifies their actual launchers and VCS revisions before success

#### Scenario: Users are warned before coordinated replacement

- **WHEN** a present companion makes update a coordinated two-tool mutation
- **THEN** Rich output identifies both affected utilities before the first install step and dry-run exposes both secret-free frozen install and verify steps

#### Scenario: Companion SDK remains old

- **WHEN** the companion launcher starts but its installed SDK distribution does not match the target revision
- **THEN** verification fails and update does not return `updated` or remove journal and snapshot evidence

#### Scenario: Partial coordinated update is recoverable

- **WHEN** core replacement succeeds but companion install or verification fails
- **THEN** update restores and verifies both prior compositions when safe or returns `update_incomplete` with retained two-tool journal, snapshot, and recovery commands

#### Scenario: Present companion provenance is ambiguous

- **WHEN** an `odcli-multica` launcher exists but its receipt, package revision, tool environment, or embedded SDK revision cannot be proven uniquely
- **THEN** preflight fails before either tool is changed
