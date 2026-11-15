## Purpose

Bind one OpenSpec change to an OdCLI environment and report that change's planning progress and implementation-task progress from the bound worktree.

## ADDED Requirements

### Requirement: Independent odcli-openspec distribution

The repository SHALL publish `odcli-openspec` as its own distribution, import package, and `odcli-openspec` console script. The distribution SHALL depend on a compatible public range of `odoo-instance-sdk` and SHALL remain installable from its built wheel without a workspace checkout. Core-only installation SHALL contain neither this package nor an OpenSpec or Node dependency. The package SHALL import no `odoo_instance_sdk.internal` module.

#### Scenario: Core wheel stays free of the extension

- **WHEN** the core distribution is built and installed without `odcli-openspec`
- **THEN** its wheel and tool environment contain no `odcli_openspec` code and no OpenSpec dependency

#### Scenario: Extension wheel installs beside core

- **WHEN** the built `odcli-openspec` wheel is installed with the built core wheel using `uv tool install --with-executables-from odcli-openspec odoo-instance-sdk`
- **THEN** both `odcli` and `odcli-openspec` are available, and the extension metadata still names a public core version range

### Requirement: Change binding is local and reversible

A binding SHALL record only the OpenSpec root provenance, change id, bind time, executable identity, and last observation identity for one canonical environment id. It SHALL NOT copy proposal, spec, design, or task content into the OdCLI catalog. `odcli-openspec env bind CHANGE` and `odcli-openspec env unbind` SHALL resolve the environment from the current directory. Unbind SHALL delete only extension-owned binding state.

#### Scenario: Bind from the current worktree

- **WHEN** `odcli-openspec env bind CHANGE` runs inside a registered environment worktree whose OpenSpec change validates
- **THEN** the binding is stored for that environment id and OpenSpec artifacts remain unchanged

#### Scenario: Unbind removes only the binding

- **WHEN** `odcli-openspec env unbind` runs for a bound environment
- **THEN** the extension binding is gone and the catalog environment row and OpenSpec files remain

#### Scenario: Dry-run does not invoke OpenSpec

- **WHEN** bind, unbind, or checkout binding is requested with `--dry-run`
- **THEN** the sanitized plan is shown and no checkout, OpenSpec process, or binding file is created

### Requirement: Target worktree is the progress authority

Progress SHALL be read by invoking the OpenSpec JSON CLI in the bound environment worktree. Planning counts SHALL come from `openspec status --json`. Implementation counts SHALL come from `openspec list --json`. A completed planning state SHALL NOT be reported as completed implementation. `implementation_percent` SHALL be an integer half-up percentage of `completedTasks / totalTasks` only when `totalTasks > 0`; otherwise it SHALL be null and the task state SHALL be `no_tasks`.

#### Scenario: Planning can be complete while tasks remain

- **WHEN** status reports every planning artifact done and list reports `completedTasks` below `totalTasks`
- **THEN** the summary keeps both counts and the implementation percentage is below 100

#### Scenario: No implementation tasks

- **WHEN** list reports `totalTasks` of 0 for the bound change
- **THEN** `implementation_percent` is null and the task state is `no_tasks`

#### Scenario: Another worktree is not reused

- **WHEN** two environments are bound to different changes
- **THEN** each summary is probed from its own worktree path

### Requirement: Failures stay typed and isolated

A missing `openspec` executable, missing change, invalid change, timeout, non-zero exit, malformed JSON, or unsupported JSON shape SHALL affect only that OpenSpec summary or binding. Unsupported shape or version SHALL be `openspec_incompatible` and SHALL NOT fall back to scraping terminal text. One broken binding SHALL NOT hide other checkout rows.

#### Scenario: Malformed JSON is not scraped

- **WHEN** OpenSpec returns a non-zero status or stdout that is not the documented JSON object
- **THEN** the affected summary carries a typed reason and no percentage is invented

#### Scenario: One broken change leaves other rows

- **WHEN** one bound worktree times out or returns an incompatible payload
- **THEN** other checkout rows remain present with their own summaries

### Requirement: Typed SDK command snapshots

The extension SDK SHALL expose list, progress, and bind operations. Every operation that launches OpenSpec or writes a binding SHALL have a `*_command()` sibling, and the convenience method SHALL run that captured command without rebuilding it.

#### Scenario: Bind executes the captured command

- **WHEN** a caller runs `bind_command(change=CHANGE).run()`
- **THEN** preview and execution use that same command snapshot
