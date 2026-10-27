## Context

`InstanceFactory.from_project()` currently resolves `preferred_http_port`, then the selected Odoo config port, then `8069`. Both foreground CLI preflight and detached execution treat that resolved value as fixed. The existing `internal.port_allocation.find_free_port()` already combines manifest/config reservations with a live loopback probe, but the main-project run path does not call it.

The project manifest is the durable non-secret project configuration, generated `.odcli/odoo.conf` is the owned Compose runtime config, and the persisted runtime record is the authority consumed by status and publication. The design must preserve immutable command snapshots, dry-run inertness, explicit-pin intent, and the existing lock/atomic-write patterns.

## Goals / Non-Goals

**Goals:**

- Make foreground and detached main-project starts recover from an unavailable automatic/default HTTP port.
- Persist automatic selection separately from the operator's explicit `preferred_http_port` intent.
- Ensure command construction, runtime identity, status, and publication observe one effective endpoint.
- Keep explicit pins exact and return an actionable conflict without mutating project configuration.
- Reuse existing allocation, manifest, generated-config, locking, and output mechanisms.

**Non-Goals:**

- Change task-environment or auxiliary-restore allocation.
- Add a port registry, reservation daemon, socket handoff, new CLI option, dependency, or database migration.
- Edit an external/source Odoo config or guarantee a port remains free between probe and process bind.
- Automatically reassign an explicitly preferred port.

## Decisions

### Store automatic assignment separately from explicit preference

Add optional `assigned_http_port` to `ProjectConfig` and its manifest round trip. Resolution precedence is `preferred_http_port`, then `assigned_http_port`, then configured Odoo port, then `8069`. `preferred_http_port` remains operator intent; `assigned_http_port` is replaceable OdCLI state.

Using `preferred_http_port` for both meanings was rejected because a later run could not distinguish a pin that must fail closed from an automatic value that may be replaced. A separate port registry was rejected because the manifest already is the project authority and another persistence surface would require reconciliation.

### Resolve under the existing project lifecycle/manifest lock before command capture

For a real main-project run, a narrow coordinator reloads the manifest under the existing project lock. When `preferred_http_port` is set, it calls the existing allocator with `requested=` so occupied or unknown ports fail exactly and no files change. Otherwise it asks the allocator for an automatic candidate, preferring a still-available `assigned_http_port`, or the configured/default candidate when no assignment exists.

If the automatic selection changes, the coordinator atomically writes the manifest and refreshes the owned generated Odoo config when present. It then rebuilds the project-bound `OdooInstance`, so the immutable foreground/detached command, base URL, runtime identity, and readiness endpoint all contain the selected port. Preview and read-only paths calculate no persistent reassignment and perform no write.

Allocating inside detached execution was rejected because the prepared argv and public plan would then disagree with the spawned process. Allocating during `from_project()` was rejected because factory construction is also used by read-only/status/publication paths and must not gain hidden writes.

### Keep the spawn-time conflict check and expose bounded selection provenance

The existing detached assertion and foreground preflight remain the last check before spawn. They close most of the probe-to-spawn window without claiming a socket reservation. An automatic assignment that loses the race fails normally and can be replaced on retry; an explicit pin remains unchanged.

Main-project detached results add bounded port-selection provenance: selected port, selection kind (`explicit`, `automatic_reused`, or `automatic_fallback`), and a stable reason when fallback occurred. Rich and machine output use the same typed result. Foreground mode emits the same safe fallback fact before handing control to native Odoo output. Status and publication are not given a second resolver: they continue reading the captured runtime endpoint.

### Ponytail full gate

No new subsystem is necessary. Port search uses `internal.port_allocation`; serialization uses `ProjectConfig`/`write_manifest`; coordination uses the current project lock; generated configuration uses the existing writer; publication uses the persisted runtime. The considered new alternatives—a registry/reservation service and a second runtime wrapper—are removed from the design because they duplicate existing authorities and do not eliminate the unavoidable bind race.

## Risks / Trade-offs

- **[Probe-to-bind race]** Another process can claim the selected port after probing. → Retain the immediate spawn-time check, fail without signaling unrelated processes, and replace only automatic assignments on retry.
- **[Partial two-file update]** Manifest persistence can succeed before generated-config refresh fails. → Hold the project lock, use existing atomic file writers, report failure before spawn, and make retry reconcile the generated config from the manifest assignment.
- **[Concurrent run attempts]** Two starts can observe the same project simultaneously. → Serialize selection/persistence with the existing project lock and retain runtime ownership/conflict checks before spawn.
- **[Legacy manifests]** Existing projects have no `assigned_http_port`. → Treat absence as automatic/default behavior; the field appears only after a real fallback/assignment write and remains optional.
- **[Output compatibility]** New result fields affect snapshots and strict consumers. → Add fields additively to the typed detached result, update all output modes/characterization fixtures, and leave existing fields unchanged.

## Migration Plan

1. Add the optional manifest field and compatibility tests; no backfill is required.
2. Add the locked main-project resolver and connect it only to real foreground/detached run preparation.
3. Add output provenance and focused runtime/publication regression coverage.
4. Rollback is code-only: older versions ignore the unknown assignment only if their strict parser permits it, so rollback SHALL remove `assigned_http_port` from affected manifests before running an older incompatible release. The implementation documentation/release note SHALL state this boundary.

## Open Questions

None. The automatic range and candidate ordering remain those of the existing `find_free_port()` contract.
