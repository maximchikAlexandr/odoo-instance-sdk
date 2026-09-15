## ADDED Requirements

### Requirement: Preparation records canonical project_id on download

The preparation workflow SHALL pass the resolved canonical `project_id` into `start_download()` before HTTP transfer for both download-only refresh and remote-backup plus local-restore flows. The backup row SHALL record the canonical `project_id` so the result is visible in the project's `backup ls` without an environment or restore link. Generic SDK backup without project context SHALL remain unowned.

#### Scenario: Refresh records project_id

- **WHEN** `odcli db refresh` downloads a remote backup for a resolved project
- **THEN** the resulting backup row has a non-null canonical `project_id`

#### Scenario: Remote restore preparation preserves ownership

- **WHEN** remote-backup plus local-restore runs for a resolved project
- **THEN** the downloaded backup row records the canonical `project_id` before the local restore begins