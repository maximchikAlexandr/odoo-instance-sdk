## ADDED Requirements

### Requirement: Checkout filestore binding follows project ownership

Checkout SHALL resolve filestore provenance while building its immutable plan. WHEN the selected project declares OdCLI-managed Compose PostgreSQL, THEN both `shared` and `copy` checkout plans SHALL carry the verified absolute `{project_root}/.odcli/filestore` path and generated `odoo.conf` SHALL set `data_dir` to that path. WHEN the project does not declare managed Compose PostgreSQL, THEN checkout SHALL NOT invent a project-owned binding and SHALL preserve any explicit source `data_dir`. Checkout SHALL NOT copy, migrate, delete, or claim ownership of a shared source filestore as part of this binding.

#### Scenario: Legacy Compose source config in shared mode

- **WHEN** a managed Compose project performs `shared` checkout from a source configuration that omits `data_dir`
- **THEN** checkout writes the verified absolute `{project_root}/.odcli/filestore` path before recording the environment as `READY`

#### Scenario: Managed Compose copy uses the project binding

- **WHEN** a managed Compose project performs `copy` checkout
- **THEN** the generated environment configuration contains the same verified absolute `{project_root}/.odcli/filestore` path used by the main project

#### Scenario: External source binding is preserved

- **WHEN** a project without managed Compose PostgreSQL performs checkout from a source configuration with an explicit `data_dir`
- **THEN** the generated environment configuration preserves that exact source binding and does not substitute an environment-owned or project-owned path

#### Scenario: Unsafe managed binding fails before readiness

- **WHEN** a managed Compose checkout cannot verify the canonical project-owned `data_dir` as a contained non-symlink directory path
- **THEN** checkout fails before creating durable checkout state and SHALL NOT record the environment as `READY`

#### Scenario: Shared ownership remains unchanged

- **WHEN** a managed Compose environment is checked out in `shared` mode and later removed
- **THEN** the environment does not own, copy, migrate, delete, or drop the source database or its project filestore
