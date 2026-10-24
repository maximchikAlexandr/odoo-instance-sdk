## ADDED Requirements

### Requirement: Project summaries expose bounded main-checkout storage

Each registered `ProjectSummary` SHALL contain one frozen path-free main-checkout storage projection with nullable `total_bytes` and boolean `complete`. WHEN the `storage` snapshot section is requested, THEN the monitor SHALL measure only the canonical registered repository root through the existing bounded directory-size boundary, skip symlink targets, cache the result under stable project identity for the existing expensive-section TTL, and keep an unavailable or ambiguous measurement explicit instead of returning an invented complete total.

Environment worktrees SHALL retain their existing independent `EnvironmentSnapshot.storage` measurements. Project totals SHALL NOT sum environment worktrees, backups, Compose volumes, or external/shared databases, and SHALL NOT expose repository paths. An OdCLI-managed environment root SHALL be distinct from its registered project root; WHEN containment cannot be disproved for a legacy/adopted row, THEN separate attribution SHALL be incomplete rather than double-counted.

#### Scenario: Main checkout and managed worktree remain separate

- **WHEN** a registered project and one OdCLI-managed environment are collected with the storage section selected
- **THEN** the project summary reports only the main checkout tree and the environment summary reports its existing environment-owned footprint
- **AND** neither card total includes the other's canonical root

#### Scenario: Project measurement is unavailable

- **WHEN** the canonical project root cannot be measured within the bounded storage probe
- **THEN** the project remains in the snapshot with `complete=false`, no path disclosure, and no invented complete byte total

#### Scenario: Storage section is not selected

- **WHEN** a finite snapshot excludes the `storage` section
- **THEN** project storage is returned as explicitly unavailable without traversing the repository root

#### Scenario: Repeated snapshot uses bounded caching

- **WHEN** two snapshots request project storage within the existing expensive-section TTL for the same project identity
- **THEN** the second snapshot reuses the cached footprint while live runtime collection remains fresh
