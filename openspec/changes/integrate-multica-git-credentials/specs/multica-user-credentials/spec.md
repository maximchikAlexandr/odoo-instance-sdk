## MODIFIED Requirements

### Requirement: Existing root-creator context may select an explicit human member

The existing verified task context and `RootCreatorContext` path SHALL remain the only bridge selection contour. When `member_id` is omitted, current root-creator traversal and validation SHALL remain unchanged. When `member_id` is supplied, the bridge SHALL normalize it, require a current human member of the already verified workspace, and carry that selected member plus source `explicit` through the existing context and execution revalidation. It SHALL NOT add a parallel selector/result abstraction or treat the selected member as authority for another checkout, task, workspace, repository, or host.

#### Scenario: Automatic selection is unchanged

- **WHEN** a credential-dependent operation omits `member_id`
- **THEN** the existing root issue's verified human creator is selected and revalidated

#### Scenario: Explicit human member is selected

- **WHEN** `member_id` identifies one current human in the verified workspace
- **THEN** the existing context carries that member with source `explicit` while retaining all verified task and repository facts

#### Scenario: Explicit member is invalid or changes

- **WHEN** the value is malformed, absent, non-human, cross-workspace, removed, or no longer matches at execution
- **THEN** the operation fails before credential delivery or mutation and does not fall back to the root creator

### Requirement: One selected credential snapshot serves existing consumers

Raw Git, synchronization, and merge-request publication SHALL resolve the selected member and exact host once through the existing credential snapshot path. They SHALL NOT fall back to the cache account or another member. Public plans/results MAY add only the selected non-secret member/source fields and SHALL preserve existing redaction.

#### Scenario: Two accounts use isolated task operations

- **WHEN** two verified operations for the same repository select different valid members
- **THEN** each existing consumer uses only its own selected exact-host credential snapshot

#### Scenario: Selected credential is unavailable

- **WHEN** the selected member has no valid credential for the exact host
- **THEN** the operation fails before Git or GitLab access without trying the cache identity or another account
