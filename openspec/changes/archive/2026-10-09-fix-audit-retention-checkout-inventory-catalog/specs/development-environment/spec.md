## MODIFIED Requirements

### Requirement: Adopt an existing caller-owned checkout

The public environment SDK SHALL support an inspectable `adopt_command(project, checkout_path, options=...)` and delegating `adopt()` operation returning the existing frozen environment result type. Adoption SHALL prepare Odoo against an existing canonical Git working directory without creating, copying, moving, resetting or renaming its code or branch. It SHALL support both linked worktrees and independent clones. Existing SDK-owned checkout behavior SHALL remain unchanged.

Adoption SHALL require COPY mode, an explicit compatible base and exactly one explicit supported COPY source: the integrated `EnvironmentCheckoutOptions.remote_name` named-source selector or `EnvironmentCheckoutOptions.backup_id` retained catalogue UUID selector. It SHALL reuse existing source selection, credential handling, archive verification, provenance, neutralization, isolated database/filestore, Python selection, port allocation, readiness, retention and recovery semantics. It SHALL NOT enable creation of a virtual environment implicitly, change a project default database or mark a merely provisioned environment as HTTP-ready.

Adoption and ordinary checkout SHALL use the same internal provenance normalization and the same public/execution projection builders after their mode-specific validation. The captured `base_revision` SHALL populate `plan.provenance.resolved_base_revision` for both paths; adoption SHALL retain its separate caller-owned checkout and repository-identity checks.

#### Scenario: Adopt a native external checkout

- **WHEN** a valid existing checkout and compatible explicit COPY inputs are supplied
- **THEN** exactly that checkout supplies the Odoo code, a separate target database and writable filestore are prepared, and one environment UUID is returned without another Git checkout

#### Scenario: Independent clone belongs to the configured project

- **WHEN** the external checkout has a different Git common directory but its verified repository identity matches the explicitly selected core project
- **THEN** the environment retains that core project identity and separately records the external Git identity; source secrets are resolved from the configured project, not copied into the clone

#### Scenario: Initial checkout is not the requested input

- **WHEN** initial adoption finds the wrong repository, HEAD differs from the resolved explicit base, or uncommitted user changes exist
- **THEN** it rejects before Odoo/catalog/artifact mutation and preserves the checkout unchanged rather than resetting it

#### Scenario: Unsupported or ambiguous input

- **WHEN** adoption is asked to use SHARED mode, infer a source, or select two COPY sources
- **THEN** it fails before provisioning with an actionable typed validation result

#### Scenario: Public adoption plan retains captured base provenance

- **WHEN** `adopt_command()` captures a valid explicit base and its public plan is inspected before execution
- **THEN** `plan.provenance.resolved_base_revision` equals the captured resolved commit and the execution projection uses the same normalized provenance
