# odoo-module-workflow Specification

## Purpose
TBD - created by archiving change developer-workflow-cli-contracts. Update Purpose after archive.
## Requirements
### Requirement: Manifest-backed module catalogue
The SDK SHALL expose `OdooInstance.modules` as one concrete `ModuleResource` which builds an on-demand module-name mapping from resolved `StartConfig.addons_path` in Odoo precedence order, parses `__manifest__.py` only with `ast.literal_eval`, and reuses existing addon-root, symlink, traversal, nearest-manifest, and runtime-owner resolution. It SHALL NOT execute manifests or add a repository/provider/cache abstraction. [Source: GH#34]

#### Scenario: Resolve module information safely
- **WHEN** `module info [MODULE]`, `module where MODULE`, or `module deps MODULE` runs from a registered worktree or initialized project
- **THEN** the command returns frozen typed identity, path, manifest metadata, and direct-dependency data from the shared catalogue
- **AND** omitted `MODULE` selects the nearest addon or fails actionably outside an addon
- **AND** shadowed candidates and missing direct manifests are reported deterministically

### Requirement: Deterministic dependency plan
The SDK SHALL compute a stable transitive topological install order with dependencies before dependants and SHALL fail for missing dependencies or cycles without mutating manifests or installing anything. [Source: GH#34]

#### Scenario: Plan transitive installation
- **WHEN** `module install-order MODULE...` receives an acyclic graph with all manifests present
- **THEN** it returns every transitive dependency before each dependant in deterministic order

#### Scenario: Reject incomplete graph
- **WHEN** the requested graph contains a missing manifest or cycle
- **THEN** the command fails with bounded typed diagnostics identifying the missing edge or cycle

### Requirement: Changed-module update selection
`module update --changed [--base REF]` SHALL be mutually exclusive with positional modules, SHALL reuse the `test --changed` merge-base and committed/staged/unstaged/untracked selector, SHALL fail closed on unmapped paths or stale HEAD, and SHALL attach base provenance, changed paths, selected installed modules, and `not_installed` to the shared immutable execution plan. Empty addon changes SHALL be a successful no-op; mutation SHALL use the existing exclusive update path and require `--yes`. [Source: GH#34]

#### Scenario: Preview changed update
- **WHEN** a caller runs `module update --changed --dry-run` with a stable HEAD and resolvable base
- **THEN** no action or process executes and Rich, JSON, and TOON expose the same selection and exact shared execution plan

### Requirement: Concurrent module operation classification
The update path SHALL map only Odoo's known concurrent-module-operation `UserError` to `module_operation_in_progress`, preserve a non-zero exit, and recommend retrying later without waiting, retrying automatically, or adding locks. Other Odoo and process errors SHALL retain their existing classification. [Source: GH#64 §8]

#### Scenario: Another module operation is active
- **WHEN** Odoo returns the recognized concurrent module-operation condition
- **THEN** Rich explains the temporary conflict and JSON/TOON return `module_operation_in_progress`
