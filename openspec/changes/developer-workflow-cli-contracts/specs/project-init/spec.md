## ADDED Requirements

### Requirement: Generated Compose config is independently idempotent
Init SHALL validate `.odcli/odoo.conf` independently of an unchanged `project.toml`; missing or stale generated config SHALL be atomically regenerated from current manifest, source config, port, and cluster binding without rewriting manifest/source config. Current config SHALL remain a true no-op, mode `0600`, tracked/symlink/unsafe targets SHALL fail before partial writes, and dry-run SHALL expose the need without files or secrets. [Source: GH#64 §3]

#### Scenario: Repair missing generated config
- **WHEN** the manifest is unchanged but generated Compose config is missing or stale
- **THEN** re-init repairs only generated config and preserves source and manifest bytes

### Requirement: Git commit settings
New project manifests SHALL explicitly write tracker-neutral `ticket_link_enabled` and `ticket_base_url` settings for Odoo commit generation; historical alpha projects with the removed vendor-specific enabled setting but no URL SHALL fail with migration guidance, project values SHALL override an already-existing global setting, and no global-config subsystem SHALL be introduced solely here. Supported config identifiers SHALL NOT name a ticket vendor. [Sources: GH#65; GH#64 §11]

#### Scenario: Initialize Git commit policy
- **WHEN** a new project is initialized with defaults
- **THEN** its manifest contains explicit `ticket_link_enabled` and `ticket_base_url` values consumed by Git commit planning and no vendor-specific setting
