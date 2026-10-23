## ADDED Requirements

### Requirement: Project registration state controls ordinary monitor visibility

Ordinary global and project-filtered monitor/catalogue planning SHALL select only project rows whose registration state is `registered`. A forgotten row SHALL remain available only to explicit lifecycle lookup by full `project_id`; it SHALL NOT create a project summary, runtime probe, cluster probe, environment group, or cache entry. The registration transition SHALL invalidate affected monitor caches without deleting retained ownership.

#### Scenario: Forgotten project disappears from snapshot

- **WHEN** a project with no included active environment is forgotten
- **THEN** subsequent global and explicit project-filtered snapshots contain no project or environment entry for it and perform no runtime, Git, storage, PostgreSQL, or Docker probe for it

#### Scenario: Retained ownership does not imply visibility

- **WHEN** a forgotten project retains stopped databases, backups, source, configuration, secrets, or a stopped owned cluster
- **THEN** those ownership records remain internally addressable while the project stays absent from ordinary monitor/catalogue output

#### Scenario: Reactivation restores one project

- **WHEN** successful initialization reactivates a forgotten project
- **THEN** the next uncached snapshot includes exactly one project under the original `project_id` and preserves existing environment compatibility
