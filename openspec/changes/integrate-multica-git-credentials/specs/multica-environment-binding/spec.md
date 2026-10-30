## ADDED Requirements

### Requirement: Optional member override preserves the existing verified binding

Credential-dependent raw Git, synchronization, and merge-request command builders SHALL accept optional typed `member_id`, and their CLI leaves SHALL expose `--member-id`. The callbacks SHALL pass the value into the existing context/revalidation path. Every current checkout, issue, workspace, repository, origin, branch, stale-plan, subprocess, and host check SHALL still run before credential delivery or mutation. Credential-independent inspection and environment preparation SHALL not accept the option.

#### Scenario: Raw Git uses an explicit member

- **WHEN** raw Git receives a valid `--member-id` for a verified checkout
- **THEN** the existing command plan selects that member while retaining the verified checkout and exact host

#### Scenario: Override cannot redirect an operation

- **WHEN** member evidence is valid but checkout, issue, workspace, repository, origin, branch, or host evidence conflicts or changes
- **THEN** existing revalidation fails before credential delivery or mutation

#### Scenario: CLI and SDK agree

- **WHEN** a credential-dependent CLI leaf receives `--member-id`
- **THEN** it passes the normalized value to the corresponding command builder without resolving credentials in the callback
