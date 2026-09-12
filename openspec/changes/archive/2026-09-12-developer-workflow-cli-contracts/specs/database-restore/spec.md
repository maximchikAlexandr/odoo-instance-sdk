## ADDED Requirements

### Requirement: Restore while project Odoo is stopped
Project restore SHALL either capture and run a bounded auxiliary Database Manager using the existing runtime/config or select an existing safe non-HTTP restore path. If neither is possible, preflight SHALL fail before target reservation/creation with an actionable exact recovery command. Existing backup, target, filestore, ownership, postcondition, compensation, and default-switch guarantees SHALL remain. [Source: GH#64 §2]

#### Scenario: Restore valid ZIP on a free project port
- **WHEN** project Odoo is stopped and a valid filestore ZIP is restored
- **THEN** the command completes without manual startup, confirms database and filestore, switches effective default to target, and cleans up only its owned auxiliary runtime

#### Scenario: Listener is unrelated
- **WHEN** a foreign listener occupies the candidate port or auxiliary startup fails
- **THEN** the listener is neither used nor stopped and no false success or unconfirmed default is left

#### Scenario: Retry retained artifact
- **WHEN** restore is retried after a controlled interruption
- **THEN** it safely resumes or compensates the retained artifact without duplicate target or lost provenance
