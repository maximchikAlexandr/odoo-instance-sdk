## ADDED Requirements

### Requirement: Unified bordered structured Rich tables

Every bounded human-readable result that presents multiple named fields or repeated records SHALL use a Rich table with a visible outer border, visible vertical separators between columns, visible horizontal separators between rows, and a visually distinct header. The contract SHALL apply to the existing structured table surface in `ps`; `env list`; backup list/inspect/validate/delete plans and results; database list/diagnostics/lifecycle summaries; PostgreSQL status/locks/stats/bloat/monitoring/image approval; module list/info/where/deps/update; resource list/doctor; Git workflow results; translation export/validation; Odoo test results; VS Code generation; and repeated multi-target plans/results.

At supported terminal widths, every table SHALL wrap or fold within the available width without dropping a primary record identity or status. Empty collections and unavailable values SHALL remain explicit within the bordered structure.

#### Scenario: Structured tables share visible geometry

- **WHEN** any audited structured Rich renderer emits a table
- **THEN** the table has an outer border, column separators, row separators, and a distinct header

#### Scenario: Representative widths remain bounded

- **WHEN** representative structured results render at 80, 120, or 180 columns with long values
- **THEN** every output line fits the requested width and primary identity and status remain readable

#### Scenario: Empty and unavailable values remain explicit

- **WHEN** an audited renderer receives an empty collection or unavailable metrics with a typed reason
- **THEN** its bordered table contains an explicit empty/unavailable row or cell and preserves the reason in details

### Requirement: Compact narrow output remains tabular

Structured human output SHALL NOT switch to unbordered prose because the terminal is narrow. At narrower supported widths, the renderer SHALL use fewer primary columns and SHALL move secondary named fields into a wrapping `Details` cell while preserving the bordered-table contract. `env list` SHALL use a bordered compact table at 80 columns and SHALL NOT emit one free-form text block per checkout.

#### Scenario: Narrow environment list is bordered

- **WHEN** `odcli env list` renders at 80 columns
- **THEN** each checkout is a row in a bordered table and branch, database, Git, provider, or path facts that do not fit primary columns appear in `Details`

#### Scenario: Narrow long values wrap without record loss

- **WHEN** a narrow structured table contains long identifiers, paths, or diagnostic details
- **THEN** Rich wraps or folds the values within the table and does not omit the record

### Requirement: Structured human facts use local tables

The Rich projections for `doctor`, `env show`, detached `run`, `deps verify`, and `module install-order` SHALL render their multiple named fields as bordered tables. `doctor` SHALL render one check table for each applicable global, project, or environment section and SHALL retain remediation/fact details. `env show` SHALL retain environment, project, runtime, and PostgreSQL facts. Detached `run` SHALL retain PID, endpoint, owner context when present, and log path. `deps verify` SHALL retain check, status, and details. `module install-order` SHALL retain deterministic ordinal and module rows. Repeated multi-target plans/results SHALL use the same row-oriented contract.

#### Scenario: Doctor groups checks into tables

- **WHEN** `odcli doctor` returns checks across global, project, or environment scopes
- **THEN** each applicable scope is rendered once as a bordered `Check | Status | Details` table with its facts and remediations preserved

#### Scenario: Environment facts remain complete

- **WHEN** `odcli env show` succeeds
- **THEN** one bordered table contains the selected environment, project, runtime, and PostgreSQL facts without changing the typed result

#### Scenario: Detached launch is a structured result

- **WHEN** `odcli run --detach` succeeds
- **THEN** one bordered table contains PID, endpoint, owner context when available, and log path

#### Scenario: Dependency checks are row-oriented

- **WHEN** `odcli deps verify` succeeds or reports issues
- **THEN** one bordered table renders each check with status and details while preserving the command exit status

#### Scenario: Install order is explicit

- **WHEN** `odcli module install-order` returns modules
- **THEN** one bordered table renders one deterministic `Order | Module` row per module

### Requirement: One human result has one emission owner

A command execution SHALL render each logical bounded Rich result exactly once. A command-local Rich projection SHALL build and serialize its renderable without writing to process stdout or stderr; the shared emitter or an explicit one-shot/live owner SHALL perform the only terminal emission. Child-process output captured as structured result data SHALL NOT also pass through as a second human result.

#### Scenario: Module list renders once

- **WHEN** `odcli module ls bcrm` completes successfully
- **THEN** the module result header and the `bcrm` result row are each rendered exactly once

#### Scenario: Projection is side-effect free

- **WHEN** a command-local Rich projection is invoked against a frozen output document
- **THEN** it returns its rendered text without writing to stdout or stderr

### Requirement: PostgreSQL human state semantics are consistent

Human projections in `ps`, `env list`, `env show`, `doctor`, and `postgres status` SHALL derive PostgreSQL lifecycle wording from the canonical typed cluster state for their source snapshot. Metric/server/query availability reasons SHALL appear separately in `Details` and SHALL NOT replace or contradict lifecycle state. The renderers SHALL show only proven ownership/attribution and SHALL keep unknown or unavailable data explicit.

#### Scenario: Healthy cluster with unavailable stats stays healthy

- **WHEN** the frozen cluster state is `healthy` and its metrics or server summary is unavailable with reason `stats_failed` or another typed reason
- **THEN** every affected human projection labels lifecycle state `healthy` and reports the availability reason separately rather than labelling the cluster `stopped`

#### Scenario: Stopped cluster is consistently stopped

- **WHEN** affected human projections receive the same frozen owned cluster state `stopped`
- **THEN** each projection labels it `stopped` and does not infer `healthy` from stale or unrelated metrics

#### Scenario: Unknown ownership is not inferred

- **WHEN** cluster ownership or process attribution is not proven in the typed source
- **THEN** the human view displays unknown/unavailable explicitly and does not claim ownership or attach the contribution to a checkout

### Requirement: Presentation changes preserve transport contracts

The bordered-table change SHALL NOT alter JSON or TOON schemas or values, CLI envelope v1, stdout/stderr routing, exit codes, operation count, prompts, or native streaming behavior. Foreground `run`, interactive `shell`, `psql`, `logs --follow`, raw `eval`/`exec` streams, and scalar `env path` SHALL retain their transport-oriented output and SHALL NOT be wrapped in tables solely for visual consistency.

#### Scenario: Machine documents are unchanged

- **WHEN** an audited bounded command emits JSON or TOON before and after the presentation change for the same frozen typed result
- **THEN** the decoded envelope values, stdout/stderr routing, and exit code are identical

#### Scenario: Native streams remain native

- **WHEN** foreground `run`, interactive `shell`, `psql`, `logs --follow`, or raw `eval`/`exec` output executes normally
- **THEN** its stream is not buffered into or decorated by a Rich table

#### Scenario: Scalar path remains scalar

- **WHEN** `odcli env path` succeeds in Rich mode
- **THEN** it prints only the validated path and no table border or label
