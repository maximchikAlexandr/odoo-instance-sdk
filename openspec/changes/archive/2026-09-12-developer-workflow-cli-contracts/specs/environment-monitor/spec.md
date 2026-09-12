## ADDED Requirements

### Requirement: Focused selection from one snapshot
The monitor boundary SHALL provide a pure selector for one environment and its matching project and PostgreSQL cluster from one existing snapshot, preserving stopped records and explicit missing metrics without recollection. [Source: GH#43]

#### Scenario: Select explicit environment
- **WHEN** a known UUID or unambiguous name is selected from a captured snapshot
- **THEN** the selector returns its environment, project, and cluster records without another metrics collection
