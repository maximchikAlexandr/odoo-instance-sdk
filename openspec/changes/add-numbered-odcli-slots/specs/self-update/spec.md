## ADDED Requirements

### Requirement: Numbered slots reject direct self-update
The update SDK primitive and CLI adapter SHALL detect a numbered-slot execution context before check, dry-run, maintenance, recovery, or mutation planning and SHALL return a stable actionable unsupported-operation error directing the operator to canonical `odcli slot install N SHA --replace`. They SHALL NOT acquire update locks, resolve refs, snapshot metadata, run uv, execute migrations, or change canonical or slot state.

#### Scenario: Slot update is rejected
- **WHEN** `odcli-N update` is invoked with any supported update arguments
- **THEN** it exits nonzero with the canonical replacement command and changes neither numbered nor canonical installation or state

#### Scenario: Canonical update remains available
- **WHEN** ordinary `odcli update` runs without a numbered-slot context
- **THEN** the existing self-update and recovery contract remains unchanged and it does not discover, replace, or remove numbered slots
