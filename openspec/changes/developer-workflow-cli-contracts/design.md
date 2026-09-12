## Context

This plan is pinned to `origin/main` SHA `0ff164636617c03a51277055af45cef009277368` and must be implemented as one lineage on `feat/odcli-developer-workflow`. Its external snapshots are GH#34 (`2026-09-09T15:08:24Z`), GH#43 (`2026-09-05T16:11:43Z`), GH#54 (`2026-09-03T13:13:24Z`), GH#62 (`2026-09-10T15:57:28Z`), GH#64 (`2026-09-10T15:47:27Z`), and GH#65 (`2026-09-10T09:25:34Z`). A later source edit or different base SHA requires a planning revision before implementation.

The baseline already centralizes immutable commands in `execution.py`, process launch in `internal/proc`, output envelopes and Rich plan projection in `commands/output.py`, typed owner resolution in `commands/context.py`, environment monitoring in `resources/monitor.py`, local paths in `internal/paths.py`, and persistent state in `storage/backup_catalog.py`. The current CLI still has `--json`, Jira-named allocation, broad/occasionally empty Rich renderers, and no public module or Git resource. Path providers still split state across platformdirs. These are extension points, not invitations to create parallel abstractions.

## Goals / Non-Goals

**Goals:**

- Establish shared lifecycle, execution-plan, output, Ticket Allocation, and storage contracts before domain workflows.
- Deliver every observable behavior from all six sources with requirement/task traceability.
- Keep previews and execution on the same immutable snapshots, with fail-closed revalidation at mutation boundaries.
- Preserve typed SDK models and one renderer, resolver, process runner, catalogue, module mapping, and Git command boundary.

**Non-Goals:**

- No production implementation in this change, GitHub issue edits/closure, PR creation, or publication.
- No JSONPath/JMESPath, renderer hierarchy, second output DTO graph, persistent module index, graph database, tracker/forge client, branch graph, automatic stash/retry, binary downloader, installer, plugin framework, or AI integration.
- No automatic module installation, manifest execution/mutation, external ticket validation, Rich field filtering, or filtering of mutations/errors/plans/streams.

## Decisions

### 1. Compile all work against one immutable source ledger

Every delta requirement and task carries `[Source: GH#…]`; `delivery-plan.md` adds complete task-to-WP coverage. The exact base and issue timestamps above define the review input. Separate per-issue changes were rejected because #34/#54/#65 depend on the same plan/output/process contracts and #64 directly changes their behavior.

### 2. Land foundations before domain siblings

The first implementation stage owns shared path migration, output/field projection, plan visibility, optional executable resolution, neutral ticket vocabulary, typed diagnostic actions, and command extraction hooks. Module, environment, and translation packages may then run in parallel with disjoint paths. Restore follows both module and environment packages so its `resources/instance.py` extension is serialized; Git follows restore so it can reuse the module mapping and safely extend the same public-export, instance, and doctor seams. Shared helpers belong in predecessor work only.

### 3. Keep field projection at the envelope boundary

`output_options()` loses `--json` and gains an optional typed-schema-backed field list only for the enumerated bounded reads. One pure projector recursively walks the concrete `msgspec.Struct` result shape already bound to each leaf and successful `result`/`data`, including nested structures and repeated rows, while preserving required envelope and structural metadata. The concrete result schema is the sole field authority: command-name field tables, root/child allowlists, and other manual projection registries are forbidden. Data collectors, public SDK methods, serializers, and Rich renderers stay unchanged by projection. A manual field registry and query-language dependency were rejected because each would become a second response schema.

### 4. Treat Rich as terminal presentation over the typed result

Command-local renderers remain adjacent to commands, while the shared boundary guarantees nested process-plan inclusion and tests actual leaves at widths 80/120/180. Environment paths use `Path.relative_to(Path.home())` and a presentation-only `~` prefix; `env path` and machine documents keep absolute paths. `backup validate` receives a shape-specific renderer. A generic renderer class tree and reconstructed argv were rejected.

### 5. Migrate storage through one journaled coordinator

`internal/paths.py` becomes the only canonical provider for `~/.odcli/{config,catalog.sqlite3,environments,projects,backups,locks,pgadmin}`. Before normal catalogue-backed work, one global lock drives a durable staged migration manifest: inventory legacy roots, detect destination conflicts, copy/rename within safe filesystem boundaries, rewrite path-bearing catalogue values transactionally, verify IDs/relations/files/modes, then remove only verified sources and mark completion. Each stage is idempotent. Repository-local `.odcli/` is explicitly excluded. Opportunistic per-provider migration was rejected because it cannot prove cross-root atomicity.

### 6. Extend the catalogue once for project-owned backups

The next sequential v15-to-v16 schema migration adds nullable indexed `project_id` with a foreign-key-compatible ownership relation without changing existing backup UUIDs or event, restore, and environment links. `start_download` captures project identity before transfer. Backfill occurs only where existing provenance maps to exactly one registered project; provenance mapping to zero or multiple projects remains null, excluded from every project-scoped query, and visible globally until an explicit relink path validates identity. Listing uses the direct field. Environment joins are rejected as ongoing ownership authority because download precedes restore.

### 7. Reuse one module mapping and one changed selector

`ModuleResource` builds one ordered mapping per operation from resolved addon roots, with safe literal manifest parsing and existing containment checks. Dependency traversal is stable DFS/topological ordering with explicit cycle paths. `update_changed` consumes the existing test selector snapshot and then the existing exclusive update planner; `ModuleUpdatePlan` holds only domain selection. A repository/cache layer and second executable plan type were rejected.

### 8. Make optional host tools ordinary planned capabilities

One existing executable resolver captures absolute `msgfmt` or `git-absorb` paths before mutation. `msgfmt` is an optional translation step with captured stdin and `LC_ALL=C`; `git-absorb` is an always-visible command with a missing-tool precondition and doctor finding. Neither modifies dependency metadata or installs software. Separate wrappers were rejected.

### 9. Restore stopped projects with a bounded owned helper runtime

The chosen strategy is a temporary Database Manager Odoo process built from the resolved project `StartConfig`, forced to the verified free project endpoint, recorded as an owned process step, waited ready with existing probes, used by the existing restore pipeline, and stopped in guaranteed cleanup. Foreign listeners fail preflight. If config cannot safely construct the helper, planning fails before target reservation with exact recovery guidance. A new direct PostgreSQL ZIP/filestore restore implementation was rejected because it would duplicate Odoo's restore semantics.

### 10. Implement Git as a narrow concrete resource

One `GitResource` exposes concrete commit/check/absorb/sync methods and frozen results. It reuses the module mapping established by the module package for scope/tag inference, shared owner/base resolution, and explicit tracker-neutral `ticket_link_enabled`/`ticket_base_url` project settings. It is serialized after restore before extending `resources/instance.py`, public exports, and doctor capability reporting. Sync captures fetched SHAs and permits only same-name origin operations; already-published rebases use the exact lease captured at fetch. `git check` gates push. Generic Git frameworks, forge APIs, auto-stash, and branch stacks were rejected.

### 11. Preserve one context and diagnostic vocabulary

Ticket Allocation renames the current CLI-private Jira allocation end-to-end, including tests and provenance, without retaining aliases because alpha permits the breaking machine-key correction. Doctor runtime facts reuse the existing owner resolver/runtime view; remediations are typed advice only. Known concurrent module-operation text is matched narrowly at the current update error adapter and no other `UserError` is reclassified.

## Risks / Trade-offs

- **[Large cross-cutting change can hide regressions]** → stage foundations first, use the canonical leaf/architecture inventories, and require focused plus full gates before handoff.
- **[Storage migration spans files and SQLite]** → journal stages, hold one lock, verify before deletion, retain sources on conflict/interruption, and test retries at every boundary.
- **[Git remote state races after fetch]** → capture exact remote SHAs, revalidate local state, use a pinned lease, and fail without automatic retry.
- **[Optional executable changes between plan and run]** → execute the captured absolute path and fail stale if its identity/precondition changes.
- **[Terminal width tests can become cosmetic]** → assert required semantic values and machine parity in real CLI leaf invocations, not renderer snapshots alone.
- **[Auxiliary restore runtime can outlive failure]** → assign explicit ownership identity and guaranteed bounded cleanup; never adopt or terminate a listener by port alone.

## Migration Plan

1. Add contract tests for shared output/plan/ticket/optional-tool behavior, then implement those foundation seams.
2. Add the journaled global-path and catalogue ownership migrations with fresh, legacy, conflict, and interrupted-retry fixtures.
3. Implement independent sibling slices for module workflow, translation validation, and environment show/runtime doctor/presentation/generated-config repair.
4. Add the bounded stopped-project restore lifecycle after both module/public exports and environment/storage/runtime-diagnostic contracts are stable.
5. Add Git commit/check/absorb/sync after restore, reusing the module mapping and serially extending instance/public-export/doctor seams.
6. Update README/help/completion/changelog and run strict OpenSpec, formatting, lint, strict typing, focused/full tests, architecture, docs, packaging, and generated-client/frontend checks.

Rollback is code-only for the CLI/domain features. The storage schema remains forward-compatible and migrated files remain canonical under `~/.odcli`; rollback tooling must not move or delete verified user data automatically.

## Open Questions

None. The selected implementation seams, migration strategy, auxiliary restore mechanism, and dependency topology are fixed for independent review.
