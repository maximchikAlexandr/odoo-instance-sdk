## ADDED Requirements

### Requirement: Alternate launchers reject direct self-update
The update SDK primitive and CLI adapter SHALL detect numbered and hot-fix contexts before check, dry-run, maintenance, recovery, or mutation planning. A numbered context SHALL direct the operator to the developer-only skill and repository-owned private service workflow for exact-SHA replacement; it SHALL NOT direct the ordinary user skill, mention, or require a public `odcli slot` command. A hot-fix context SHALL direct the agent to skill-managed canonical update/reconciliation and SHALL NOT offer revision replacement. Rejection SHALL NOT acquire update locks, resolve refs, snapshot metadata, run uv, execute migrations, or change any installation, checkout, or state root.

#### Scenario: Numbered update is rejected
- **WHEN** `odcli-N update` is invoked with any supported update arguments
- **THEN** it exits nonzero with private service-tool guidance and changes neither numbered nor canonical tool, checkout, database, filestore, port, or state

#### Scenario: Hot-fix update is rejected
- **WHEN** `odcli-fix-ISSUE update` is invoked
- **THEN** it exits nonzero with skill-managed reconciliation guidance and changes no canonical, numbered, or hot-fix installation or state

#### Scenario: Canonical update remains available
- **WHEN** ordinary `odcli update` runs without an alternate context
- **THEN** the existing self-update/recovery contract remains unchanged and it does not discover, replace, or remove alternate launchers, roots, or linked checkouts
