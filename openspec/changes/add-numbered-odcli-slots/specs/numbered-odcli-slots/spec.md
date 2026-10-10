## ADDED Requirements

### Requirement: Numbered slot identity and isolated user root
The system SHALL accept only a positive decimal slot number without signs, leading zeroes, whitespace, or suffixes. Slot `N` SHALL expose launcher `odcli-N`, select the absolute root `<real-home>/.odcli-N`, and preserve the process `HOME` and all Git, SSH, uv, and unrelated user configuration. Ordinary `odcli` SHALL continue to select `<real-home>/.odcli` when no explicit root is present.

#### Scenario: Canonical command remains unchanged
- **WHEN** ordinary `odcli` starts without a selected user root
- **THEN** every SDK-owned global path remains below `~/.odcli` and `HOME` remains unchanged

#### Scenario: Numbered launcher selects one root
- **WHEN** `odcli-2` invokes an ordinary command
- **THEN** every SDK-owned global path is below `~/.odcli-2` while the child receives the caller's unchanged `HOME`

#### Scenario: Invalid slot identity is rejected
- **WHEN** slot management receives `0`, a negative value, a leading-zero value, whitespace, or non-decimal text
- **THEN** it fails before creating, replacing, or deleting a launcher, tool environment, manifest, or state root

### Requirement: Exact-SHA slot installation and provenance
Canonical `odcli slot install N SHA` SHALL require a full lowercase 40-character Git SHA, resolve it against the fixed credential-free SDK repository, install it in a slot-specific uv tool directory, and verify the installed distribution's repository and commit provenance before publishing `odcli-N`. A newly installed slot SHALL start with no `~/.odcli-N` directory or contents. An existing slot SHALL fail closed unless explicit replacement is requested.

#### Scenario: Fresh exact revision is installed
- **WHEN** a valid unused number and repository commit SHA are supplied
- **THEN** the manager installs a dedicated uv tool, verifies the exact repository and SHA, atomically publishes `odcli-N`, records bounded manager metadata outside the slot state, and leaves `~/.odcli-N` absent

#### Scenario: Install provenance differs
- **WHEN** uv completes but installed provenance does not match the fixed repository and requested full SHA
- **THEN** the manager publishes no launcher and removes only its uncommitted temporary install artifacts

#### Scenario: Existing slot is not implicitly overwritten
- **WHEN** installation targets an existing slot without explicit replacement
- **THEN** the command fails without changing that slot, canonical `odcli`, or any other slot

### Requirement: Explicit slot replacement
Canonical `odcli slot install N SHA --replace` SHALL be the only supported revision-changing path for a numbered slot. It SHALL take the slot's exclusive lifecycle lock, verify the replacement in a temporary slot-local uv layout, publish the verified launcher and manifest as one recoverable transition, and preserve `~/.odcli-N` for the replacement revision to migrate only when it is next invoked. It SHALL NOT invoke or modify canonical `odcli update`.

#### Scenario: Replace an idle slot
- **WHEN** an existing idle slot is explicitly replaced with a different valid SHA
- **THEN** `odcli-N` subsequently executes the verified new SHA against the same `~/.odcli-N` and canonical `odcli` is unchanged

#### Scenario: Running slot blocks replacement
- **WHEN** `odcli-N` holds the shared lifecycle lock
- **THEN** replacement fails before changing its launcher, manifest, uv environment, or state

### Requirement: Bounded slot discovery and safe removal
Canonical `odcli slot list` SHALL report only valid manager manifests with slot number, verified requested SHA, launcher path, tool path, state path, and structural health; it SHALL NOT execute installed slot code or scan arbitrary home directories. Canonical `odcli slot remove N` SHALL take the exclusive lifecycle lock, validate every target against deterministic manager-owned paths and the recorded launcher identity, and remove only slot `N`'s launcher, uv tool layout, manifest, lock after release, and `~/.odcli-N`. It SHALL fail closed on symlinks, identity mismatch, unexpected path escape, or a running slot.

#### Scenario: List two slots
- **WHEN** two valid numbered slots are installed
- **THEN** listing returns two deterministic records without reading or changing either SQLite catalogue

#### Scenario: Remove one of two slots
- **WHEN** slots `1` and `2` exist and idle slot `1` is removed
- **THEN** only `odcli-1`, its tool layout, manager metadata, lock, and `~/.odcli-1` are removed while `odcli`, `~/.odcli`, `odcli-2`, and `~/.odcli-2` remain byte-for-byte unchanged

#### Scenario: Modified launcher blocks removal
- **WHEN** the selected launcher's content or path does not match the recorded manager identity
- **THEN** removal fails without deleting the launcher, tool layout, state, canonical installation, or neighboring slots

### Requirement: Slot isolation boundary is explicit
Documentation and slot command help SHALL state that numbered roots isolate only OdCLI-managed user state: SQLite catalogue, configuration, backups, environment artifacts, project runtime state, locks, bug reports, and migration/update journals. They SHALL state that project checkouts, Odoo processes, PostgreSQL clusters and databases, filestores, Docker resources, and ports are external and SHALL be separated by independent project copies or explicit resource configuration before concurrent use.

#### Scenario: Operator inspects concurrency guidance
- **WHEN** an operator reads slot help or installation documentation
- **THEN** it identifies both the isolated OdCLI state classes and the non-isolated external resources, with a requirement to use separate project copies or disjoint external resources

### Requirement: Concurrent incompatible catalogues remain isolated
Each process SHALL derive all SDK-owned global paths from its selected root for its complete lifetime. Slot migration, configuration writes, backup operations, environment operations, locks, journals, and catalogue access SHALL NOT read, write, lock, migrate, or delete canonical or neighboring slot roots.

#### Scenario: Two revisions perform incompatible migrations
- **WHEN** two installed slot revisions concurrently migrate incompatible SQLite schemas and write state
- **THEN** each revision reads and writes only its own `~/.odcli-N`, and canonical `~/.odcli` plus the neighboring slot remain unchanged by the other process
