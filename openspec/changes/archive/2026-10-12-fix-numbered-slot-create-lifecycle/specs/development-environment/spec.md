## ADDED Requirements

### Requirement: Adoption compares one canonical repository identity

Environment adoption SHALL compare configured and caller-owned checkout remotes through one shared strict repository-identity normalizer before any Odoo, catalogue, filesystem, database, or environment mutation. The normalizer SHALL treat scp-like `git@host:path` and `ssh://git@host/path` forms as the same SSH repository when their normalized host, optional port, user `git`, and path are equal. It SHALL retain transport, host, port, and repository-path distinctions; it SHALL reject credentials other than the allowed SSH `git` user, query/fragment data, control or whitespace characters, unsupported transports, and relative paths. Numbered checkout coordination SHALL consume the same normalizer rather than own a second implementation.

#### Scenario: Transport-equivalent SSH origins are accepted
- **WHEN** the selected project origin is `ssh://git@host/owner/repository.git` and the caller-owned checkout origin is `git@host:owner/repository.git`, or the two forms are reversed
- **THEN** adoption SHALL treat them as one repository identity and continue with the existing base, cleanliness, source, and provisioning checks

#### Scenario: Different repositories remain rejected
- **WHEN** normalized origins differ by host, explicit port, repository path, or transport
- **THEN** adoption SHALL raise the existing repository-identity conflict before mutation and preserve both checkouts and all project resources

#### Scenario: Unsafe remote syntax remains rejected
- **WHEN** either remote contains embedded credentials, query or fragment data, control or whitespace characters, an unsupported transport, or an unqualified relative path
- **THEN** adoption SHALL fail closed without echoing secret-bearing input or weakening the independent-clone repository check

### Requirement: Explicit COPY sources restore only into the local project target

For named-remote and retained-backup COPY checkout or adoption, only backup acquisition SHALL access the selected remote source. Target preflight, auxiliary Database Manager startup or reuse, restore, the single captured PostgreSQL before/after probe pair, generated filestore, catalogue binding, and compensation SHALL resolve from the configured local project runtime and PostgreSQL cluster. The source kind SHALL NOT disable the local auxiliary restore lifecycle or redirect target mutation to the backup source.

#### Scenario: Named remote provisions a local adopted environment
- **WHEN** adoption selects a named remote, an absent local target database, and a free or exactly-owned local auxiliary endpoint
- **THEN** the backup SHALL be acquired from that named remote, restored through the bounded local auxiliary Database Manager, proven by the captured local PostgreSQL after-probe, and bound to the adopted environment with an isolated filestore

#### Scenario: Retained backup provisions a local adopted environment
- **WHEN** adoption selects an exact retained backup UUID and an absent local target database
- **THEN** it SHALL skip remote acquisition, restore through the same bounded local auxiliary Database Manager, and apply the same local target and cleanup postconditions as named-remote adoption

#### Scenario: Local target postcondition fails closed
- **WHEN** restore returns without the captured local PostgreSQL after-probe finding the exact target
- **THEN** adoption SHALL remain non-ready, retain truthful COPY journal and recovery evidence, remove only proven partial owned resources, and publish no numbered launcher or ready manifest
