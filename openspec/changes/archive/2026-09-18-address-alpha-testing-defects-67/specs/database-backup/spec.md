## MODIFIED Requirements

### Requirement: Project ownership captured at download start

Project-backed backup operations SHALL pass the resolved canonical project identifier into `start_download()` for local and remote sources before HTTP transfer; source type SHALL NOT determine project ownership. All project-owned download flows—including download-only refresh and remote-backup plus local-restore—SHALL record the canonical `project_id` in the backup row before transfer. Generic SDK backup without project context SHALL remain unowned (`project_id = NULL`) and visible only in the global list. Ownership SHALL NOT be inferred from environment/restore joins or from URL/database/branch matching.

For an already-created row with `project_id = NULL`, an explicit safe relink to a resolved current project or a documented repair path SHALL be available. Automatic ambiguous backfill SHALL NOT be performed.

#### Scenario: Start project download

- **WHEN** backup download is planned and executed from a resolved project owner
- **THEN** its catalogue row records that project before transfer state advances

#### Scenario: Generic backup stays unowned

- **WHEN** `client.instance(remote_url).databases.backup(...)` is called without project context
- **THEN** the resulting backup row has `project_id = NULL`

#### Scenario: Unowned row can be relinked

- **WHEN** an existing unowned backup is relinked to a resolved project
- **THEN** the UUID, file, and history remain unchanged and the row gains the canonical `project_id`