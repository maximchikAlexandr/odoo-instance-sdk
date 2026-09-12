## ADDED Requirements

### Requirement: Direct project ownership of backups
The catalogue SHALL persist canonical `project_id` when a project command creates a backup row and SHALL scope ordinary project lists by that direct ownership rather than environment/restore linkage. `--all-projects` SHALL remain global; legacy rows SHALL remain unowned unless provenance proves exactly one deterministic owner or an explicit safe relink is performed. Zero-owner or multi-project provenance SHALL remain null, SHALL NOT appear in any project-scoped query, and SHALL remain visible globally. [Source: GH#64 §1]

#### Scenario: Downloaded backup is immediately project-visible
- **WHEN** a local or remote project download succeeds before any restore or environment link exists
- **THEN** it appears in that project's list, not another project's list, and appears globally with `--all-projects`

#### Scenario: Ambiguous legacy ownership stays unowned
- **WHEN** a legacy backup has provenance resolving to two registered projects
- **THEN** migration leaves `project_id` null, excludes the backup from both project-scoped queries, and retains it in the global query until explicit safe relink

### Requirement: Unified catalogue migration
The sequential v15-to-v16 catalogue migration and all path-bearing rows SHALL migrate under the unified root without changing backup UUIDs, event/restore/environment relationships, deterministic ownership, or valid backup/environment paths; retries after completion or interruption SHALL not duplicate records. [Source: GH#64 §7]

#### Scenario: Migrate existing catalogue
- **WHEN** an installation has a populated v15 catalogue plus legacy backup and environment paths
- **THEN** migration reaches v16, preserves UUID resolution and every event/restore/environment relation, and verifies the destination before legacy removal
