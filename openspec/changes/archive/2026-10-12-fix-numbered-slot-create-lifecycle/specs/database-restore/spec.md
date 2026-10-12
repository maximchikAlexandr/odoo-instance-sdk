## ADDED Requirements

### Requirement: COPY auxiliary restore lifecycle is independent of backup source kind

Every COPY checkout that can restore a backup SHALL capture and run the existing bounded local auxiliary Database Manager lifecycle when the project runtime supports it, regardless of whether the archive came from a local-source backup, a named remote, or an exact retained catalogue backup. Remote backup acquisition SHALL remain a separate source operation and SHALL NOT authorize the remote endpoint as the target Database Manager. The immutable command SHALL retain one auxiliary start/readiness/request-authorization/cleanup sequence and one coordinator-owned local target absence/postcondition probe pair.

#### Scenario: Named-remote plan includes local auxiliary restore actions
- **WHEN** a named-remote COPY checkout or adoption command is inspected
- **THEN** its sanitized immutable plan SHALL include the existing local auxiliary identity, port, start, readiness, restore-request revalidation, and cleanup actions around the restore, plus the existing remote backup wait/transfer actions

#### Scenario: Retained-backup plan includes local auxiliary restore actions
- **WHEN** a retained-backup COPY checkout or adoption command is inspected
- **THEN** its sanitized immutable plan SHALL include the same local auxiliary restore actions without remote backup wait or transfer actions

#### Scenario: Source acquisition failure starts no target mutation
- **WHEN** named-remote backup acquisition fails before a verified archive is available
- **THEN** the target database SHALL remain absent, no ready environment SHALL be recorded, and any owned auxiliary runtime or secret configuration already created SHALL be cleaned under the existing failure contract

#### Scenario: Auxiliary cleanup preserves the primary outcome
- **WHEN** the local auxiliary runtime was started for named-remote or retained-backup COPY and restore succeeds, fails, or is cancelled
- **THEN** the SDK SHALL stop and unregister only its exact owned runtime, remove its temporary secret configuration, and preserve the primary result or failure while keeping unrelated runtimes and source assets unchanged
