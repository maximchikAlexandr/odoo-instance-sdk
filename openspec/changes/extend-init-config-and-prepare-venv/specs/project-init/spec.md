## ADDED Requirements

### Requirement: Stored project config-generation inputs

`ProjectConfig` SHALL store ordered project-relative addon roots and an ordered list of server-wide modules in `.odcli/project.toml`. Addon roots SHALL reject absolute paths and paths that escape the repository root after resolution. On repeated init, an omitted option SHALL retain its stored value, while an explicitly supplied option SHALL replace the complete stored list, including an explicitly empty list.

#### Scenario: Ordered settings round-trip
- **WHEN** init receives addon roots `addons/community`, `addons/private` and server-wide modules `base`, `web`
- **THEN** the manifest round-trips both lists in the supplied order

#### Scenario: Re-init retains omitted settings
- **WHEN** a project already stores addon roots and server-wide modules and repeated init omits both options
- **THEN** init uses the stored values for its resolved manifest and generated config

#### Scenario: Explicit values replace stored settings
- **WHEN** repeated init explicitly supplies a different addon-root or server-wide-module list
- **THEN** the supplied list replaces that stored list and is used for config regeneration

#### Scenario: Escaping addon root is rejected
- **WHEN** init receives an absolute addon root or a relative addon root that resolves outside the repository
- **THEN** init fails before writing the manifest or generated config

### Requirement: Project Odoo configuration generation

The public init operation SHALL include generation of `.odcli/odoo.conf` in its immutable plan and SHALL generate it before any database bootstrap. The existing generator SHALL accept an absent source config, preserve applicable source options when one exists, overlay the resolved database connection, database name and filter, HTTP interface and port, data directory, logfile, secrets, ordered absolute addon paths, and server-wide modules, and write the project config atomically with mode `0600`.

#### Scenario: Config generated without a source
- **WHEN** init resolves a complete project without a source config
- **THEN** `.odcli/odoo.conf` is generated with all resolved runtime values before bootstrap begins

#### Scenario: Source settings are preserved and resolved settings win
- **WHEN** init uses an existing source config containing applicable custom settings and values also resolved by the project
- **THEN** the generated config preserves the custom settings and overlays every project-resolved value

#### Scenario: Addon paths preserve order
- **WHEN** init stores multiple addon roots
- **THEN** `addons_path` contains their repository-root-resolved absolute paths in stored order

#### Scenario: Server-wide modules are generated
- **WHEN** init stores server-wide modules
- **THEN** `server_wide_modules` in the generated config contains those modules in stored order

#### Scenario: Config exists and differs
- **WHEN** generated config content differs from an existing `.odcli/odoo.conf`
- **THEN** init applies the existing non-identical init confirmation or headless-refusal contract to the manifest and config as one replacement set

#### Scenario: Confirmed dry-run remains inert
- **WHEN** confirmed init dry-run resolves a different manifest or generated config
- **THEN** its plan reports both artifacts and bootstrap ordering without changing either artifact or starting bootstrap

#### Scenario: Re-init reuses prepared resources
- **WHEN** repeated init resolves unchanged settings and project lifecycle resources are already complete
- **THEN** init reuses those resources, verifies the existing postconditions, and does not recreate them
