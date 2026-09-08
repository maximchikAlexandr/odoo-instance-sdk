## MODIFIED Requirements

### Requirement: `module` commands

```bash
odcli module list --state installed
odcli module update comerta_base --dry-run
odcli module update comerta_base --yes
odcli module test comerta_base --test-tags /comerta_base --reload-tests
```

- `list [MODULE...]` MUST read `ir.module.module`.
- `update` MUST require `--yes`; before spawning it SHALL reject an empty requested selection and SHALL pass the requested names into the Odoo domain as a real list using the same JSON-decode technique as module listing, not a serialized list string.
- After `button_immediate_upgrade()`, `update` SHALL verify that every requested module appears in the returned `updated` collection; a missing module or empty recordset SHALL be a non-zero failure and SHALL never emit `ok=true`.
- `update` SHALL operate from either a ready environment or initialized project through the shared context and configured database; explicit `--env` SHALL retain precedence.
- `test` MUST remain a backward-compatible alias to the top-level local Odoo test operation and SHALL support both context kinds without fabricating an environment.
- install/uninstall MUST NOT be added. Public `ModuleResource` or `TestResource` MUST NOT exist.

#### Scenario: Module list

- **WHEN** `odcli module list --state installed` executes
- **THEN** installed modules are listed via local Odoo shell

#### Scenario: Requested names remain a list

- **WHEN** `odcli module update sale --yes` builds its Odoo shell source
- **THEN** the search domain receives `['sale']` as a list rather than the string `'["sale"]'`

#### Scenario: Empty recordset is not success

- **WHEN** Odoo returns no upgraded record for a requested module
- **THEN** the command exits non-zero with a sanitized failure and does not emit `ok=true` or claim that module was updated

#### Scenario: Project-context update

- **WHEN** `odcli module update sale --yes` runs from an initialized main checkout without an exact environment
- **THEN** it uses the project runtime and configured database through the existing update command path

#### Scenario: Module test compatibility

- **WHEN** an existing caller invokes `odcli module test comerta_base --test-tags /comerta_base`
- **THEN** the request reaches the same preflight, `OdooTestSpec`, runner, and result path as the top-level command

### Requirement: Shared human dry-run projection

Default Rich dry-run output SHALL use one shared projection showing the command goal, resolved project/environment/database/modules, intended mutations, preconditions, warnings, and the exact sanitized `display` of every captured `ProcessStep` in plan order. It SHALL collapse implementation-only probes and omit repeated classifications, executable, cwd, timeout, environment, stdin, and fingerprint details, but semantic summaries SHALL NOT replace or hide real subprocess commands. Argument boundaries and secret redaction SHALL come from the immutable process-step projection. JSON and TOON SHALL retain the complete immutable plan and remain semantically equal.

#### Scenario: Human plan is decision-oriented and executable-aware

- **WHEN** a mutating command with process steps is invoked with `--dry-run` in default Rich mode
- **THEN** the user sees targets, mutations, preconditions, warnings, and every exact sanitized process display without the remaining low-level fields

#### Scenario: Human plan is decision-oriented

- **WHEN** a mutating command is invoked with `--dry-run` in default Rich mode
- **THEN** the user sees targets, mutations, preconditions, and warnings without low-level execution fields

#### Scenario: Confirmed dry-run command families are visible

- **WHEN** Rich dry-run is invoked for `run`, `env checkout`, `postgres up`, `postgres stop`, `module update`, or `translations export` and its immutable plan contains a process step
- **THEN** output is non-empty and contains every such process display even when a precondition fails or a command-specific summary exists

#### Scenario: Conditional process branches are visible

- **WHEN** recording or fake execution proves that `env sync`, `env remove`, `db drop`, `test`, or a project/environment run variant contains a conditional process step
- **THEN** the same Rich contract shows that process display and does not replace it with only a semantic summary

#### Scenario: Machine plan remains complete

- **WHEN** the same dry-run is emitted as JSON or TOON
- **THEN** the full immutable execution snapshot, including exact redacted process details and fingerprint, is present and both decoded documents are equal

## ADDED Requirements

### Requirement: Project-owned catalogue scope

Commands that list catalogue records carrying project ownership SHALL resolve the current project by default and return only records owned by that project. `backup list` and `resource list` SHALL follow the existing `env list` project-selection pattern; global output SHALL require explicit `--all-projects`. Outside any project, the command SHALL require `--all-projects` rather than silently selecting a global page. Already project-bound commands such as `db list` SHALL remain project-bound and SHALL not gain a redundant scope switch. Filters, pagination, and read-only behavior SHALL remain unchanged. Machine schemas SHALL remain unchanged except for the additive CLI `env list.worktree_path` field and the new CLI `env path` envelope defined by CLI worktree path access.

#### Scenario: Backup list defaults to current project

- **WHEN** `backup list` runs inside an initialized project without `--all-projects`
- **THEN** every returned record belongs to the resolved project and unrelated catalogue records are absent

#### Scenario: Resource list defaults to current project

- **WHEN** `resource list` runs inside an initialized project without `--all-projects`
- **THEN** its inventory is limited to the resolved project using the existing ownership identities

#### Scenario: Global catalogue output is explicit

- **WHEN** an owned catalogue list is run with `--all-projects`
- **THEN** it may return records from all projects and its provenance reports the explicit cross-project selection

#### Scenario: Outside project fails without global opt-in

- **WHEN** `backup list` or `resource list` runs outside project context without `--all-projects`
- **THEN** it exits actionably without returning a global catalogue page

### Requirement: Rich list presentation contract

Every CLI command named `list` SHALL render its bounded Rich result as exactly one readable Rich table per logical result set, with stable title/heading and columns appropriate to its records. The audited inventory SHALL include `env list`, `backup list`, `db list`, `resource list`, and `module list`. Filesystem or storage byte counts SHALL use the existing shared human-readable byte formatter in Rich mode. `db list` SHALL show its bound cluster once in the title or heading and columns for database, size, sessions, default, and origin. JSON and TOON SHALL preserve exact integer byte values and existing list schemas except for the single approved additive change to an existing list schema: CLI `env list` SHALL add `worktree_path` as defined by CLI worktree path access. Every other list schema SHALL remain unchanged. The separately approved new `env path` leaf SHALL use its new envelope contract and SHALL NOT alter another leaf's schema.

#### Scenario: Backup list has one table

- **WHEN** one Rich `backup list` invocation returns records
- **THEN** it emits exactly one table and every byte-size cell is human-readable

#### Scenario: Database list does not repeat cluster

- **WHEN** Rich `db list` returns multiple databases from one bound cluster
- **THEN** the cluster appears once and each database occupies one row with database, human size, sessions, default, and origin columns

#### Scenario: All list families follow one contract

- **WHEN** the parameterized presentation inventory renders representative `env`, `backup`, `db`, `resource`, and `module` list results
- **THEN** each Rich result is tabular, non-duplicated, and uses human sizes wherever it displays filesystem or storage bytes

#### Scenario: Machine bytes remain exact

- **WHEN** the same list result is emitted as JSON and TOON
- **THEN** byte fields remain exact integers and strict-decoded documents are semantically equal; `env list` differs only by its approved additive `worktree_path`, while every other existing list schema is unchanged and the new `env path` envelope changes no list schema

### Requirement: Project-bound HTTP endpoint precedence

Every project-bound command that resolves the local Odoo endpoint SHALL apply one rule: an explicit command override first, then `ProjectConfig.preferred_http_port`, then `http_port` from the effective generated or source Odoo config, then the existing Odoo default only when neither configured value exists. The selected interface SHALL retain existing safe local-interface rules. Commands SHALL share the existing project runtime resolution path and SHALL NOT add another port setting or configuration abstraction.

#### Scenario: Preferred project port wins for restore

- **WHEN** `project.toml` specifies `preferred_http_port=8068`, the Odoo config omits `http_port`, and `db restore` performs project-bound postconditions
- **THEN** it uses the local Odoo endpoint on port 8068 rather than defaulting to 8069

#### Scenario: Explicit override wins

- **WHEN** an affected command accepts and receives an explicit endpoint or port override while the project also has a preferred port
- **THEN** the explicit value is used

#### Scenario: Odoo config remains fallback

- **WHEN** no explicit override and no preferred project port exist but the effective Odoo config contains `http_port`
- **THEN** every affected project-bound command uses that config port

#### Scenario: Affected commands share precedence

- **WHEN** a parameterized contract exercises every CLI path that reads `http_port` or infers a project-local Odoo endpoint
- **THEN** all paths resolve the same effective endpoint for equivalent inputs

### Requirement: CLI worktree path access

`odcli env list` SHALL add a `WORKTREE` column in Rich output and a CLI-only `worktree_path` field for every environment result in JSON and TOON. This field SHALL be the sole approved additive change to the existing `env list` machine schema. Values SHALL come from the existing catalogue `DevelopmentEnvironment.worktree_path` joined by stable environment ID, not directory-name inference. This CLI enrichment SHALL not add the path to canonical monitor, FastAPI, or dashboard snapshots and SHALL perform no second metrics collection.

The CLI SHALL add read-only `odcli env path [ENVIRONMENT]`. From inside an exact registered worktree, omission of the selector SHALL resolve that environment. From a project root or elsewhere, the positional selector SHALL use the existing environment name-or-UUID resolver. Unknown, ambiguous, removed, missing, or non-directory worktrees SHALL fail actionably; no arbitrary environment SHALL be selected. Root `--env` SHALL not become a command-local substitute.

Rich/default success SHALL write exactly one plain absolute path followed by one newline, with no label, table, ANSI, progress, or completion summary, so command substitution works even when the path contains spaces. JSON and TOON SHALL emit the approved new ordinary single-document `env path` envelope whose `result` contains exactly `environment_id`, `name`, and `worktree_path`; the existing standard envelope fields remain required, and this new leaf contract SHALL NOT modify any existing machine payload or schema. The CLI SHALL not add `env cd` or a nested shell.

#### Scenario: Path from registered worktree

- **WHEN** `odcli env path` runs anywhere inside a registered environment worktree
- **THEN** stdout is exactly that environment's absolute stored path plus a newline

#### Scenario: Positional name and UUID selection

- **WHEN** `env path` receives an unambiguous existing environment name or UUID from the project root
- **THEN** both selectors resolve the same stored path without guessing storage layout

#### Scenario: Invalid environment is not guessed

- **WHEN** selection is unknown, ambiguous, removed, or has a missing/non-directory worktree
- **THEN** the command exits non-zero with an actionable sanitized error and emits no path

#### Scenario: Path with spaces supports shell command substitution

- **WHEN** the stored absolute worktree path contains spaces and Rich/default `env path` succeeds
- **THEN** it emits the path without quoting or ANSI so `cd "$(odcli env path <environment>)"` selects the directory

#### Scenario: Machine path parity

- **WHEN** `env path` succeeds in JSON and TOON modes
- **THEN** each emits one standard new-leaf envelope whose `result` contains exactly equal `environment_id`, `name`, and absolute `worktree_path`, and no existing leaf schema changes

#### Scenario: Help documents normal shell flow

- **WHEN** the user reads `env path --help` or the short README example
- **THEN** it shows path retrieval and ordinary shell `cd` without advertising an `env cd` command

### Requirement: Human-oriented Rich bounded output

Every bounded CLI leaf SHALL render Rich output as a human-oriented summary using an appropriate table, labelled panel, concise sentence, and spacing for distinct sections. Rich SHALL NOT use raw JSON, concatenated structured documents, or unstructured key-value dumps as the primary result. Genuinely nested details MAY include contained pretty-printed JSON only when converting them would hide useful structure. JSON and TOON SHALL preserve their complete existing payloads, schemas, and single-document parity except for exactly two approved CLI-only worktree contracts: additive `worktree_path` in each `env list` environment result and the new `env path` envelope whose `result` contains `environment_id`, `name`, and `worktree_path`. Every other bounded-leaf machine payload and schema SHALL remain unchanged. Existing shared emitters and command-local renderers SHALL be corrected in place; no parallel renderer framework SHALL be introduced.

#### Scenario: Restore result is a human summary

- **WHEN** bounded Rich `db restore` completes
- **THEN** its primary result is a concise labelled summary or panel rather than a JSON-shaped payload

#### Scenario: Structured families remain separated

- **WHEN** a Rich result contains a summary plus nested details or multiple logical sections
- **THEN** labels, indentation, and whitespace make the sections distinct without concatenated documents

#### Scenario: Presentation inventory covers bounded CLI

- **WHEN** the shared parameterized presentation contract renders every bounded command family
- **THEN** none has empty output, raw primary JSON, concatenated documents, or an unstructured key-value dump

#### Scenario: Machine documents are unchanged

- **WHEN** the same results are emitted in JSON and TOON
- **THEN** complete structured payloads, schemas, exit semantics, and decoded parity remain unchanged except for the additive `env list.worktree_path` field and the new `env path` envelope; no other machine contract changes
