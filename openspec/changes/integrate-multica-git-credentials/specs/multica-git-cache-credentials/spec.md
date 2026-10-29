## ADDED Requirements

### Requirement: Native cache reads use one verified read-only credential

Native Multica shared-cache clone/fetch children SHALL inherit one explicitly configured member/host and cache-read purpose from the owning daemon generation. Before returning credentials, the existing helper/resolver path SHALL verify the configured login's runtime provider ID and require token self-information for the exact host to report the same ID, active state, and exactly the standard `read_repository` scope. It SHALL NOT infer identity from a task, project lead, branch, path, ambient helper, or another credential.

#### Scenario: Cold cache uses the configured account

- **WHEN** a cold-cache clone requests the exact host and the configured token passes owner and scope checks
- **THEN** the short-lived helper supplies that credential and leaves remote URLs and persisted Git configuration secret-free

#### Scenario: Scope or identity is unsafe

- **WHEN** token information is inactive, mismatched, unavailable, granular, broader, write-capable, or cannot prove the complete scope set
- **THEN** the helper fails closed and does not try another member

### Requirement: Daemon-generation configuration is process-local

The daemon launch contract SHALL clear inherited credential helpers and choose the existing OdCLI helper through process-local Git configuration/environment. One daemon generation SHALL keep that launch environment immutable. Credential replacement SHALL use the existing stop → one-shot validate → start → status interfaces after old Git children exit; rollback SHALL repeat the lifecycle with the prior environment. The product SHALL NOT add a hot-rotation API, mutable rotation state, refusal subsystem, or lock.

#### Scenario: Replacement isolates generations

- **WHEN** an operator stops the old generation, validates a replacement, and starts a new generation
- **THEN** only new children inherit the replacement and no persisted repository/global Git setting must be rewritten

#### Scenario: Rollback restores the prior launch environment

- **WHEN** the replacement generation is stopped and the prior environment is used for another start
- **THEN** the rollback generation does not inherit replacement inputs

### Requirement: Cache and task identities never substitute

The cache credential SHALL authorize only native shared-cache remote reads. Verified task operations SHALL use the task member selected by the existing bridge context and SHALL NOT consume or fall back to the cache credential. Cached objects SHALL NOT prove remote authorization for a task member.

#### Scenario: Two task members share cached objects

- **WHEN** two task operations select different members after one cache account fetched the repository
- **THEN** each task operation uses only its own credential and the cache generation remains unchanged

#### Scenario: Task member has no credential

- **WHEN** the selected task member cannot resolve the exact-host credential
- **THEN** the task operation fails even if the required objects are already cached
