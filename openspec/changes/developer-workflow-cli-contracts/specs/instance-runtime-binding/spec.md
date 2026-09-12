## ADDED Requirements

### Requirement: Effective runtime diagnosis
Doctor SHALL reuse the existing resolver/runtime view to report owner kind (`environment` or `project`), selection source, and effective Python, Odoo binary, config, database, and HTTP URL while distinguishing configured values from current availability. It SHALL work for initialized default checkouts, start no process, and expose no passwords or environment secrets. [Source: GH#43]

#### Scenario: Diagnose project without environment
- **WHEN** doctor runs in an initialized default checkout with invalid Python, stopped Odoo, available PostgreSQL, or ambiguous database state
- **THEN** it reports each configured/available fact and actionable finding under project ownership without inventing an environment
