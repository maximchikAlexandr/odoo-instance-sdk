## ADDED Requirements

### Requirement: Project restore reports complete binding outcome
A main-checkout restore result SHALL expose the source kind, nullable backup ID, target database, effective local Odoo configuration identity, managed filestore identity, project ID, binding publication state, and a stable sanitized failure reason when unsuccessful. Local-archive restore SHALL retain a null backup ID rather than inventing one. Secret values and complete configuration contents SHALL NOT appear.

#### Scenario: Catalogue backup restore succeeds
- **WHEN** a registered backup is restored and published to a main checkout
- **THEN** the typed result reports its exact backup ID, target database, configuration and filestore identities, and `binding_published=true`

#### Scenario: Local archive restore fails
- **WHEN** a caller-owned archive fails after selection
- **THEN** the failure reports `backup_id=null`, target when known, retained artifacts, and a stable failure reason while the previous project binding remains active

### Requirement: Main checkout restore and environment replacement remain distinct
Project restore SHALL publish a new project database/filestore binding only after success and SHALL NOT use environment replacement semantics. Existing `--replace` SHALL remain restricted to a stopped COPY environment and SHALL NOT accept a project runtime as an environment.

#### Scenario: Project selector with replace
- **WHEN** a caller combines `--project PATH` with environment-only `db restore --replace`
- **THEN** the CLI rejects the selector combination before planning or mutation
