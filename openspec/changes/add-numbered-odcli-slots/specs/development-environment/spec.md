## ADDED Requirements

### Requirement: Selected global user root
The SDK SHALL resolve its global user root through the central path provider. An absent selector SHALL mean canonical `~/.odcli`; a trusted launcher's explicit absolute selector SHALL be normalized without consulting or replacing `HOME`, and every global configuration, catalogue, backup, environment, project, lock, pgAdmin, bug-report, storage-migration, and update path SHALL descend from that root. Repository-local `<checkout>/.odcli` SHALL remain beneath the linked checkout and SHALL NOT move into the global root.

#### Scenario: Alternate root reaches all global providers
- **WHEN** `odcli-3` selects `~/.odcli-3` or `odcli-fix-42` selects `~/.odcli-fix-42`
- **THEN** every SDK-owned global provider returns a descendant only of that selected root while `HOME` remains unchanged

#### Scenario: Numbered project state and interpreter stay linked
- **WHEN** `odcli-N` operates in its recorded independent checkout
- **THEN** `.odcli/project.toml`, private environment/configuration, filestore, logs, and runtime files remain under that checkout, while its recorded SDK-owned Python environment resolves beneath the selected numbered root and is not shared with any canonical, numbered, or hot-fix identity

### Requirement: Noncanonical roots do not adopt legacy storage
Startup storage migration SHALL run legacy platformdirs discovery, copy, path rewriting, and source cleanup only for canonical default state. With an explicit noncanonical selector, startup SHALL create or use that root independently and SHALL NOT inspect, copy, rewrite, lock, or delete canonical `~/.odcli`, any legacy root, or a neighboring alternate root.

#### Scenario: Empty alternate root starts beside populated legacy storage
- **WHEN** a numbered or hot-fix command first starts while canonical and legacy storage contain data
- **THEN** its root begins independently without copied catalogue, configuration, paths, or migration history and all pre-existing roots remain unchanged

#### Scenario: Canonical migration remains compatible
- **WHEN** ordinary `odcli` starts with legacy storage and no explicit selector
- **THEN** the existing canonical legacy migration contract remains in force
