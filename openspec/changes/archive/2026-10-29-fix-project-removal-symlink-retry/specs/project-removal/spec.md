## MODIFIED Requirements

### Requirement: Recoverable ownership-safe purge

Purge SHALL execute in this order: stop exact owned runtimes; unpublish exact
routes; remove environments through their existing captured cleanup commands;
delete provenance-authorized databases and contained filestores; delete
exclusively owned unreferenced backups; destroy the exact unused SDK-owned
Compose container and volume; delete contained SDK-owned global/project
artifacts, including the primary Python environment, and an ownership-proven
primary source root; then mark the project forgotten. Each child operation SHALL
retain its existing lock, revalidation, confirmation-independent ownership,
idempotence, and postcondition checks.

Remote Odoo instances, remote Git repositories, Docker images, external/shared
clusters and databases, borrowed backups, shared Python environments, top-level
symlink escapes, nested source-root symlinks, unknown files, and
caller/other-tool-managed checkouts SHALL be retained and reported. Nested
symlinks inside an ownership-proven non-source directory SHALL be unlinked without
following or modifying their targets. Purge SHALL NOT mark the project forgotten
until every required mutation has completed and every retained item has a
terminal reason.

#### Scenario: Complete isolated purge

- **WHEN** an exactly confirmed project contains only proven exclusive SDK
  resources, including a primary venv with nested interpreter symlinks
- **THEN** purge stops and unpublishes it, deletes those resources in dependency
  order without touching symlink referents, verifies their postconditions, and
  finally marks the project forgotten

#### Scenario: Another project and shared resources are unchanged

- **WHEN** the selected project relates to a second project or shared/external
  resources
- **THEN** purge retains those resources with reasons and the second project's
  catalogue rows, files, databases, processes, routes, containers, and volumes
  remain unchanged

#### Scenario: Other-tool checkout is retained

- **WHEN** the registered root or an environment checkout is caller-owned,
  linked, externally managed, or has ambiguous ownership
- **THEN** purge does not remove it and reports the ownership reason

#### Scenario: Unproven database or volume is retained

- **WHEN** database lifecycle evidence, active cluster claim, exclusive-use
  proof, Docker labels, or contained filestore identity is absent or changed
- **THEN** the affected database, filestore, container, and volume are not
  mutated and the decision is reported

### Requirement: Partial failure and retry remain truthful

On failure, the project SHALL remain registered and the sanitized typed error
SHALL identify the failed step plus ordered completed, retained, and remaining
decisions. The executor SHALL never report full success after a partial failure.
A later invocation by full id SHALL rebuild from durable current state, treat
verified completed point operations idempotently, and continue only the remaining
safe work. When the primary project Python environment is already absent, purge
observation SHALL use existing durable manifest, database, cluster, and ownership
evidence without recreating or executing that environment; incomplete evidence
SHALL remain a fail-closed error.

#### Scenario: Failure before terminal forget

- **WHEN** a purge step fails after earlier steps succeeded
- **THEN** the error reports those completed steps and the remaining suffix, the
  project remains registered, and evidence required for safe retry is retained

#### Scenario: Retry after missing source

- **WHEN** source deletion succeeded but a later catalogue action failed
- **THEN** retry by full project id resolves the row, does not repeat unsafe
  deletion, and can complete the remaining verified actions

#### Scenario: Retry after the primary Python environment is absent

- **WHEN** the owned primary Python environment is already absent while a
  registered project still has database or Compose cleanup remaining
- **THEN** retry by full project id reconstructs the required observations from
  durable evidence, does not recreate or execute the environment, and completes
  only ownership-proven remaining actions

#### Scenario: Missing recovery evidence remains inert

- **WHEN** the primary Python environment is absent and required manifest,
  database, cluster, credential, or exclusive-use evidence is missing or changed
- **THEN** purge reports an incomplete observation and mutates neither the
  remaining resources nor registration state

#### Scenario: Repeated successful purge

- **WHEN** purge is repeated by full id after successful completion
- **THEN** it returns the same forgotten terminal state with already-absent
  resources represented idempotently and no unrelated mutation
