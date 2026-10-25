## ADDED Requirements

### Requirement: Project summaries expose bounded main-checkout storage

Each `ProjectSummary` SHALL expose a frozen path-free footprint with nullable `total_bytes` and boolean `complete`. WHEN storage is requested, THEN only the canonical registered root SHALL be measured. The existing Python fallback SHALL have a ten-second monotonic deadline and a 100,000-entry budget counted before skip/resolve/dedup/stat; it SHALL check time before each yielded directory and entry, skip symlink targets, and return no partial total.

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

### Requirement: Project and environment storage attribution remains separate

Project storage SHALL contain only the registered main checkout. Environment worktrees SHALL retain their independent `EnvironmentSnapshot.storage`. Project totals SHALL NOT include worktrees, backups, Compose volumes, or external/shared databases, and SHALL expose no path. WHEN project/environment containment cannot be disproved for a legacy or adopted row, THEN separate attribution SHALL be incomplete instead of double-counted.

#### Scenario: Main checkout and managed worktree remain separate

- **WHEN** a registered project and one OdCLI-managed environment are collected with storage selected
- **THEN** the project reports only its main checkout and the environment reports its existing owned footprint
- **AND** neither total includes the other's canonical root

#### Scenario: Legacy containment is ambiguous

- **WHEN** a legacy or adopted environment root may be contained by its registered project root
- **THEN** the project measurement is incomplete and no separate complete totals are claimed
