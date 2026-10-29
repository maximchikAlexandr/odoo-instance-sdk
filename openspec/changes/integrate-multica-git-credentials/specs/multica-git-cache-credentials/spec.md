## ADDED Requirements

### Requirement: Native Multica cache uses one verified read-only credential

Native Multica clone and fetch operations for the shared repository cache SHALL use one explicitly configured member/host credential inherited by the owning daemon, including cache creation before a task worktree exists and later background fetches. Before returning credentials, the helper SHALL reuse/split the existing runtime GitLab owner verification to authenticate the configured login and obtain its stable provider user ID, then authenticate `GET /api/v4/personal_access_tokens/self` on the exact requested GitLab host and require an active token whose user ID equals that runtime owner ID and whose complete standard scope set is exactly `read_repository`. It SHALL NOT require a stored provider ID. An unavailable owner/self-information endpoint, granular scope, additional scope, write-capable scope, owner mismatch, inactive token, or unexpected response SHALL fail before Git authentication. The cache member SHALL NOT be inferred from a project lead, task, branch, path name, ambient helper, or another available account.

#### Scenario: Cold cache uses the configured read-only account

- **WHEN** the selected daemon has no cached repository and its configured cache member has one exact-host active token with only `read_repository`
- **THEN** native clone obtains credentials through the short-lived helper, creates the cache, and leaves the remote URL and Git configuration free of credentials

#### Scenario: Background fetch keeps the same cache owner

- **WHEN** a later background fetch runs in the same daemon generation
- **THEN** it resolves the same configured member/host pair without consulting any task checkout or changing global account state

#### Scenario: Write-capable cache token is rejected

- **WHEN** token self-information reports `api`, `write_repository`, another additional standard scope, a granular scope, or cannot prove the full scope set
- **THEN** the helper returns no credential and native Git fails without trying another member

#### Scenario: Cache context is absent or ambiguous

- **WHEN** the helper lacks explicit cache-read purpose or one normalized member/host pair, or the requested host differs
- **THEN** it fails without reading another credential, invoking an ambient helper, or inferring a project

### Requirement: Cache helper configuration is process-local and replacement is generation-bound

The Multica daemon launch contract SHALL clear inherited Git credential helpers and select the stable `git-credential-odcli` executable through process-local Git configuration together with the explicit cache member and cache-read purpose. It SHALL NOT write tokens, member IDs, or helper commands into repository, worktree, system, or global Git configuration. One daemon generation SHALL keep its launch environment immutable. Replacing the cache member or credential SHALL use the existing `multica daemon stop`, one-shot `git credential fill` through `git-credential-odcli`, `multica daemon start`, and `multica daemon status --output json` interfaces: stop and wait for clone/fetch/index-pack children, validate the candidate, start one new generation with the replacement environment, then verify its identity/status. The product SHALL NOT add a hot-rotation API, mutable rotation state, refusal subsystem, or rotation lock.

#### Scenario: Daemon starts with cache authentication

- **WHEN** a daemon starts after the configured cache credential passes owner/scope validation
- **THEN** all of that daemon generation's remote cache clone/fetch children inherit only the process-local OdCLI helper selection

#### Scenario: Replacement creates a distinct daemon generation

- **WHEN** an operator replaces the cache credential using stop → validate → restart
- **THEN** the old daemon and its Git children have exited before validation, the candidate is validated before start, and only the new generation's children inherit the replacement environment

#### Scenario: Daemon configuration is rolled back

- **WHEN** cache authentication is rolled back by stopping the replacement generation and restoring or removing its process-local helper inputs before another start
- **THEN** the rollback generation uses only the restored launch environment, does not retain the replacement credential, and no persisted Git configuration requires cleanup

### Requirement: Cache and task identities never substitute for each other

The cache credential SHALL authorize only remote operations performed for the native shared cache. Credential-dependent operations in a verified task checkout SHALL select a task member through the `odcli-multica` contract and SHALL NOT consume or fall back to the cache member. Existing cached objects SHALL NOT be represented as proof that the task member can read the remote repository.

#### Scenario: Two task authors share one cached repository

- **WHEN** two verified task checkouts for the same repository select different members after the shared cache was fetched by the cache account
- **THEN** each task operation uses only its selected member credential and the shared cache member remains unchanged

#### Scenario: Task credential is unavailable

- **WHEN** a verified task operation cannot resolve its selected member for the exact host
- **THEN** it fails without using the cache credential even when the requested objects already exist locally
