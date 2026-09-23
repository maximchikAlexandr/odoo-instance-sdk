## MODIFIED Requirements

### Requirement: Streaming remote backup transfer

Remote refresh SHALL accept an optional `test_instance.database`. If it is explicitly set, it SHALL be used without requiring database list availability. If it is absent, remote-refresh preflight SHALL obtain names through the existing `DatabaseResource.names()`; exactly one database SHALL be selected for the current operation; zero databases SHALL fail with `remote_database_none`; multiple databases SHALL fail with `remote_database_ambiguous` listing available names; an unavailable list SHALL fail with `remote_database_list_unavailable`. All three fail before download. The auto-detected name SHALL be reflected in plan/result and backup provenance but SHALL NOT be written back to `project.toml`.

#### Scenario: explicit database has priority

- **WHEN** `test_instance.database` is explicitly set
- **THEN** it is used and database list availability is not required

#### Scenario: single database auto-selected

- **WHEN** `test_instance.database` is absent and the instance exposes exactly one database
- **THEN** that database is selected and the name appears in plan/result and backup provenance

#### Scenario: zero databases error

- **WHEN** `test_instance.database` is absent and the instance exposes zero databases
- **THEN** the command fails before download with `remote_database_none`

#### Scenario: many databases list names

- **WHEN** `test_instance.database` is absent and the instance exposes multiple databases
- **THEN** the command fails before download with `remote_database_ambiguous`, lists the available names, and asks to set `test_instance.database`

#### Scenario: unavailable list is a distinct error

- **WHEN** `test_instance.database` is absent and the database list is unavailable
- **THEN** the command fails before download with `remote_database_list_unavailable`

#### Scenario: auto-detected name is not written back

- **WHEN** a name is auto-detected and the operation completes
- **THEN** `project.toml` is unchanged and backup provenance records the name for the operation only