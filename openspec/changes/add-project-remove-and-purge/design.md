## Context

Successful initialization writes one canonical row to `projects`. That row is both the user-visible registration consumed by monitor planning and the ownership target referenced by backups and environments. Deleting it to implement “forget” would either violate foreign keys or discard exactly the provenance needed to retain resources safely. Conversely, leaving it unchanged keeps a finished project in monitor snapshots forever.

The repository already has the required narrow lifecycle operations: immutable `Command` plans and `ActionStep` values, project/runtime and environment stop, publication removal, guarded environment cleanup, provenance-checked database and filestore deletion, backup point deletion, Compose cluster identity, local resource inventory, bounded output, and per-resource locks. The design must compose those contracts without bypassing their ownership checks or adding a second runner/catalogue.

## Goals / Non-Goals

**Goals:**

- Make project registration reversible without losing retained-resource ownership.
- Provide one typed SDK command and one root CLI leaf for safe forget and full local purge.
- Capture a deterministic delete/retain plan, require exact project confirmation, and preserve that plan through dry-run and execution.
- Fail closed on ownership ambiguity, active-use drift, partial failure, unsafe paths, and concurrent lifecycle work.
- Make retries and later initialization converge on the same canonical `project_id`.

**Non-Goals:**

- No deletion of remote Odoo instances, remote Git repositories, Docker images, external/shared PostgreSQL clusters or databases, borrowed backups, shared Python environments, or caller/other-tool-managed checkouts.
- No automatic age policy, background garbage collector, force flag that bypasses ownership, or `--yes` shortcut for purge confirmation.
- No parallel resource catalogue, purge journal table, generic cleanup framework, renderer registry, or new process runner.
- No migration that guesses ownership for legacy source trees or resources.

## Decisions

### D1. Preserve one project ownership row and separate registration state

Add checked `registration_state` (`registered` or `forgotten`) and nullable `forgotten_at` columns to the existing `projects` row. Existing rows migrate as `registered`. Forget changes only those fields after its safety preflight; it does not null or delete foreign keys. Initialization upserts canonical repository provenance and restores `registered` with `forgotten_at=NULL`.

Monitor and ordinary project discovery select only `registered` rows. Explicit lifecycle resolution by full `project_id` may read either state so an absent source tree and interrupted retry remain recoverable. Catalogue foreign keys, resource ownership, timestamps, and the canonical identifier remain stable.

Alternatives considered: deleting the row is unsafe for retained references; copying ownership into tombstone tables creates a second authority; inferring registration from manifest existence makes read-only filesystem drift mutate user-visible lifecycle state.

### D2. Add one minimal public project lifecycle facade

Attach a `ProjectResource` facade to `OdooClient.projects`. It exposes frozen JSON-safe plan/result types plus `remove_command(selector, purge=False, confirmation=None)` and the delegating convenience method. The selector is a typed exact path-or-full-id value; the CLI requires exactly one of command-local `--project PATH` and `--project-id ID`.

The command returns one immutable public `ExecutionPlan`. Child lifecycle plans are captured once, flattened into that plan in execution order, and consumed without rebuilding argv, paths, observations, or actions. CLI code performs only Click parsing, exact interactive confirmation collection, envelope construction, and Rich rendering.

Alternative considered: a CLI-only coordinator violates the SDK-first boundary. Adding unrelated methods to `EnvironmentResource` or `BackupResource` obscures project-level ownership.

### D3. Use one project lifecycle lock and revalidate captured identity

Reuse the existing project manifest lock key for initialization, forget, and purge. Under the lock, execution re-reads the project row and verifies canonical `project_id`, repository root, Git common directory, registration state, and the captured resource-decision fingerprint before consuming any mutating step. Child commands retain their existing environment, backup, database, cluster, publication, and Python-environment locks and execution-time checks.

The project lock is acquired before child locks, and child operations are executed serially in stable identity order. A changed resource set, ownership claim, active binding, symlink boundary, or exclusive-use fact fails before the affected mutation and reports the unconsumed suffix.

Alternative considered: a new lock service is unnecessary. Independent point-operation locks alone allow init or checkout to add resources between the project plan and final deregistration.

### D4. Build the removal plan from the existing catalogue and resource inventory

Extend the existing internal resource projection with a project-removal view. Every known resource receives one stable identity, action (`delete`, `retain`, `stop`, `unpublish`, or `forget`), reason code, ownership confidence, active-use state, dependencies, and sanitized display target. The projection covers project and environment runtimes/routes, environments and their worktrees/venvs/config/logs, project manifest/secrets/primary venv/logs/temp files, owned databases/filestores/backups, and the exact Compose container/volume claim.

Unknown files below an otherwise owned directory, symlinks escaping the owned root, shared/external resources, caller-owned or other-tool-managed checkouts, borrowed backups, Docker images, remote repositories, and remote instances are retained and listed. A source root is deletable only when its canonical registered root and Git metadata prove it is the selected primary checkout and it is not a linked/caller/other-tool-managed checkout; legacy ambiguity is retained.

Alternatives considered: hard-coded directory sweeping loses catalogue relationships; a new inventory store duplicates ownership; treating exact confirmation as ownership would turn user intent into false provenance.

### D5. Forget is a narrow catalogue transition with active-resource refusal

Non-purge remove performs no file, database, process, publication, container, or backup mutation. It refuses while the project has a live/stale-unresolved project runtime, active publication, any non-removed environment, cleanup-in-progress record, or another managed resource that ordinary monitor/catalogue discovery would hide while active. Stopped retained databases, clusters, backups, source/config/secrets, and other inert resources remain owned and are reported.

After preflight and revalidation, the final action marks the row `forgotten`; monitor cache invalidation follows the existing catalogue-change boundary. A second forget returns the same successful forgotten state without changing timestamps or ownership.

### D6. Purge requires the exact token before all mutation

The confirmation token is `<registered-root-basename>:<full-project_id>`. It is derived from the captured catalogue row, uses the complete identifier, and is shown with the delete/retain plan. Rich TTY mode reads it interactively unless `--confirm-project` is supplied. Non-interactive Rich and all JSON/TOON execution require `--confirm-project`; missing, shortened, or mismatched values fail before lock acquisition or mutation. `--yes` never supplies or replaces the token. Dry-run never prompts or validates a supplied token as execution authority.

### D7. Purge is ordered for recoverability and composes guarded point operations

Purge consumes these deterministic phases:

1. stop exact project/environment runtimes and remove matching publication routes;
2. remove environments in stable UUID order through their captured `remove_command` plans, retaining caller-owned code and shared inputs;
3. drop only provenance-authorized project databases and contained filestores through existing guarded database operations;
4. delete only project-owned, unreferenced backups through existing backup commands;
5. stop and destroy the exact SDK-owned Compose container/volume only after no retained or active database/environment dependency remains;
6. remove contained project-owned global artifacts and primary venv, then the project-local configuration/secrets and an ownership-proven primary source root;
7. mark the project forgotten only after all required delete actions completed and every retained resource has an explicit terminal reason.

Each phase keeps the evidence needed by later cleanup until dependents complete. Docker images, external clusters, shared resources, remote systems, and unproven paths never enter a delete step.

Alternative considered: one recursive filesystem delete cannot prove database/container ownership, preserve retry evidence, or report partial completion.

### D8. Report partial failure from the execution ledger; do not add a journal

The command result contains project identity, mode, final registration state, and ordered completed, retained, and remaining resource decisions. A failure raises one sanitized typed error whose JSON-safe details contain those same three lists and the failed step. The catalogue stays `registered` until the terminal forget action, so a new invocation can select by full id and reconstruct the remaining plan from durable current state. Existing idempotent point operations absorb already-removed resources.

Alternatives considered: a dedicated purge journal would duplicate existing resource state and command consumption. Rebuilding and comparing the current projection is sufficient because every destructive child has a durable idempotence boundary.

### D9. Ponytail full subsystem gate

- `ProjectResource` public facade: necessary to satisfy the typed SDK-first contract; no existing facade represents project registration. Keep it as one thin facade.
- Project-removal coordinator/projection: necessary to order cross-resource ownership decisions and expose one immutable plan. Keep one module composed from existing commands and inventory.
- Separate ownership catalogue, purge journal/state-machine service, generic cleanup/renderer/runner abstractions, dependency, and policy engine: existing catalogue rows, command ledger, point lifecycle operations, output pipeline, and locks already cover them. Omit all of these.

## Risks / Trade-offs

- **Catalogue migration or filtering accidentally hides active legacy projects** → migrate every existing row to `registered`, assert fresh/prior-head metadata equivalence, and test all global/project-filtered monitor reads.
- **Plan becomes stale between preview and execution** → capture a resource fingerprint, hold the project lifecycle lock, and retain child execution-time ownership/identity checks.
- **A partial purge removes evidence needed for retry** → order dependent resources before manifests/source/catalogue transition and keep registration active until the final action.
- **Path or Git ownership is ambiguous** → retain the target with a stable reason; never follow symlinks or broaden containment after confirmation.
- **Compose destruction affects another project** → require the active catalogue claim plus exact project/container/volume labels and refuse while any dependency or identity differs.
- **Large resource sets produce unbounded output** → use the existing bounded projection policy with totals and deterministic truncation while retaining full action counts and failure identity.

## Migration Plan

1. Add the checked registration columns and indexes through the next single-head catalogue migration; backfill existing rows as `registered` and preserve every relation and timestamp.
2. Deploy read paths that treat only `registered` rows as user-visible while explicit lifecycle lookup can resolve forgotten rows.
3. Deploy init reactivation and project removal in the same release so forgotten projects have a supported return path.
4. Downgrade only when no row is `forgotten`; otherwise refuse rather than silently re-registering removed projects.

## Open Questions

None. Ambiguous ownership is retained and reported rather than delegated to implementation-time judgment.
