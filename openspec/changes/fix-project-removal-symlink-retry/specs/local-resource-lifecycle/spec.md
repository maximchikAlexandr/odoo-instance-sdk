## MODIFIED Requirements

### Requirement: Purge ownership decisions are fail-closed

A purge delete decision SHALL require both proven SDK/project ownership and
exclusive use at plan and execution time. The selected filesystem target itself
SHALL NOT be a symlink and SHALL be canonically contained in its allowed root.
Purge SHALL permit nested symlink entries in a proven non-source directory, and
recursive deletion SHALL unlink those entries without traversing or modifying
their targets. A
project source root SHALL additionally match registered Git provenance, SHALL
reject nested symlink entries and unknown owned metadata, and SHALL NOT be a
linked, caller-owned, or other-tool-managed checkout. Unknown or conflicting
evidence SHALL produce a retained decision with a stable reason, never a forceable
delete action.

#### Scenario: Top-level symlink escape is retained

- **WHEN** a selected filesystem target is itself a symlink or its canonical
  target escapes the allowed root
- **THEN** purge retains the selected target and never traverses or deletes its
  referent

#### Scenario: Nested symlink in an owned non-source directory is unlinked

- **WHEN** an ownership-proven contained non-source directory includes nested
  file or directory symlinks whose targets are outside the directory
- **THEN** purge removes the owned directory and symlink entries without
  traversing, modifying, or deleting any external target

#### Scenario: Nested symlink in a source root remains guarded

- **WHEN** a registered source root contains a nested symlink
- **THEN** purge retains the source root and its nested entry without traversing
  or deleting the link target

#### Scenario: Borrowed or referenced backup is retained

- **WHEN** a backup is borrowed by an environment, belongs to another project,
  or still has an active reference
- **THEN** purge retains it and reports every blocking relationship

#### Scenario: Exact owned Compose volume is deletable

- **WHEN** the active cluster claim, project id, compose project, container
  label, volume label, database state, and exclusive-use checks all match
- **THEN** the projection may place container stop/removal and volume deletion
  after dependent database/environment cleanup
