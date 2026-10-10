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

`module update --changed [--base REF]` SHALL be mutually exclusive with positional modules, SHALL reuse the `test --changed` merge-base and committed/staged/unstaged/untracked selector, SHALL fail closed on unmapped paths or stale HEAD, and SHALL attach base provenance, changed paths, selected installed modules, and `not_installed` to the shared immutable execution plan. Empty addon changes SHALL be a successful no-op; mutation SHALL use the existing exclusive update path and require `--yes`.

On a non-zero return code, `module update` SHALL prioritize a valid nonce-framed payload and use the `user_error` or `finalization_error` from the common shell wrapper. When the payload is missing or malformed, the command SHALL fall back to the last `_TIMEOUT_TAIL_BYTES = 8192` redacted bytes of `stderr`, not the first N characters. Full unlimited tracebacks SHALL NOT be emitted and existing redaction SHALL NOT be weakened. Rich, JSON, and TOON SHALL return the same stable error code and safe details. Secrets, terminal escapes, and sensitive paths SHALL remain sanitized. The common shell-error contract SHALL be reused; no separate parser for `module update` SHALL be added.

#### Scenario: Preview changed update

- **WHEN** a caller runs `module update --changed --dry-run` with a stable HEAD and resolvable base
- **THEN** no action or process executes and Rich, JSON, and TOON expose the same selection and exact shared execution plan

#### Scenario: long startup log does not hide the traceback

- **WHEN** `odcli module update <MODULE> --yes` fails with a startup log longer than the limit and a traceback at the end
- **THEN** the final error contains the exception type and the root cause

#### Scenario: framed payload has priority

- **WHEN** a valid nonce-framed `user_error` payload is present
- **THEN** it is used with priority over the general `stderr` tail

#### Scenario: fallback uses bounded tail

- **WHEN** the framed payload is missing or malformed
- **THEN** a bounded redacted tail of `stderr` is used and truncation is reported explicitly

#### Scenario: same error code across formats

- **WHEN** `module update` fails and is rendered in Rich, JSON, and TOON
- **THEN** all three return the same stable error code and safe details

### Requirement: Concurrent module operation classification
The update path SHALL map only Odoo's known concurrent-module-operation `UserError` to `module_operation_in_progress`, preserve a non-zero exit, and recommend retrying later without waiting, retrying automatically, or adding locks. Other Odoo and process errors SHALL retain their existing classification. [Source: GH#64 §8]

#### Scenario: Another module operation is active
- **WHEN** Odoo returns the recognized concurrent module-operation condition
- **THEN** Rich explains the temporary conflict and JSON/TOON return `module_operation_in_progress`

### Requirement: Repository-aware module context inventory
The public module resource SHALL expose one inspectable read-only context operation that preserves configured addon-root precedence and returns, for every resolved and shadowed module, module name, manifest and code paths, manifest version, direct dependencies with resolved paths, repository identity, installed state/version when available, and related committed/staged/unstaged/untracked Git changes. Addon roots SHALL be accepted only when they are inside the selected main/adopted checkout or an explicitly registered client/product repository; canonical roots and repositories SHALL be de-duplicated without reordering.

#### Scenario: File and installed module facts are combined
- **WHEN** safe filesystem roots, repository Git state, and a ready selected database are available
- **THEN** one frozen result distinguishes filesystem version from installed database version and relates each changed path to its resolved module and repository

#### Scenario: Same module exists in two repositories
- **WHEN** two allowed roots contain the same technical module name
- **THEN** the first root remains authoritative, shadowed candidates retain repository/path provenance, and facts from the repositories are not merged into one identity

### Requirement: Partial module context is explicit
Module context collection SHALL isolate unavailable filesystem roots, repositories, Git probes, database connections, and installed-module queries. An unavailable source SHALL produce a typed availability state and sanitized reason, not an empty-success value. The operation SHALL NOT install, update, execute, or import addon code, mutate Git, write manifests, or create a second module index.

#### Scenario: Database is unavailable
- **WHEN** filesystem discovery succeeds but the selected Odoo database cannot be queried
- **THEN** file modules and dependency paths remain available while installed state is marked unavailable rather than uninstalled

#### Scenario: Addon root escapes an allowed repository
- **WHEN** an addon root is symlinked, traverses outside every registered repository, or resolves to an unregistered repository
- **THEN** that root is rejected with a bounded warning and no files below it are inspected
