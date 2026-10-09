## ADDED Requirements

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
