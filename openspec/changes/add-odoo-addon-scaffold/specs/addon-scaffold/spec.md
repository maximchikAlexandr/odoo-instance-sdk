# Spec Delta

## Purpose

Create a loadable Odoo 19 addon from built-in, user, project, and command-line scaffold settings by handing one generated template to the native Odoo scaffold renderer.

## ADDED Requirements

### Requirement: Scaffold configuration precedence

The scaffold operation SHALL resolve settings in this order, from lowest to highest: built-in defaults, the user config file, the project `.odcli/project.toml` `[scaffold]` table, then command-line overrides. A missing user config file SHALL mean built-in defaults and SHALL NOT be an error. Manifest values SHALL merge as shallow key overrides. `include`, `exclude`, and `omit_manifest` SHALL apply at each layer in that order. A higher layer SHALL be able to reverse an earlier optional-component choice. The same component SHALL NOT be both included and excluded in one layer. `omit_manifest` SHALL remove only optional manifest fields. User and project scaffold config SHALL reject secret-bearing keys.

#### Scenario: Project override keeps the global author

- **WHEN** the user config sets `author` and `include = ["wizard"]` and the project config sets only `exclude = ["wizard"]`
- **THEN** the effective author remains the user value and `wizard` is not selected

#### Scenario: Missing user config is valid

- **WHEN** the user config path does not exist and the project config supplies a non-empty author
- **THEN** scaffolding proceeds with built-in defaults plus the project layer

#### Scenario: One layer conflicts on a component

- **WHEN** one layer both includes and excludes `tests`
- **THEN** the operation fails before creating a temporary template or destination files

### Requirement: Generated addon shape

The operation SHALL always emit `__manifest__.py` and `__init__.py`. The bundles `models`, `security`, and `views` SHALL always be included and SHALL NOT be disableable. The only optional bundles SHALL be `wizard`, `controllers`, `data`, `demo`, `report`, `tests`, and `static/description`. Unknown or path-like component names SHALL fail. The effective manifest SHALL always contain non-empty `name`, `author`, and `license`. `name` SHALL be derived from the normalized technical module name unless overridden. `license` SHALL default to `LGPL-3`. A missing effective author SHALL fail before filesystem mutation. `data` and `demo` manifest entries SHALL match the generated component files and SHALL NOT be independently overridden or omitted. Manifest Python SHALL be deterministic, with stable key order.

#### Scenario: Baseline cannot be removed

- **WHEN** the caller excludes `models`
- **THEN** the operation fails before filesystem mutation

#### Scenario: Optional wizard is reversible

- **WHEN** the effective selection includes `wizard` and not `demo`
- **THEN** the addon contains the wizard package and its imports and manifest entries, and contains no demo files or demo manifest entries

#### Scenario: Missing author fails closed

- **WHEN** no layer supplies a non-empty author
- **THEN** the operation fails before creating a temporary template or destination files

### Requirement: Native renderer and safe destination

The operation SHALL normalize and report the technical module name, reject path traversal, and refuse an existing addon target. It SHALL materialize one temporary custom template containing only the selected fragments and the resolved manifest, then invoke the configured Odoo 19 executable with `scaffold --template <temporary-template>`. The temporary tree SHALL be removed on success and failure. A native failure SHALL leave no partial addon. `--dry-run` and command construction SHALL validate inputs and SHALL NOT create the temporary template or the destination. The preview plan SHALL contain filesystem action steps and the exact native process step consumed by execution.

#### Scenario: Dry-run writes nothing

- **WHEN** scaffold is invoked with `--dry-run` for a valid new module
- **THEN** the plan names the destination, selected components, and native argv
- **AND** neither the temporary template nor the destination exists afterward

#### Scenario: Existing target is refused

- **WHEN** the destination already contains the normalized module directory
- **THEN** the operation fails and does not modify that directory

#### Scenario: Native failure cleans the partial addon

- **WHEN** the native scaffold process exits non-zero after creating destination files
- **THEN** those destination files are removed and the temporary template is removed

### Requirement: Shared CLI and SDK result

`odcli scaffold` and `OdooClient.scaffolds.create()` SHALL use the same operation. `create()` SHALL delegate to `create_command().run()`. Rich, JSON, and TOON output SHALL carry the same typed result: normalized module name, destination, selected components, created files, effective manifest fields, and the native template source. Click callbacks SHALL parse arguments and render results only.

#### Scenario: Machine output matches the SDK result

- **WHEN** the same scaffold inputs are run through JSON CLI output and through `create()`
- **THEN** both results contain the same module name, destination, components, created files, and manifest fields
