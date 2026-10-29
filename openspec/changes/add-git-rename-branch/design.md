## Context

Ticket iteration parsing, catalogue filtering, local/origin inspection, and stale-name revalidation currently live in `commands/env/checkout.py`, even though they are domain rules needed by a second public Git operation. `GitResource` already owns inspectable Git commands, `OdooInstance` retains its `OdooClient`, and the root CLI context already resolves registered projects/environments. The implementation must preserve repository rules for one immutable command snapshot, `internal/proc` process execution, typed bounded output, SDK-first CLI delegation, and the test-owned `PUBLIC_LEAF_CASES` authority.

## Goals / Non-Goals

**Goals:**

- Reuse exactly one SDK-owned ticket allocation algorithm for checkout and rename.
- Plan and execute one non-force local current-branch rename after revalidating only the volatile inputs that can invalidate that rename.
- Preserve HEAD, index, worktree, dirty/untracked state, selector behavior, output envelopes, and existing checkout semantics.
- Return a frozen JSON-safe result through public SDK and bounded CLI surfaces.

**Non-Goals:**

- Jira or Multica API validation, ticket discovery, or new ticket formats.
- Push, remote branch rename/deletion, branch switching, worktree/environment/database creation, adoption, or commit creation.
- Generic explicit-repository mode or a second Git resource/runner/registry.

## Decisions

### 1. Move allocation into one narrow SDK-owned module

Move the existing `_TicketAllocation` data and ticket validation/parsing, catalogue projection, local/origin evidence capture, maximum-iteration selection, bounded provenance, and stale-absence helpers from `commands/env/checkout.py` into one narrow SDK-owned module. Both environment checkout and `GitResource.rename_branch_command()` call those helpers; their behavior and data shape stay unchanged, and the CLI keeps only argument conversion and rendering.

Alternative considered: call private functions in `commands/env/checkout.py` from `GitResource`. Rejected because it reverses the SDK-first dependency direction and keeps reusable domain behavior in a Click adapter. Duplicating the parser/allocation for rename is rejected by the issue contract.

### 2. Treat an already-matching current branch as an idempotent no-op

Rename planning captures the current attached branch before allocation. If it is exactly `TICKET` or valid `TICKET_N`, the result selects that name and contains no mutation steps. Otherwise current-branch evidence remains part of collision input and the shared maximum rule chooses the next name.

Alternative considered: always allocate after counting the current branch. Rejected because repeated invocation would unnecessarily advance the suffix and violate explicit idempotency.

### 3. Revalidate only inputs that can invalidate the rename

Planning captures the attached current branch and HEAD together with the shared allocator's local/catalogue/current-`origin` evidence. A failed probe is an error, never an empty set. Immediately before mutation, named read-only steps revalidate current branch, HEAD, and selected-name absence in the same three allocation sources; the only mutating step is a non-force `git branch -m`. Every Git child goes through `internal/proc` with argv and `shell=False`. Index, staged, unstaged, and untracked state are not planning inputs because branch rename does not depend on them; focused tests prove they remain unchanged.

Alternative considered: revalidate index, porcelain state, upstream configuration, and every allocation value. Rejected because those values cannot change the validity of this single local rename; adding them would create failure modes unrelated to the requested operation. Rerunning allocation and dynamically selecting another name is also rejected because dry-run must remain inspectable and a stale target must fail rather than silently change.

### 4. Keep standard Git branch-rename semantics

Execution consumes the named revalidation evidence, refuses a changed branch/HEAD or selected-name collision, and then runs exactly one non-force current-branch rename. Git moves the branch's local configuration and retains its upstream relation according to normal `git branch -m` behavior. There is no post-rename cleanup phase, custom compensation path, fetch, push, or remote-ref mutation.

Alternative considered: clear a now-differently-named upstream and add compensation if that cleanup fails. Rejected by Ponytail full: the request is local branch rename, standard Git intentionally preserves branch configuration, and an extra mutation/recovery subsystem would be larger and more surprising than Git itself. Force rename or switch/create remains rejected because it can overwrite or switch existing state.

### 5. Keep CLI changes transport-only

`odcli git rename-branch TICKET` uses the existing ticket Click type, root selectors, `run_or_preview`, `_rich`, and output options, with exactly one new inventory row and README entry. The public result model carries `previous_branch`, `selected_branch`, and `renamed`; implementation-specific evidence remains in the redacted plan/provenance.

Alternative considered: a new `branch` group or CLI-side catalogue/Git orchestration. Rejected because the requested command belongs to the existing group and all domain behavior must remain public-SDK owned.

### Ponytail full gate

No new subsystem, framework, dependency, registry, runner, configurable naming policy, cleanup workflow, or recovery engine is needed.

- **Shared allocation module — retain.** Necessary because checkout and rename must use the same existing collision-sensitive code. Importing the Click adapter from `GitResource` would reverse SDK-first layering; duplicating it would create two policies. The module is a move of current data/helpers, not a redesign.
- **Public SDK command/result — retain.** Necessary because every process-spawning CLI leaf in this repository delegates to an inspectable public SDK command. The existing alternative is raw CLI Git orchestration, which violates the main command-execution contract. The result contains only `previous_branch`, `selected_branch`, and `renamed`.
- **CLI/inventory/docs — retain.** Necessary public surface and existing contract fixtures for one requested leaf; no new group or registry is added.
- **Upstream cleanup, postcondition phase, and compensation — remove.** Standard non-force `git branch -m` is the complete mutation and preserves local branch configuration. Extra ticket providers, custom suffix formats, explicit-repository mode, remote rename, and generalized branch management also remain out of scope.

## Risks / Trade-offs

- [Remote evidence can change between planning and execution] → repeat the same bounded origin-head probe as a named revalidation step and fail stale without mutation.
- [Catalogue rows can change without a Git process step] → re-read matching history inside the command callback before the first mutation under the existing provisioning/catalogue locking boundary.
- [Extracting checkout allocation can regress mature behavior] → retain its typed evidence/provenance and move existing unit cases to the shared owner before adding rename cases.
- [Branch rename must not disturb developer work] → test HEAD, index tree, porcelain output, and untracked bytes before/after the standard Git mutation; do not turn unrelated dirty state into a stale-plan precondition.

## Migration Plan

1. Add result/evidence types and the shared allocator while porting existing checkout allocation tests unchanged.
2. Switch environment checkout imports to the moved helpers without changing its public output or command shape.
3. Add the minimal SDK rename command/convenience operation, CLI leaf, inventory/docs, and focused tests.
4. Run strict OpenSpec and repository gates; deployment is an ordinary backward-compatible package update with no data migration.

Rollback removes the new CLI/SDK surface and restores checkout imports to the prior owner; no persisted schema or remote state requires rollback.

## Open Questions

None. The issue and existing architecture determine ticket syntax, evidence sources, selector behavior, local-only scope, and stale-plan handling.
