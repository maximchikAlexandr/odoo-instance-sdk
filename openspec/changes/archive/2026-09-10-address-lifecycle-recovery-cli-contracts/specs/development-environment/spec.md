## ADDED Requirements

### Requirement: Owned Python runtime readiness preflight

Checkout with `create_venv=true` SHALL execute exactly one bounded, immutable and inspectable preflight using the selected owned Python, the recorded Odoo entry point, the resolved worktree cwd and child environment before recording the environment as `ready`. The preflight SHALL invoke `<python> <odoo-bin> --help` through the existing process boundary with a 30-second timeout, SHALL require exit code zero, and SHALL redact its command and diagnostics through the existing projection. Shared Python environments SHALL retain their existing checkout behavior.

#### Scenario: Valid owned environment becomes ready

- **WHEN** the owned Python executes the configured Odoo entry point preflight successfully
- **THEN** checkout completes its remaining postconditions and SHALL be eligible to record `ready`

#### Scenario: Missing Odoo runtime dependency

- **WHEN** the owned Python cannot import the configured Odoo entry point and the preflight exits non-zero
- **THEN** checkout never reports `ready`, returns an actionable sanitized failure, and runs the existing rollback path

#### Scenario: Preflight timeout

- **WHEN** the owned-runtime preflight does not finish within 30 seconds
- **THEN** checkout terminates that exact preflight through the existing process boundary and leaves failed cleanup retryable

### Requirement: Direct and retryable COPY environment removal

Removal of an SDK-owned COPY environment SHALL drop its exact target database through the existing guarded direct PostgreSQL drop operation after revalidating authoritative cluster, database, environment and filestore ownership. It SHALL NOT require, start, or contact an Odoo HTTP listener, and SHALL NOT require the recorded HTTP port to be free. The same `env remove` operation SHALL accept `cleanup_failed` as a retryable source state and continue only the retained owned cleanup steps. Ownership mismatch, an active owned Odoo runtime, unsafe database sessions, unreadable destructive evidence, or a changed target SHALL fail closed without terminating a process or session implicitly.

#### Scenario: Stopped COPY environment is removed

- **WHEN** a stopped COPY environment has matching authoritative ownership and no unsafe active database use
- **THEN** `env remove` directly drops the exact target database, verifies its absence, cleans the remaining owned artifacts, and records `removed`

#### Scenario: Cleanup retry resumes

- **WHEN** an earlier removal left the environment in `cleanup_failed` with retained owned artifacts
- **THEN** a repeated `env remove` revalidates current evidence, skips already absent artifacts, and safely continues cleanup

#### Scenario: Unrelated port occupant

- **WHEN** an unrelated process occupies the environment's recorded HTTP port but no owned runtime or unsafe database use is proven
- **THEN** removal neither terminates nor contacts that process and the unrelated port alone does not block direct database cleanup

#### Scenario: Unsafe active use fails closed

- **WHEN** the exact environment owns a live Odoo runtime or current database sessions cannot be safely attributed and cleared by an existing explicit contract
- **THEN** removal performs no database drop and reports the blocking evidence

### Requirement: Jira ticket branch allocation for CLI checkout

The CLI checkout adapter SHALL validate its positional Jira ticket against `^[A-Z][A-Z0-9]+-[1-9][0-9]*$` before constructing Git or catalogue probes. Under the existing repository checkout lock it SHALL resolve branch evidence for that repository from exactly: all local branch refs, all environment catalogue rows including removed/history rows, and current `origin` branch heads obtained by recorded `git ls-remote --heads` steps without fetch or ref mutation. Only the exact ticket and `<TICKET>_<N>` with a positive decimal `N` SHALL match; the unsuffixed branch is iteration zero. The resolved branch SHALL be the unsuffixed ticket when no match exists, otherwise `<TICKET>_<max(N)+1>` across the union; gaps and removed names SHALL never be reused.

The captured resolution and per-source evidence SHALL feed the existing exact-branch `EnvironmentResource.checkout_command` and all derived worktree, catalogue, environment-name, database/provenance, output and cleanup values. Execution SHALL revalidate that the captured new name remains absent from all three sources before mutation and SHALL raise a stale/conflict error instead of choosing another suffix. The public SDK exact-branch input and `EnvironmentCheckoutOptions.name` SHALL remain compatible; only the CLI adapter imposes Jira allocation.

#### Scenario: First ticket checkout

- **WHEN** no local ref, catalogue history row, or current `origin` head matches `PROJ-123`
- **THEN** CLI checkout captures branch `PROJ-123` created from the selected base

#### Scenario: Sparse history reserves iterations

- **WHEN** matching evidence contains `PROJ-123`, `PROJ-123_1`, and a removed catalogue row for `PROJ-123_3`
- **THEN** CLI checkout captures `PROJ-123_4` and does not fill the missing iteration

#### Scenario: Invalid ticket fails before probes

- **WHEN** the positional value does not match the Jira ticket grammar
- **THEN** checkout fails as usage before Git, catalogue, worktree, filesystem, database or process planning

#### Scenario: Late branch collision

- **WHEN** a local ref, catalogue row, or current `origin` head acquires the captured branch after planning
- **THEN** execution fails stale before mutation and a later invocation performs a fresh allocation

#### Scenario: Candidate source is unavailable

- **WHEN** local-ref, catalogue-history, or `origin`-head inspection cannot complete successfully
- **THEN** checkout fails before mutation and SHALL NOT treat the unavailable source as an empty candidate set

### Requirement: Applied environment settings evidence

The catalogue SHALL store one versioned, secret-free `applied_settings_json` snapshot for each environment through the next sequential additive schema migration. Successful checkout SHALL atomically record normalized field-specific evidence for: effective Python selector/path/ownership, dependency input identities and redacted semantic fingerprints, SDK-managed Odoo config values, normalized add-on paths, and Git ticket/resolved-branch/base provenance. Successful `env sync` SHALL update only the Python/dependency evidence it actually applies and preserve the other components. A failed, interrupted or dry-run checkout/sync SHALL NOT advance any applied evidence; legacy rows without sufficient evidence SHALL remain valid and diagnose the affected component as `unknown`.

Whole-file hashes SHALL NOT define drift. Odoo settings SHALL be parsed and normalized by field, lists/paths SHALL use the existing canonicalization, and dependency fingerprints SHALL use the existing secret projection before digesting normalized semantic requirement entries so no secret enters stored evidence or a fingerprint. Allocated port and selected database SHALL compare to their recorded environment bindings rather than later project defaults. Ordinary worktree changes, ahead/behind counts and uncommitted files SHALL be reported only as Git context, not configuration drift.

#### Scenario: Checkout records applied components

- **WHEN** checkout reaches all readiness postconditions
- **THEN** its environment row atomically records complete applied evidence corresponding to the artifacts and bindings made ready

#### Scenario: Sync updates only applied dependencies

- **WHEN** `env sync` successfully applies a changed dependency input
- **THEN** Python/dependency evidence advances while Odoo config, add-on and Git applied evidence remains unchanged

#### Scenario: Failed sync preserves evidence

- **WHEN** compile or install fails during `env sync`
- **THEN** the prior applied snapshot remains authoritative and diagnostics continue to report the unapplied input as drift

#### Scenario: Legacy evidence remains unknown

- **WHEN** a pre-migration environment has no trustworthy evidence for a component
- **THEN** the component reports `unknown` with a concrete reason and no inferred applied value is backfilled

#### Scenario: Current input cannot be inspected

- **WHEN** a current project setting or managed artifact cannot be parsed or read safely
- **THEN** diagnosis reports the affected component as `unknown`, preserves applied evidence, and exposes no raw secret or unsafe path
