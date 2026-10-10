## ADDED Requirements

### Requirement: Alternate launchers reject direct self-update
The update SDK primitive and CLI adapter SHALL detect numbered-slot and hot-fix execution contexts before check, dry-run, maintenance, recovery, or mutation planning. A numbered context SHALL direct the operator to canonical `odcli slot install N SHA --replace`; a hot-fix context SHALL direct the agent to skill-managed canonical update/reconciliation and SHALL NOT offer revision replacement. Rejected execution SHALL NOT acquire update locks, resolve refs, snapshot metadata, run uv, execute migrations, or change any installation or state root.

#### Scenario: Slot update is rejected
- **WHEN** `odcli-N update` is invoked with any supported update arguments
- **THEN** it exits nonzero with the canonical replacement command and changes neither numbered nor canonical installation or state

#### Scenario: Hot-fix update is rejected
- **WHEN** `odcli-fix-ISSUE update` is invoked with any supported update arguments
- **THEN** it exits nonzero with skill-managed reconciliation guidance and changes no canonical, numbered, or hot-fix installation or state

#### Scenario: Canonical update remains available
- **WHEN** ordinary `odcli update` runs without a numbered-slot context
- **THEN** the existing self-update and recovery contract remains unchanged and it does not discover, replace, or remove alternate launchers or roots
