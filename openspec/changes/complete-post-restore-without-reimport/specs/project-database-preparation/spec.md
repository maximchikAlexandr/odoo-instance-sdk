## ADDED Requirements

### Requirement: Post-import restore checkpoint is truthful and minimal

Database preparation SHALL persist a nullable closed `post_restore_checkpoint` on the append-only restore binding. It SHALL set `reset_pending`, `reset_complete`, or `reset_not_requested` only after database import, exact target verification, filestore restore, and neutralization have all completed for the selected target. A binding created before those postconditions complete SHALL use a null checkpoint and SHALL NOT be eligible for completion without reimport. Existing bindings migrated with a null checkpoint SHALL remain ineligible. Checkpoints and their projections SHALL contain no password, environment value, Odoo config content, ORM script, or unsanitized process output.

#### Scenario: Administrator reset fails after completed import

- **WHEN** import, target verification, filestore restore, and neutralization complete but the requested administrator reset fails
- **THEN** preparation SHALL retain the exact target with state `incomplete`, checkpoint `reset_pending`, pending step `admin_reset`, and the previous project default

#### Scenario: Default publication fails after reset

- **WHEN** the requested administrator reset completes but default publication fails
- **THEN** preparation SHALL append checkpoint `reset_complete`, report `default_switch` as pending, and leave the previous project default effective

#### Scenario: Default publication fails without requested reset

- **WHEN** no administrator reset was requested and default publication fails after completed import
- **THEN** preparation SHALL retain checkpoint `reset_not_requested`, report `default_switch` as pending, and SHALL NOT claim an administrator reset occurred

#### Scenario: Import is incomplete

- **WHEN** archive import, target verification, filestore restore, or neutralization does not complete even though the target existence probe proves a database exists
- **THEN** the retained incomplete binding SHALL have a null checkpoint and completion without reimport SHALL fail before target mutation

### Requirement: Proven post-restore work can complete without reimport

The public SDK SHALL expose one typed inspectable completion command for an exact project-owned target whose latest restore binding is incomplete and has a non-null post-restore checkpoint. Planning and execution SHALL validate the project, active cluster claim, endpoint, database identity, data directory, source provenance, target existence, checkpoint, prior project binding, and managed filestore. Execution SHALL hold the existing project preparation lock and revalidate captured evidence before every mutation and before publication. It SHALL NOT read or import the archive, drop or recreate the database, replace the filestore, or switch the default before all pending work succeeds.

#### Scenario: Explicit module repair precedes reset

- **WHEN** completion is requested for a `reset_pending` target with explicitly named modules
- **THEN** it SHALL update exactly those modules through the existing module-update operation in deterministic order, then reset the administrator, then publish the default

#### Scenario: No modules are selected

- **WHEN** completion is requested without module names
- **THEN** it SHALL run no module update and SHALL continue with only the steps required by the checkpoint

#### Scenario: Module repair fails

- **WHEN** an explicitly selected module update fails
- **THEN** completion SHALL report the module step failure without running administrator reset or default publication, retain the latest checkpoint, and leave the prior default effective

#### Scenario: Reset completes during retry

- **WHEN** administrator reset succeeds for a `reset_pending` binding
- **THEN** the catalogue SHALL compare the latest exact binding and append an incomplete `reset_complete` binding without emitting another database-restored event

#### Scenario: Completion publishes the target

- **WHEN** every required module update and administrator reset succeeds and the captured project binding remains current
- **THEN** completion SHALL use the existing atomic default switch, append an exact complete binding without emitting another database-restored event, and return the same target as the effective default

#### Scenario: Foreign or changed evidence is rejected

- **WHEN** the target lacks a completion checkpoint or its cluster, endpoint, database identity, data directory, source provenance, checkpoint, existence, filestore containment, or project binding differs from the captured plan
- **THEN** completion SHALL fail closed before module update, reset, or default publication

#### Scenario: Concurrent preparation is serialized

- **WHEN** completion and another preparation action target the same project concurrently
- **THEN** the existing project preparation lock SHALL serialize them and the later action SHALL revalidate the latest binding before mutation

#### Scenario: Repeated successful completion is a no-op

- **WHEN** the latest binding is complete and the current owned project binding already selects the same target and managed filestore
- **THEN** completion SHALL return a no-op success without updating modules, resetting the administrator, importing data, or rewriting the binding
