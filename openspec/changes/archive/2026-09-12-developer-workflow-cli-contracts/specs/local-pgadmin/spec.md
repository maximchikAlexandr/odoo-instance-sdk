## ADDED Requirements

### Requirement: pgAdmin follows unified storage migration
All pgAdmin private/data artifacts SHALL resolve through the canonical unified OdCLI path provider and participate in the same locked conflict-safe migration, without a parallel root. [Source: GH#64 §7]

#### Scenario: Migrate pgAdmin state
- **WHEN** legacy pgAdmin data exists and the unified destination is compatible
- **THEN** it is preserved below `~/.odcli/pgadmin/` with existing permissions and ownership
