## ADDED Requirements

### Requirement: Selected global user root
The SDK SHALL resolve its global user root exactly once through the central path provider. An absent selector SHALL mean canonical `~/.odcli`; an explicit absolute selector supplied by a trusted launcher SHALL be normalized without consulting or replacing `HOME`, and every global configuration, catalogue, backup, environment, project, lock, pgAdmin, bug-report, storage-migration, and update path SHALL descend from that root. Repository-local `<project>/.odcli` SHALL remain project-local and unchanged.

#### Scenario: Alternate root reaches all global providers
- **WHEN** a numbered launcher selects `~/.odcli-3` or a hot-fix launcher selects `~/.odcli-fix-42`
- **THEN** global path providers for catalogue, configuration, backups, environments, projects, locks, pgAdmin, reports, and journals return descendants only of that selected root

#### Scenario: Repository-local state is not redirected
- **WHEN** a numbered command operates on a project containing `.odcli/project.toml`, `.odcli/.env`, `.odcli/odoo.conf`, or `.odcli/filestore`
- **THEN** those paths remain beneath the selected project checkout rather than the numbered user root

### Requirement: Noncanonical roots do not adopt legacy storage
Startup storage migration SHALL run legacy platformdirs discovery, copy, path rewriting, and source cleanup only for the canonical default root. When an explicit noncanonical root is selected, startup SHALL create or use that root as independent state and SHALL NOT inspect, copy, rewrite, lock, or delete canonical `~/.odcli` or any legacy platformdirs root.

#### Scenario: Empty alternate root starts beside populated legacy storage
- **WHEN** a numbered or hot-fix command first starts while canonical and legacy storage contain data
- **THEN** its root begins independently without copied catalogue, configuration, paths, or migration history, and all canonical and legacy bytes remain unchanged

#### Scenario: Canonical migration remains compatible
- **WHEN** ordinary `odcli` starts with legacy storage and no explicit root selector
- **THEN** the existing canonical legacy migration contract remains in force
