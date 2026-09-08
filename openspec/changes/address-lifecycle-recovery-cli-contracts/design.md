## Context

GitHub #61 now contains nine independently observable gaps. This revision is based on `origin/main` at `f0eb29a9a96ad4a62c4113d9bf2acf7b4c14c4cf`; the merge contains MYL-121's final feature result `344c7be6bda0873ae8423061d837babcbec15e9a`. Main now supplies safe Compose initialization, project-scoped catalogues, human Rich output and progress, exact dry-run process displays, CLI worktree paths, module-result validation, recorded Git baselines, reusable address probes, catalogue integrity, and the finalized output/helper boundaries. Those results are baseline dependencies and have been removed from this implementation scope.

The current seams are `commands/env.py` and `cli.py` for Click/context/output, `resources/environment.py` for captured checkout/sync/remove commands, `internal/git_worktree.py` for process-bound Git probes, `storage/backup_catalog.py` plus `internal/doctor.py` for durable evidence/diagnosis, `internal/database_preparation.py` and guarded PostgreSQL operations for restore/drop, `resources/instance.py` plus `internal/proc` for runtime identity/processes, and `internal/process_metrics.py` for memory. The design extends those seams and preserves the single inventories/coordinators merged by MYL-121.

## Goals / Non-Goals

**Goals:**

- Make owned checkout readiness, COPY cleanup and failed-cleanup retry truthful.
- Add compatible concise resource spellings without duplicating callbacks or changing established machine identities.
- Report one correctly named platform-selected process-tree memory value.
- Preserve invocation mode on every shared failure path.
- Replace a stopped COPY environment from a retained backup with compensating recovery and unchanged identity.
- Stop only a live process whose environment ownership is proven at execution time.
- Allocate one never-reused Jira iteration branch deterministically for each CLI checkout.
- Diagnose field-specific environment drift from successfully applied settings without mutation.

**Non-Goals:**

- No Jira API/client, project-key configuration, branch registry/counter, retry/reservation service, watcher, background reconciler, generic config-diff engine, or automatic drift repair.
- No second dependency resolver, lifecycle/restore coordinator, process supervisor, renderer, runner, command inventory or metrics pass.
- No implicit session termination, unrelated-port process termination, shared/source database overwrite, project-context replacement, or command-local environment selector.
- No metric selector, duplicate memory column, dependency, privileged setup, `.localhost` browser-session work, `env show` command, or production implementation in this planning change.

## Decisions

### 1. Treat merged MYL-121 behavior as the only baseline

The feature commit is replayed directly onto merge commit `f0eb29a9a96ad4a62c4113d9bf2acf7b4c14c4cf`; none of the obsolete pre-merge MYL-121 commits are replayed. Every #61 path reuses the post-merge typed context, Rich/progress, catalogue scoping, endpoint resolution, worktree-path, address-probe, architecture inventory and output helpers. Reintroducing their older variants is rejected because it would duplicate already verified production behavior.

### 2. Validate an owned runtime with one entry-point process step

After owned-venv creation/dependency installation and generated-config creation, but before the `ready` transition, checkout adds one `PreparedStep`: recorded Python, recorded `odoo-bin`, `--help`, resolved worktree cwd, captured child environment, 30-second timeout, read-only mode. Running the actual entry point imports Odoo's startup dependency graph without starting a server or touching a database. Non-zero or timeout follows existing checkout rollback and retained-cleanup reporting. Shared runtimes do not gain another probe; adding a resolver is rejected.

### 3. Route COPY removal through the existing guarded PostgreSQL drop

`EnvironmentResource.remove` stops reserving or contacting the environment HTTP port for COPY deletion. Its preflight resolves the project cluster and consumes the authoritative cluster/database/filestore evidence already used by guarded `db drop`, additionally binding the exact target to the selected environment. It rejects a proven live environment runtime and unsafe sessions; it never infers ownership from a port. `cleanup_failed` is a retry source and each cleanup action verifies present/absent state before acting. A special HTTP recovery command is rejected.

### 4. Register aliases on the same Click command objects

After each existing command object is defined, its group registers that object under the canonical and retained spelling. Output builders keep the existing stable semantic command ID. Help annotates the alternate spelling on one operation and de-duplicates listings. `PUBLIC_LEAF_CASES` remains the only bounded-leaf inventory; alias tests reference it instead of adding a table.

### 5. Select memory at the process-metrics adapter

`internal/process_metrics` owns the selection. On Darwin a private standard-library `ctypes` adapter calls `libproc.proc_pid_rusage` with `RUSAGE_INFO_V4` for the validated root and readable children and extracts `ri_phys_footprint`; elsewhere the existing psutil RSS path remains. Root unavailability/identity mismatch makes memory unavailable and unreadable children follow the existing omit-child rule. `ProcessTreeResult` and monitor schema v4 use `memory_bytes`; Rich and generated API/dashboard types read that same field. A subprocess metric or fallback metric is rejected.

### 6. Make invocation mode mandatory at the shared failure boundary

The post-MYL-121 `fail()`/failure-document path receives a required resolved `dry_run` boolean. Each dry-run-capable callback captures mode once and passes it to success and every failure branch. A repository-wide caller audit removes reliance on the false default without changing exception classification, occupied-port behavior, execution, or exit codes.

### 7. Replace by moving the proven prior pair aside and restoring to the exact target

The CLI uses the merged owner-neutral context, then requires an environment owner and `--replace`. Existing locks are acquired in established order and the plan validates backup identity/content, active cluster ownership, exact COPY target, contained filestore, generated-config/catalogue equality, stopped runtime and inactive database use. Because restore exposes no explicit force contract, replacement never terminates active database sessions and fails before mutation when any remain. `--replace` rejects the existing `--target` option before mutation so the target stays exactly recorded; the existing `--reset-admin-password` behavior remains compatible. Collision-free rollback names derive from environment UUID; the prior database is renamed through the PostgreSQL transport and the prior contained filestore is atomically renamed without following symlinks.

With the target absent, the retained-backup preparation/restore pipeline restores to the unchanged target name and, when requested, performs the existing admin-password reset. Database existence, matching filestore, successful requested password reset and restore provenance are postconditions. Only then does one catalogue transaction bind the environment to the new backup; guarded cleanup removes rollback artifacts. Any failure before that commit, including password-reset failure, uses the same compensation: remove only a proven partial new pair, restore the prior pair in reverse order and verify it before retaining prior provenance. Unprovable compensation records `cleanup_failed` with sanitized retained identities; neither outcome advertises the new backup. A second restore coordinator or in-place shared overwrite is rejected.

### 8. Adopt a persisted runtime only after live identity revalidation

Top-level `stop` uses merged root context resolution and requires an environment. Without a runtime migration, planning and execution re-read the runtime row for environment owner plus PID/create time, and the environment row for `runtime_json` (`odoo_bin`, `runtime_cwd`) plus generated-config path. Execution compares those expected values with the live PID create time, executable, argv, cwd and config argument. POSIX additionally requires the live SDK-created process-group identity `pgid == pid` before existing bounded terminate/kill; Windows applies the same available create-time/executable/argv/cwd/config checks before existing process-tree termination. A vanished matching PID is idempotent and clears its stale runtime row; any existing mismatch or inaccessible required evidence retains the row and fails closed without a signal. Port state is never evidence.

### 9. Allocate Jira branches in one CLI-private checkout adapter

The public SDK continues accepting an exact branch and optional environment name. The CLI adapter instead validates `JIRA_TICKET`, then captures three probes: local heads via system Git, `EnvironmentResource.list(project=..., include_removed=True)` filtered by canonical repository identity, and `git ls-remote --heads origin <ticket> <ticket>_*`. It parses only the exact ticket and positive integer suffix, chooses unsuffixed when empty or maximum plus one, and passes that resolved new branch into the existing checkout command with no CLI name override.

The existing repository checkout lock serializes local/catalogue planning and execution. Execution re-runs absence probes for the captured name before any mutation; remote races still fail stale, and a rerun chooses again. It always uses the selected base because an allocated candidate is never attached to an existing ref. Fetching, gap filling, retry loops and a durable counter are rejected.

### 10. Store versioned field evidence and compare it in doctor

The next sequential catalogue migration adds non-null versioned `applied_settings_json`, defaulting legacy rows to an explicit unknown document without deriving claims. Checkout finalization writes all applied components atomically with `ready`. Successful sync updates only Python/dependency components in the same transaction as its successful lifecycle result; failures, interrupts and previews write nothing.

Evidence is field-specific and secret-free: canonical effective Python selector/path/ownership; dependency input identities plus fingerprints of normalized meaningful entries after common secret projection; normalized SDK-managed Odoo scalar/list values; canonical add-on paths; and Jira ticket/resolved branch/base provenance. Allocated database/port remain environment bindings and later default changes do not produce drift. Whole config hashes and raw secret-bearing requirements are rejected.

One private frozen drift projection in `internal/doctor.py` parses current project/config inputs through existing loaders and compares each component to applied evidence and actual artifacts. It returns `in_sync`, `drifted`, or `unknown` with bounded sanitized reason/remediation. Dirty/ahead/behind Git data remains context; only branch/base identity is drift. `doctor` renders it now and a future `env show` may reuse it. Diagnosis never updates evidence or calls repair.

### 11. Keep tests focused on shared contracts

One parameterized alias suite covers callback identity, Rich, JSON/TOON, confirmation and dry-run parity. Root-cause suites cover owned entry-point success/failure/timeout; stopped COPY deletion and `cleanup_failed` retry; Darwin/non-Darwin memory selection; shared-failure provenance; replace success/compensation; owned/stale/reused/already-stopped processes; Jira grammar/three-source allocation/stale collision; and drift normalization/snapshot write discipline. Existing architecture, machine parity, redaction, OpenAPI/dashboard, doctor and documentation checks are extended, not cloned.

## Risks / Trade-offs

- **[Remote branch heads can race after planning]** → capture exact evidence, revalidate chosen-name absence before mutation, fail stale, and require a fresh invocation instead of hiding the race with retries.
- **[Applied dependency evidence could contain secrets]** → normalize after the common secret projection and persist only secret-free identities/fingerprints; include redaction/fingerprint regression tests.
- **[Darwin native structure drift could misread memory]** → use documented versioned `RUSAGE_INFO_V4`, validate return/size boundaries, keep measurement unavailable on failure, and fake the native call in focused tests.
- **[Replacement spans PostgreSQL, filesystem and catalogue]** → serialize with existing locks, retain the old pair until all postconditions pass, compensate in reverse order, and expose `cleanup_failed` when atomicity cannot be proven.
- **[PID reuse could target another process]** → join the runtime row's owner/PID/create time with environment runtime/config expectations, require every available live identity check and POSIX process-group ownership at execution revalidation, and never use port occupancy.
- **[Aliases can drift in help/tests]** → register identical Click objects and keep one semantic command ID and leaf inventory.

## Migration Plan

1. Add focused failing characterization for all nine work items, including the new Jira and drift contracts.
2. Add the sequential applied-settings catalogue migration and frozen internal drift projection with legacy-unknown behavior.
3. Implement shared failure-mode and process-metrics fixes, then aliases, Jira checkout adapter and help/docs.
4. Add owned-runtime preflight and route COPY removal/retry through guarded direct drop.
5. Add replacement restore and stop by extending existing context, lifecycle, locking, compensation and process boundaries.
6. Wire applied snapshot writes into successful checkout/sync and read-only drift into doctor.
7. Update schema/OpenAPI/dashboard projections and README, then run strict OpenSpec, Ruff, strict mypy, focused/full pytest, architecture, docs, frontend/codegen and package gates.

Rollback removes additive aliases/commands and returns monitor emission to the prior schema. The additive applied-settings column remains inert for older code. A successfully replaced environment remains an ordinary valid COPY environment; retained `cleanup_failed` artifacts remain recoverable by retry/removal rather than being deleted during code rollback.

## Open Questions

None. All nine externally observable contracts and current-main extension points are settled for independent verification.
