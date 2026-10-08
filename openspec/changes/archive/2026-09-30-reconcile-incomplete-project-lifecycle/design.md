## Context

Three lifecycle gaps share the same failure pattern: durable state is published before the operation's later postconditions are complete, while retry/recovery looks only at the published state. The existing code already has the required boundaries: immutable init and database-preparation commands, owned Compose identity, SQL bootstrap verification, one project preparation lock, atomic manifest and generated-config writers, captured `exists-after` restore probing, restore provenance, and fail-closed database drop planning.

Today `_handle_existing_manifest()` returns before `init_project_command()` whenever the manifest and generated config match. `materialize.py` sets `database_confirmed` only after restore returns, so an exception after PostgreSQL creates the target neither consumes the captured existence probe nor publishes a binding. The successful default-switch phase writes only `project.toml`, although the project-owned `.odcli/odoo.conf` is the effective config.

The implementation must preserve exact PostgreSQL image approval, immutable process execution, secret-free output, source-config immutability, and database deletion ownership checks.

## Goals / Non-Goals

**Goals:**

- Make identical Compose init complete or verify its Compose/bootstrap phases before success.
- Publish explicit, exact partial-restore provenance only after the captured post-failure probe proves the target exists in the active owned cluster.
- Keep manifest and project-owned generated database selection coherent before a successful default-switch result.
- Reuse the existing command, lock, catalog, config, and output boundaries.

**Non-Goals:**

- No generic lifecycle engine, resumable workflow framework, second process runner, or new external dependency.
- No automatic deletion after restore failure and no relaxation of drop authorization for unknown databases.
- No changes to user-managed Odoo config, image trust, secret handling, restore collision rules, or public command names.
- No attempt to guarantee cross-file atomicity across an OS crash; normal exceptions are compensated and a later init/prepare operation repairs detectable drift.

## Decisions

### D1. Route identical Compose init through the existing captured follow-ups

Change the existing-manifest decision from a boolean early return to a bounded outcome: complete no-op, generated-config repair, resume Compose, or proceed with overwrite. For identical Compose configuration, construct the same `init_project_command()` but omit the already-satisfied manifest mutation and retain its captured ensure-running and bootstrap steps. The command revalidates manifest equality under the existing project lock before effects, executes the existing image/cluster gates, and accepts `tmp` only through the existing SQL proof.

A fully complete project may consume only read/verification work and SHALL not recreate `tmp`; missing work follows the same captured steps as first init. The real no-op envelope uses the requested `dry_run` value, rather than hard-coding true.

Alternative: persist a new phase-state file. Rejected because Compose health and SQL bootstrap state are already authoritative and a second journal would itself need reconciliation. Alternative: delete or rewrite the manifest on failure. Rejected because the manifest is valid durable intent and later retry can safely finish it.

### D2. Record partial restore state only from post-failure owned-cluster proof

The preparation command already captures `database.restore.exists-after`. On a restore exception, consume that exact prepared probe before leaving the preparation lock. Only an affirmative result for the preselected target, combined with the active cluster claim and the already captured catalogue/local-archive source identity, creates an incomplete restore binding.

Extend the existing restore record with a small state field (`complete` or `incomplete`) through one catalogue migration; historical rows migrate as `complete`. Normal successful restore records remain complete. Incomplete bindings are visible in inventory/failure context and authorize only the same exact guarded drop/retry reconciliation as complete bindings: active cluster identity, endpoint, database name, source provenance, volume identity, no active environment/runtime binding, and safe contained filestore evidence all remain required. A failed or unavailable probe publishes no ownership evidence and no destructive path.

Alternative: insert an ordinary successful restore binding. Rejected because it would make audit output claim completion. Alternative: allow `db rm` by target name plus active cluster. Rejected because that weakens the unrelated-database protection that this change must preserve.

### D3. Compensate the two-file default switch under the existing preparation lock

Add one narrow helper for project-owned default switching. It preflights the generated-config target, captures the prior manifest and generated-config bytes/mode, derives the new generated config from the reloaded non-conflicting manifest, then writes both using the existing atomic writers before returning success. If the second write fails during an ordinary exception, it restores the first file from the captured owner-validated snapshot and reports failure; no `default_switched=true` result is emitted. The helper never writes an external/user-managed `source_config`.

The project lock continues to serialize the operation. Startup/init repair logic remains the recovery boundary for a process/host crash between file replacements.

Alternative: change only launch argv. Rejected because the selected generated config would remain observably stale and its `dbfilter` could still hide the restored database. Alternative: introduce a shared generated file or symlink. Rejected as a format and ownership expansion unrelated to the defect.

### D4. Extend existing public-boundary regression matrices

Tests exercise the real CLI/SDK composition with injected failures at existing seams. Init coverage performs fail-after-manifest then retry, complete retry without recreation, precise failure, and truthful machine metadata. Preparation coverage proves post-failure detection and incomplete binding, unknown/unrelated refusal, supported exact drop/retry, successful config synchronization, user-config preservation, compensation, and plan-step parity. Catalogue migration tests prove historical rows, fresh schema, and metadata equivalence.

No second CLI inventory, restore fake, or output renderer is introduced.

## Risks / Trade-offs

- **[Post-failure PostgreSQL is unavailable]** → Keep the restore failure primary, report that target ownership is unconfirmed, and create no binding; a later explicit retry may reconcile only after exact evidence is available.
- **[A partial restore binding could be mistaken for a usable database]** → Persist and expose the explicit `incomplete` state; never switch the default to it and keep normal coalescing/readiness paths limited to complete restores.
- **[Cross-file crash window during default switch]** → Use atomic per-file replacement, compensate ordinary exceptions from validated snapshots, and reuse generated-config drift repair on the next operation.
- **[Retry races with another preparation]** → Keep probe, binding, switch, and compensation inside the existing canonical preparation lock and reject captured manifest drift.
- **[Migration broadens strict schema fixtures]** → Add one linear revision and update both declarative metadata and upgrade/fresh-schema equivalence tests.

## Migration Plan

1. Add the linear catalogue migration and declarative schema state for restore completion state; existing rows become `complete`.
2. Deploy readers that distinguish incomplete bindings before writers can create them.
3. Enable failure-path recording, exact reconciliation, default-switch compensation, and Compose init resume.
4. Rollback code only after incomplete bindings are reconciled; migration downgrade removes the state field after mapping remaining rows to the legacy complete-only representation.

## Open Questions

None. Unknown target existence remains fail-closed and requires no implicit recovery authority.
