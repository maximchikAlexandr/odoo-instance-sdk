## ADDED Requirements

### Requirement: Generic GitLab command surface

The public CLI SHALL register `odcli git clone`, repository/member options on `odcli git commit` and `odcli git sync`, and `odcli git mr create` with the exact argument families specified by this change. These leaves SHALL be represented in `PUBLIC_LEAF_CASES`, expose public typed SDK command siblings for process/mutation work, support the existing bounded Rich/JSON/TOON and dry-run conventions, and work from outside an initialized OdCLI project when their explicit inputs are complete.

#### Scenario: Help outside a project
- **WHEN** `odcli git --help` and `odcli git mr create --help` run outside any initialized project
- **THEN** they show the new commands/options without attempting project resolution, credential reads, Git, or network access

#### Scenario: Missing explicit generic context
- **WHEN** a generic-repository operation lacks its required `--repo` or `--member-id`
- **THEN** Click returns an actionable usage error before SDK, credential, Git, or GitLab work

### Requirement: README inventory matches executable CLI

README SHALL list every actual top-level and nested command in the executable Click tree, including the new GitLab examples, and SHALL describe explicit-repository use without implying GitHub support, implicit initialization, standalone push, or automatic superproject gitlink publication. A contract test SHALL compare the maintained inventory markers with registered command paths.

#### Scenario: Documentation inventory is checked
- **WHEN** the CLI/documentation contract test enumerates registered commands
- **THEN** README contains each public command exactly once and no removed or unregistered command is presented as shipped
