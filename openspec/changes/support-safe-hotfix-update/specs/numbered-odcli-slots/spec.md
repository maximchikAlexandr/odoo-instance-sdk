## ADDED Requirements

### Requirement: Existing hot-fix identity advances only through reviewed provenance
The repository-owned hot-fix helper SHALL expose an `advance ISSUE CANDIDATE_SHA --pr PR --review REVIEW` operation for an existing `odcli-fix-ISSUE` identity. Before invoking any mutating alternate-tool operation, it SHALL inspect the installed identity and require a healthy package-owned manifest whose kind and identifier match `ISSUE`, whose recorded PR matches `PR`, and whose installed SHA differs from `CANDIDATE_SHA`. It SHALL verify that the open pull request head equals the complete lowercase `CANDIDATE_SHA`, that the pull request links the same issue in the configured repository, and that `REVIEW` contains a successful independent transition review binding the installed SHA, candidate SHA, and a non-empty session reference different from the installed manifest's review session. The helper SHALL reject stale, reused-session, mismatched, malformed, or incomplete inputs before mutation.

#### Scenario: Reviewed successor advances one identity
- **WHEN** an existing healthy hot-fix identity is advanced to a different current pull-request head with a successful transition review for its installed and candidate SHAs
- **THEN** the helper delegates exactly one replacement to the shared alternate-tool lifecycle and reports the retained launcher identity plus old and new SHAs in bounded JSON-safe output

#### Scenario: Stale review or moved pull request fails before mutation
- **WHEN** the review names a different installed or candidate SHA, lacks a session reference, reuses the installed review session, reports incompatibility, or the pull-request head no longer equals the candidate SHA
- **THEN** the helper fails before invoking replacement and the installed manifest, launcher, tool, state root, and canonical installation remain unchanged

#### Scenario: Issue or pull-request identity mismatch fails before mutation
- **WHEN** the requested issue differs from the installed hot-fix identity, the requested PR differs from its manifest, or that PR does not link the issue in the configured repository
- **THEN** the helper fails before replacement and SHALL NOT adopt, overwrite, remove, or relabel any installation

### Requirement: Transition review proves preserved-state compatibility
The independent reviewer helper SHALL provide a transition review operation that reads both complete lowercase installed and candidate SHAs and emits a structured verdict containing those exact SHAs, a compatibility boolean, findings, and a non-empty review session reference. Its read-only prompt SHALL require inspection of candidate selector confinement, the existing installed hot-fix manifest contract, catalogue and migration compatibility for the preserved selected root, and explicitly shared project, Odoo, PostgreSQL, database, filestore, Docker, and port resources. Uncertainty SHALL produce `compatible=false`. The candidate package SHALL NOT execute against or migrate the preserved hot-fix state as part of review or staging.

#### Scenario: Compatible state transition is approved
- **WHEN** an independent review can prove that the candidate can open and migrate the installed hot-fix state without violating selector or shared-resource isolation
- **THEN** its structured result binds both SHAs and the review session and may be consumed by the advance operation

#### Scenario: Unproven transition is rejected
- **WHEN** catalogue/migration compatibility, selector confinement, installed provenance, or shared-resource safety cannot be established from read-only inspection
- **THEN** the reviewer emits `compatible=false` and the advance operation rejects the result before mutation

### Requirement: Shared lifecycle replaces code while retaining state
The package-owned alternate-tool lifecycle SHALL permit replacement of an existing hot-fix only when its verified manifest matches the selected identity, its selected state root is an existing non-symlink directory, and the candidate carries the same issue and PR identity with exact candidate review provenance. It SHALL stage the immutable candidate by exact SHA, verify source provenance, materialize its executables, and run its selector-capability probe against a disposable root before acquiring the exclusive lifecycle lock. Under that lock it SHALL re-read and revalidate the installed identity, atomically replace only the tool, bin, launcher, and manifest, and retain the same state-root path and all of its contents.

#### Scenario: Populated state survives activation
- **WHEN** a candidate passes transition review, immutable staging, provenance, and selector checks and replacement completes
- **THEN** the launcher name and `ODCLI_USER_ROOT` path are unchanged, the manifest records the candidate SHA and review provenance, all pre-existing catalogue, backup, environment metadata, and project configuration bytes remain present, and the old tool artifacts are removed

#### Scenario: Preparation failure preserves the old installation
- **WHEN** candidate installation, provenance validation, executable materialization, or disposable selector probing fails
- **THEN** no published path or preserved-state byte changes and the old launcher remains usable at its original SHA

#### Scenario: Activation failure restores the old installation
- **WHEN** moving staged artifacts, relocating executable interpreters, or publishing the launcher or manifest fails after the exclusive lock is acquired
- **THEN** the lifecycle restores the previous tool, bin, launcher, and manifest, leaves preserved state unchanged, removes safely owned staging artifacts, and the old launcher remains usable

### Requirement: Hot-fix update serializes without disturbing active resources
The advance operation SHALL reject replacement when invoked from the selected hot-fix being replaced. The shared lifecycle SHALL use the existing selected-identity lock so install, advance, remove, and launcher execution cannot mutate or consume a half-published identity concurrently. It SHALL preserve canonical OdCLI, neighboring alternate identities, running Odoo processes, external PostgreSQL services, databases, filestores, Docker resources, and ports; update SHALL perform no cleanup or resource lifecycle operation.

#### Scenario: Selected tool cannot replace itself
- **WHEN** `odcli-fix-ISSUE` is the active launcher identity and its helper requests advance of the same issue
- **THEN** the request fails before staging or mutation instead of waiting on its own shared lifecycle lock

#### Scenario: Concurrent launcher use delays publication safely
- **WHEN** another process is using the selected hot-fix while an externally invoked advance has completed staging
- **THEN** publication waits for the existing shared lock holder, then revalidates the manifest under the exclusive lock before switching, and no process observes a mixed installation

#### Scenario: Neighbor and external resources are preserved
- **WHEN** an advance succeeds or fails while canonical OdCLI, another alternate identity, Odoo, PostgreSQL, databases, filestores, Docker resources, and allocated ports exist
- **THEN** only the selected hot-fix tool artifacts and manifest may change, and before/after evidence shows every nonselected or external resource retains its identity and state

### Requirement: Developer guidance and regression coverage use the supported boundary
The autonomous-work skill SHALL describe transition review and `fix_tool.py advance` as the only supported way to update an active isolated hot fix and SHALL continue to distinguish it from canonical `fix_tool.py update`. Automated coverage SHALL exercise successful populated-state preservation and preparation/activation rollback through the package-owned lifecycle, plus stale review, moved PR, identity mismatch, self-replacement, and lifecycle conflict behavior through the developer-helper boundary. Architecture inventory checks SHALL continue to reject a second installer, lifecycle lock, provenance validator, launcher renderer, or cleanup implementation.

#### Scenario: Agent follows the supported update path
- **WHEN** an agent needs to move an active hot fix to a newly reviewed pull-request head
- **THEN** repository guidance directs it to create a transition review and invoke `fix_tool.py advance`, without remove/reinstall, direct filesystem mutation, or canonical `odcli update`

#### Scenario: Regression suite covers preservation and failure paths
- **WHEN** repository test and architecture gates run
- **THEN** they prove the advance success and specified failure scenarios through existing shared lifecycle and helper seams and reject duplicated lifecycle mechanics
