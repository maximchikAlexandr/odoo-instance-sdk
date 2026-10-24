## ADDED Requirements

### Requirement: Project summaries expose bounded main-checkout storage

Each registered `ProjectSummary` SHALL contain one frozen path-free main-checkout storage projection with nullable `total_bytes` and boolean `complete`. WHEN the `storage` snapshot section is requested, THEN the monitor SHALL measure only the canonical registered repository root through the existing directory-size helper after bounding its Python fallback with a monotonic ten-second deadline and a maximum of 100,000 visited directories/entries. The budget SHALL count entries before skip, resolution, deduplication, or stat outcomes; the deadline SHALL be checked before each yielded directory and entry. The probe SHALL skip symlink targets, cache its result under stable project identity for the existing expensive-section TTL, and keep an unavailable or ambiguous measurement explicit instead of returning an invented complete total.

Environment worktrees SHALL retain their existing independent `EnvironmentSnapshot.storage` measurements. Project totals SHALL NOT sum environment worktrees, backups, Compose volumes, or external/shared databases, and SHALL NOT expose repository paths. An OdCLI-managed environment root SHALL be distinct from its registered project root; WHEN containment cannot be disproved for a legacy/adopted row, THEN separate attribution SHALL be incomplete rather than double-counted.

#### Scenario: Main checkout and managed worktree remain separate

- **WHEN** a registered project and one OdCLI-managed environment are collected with the storage section selected
- **THEN** the project summary reports only the main checkout tree and the environment summary reports its existing environment-owned footprint
- **AND** neither card total includes the other's canonical root

#### Scenario: Project measurement is unavailable

- **WHEN** `du` is missing, fails, or times out and the fallback then fails, reaches its deadline, or reaches its entry budget before completion
- **THEN** the project remains in the snapshot with `total_bytes=null`, `complete=false`, no path disclosure, and no partial fallback sum

#### Scenario: Large fallback traversal is finite

- **WHEN** a synthetic fallback traversal completes below 100,000 visited entries
- **THEN** its complete symlink-skipping total is returned
- **AND WHEN** the next traversal reaches 100,000 visited entries or exhausts the ten-second monotonic deadline before completion
- **THEN** it returns no total and the project projection is incomplete

#### Scenario: Storage section is not selected

- **WHEN** a finite snapshot excludes the `storage` section
- **THEN** project storage is returned as explicitly unavailable without traversing the repository root

#### Scenario: Repeated snapshot uses bounded caching

- **WHEN** two snapshots request project storage within the existing expensive-section TTL for the same project identity
- **THEN** the second snapshot reuses the cached footprint while live runtime collection remains fresh
