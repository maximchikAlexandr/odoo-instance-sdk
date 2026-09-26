## ADDED Requirements

### Requirement: Adopt an existing caller-owned checkout

The public environment SDK SHALL support an inspectable `adopt_command(project, checkout_path, options=...)` and delegating `adopt()` operation returning the existing frozen environment result type. Adoption SHALL prepare Odoo against an existing canonical Git working directory without creating, copying, moving, resetting or renaming its code or branch. It SHALL support both linked worktrees and independent clones. Existing SDK-owned checkout behavior SHALL remain unchanged.

Adoption SHALL require COPY mode, an explicit compatible base and exactly one explicit supported COPY source. It SHALL reuse existing source selection, credential handling, archive verification, neutralization, isolated database/filestore, Python selection, port allocation and recovery semantics. It SHALL NOT enable creation of a virtual environment implicitly, change a project default database or mark a merely provisioned environment as HTTP-ready.

#### Scenario: Adopt a native external checkout

- **WHEN** a valid existing checkout and compatible explicit COPY inputs are supplied
- **THEN** exactly that checkout supplies the Odoo code, a separate target database and writable filestore are prepared, and one environment UUID is returned without another Git checkout

#### Scenario: Independent clone belongs to the configured project

- **WHEN** the external checkout has a different Git common directory but its verified repository identity matches the explicitly selected core project
- **THEN** the environment retains that core project identity and separately records the external Git identity; source secrets are resolved from the configured project, not copied into the clone

#### Scenario: Initial checkout is not the requested input

- **WHEN** initial adoption finds the wrong repository, HEAD differs from the resolved explicit base, or uncommitted user changes exist
- **THEN** it rejects before Odoo/catalog/artifact mutation and preserves the checkout unchanged rather than resetting it

#### Scenario: Unsupported or ambiguous input

- **WHEN** adoption is asked to use SHARED mode, infer a source, or select two COPY sources
- **THEN** it fails before provisioning with an actionable typed validation result

### Requirement: Code ownership is separate from environment artifact ownership

Environment evidence SHALL separately represent core project identity, actual checkout identity, code ownership (`sdk_owned`, `caller_owned`, or `unknown`) and an explicit SDK artifact root. Caller-owned code SHALL remain outside the set of removable SDK artifacts, even when its path lies under another tool's managed root. All generated config, lock, logfile and optional owned Python files SHALL use the SDK artifact root rather than the checkout's parent. Writable database and filestore ownership SHALL remain subject to existing COPY safety rules.

Migration SHALL preserve existing SDK-owned cleanup only where recorded canonical layout proves that ownership. Unknown legacy evidence SHALL NOT grant new delete rights. Normal SDK-owned checkout SHALL keep its existing placement and branch allocation; the external checkout operation is an explicit exception for code placement only.

#### Scenario: Failed adoption rolls back owned artifacts only

- **WHEN** dependency preparation or database restore fails after adoption begins
- **THEN** existing core recovery reports retained resource identities and cleans only proven SDK-owned artifacts, leaving external code, Git metadata, branches and caller-owned Python untouched

#### Scenario: Remove adopted environment with dirty or absent code

- **WHEN** removal by UUID has safe database/runtime ownership evidence but the external checkout is dirty or was removed by its owner
- **THEN** cleanup can remove only the independently proven SDK-owned artifacts without invoking Git removal/reset/prune or recursively deleting the external checkout or its parent

#### Scenario: Active Odoo process or unknown ownership

- **WHEN** an adopted environment still owns a live Odoo runtime or destructive artifact ownership cannot be proved
- **THEN** normal guarded removal refuses the unsafe operation and does not terminate unrelated activity

### Requirement: Adoption identity and retry remain deterministic

Adoption SHALL capture a non-mutating immutable plan, revalidate volatile inputs before effects and reserve by explicit project plus canonical checkout identity under existing locking/catalog boundaries. Two simultaneous preparations SHALL NOT create two active environments for the same checkout. A repeat against a ready matching environment SHALL return the same UUID without another backup download, restore or dependency mutation. Different source/base/options SHALL conflict; incomplete or cleanup-failed records SHALL report recovery instead of being overwritten.

#### Scenario: Response was lost after successful adoption

- **WHEN** the caller repeats adoption with identical captured project/checkout/source/base inputs
- **THEN** it recovers the same ready environment and performs no second restore

#### Scenario: Checkout changes after preview

- **WHEN** path identity, repository identity, HEAD, manifest, port or source evidence changes between command construction and execution
- **THEN** execution rejects the stale plan before the first new mutation rather than rebuilding against changed inputs

#### Scenario: Existing environment has normal development edits

- **WHEN** an already adopted environment contains subsequent code edits or commits on its recorded branch and the caller inspects it
- **THEN** those changes are reported as Git context without re-adopting, resetting, or treating normal edits as replaced checkout identity

#### Scenario: Retry after ordinary development edits

- **WHEN** identical adoption inputs identify a ready existing environment on the same recorded checkout/branch after normal edits or commits
- **THEN** the existing UUID is returned without mutation before applying clean-tree/base-HEAD preconditions that apply only to first adoption

### Requirement: Adopted environments remain usable through core lifecycle surfaces

Existing environment get/list, cwd resolution, configuration rebasing, sync, runtime selection, diagnostics, inventory and removal SHALL support external checkout paths and explicit core project identity. They SHALL NOT infer the project solely from the external Git common directory or infer artifact ownership from `worktree_path.parent`. Runtime startup SHALL require an available matching code checkout. Missing/replaced code SHALL be diagnosable without losing the environment UUID or independently proven cleanup evidence.

#### Scenario: Core runtime uses adopted code and isolated data

- **WHEN** the caller explicitly starts an adopted environment by UUID
- **THEN** Odoo runs with the recorded borrowed checkout, SDK-owned generated configuration and that environment's COPY database/filestore

#### Scenario: Main-project inventory finds adopted environment

- **WHEN** the caller lists environments for the configured core project
- **THEN** the adopted environment remains in the project even if Multica used an independent Git clone

#### Scenario: External checkout was replaced

- **WHEN** a different repository appears at the recorded path
- **THEN** diagnostics report stale identity and startup/sync refuse to operate on that code; removal of separately proven owned data remains available
