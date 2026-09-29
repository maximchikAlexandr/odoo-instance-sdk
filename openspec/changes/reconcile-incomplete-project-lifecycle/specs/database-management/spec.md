## ADDED Requirements

### Requirement: Guarded removal reconciles an exact incomplete restore

The public database removal path SHALL accept an `incomplete` restore binding as recovery evidence only when its active owned cluster claim, endpoint, database name, source provenance, volume identity, absence of active environment/runtime use, and any required contained filestore evidence satisfy the existing guarded drop checks at execution time. Successful removal SHALL record the drop and retire the incomplete binding. A database with no exact complete or incomplete binding, mismatched evidence, or unrelated ownership SHALL remain protected.

#### Scenario: Remove exact retained partial database

- **WHEN** an incomplete binding for `staging` still matches the active owned cluster and all existing drop guards pass
- **THEN** the supported database removal command drops only `staging`, cleans only proven contained artifacts, and records the reconciliation

#### Scenario: Unrelated database remains protected

- **WHEN** a database exists in the same cluster without an exact valid complete or incomplete binding
- **THEN** the removal command refuses it without dropping the database or its files

#### Scenario: Partial binding changed before execution

- **WHEN** the cluster, target identity, source provenance, active use, volume attachment, or filestore containment differs from the captured incomplete binding at execution time
- **THEN** removal fails closed and retains the evidence for diagnosis
