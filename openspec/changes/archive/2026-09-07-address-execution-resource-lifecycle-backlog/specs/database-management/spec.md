## ADDED Requirements

### Requirement: Side-effect-free project-cluster database inventory

The CLI-private/internal database inventory SHALL project the exact PostgreSQL cluster bound to the initialized project through existing public SDK and PostgreSQL transport methods, independent of Odoo availability, `dbfilter`, and the Odoo database-manager list. It SHALL add no public SDK method. Each item SHALL contain the cluster identity and exact database name, logical size when available, active-connection count, current-default status, environment/runtime bindings, and restore/backup UUID provenance or explicit unknown origin. Reading an unavailable or partial cluster SHALL not mark any catalogue database dropped.

#### Scenario: Odoo is stopped

- **WHEN** the SDK-owned project PostgreSQL cluster is reachable and Odoo is stopped
- **THEN** inventory still returns the PostgreSQL database list and known relationships

#### Scenario: Odoo list is restricted

- **WHEN** Odoo would hide a PostgreSQL database because of `dbfilter`
- **THEN** the direct cluster inventory includes it and performs no dropped-audit reconciliation

#### Scenario: Same name exists in another cluster

- **WHEN** another configured cluster has a database with the same name
- **THEN** inventory identity and relationships remain scoped to the selected cluster and exact name

#### Scenario: Origin is unknown

- **WHEN** a PostgreSQL database has no matching restore or lifecycle provenance
- **THEN** it is reported with unknown origin rather than inferred from name or timestamp

#### Scenario: Canonical public method inventory is unchanged

- **WHEN** project-cluster inventory is added and `test_discovered_public_methods` characterizes the SDK
- **THEN** the canonical public method set remains unchanged and the inventory remains CLI-private/internal

### Requirement: Retry-safe cluster and database ownership evidence

The existing guarded project-cluster drop command SHALL consume one CLI-private ownership identity stored in the existing backup catalogue. Under the project-cluster lock and before the first Compose side effect, `odcli` SHALL atomically insert a non-authoritative `pending` claim containing a random immutable `cluster_id`, exact project id, Compose project name, and expected named data-volume identity. Compose render/up SHALL use that persisted ID as an SDK ownership label on the named volume and attached service/container. Only after readiness and exact inspection prove the current target is attached to that expected volume with the matching label SHALL one catalogue transaction promote the same claim to authoritative `active`. A `pending` claim SHALL never authorize restore or drop.

Retry SHALL reuse the existing pending ID. With no volume it SHALL resume creation; with the exact matching labeled volume it SHALL repeat readiness/attachment inspection and MAY activate the claim. Missing, malformed, foreign, or mismatched evidence SHALL fail closed without relabeling, adopting, deleting, or allocating another identity. A missing catalogue claim SHALL NOT be backfilled from a volume label alone. Declarative `mode="compose"`, host, port, database name, or Compose project name alone SHALL NOT prove creation or ownership.

Before the new local restore implementation, an additive migration SHALL create the state-constrained claim and nullable restore identity fields. The common preparation/audit path SHALL let both the existing remote `db refresh --restore` and new catalogue restore operate on external PostgreSQL bindings and legacy Compose targets without an authoritative claim, atomically recording `cluster_id=null`/unknown on the exact completed restore row and corresponding restored database event together with exact database name and restore-time data directory. For a currently inspected target matching an `active` claim, both paths SHALL instead record that exact non-null `cluster_id`. An existing `pending` claim SHALL block restore until activation, and malformed or mismatched evidence for an existing claim SHALL fail closed rather than be downgraded to a no-claim legacy target. Nullable provenance remains readable restore history but SHALL NOT prove ownership or later authorize drop. Drop SHALL fail closed unless planning and immediate pre-mutation revalidation establish equality among the inspected current volume label, the exact `active` project claim, and the latest completed exact `(cluster_id, database)` restore binding, including recorded/current Compose project and volume identity. Missing, null, unknown, malformed, foreign, changed, or mismatched evidence, `origin=unknown`, and endpoint reuse SHALL be blockers before session termination, `DROP DATABASE`, audit reconciliation, or filestore mutation.

#### Scenario: Interrupt occurs before volume creation

- **WHEN** creation is interrupted after the pending claim commits but before the named volume exists
- **THEN** retry reuses the pending `cluster_id`, creates with that label, and may activate only after readiness and exact inspection

#### Scenario: Interrupt occurs after Compose side effects

- **WHEN** volume creation or Compose up succeeds with the pending label but interruption occurs before activation commits
- **THEN** retry reuses that same pending identity, repeats readiness/attachment/label inspection, and activates the same row without relabeling or adoption

#### Scenario: Pending evidence conflicts on retry

- **WHEN** a pending claim exists but the current volume label or attachment is missing, malformed, or different
- **THEN** retry fails closed without activation, restore, relabeling, adoption, deletion, or allocation of a replacement identity

#### Scenario: Active claim permits completed restore recording

- **WHEN** the current inspected target exactly matches an active claim and either remote refresh restore or catalogue restore completes
- **THEN** the common audit transaction records that same `cluster_id` on both exact restore provenance rows

#### Scenario: External and legacy restore provenance is nullable

- **WHEN** either restore source completes against an external PostgreSQL binding or a no-claim legacy Compose target
- **THEN** the common audit transaction records `cluster_id=null`/unknown on both exact provenance rows without inventing an ownership claim

#### Scenario: Pending claim refuses restore rather than becoming legacy

- **WHEN** either restore source targets a cluster with a `pending` creation claim
- **THEN** restore refuses before database mutation or completed audit and does not record nullable provenance

### Requirement: Database deletion protects active bindings

The command SHALL also refuse system/template databases, the project default without its existing explicit override, and any database bound to an active environment or confirmed live process. It SHALL display cluster identity, exact database, connections, bindings, backup provenance, proven ownership, and filestore disposition in preview. Immediately before mutation it SHALL revalidate all ownership and safety evidence; any stale, changed, or unreadable value SHALL refuse before session termination, `DROP DATABASE`, audit reconciliation, or filestore mutation. Success SHALL be confirmed directly through PostgreSQL before audit reconciliation, and the downloaded backup SHALL remain untouched. This internal evidence SHALL add no public SDK method or second catalogue.

#### Scenario: Declarative Compose has no creation evidence

- **WHEN** the project manifest declares `mode="compose"` but no matching catalogue `cluster_id` and inspected labeled data volume prove a successful `odcli` creation
- **THEN** cluster ownership is unknown and drop refuses before session termination, `DROP DATABASE`, audit reconciliation, and filestore mutation

#### Scenario: Legacy restore row has no cluster identity

- **WHEN** an exact host/port/database restore row predates the identity migration and has null `cluster_id`
- **THEN** database ownership is unknown and drop refuses without implicitly backfilling the row or mutating sessions, database, audit, or filestore

#### Scenario: New identity-null restore cannot be dropped

- **WHEN** an external or no-claim legacy restore completed with `cluster_id=null`/unknown
- **THEN** drop refuses before session termination, `DROP DATABASE`, audit reconciliation, and filestore mutation even though the restore history is valid

#### Scenario: Endpoint is reused by another cluster identity

- **WHEN** the current endpoint, port, or database name equals recorded values but the inspected volume `cluster_id` differs from the catalogue or restore identity
- **THEN** drop treats the target as another cluster and performs no session termination, `DROP DATABASE`, audit reconciliation, or filestore mutation

#### Scenario: Exact identity match permits the ownership gate

- **WHEN** the inspected labeled volume, exact project catalogue row, and latest completed database restore binding contain the same valid `cluster_id`, Compose project, volume identity, and exact database name
- **THEN** the ownership gate passes and the command proceeds only to the remaining safety checks and confirmation

#### Scenario: Cluster ownership is unknown or foreign

- **WHEN** the selected cluster lacks canonical evidence that `odcli` created it or evidence identifies another owner
- **THEN** drop refuses before session termination, `DROP DATABASE`, audit reconciliation, and filestore mutation

#### Scenario: Database origin is unknown

- **WHEN** inventory reports `origin=unknown` for the exact target database
- **THEN** drop refuses before session termination, `DROP DATABASE`, audit reconciliation, and filestore mutation

#### Scenario: Exact restore binding is absent or mismatched

- **WHEN** no completed `odcli` restore binding matches the exact selected cluster and database identity
- **THEN** drop refuses before session termination, `DROP DATABASE`, audit reconciliation, and filestore mutation

#### Scenario: Ownership changes during revalidation

- **WHEN** planning proved ownership but immediate pre-mutation revalidation finds the catalogue row, inspected volume label/identity, or exact restore evidence missing, malformed, changed, or unavailable
- **THEN** drop fails closed without terminating sessions, dropping the database, reconciling audit, or mutating a filestore

#### Scenario: Proven disposable database may be dropped

- **WHEN** a disposable cluster's inspected labeled volume, exact project ownership row, and completed exact restore binding share one valid `cluster_id` and every existing safety check and confirmation passes at revalidation
- **THEN** the command may terminate authorized target sessions, drop only that database, verify absence, reconcile audit, and then evaluate filestore cleanup

#### Scenario: Active environment uses the target

- **WHEN** a ready or creating environment is bound to the target database
- **THEN** drop fails before terminating sessions or issuing `DROP DATABASE`

#### Scenario: Live project process uses the target

- **WHEN** a confirmed live recorded project process is bound to the target
- **THEN** drop fails closed even if no ordinary client session is visible at initial planning

#### Scenario: PostgreSQL absence is not confirmed

- **WHEN** the drop request returns but the target's absence cannot be verified
- **THEN** the operation fails and does not record successful deletion

### Requirement: Provenance-bound filestore cleanup

Only after cluster and exact database ownership have passed the required gate and the restored database is confirmed dropped SHALL the command evaluate the exact `<recorded-data-dir>/filestore/<database>` captured from that database's restore binding. It SHALL remove that filestore only when its ownership is proven, the path remains contained, and neither the path nor traversed components redirect through an external symlink. Unknown filestore ownership SHALL preserve the filestore and report the reason; it SHALL NOT permit deletion of a database whose own ownership was unproven. Database success followed by filestore failure SHALL be a typed partial failure with an actionable retained path; it SHALL not recreate the database or delete the source backup.

#### Scenario: Owned filestore is deleted

- **WHEN** restore provenance records the data directory and the exact filestore remains a contained SDK-owned directory
- **THEN** database absence is confirmed first and then only that database's filestore is removed

#### Scenario: Filestore ownership is unknown

- **WHEN** cluster and exact database ownership are proven but the restore binding does not prove which data directory owns the filestore
- **THEN** the already-authorized database drop may complete but the possible filestore is preserved with an explicit warning

#### Scenario: Filestore cleanup fails after drop

- **WHEN** PostgreSQL confirms deletion but the proven filestore cannot be completely removed
- **THEN** the result identifies database deletion as complete, filestore cleanup as failed, returns non-zero, and leaves audit/provenance truthful
