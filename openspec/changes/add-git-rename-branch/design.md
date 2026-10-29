## Context

Ticket iteration parsing, catalogue filtering, local/origin inspection, and stale-name revalidation currently live in `commands/env/checkout.py`, even though they are domain rules needed by a second public Git operation. `GitResource` already owns inspectable Git commands, `OdooInstance` retains its `OdooClient`, and the root CLI context already resolves registered projects/environments. The implementation must preserve repository rules for one immutable command snapshot, `internal/proc` process execution, typed bounded output, SDK-first CLI delegation, and the test-owned `PUBLIC_LEAF_CASES` authority.

## Goals / Non-Goals

**Goals:**

- Reuse exactly one SDK-owned ticket allocation algorithm for checkout and rename.
- Plan and execute a non-force local current-branch rename with complete volatile-evidence revalidation and bounded compensation.
- Preserve HEAD, index, worktree, dirty/untracked state, selector behavior, output envelopes, and existing checkout semantics.
- Return a frozen JSON-safe result through public SDK and bounded CLI surfaces.

**Non-Goals:**

- Jira or Multica API validation, ticket discovery, or new ticket formats.
- Push, remote branch rename/deletion, branch switching, worktree/environment/database creation, adoption, or commit creation.
- Generic explicit-repository mode or a second Git resource/runner/registry.

## Decisions

### 1. Move allocation into one narrow SDK-owned module

Introduce a focused internal ticket-branch allocation module that owns ticket validation/parsing, catalogue projection, local/origin evidence capture, maximum-iteration selection, bounded provenance, and stale absence checks. Both environment checkout and `GitResource.rename_branch_command()` consume its typed immutable snapshot; the CLI keeps only argument conversion and rendering.

Alternative considered: call private functions in `commands/env/checkout.py` from `GitResource`. Rejected because it reverses the SDK-first dependency direction and keeps reusable domain behavior in a Click adapter. Duplicating the parser/allocation for rename is rejected by the issue contract.

### 2. Treat an already-matching current branch as an idempotent no-op

Rename planning captures the current attached branch before allocation. If it is exactly `TICKET` or valid `TICKET_N`, the result selects that name and contains no mutation steps. Otherwise current-branch evidence remains part of collision input and the shared maximum rule chooses the next name.

Alternative considered: always allocate after counting the current branch. Rejected because repeated invocation would unnecessarily advance the suffix and violate explicit idempotency.

### 3. Capture one repository snapshot and explicit allocation evidence

Planning uses canonical repository root/common-dir, attached branch, HEAD SHA, index tree, porcelain dirty state, local heads, relevant catalogue history, relevant current `origin` heads, and upstream configuration. A failed probe is an error, never an empty set. The command snapshot contains named read-only revalidation steps followed by only the necessary local mutation/postcondition steps; every Git child goes through `internal/proc` with argv and `shell=False`.

Alternative considered: rerun allocation inside the callback and construct the rename dynamically. Rejected because the selected name and effects would not be inspectable in dry-run and would violate immutable-command rules.

### 4. Use non-force Git rename with explicit upstream cleanup and compensation

Execution first consumes all named revalidation evidence and refuses any mismatch or selected-name collision. It then runs non-force current-branch rename, removes inherited upstream only when it points to a differently named remote branch, and verifies final branch, HEAD, index/dirty state, and upstream. If cleanup or postconditions fail, a bounded compensation path renames back only while the captured old name remains free and restores the captured branch configuration; otherwise it reports incomplete recovery without overwrite.

Alternative considered: force rename or switch/create the target ref. Rejected because either can overwrite/switch existing state. Leaving inherited upstream untouched is rejected because later sync/push could target the old remote name.

### 5. Keep CLI changes transport-only

`odcli git rename-branch TICKET` uses the existing ticket Click type, root selectors, `run_or_preview`, `_rich`, and output options, with exactly one new inventory row and README entry. The public result model carries `previous_branch`, `selected_branch`, and `renamed`; implementation-specific evidence remains in the redacted plan/provenance.

Alternative considered: a new `branch` group or CLI-side catalogue/Git orchestration. Rejected because the requested command belongs to the existing group and all domain behavior must remain public-SDK owned.

### Ponytail full gate

No new subsystem, framework, dependency, registry, runner, or configurable naming policy is needed. The only extracted component is necessary because two existing public workflows must share one collision-sensitive algorithm; the existing alternative is CLI-private code that cannot be reused without violating SDK-first layering, so the narrow shared SDK module is retained. Extra ticket providers, custom suffix formats, explicit-repository mode, remote rename, and generalized branch management are removed from scope.

## Risks / Trade-offs

- [Remote evidence can change between planning and execution] → repeat the same bounded origin-head probe as a named revalidation step and fail stale without mutation.
- [Catalogue rows can change without a Git process step] → re-read matching history inside the command callback before the first mutation under the existing provisioning/catalogue locking boundary.
- [Upstream cleanup can fail after rename] → capture branch configuration, verify postconditions, and perform bounded non-overwriting compensation with explicit incomplete-recovery reporting.
- [Extracting checkout allocation can regress mature behavior] → retain its typed evidence/provenance and move existing unit cases to the shared owner before adding rename cases.
- [Dirty-state snapshots can include untracked paths and mutable index state] → capture both index tree and porcelain output, revalidate before mutation, and assert equality after rename/compensation.

## Migration Plan

1. Add result/evidence types and the shared allocator while porting existing checkout allocation tests unchanged.
2. Switch environment checkout to the shared allocator without changing its public output or command shape.
3. Add the SDK rename command/convenience operation, CLI leaf, inventory/docs, and focused tests.
4. Run strict OpenSpec and repository gates; deployment is an ordinary backward-compatible package update with no data migration.

Rollback removes the new CLI/SDK surface and restores checkout imports to the prior owner; no persisted schema or remote state requires rollback.

## Open Questions

None. The issue and existing architecture determine ticket syntax, evidence sources, selector behavior, local-only scope, and stale-plan handling.
