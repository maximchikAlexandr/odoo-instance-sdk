## ADDED Requirements

### Requirement: One shared alternate-tool lifecycle
Numbered and hot-fix launchers SHALL use one package-owned implementation for deterministic identity paths, uv installation, exact provenance, selector-capability probing, manifest serialization, shim rendering, lifecycle locking, bounded inspection, and fail-closed removal. Numbered command code and the hot-fix skill wrapper SHALL contain only their distinct policy and presentation logic and SHALL NOT implement or copy those lifecycle mechanics.

#### Scenario: Both launcher kinds use one lifecycle implementation
- **WHEN** a numbered slot and a reviewed hot fix are installed, invoked, inspected, and removed
- **THEN** both operations pass through the same install/probe/manifest/shim/lock/remove implementation while their adapters apply only numbered replace policy or hot-fix PR/reviewer/retirement policy

#### Scenario: Duplicate lifecycle mechanics are rejected
- **WHEN** architecture checks inspect the numbered command and hot-fix skill surfaces
- **THEN** they fail if either surface contains its own uv installer, provenance verifier, capability probe, shim renderer, lifecycle-lock implementation, or cleanup algorithm outside the shared module

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
Canonical `odcli slot install N SHA` SHALL require a full lowercase 40-character Git SHA and delegate fixed-repository installation, installed provenance, root-selector capability, manifest, launcher publication, and cleanup to the shared alternate-tool lifecycle. A newly installed slot SHALL start with no `~/.odcli-N` directory or contents. An existing slot SHALL fail closed unless explicit replacement is requested.

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
Canonical `odcli slot install N SHA --replace` SHALL be the only supported revision-changing path for a numbered slot. The numbered adapter SHALL authorize explicit replacement, then delegate exclusive locking, temporary verification, recoverable publication, and manifest/shim changes to the shared lifecycle. It SHALL preserve `~/.odcli-N` for the replacement revision to migrate only when next invoked and SHALL NOT invoke or modify canonical `odcli update`.

#### Scenario: Replace an idle slot
- **WHEN** an existing idle slot is explicitly replaced with a different valid SHA
- **THEN** `odcli-N` subsequently executes the verified new SHA against the same `~/.odcli-N` and canonical `odcli` is unchanged

#### Scenario: Running slot blocks replacement
- **WHEN** `odcli-N` holds the shared lifecycle lock
- **THEN** replacement fails before changing its launcher, manifest, uv environment, or state

### Requirement: Bounded slot discovery and safe removal
Canonical `odcli slot list` SHALL project the shared lifecycle's bounded inspection of valid manifests with slot number, verified requested SHA, launcher path, tool path, state path, and structural health; it SHALL NOT execute installed slot code or scan arbitrary home directories. Canonical `odcli slot remove N` SHALL authorize the numbered removal intent and delegate exclusive locking, identity checks, and deletion to the shared lifecycle. Removal SHALL affect only slot `N`'s launcher, uv tool layout, manifest, lock after release, and `~/.odcli-N`, and SHALL fail closed on symlinks, identity mismatch, path escape, or a running slot.

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
Each process SHALL derive all SDK-owned global paths from its selected root for its complete lifetime. Numbered and hot-fix migration, configuration writes, backup operations, environment operations, locks, journals, and catalogue access SHALL NOT read, write, lock, migrate, or delete canonical, numbered, or hot-fix neighboring roots.

#### Scenario: Two revisions perform incompatible migrations
- **WHEN** two installed slot revisions concurrently migrate incompatible SQLite schemas and write state
- **THEN** each revision reads and writes only its own `~/.odcli-N`, and canonical `~/.odcli` plus the neighboring slot remain unchanged by the other process

#### Scenario: Numbered and hot-fix revisions migrate concurrently
- **WHEN** `odcli-1` and `odcli-fix-42` run incompatible SQLite migrations while canonical and other alternate roots contain sentinels
- **THEN** each revision accesses only its selected root and every nonselected root remains byte-for-byte unchanged

### Requirement: Hot-fix identity and isolated user root
The existing `odcli-fix-ISSUE` workflow SHALL accept only a positive canonical decimal GitHub issue number, retain its exact reviewed SHA and PR linkage rules, and select absolute root `<real-home>/.odcli-fix-ISSUE` while preserving real `HOME`. Its uv tool, manifest, and lifecycle lock SHALL live under deterministic manager-owned paths outside canonical `~/.odcli` and outside its state root. A new hot-fix root SHALL start absent and SHALL NOT copy any canonical, legacy, numbered, or neighboring hot-fix data.

#### Scenario: Fresh hot-fix starts isolated
- **WHEN** reviewed hot fix `odcli-fix-42` is installed beside populated canonical, legacy, numbered, and hot-fix roots
- **THEN** its launcher selects `~/.odcli-fix-42`, leaves that root absent until first use, and changes no existing state root

#### Scenario: Invalid issue identity is rejected
- **WHEN** hot-fix installation receives zero, a negative number, leading-zero text, whitespace, or non-decimal text
- **THEN** it fails before creating a tool, launcher, manifest, lock, branch change, or state root

### Requirement: Hot-fix compatibility is isolation-aware
Hot-fix compatibility review and installation SHALL verify the full SHA, fixed repository, linked PR/issue, installed provenance, and the revision's ability to honor the explicit root selector before launcher publication. Compatibility SHALL evaluate only explicitly shared external project/Odoo/PostgreSQL/database/filestore/Docker/port resources; it SHALL NOT require catalogue-schema compatibility with canonical or neighboring isolated roots. Unknown selector capability or unsafe shared external resources SHALL block installation.

#### Scenario: Incompatible catalogue is allowed behind isolation
- **WHEN** a reviewed hot-fix revision has an incompatible catalogue schema but proves explicit-root confinement and safe external-resource use
- **THEN** installation MAY publish its isolated launcher without reading or migrating canonical state

#### Scenario: Selector capability is absent
- **WHEN** the exact hot-fix revision cannot prove it honors the explicit user-root selector
- **THEN** installation publishes no launcher and removes only uncommitted temporary artifacts

### Requirement: Hot-fix retirement is selective and fail closed
Skill-managed reconciliation SHALL retain the existing merged-PR, default-branch ancestry, installed-canonical revision, issue closure, and safe local-branch gates. For an eligible idle hot fix it SHALL validate deterministic paths, manifest and launcher identity, take the exclusive lifecycle lock, and remove only that hot fix's launcher, uv layout, manager metadata, lock after release, safe branch, and `~/.odcli-fix-ISSUE`. It SHALL fail closed on an active lock, symlink, corrupt identity, path escape, or unproven gate and SHALL preserve canonical, numbered, and neighboring hot-fix roots.

#### Scenario: One eligible hot fix retires
- **WHEN** two hot fixes exist and exactly one satisfies every retirement gate while idle
- **THEN** only the eligible hot fix and its isolated root are removed and the other hot fix plus canonical and numbered roots remain byte-for-byte unchanged

#### Scenario: Active hot fix blocks retirement
- **WHEN** the selected hot-fix launcher holds its shared lifecycle lock
- **THEN** reconciliation retains its launcher, tool, manifest, lock, state, and branch and reports that it is running
