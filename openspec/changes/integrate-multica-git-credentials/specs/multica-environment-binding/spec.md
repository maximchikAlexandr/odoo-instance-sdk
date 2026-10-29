## ADDED Requirements

### Requirement: Credential member override preserves verified task binding

Credential-dependent `MulticaOdooClient` command builders for native Git passthrough, Git synchronization/publication, and GitLab merge-request publication SHALL accept an optional typed `member_id`; their CLI leaves SHALL expose the same input as `--member-id`. The explicit member SHALL take precedence over root-creator credential selection only after `VerifiedTaskContext` has proved the exact checkout, issue, run, project, workspace, runtime, daemon, and repository. Execution SHALL revalidate the selected member and all existing context, origin, branch, and stale-plan safeguards before credential delivery or mutation. Context inspection and environment preparation SHALL remain credential-independent and SHALL NOT accept this option.

#### Scenario: Raw Git uses an explicit member

- **WHEN** raw Git is requested for a verified checkout with `--member-id` naming a valid human workspace member
- **THEN** the frozen command selects that member while Git remains restricted to the verified checkout and exact remote host

#### Scenario: Sync uses automatic selection without the flag

- **WHEN** `git sync` omits `--member-id`
- **THEN** the existing verified root-creator behavior and all current sync/push safeguards remain unchanged

#### Scenario: Override cannot redirect the operation

- **WHEN** an explicit member is valid but checkout, issue, workspace, repository, origin, branch, or host evidence is invalid or changes after planning
- **THEN** the operation fails before credential delivery or mutation and does not reinterpret the member as authority for the conflicting resource

#### Scenario: CLI and SDK inputs agree

- **WHEN** a credential-dependent CLI leaf receives `--member-id`
- **THEN** it passes the normalized value to the corresponding typed command builder and does not read helper environment variables or resolve credentials in the callback
