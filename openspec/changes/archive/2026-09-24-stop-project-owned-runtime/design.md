## Context

Detached launch already resolves project and environment contexts through `ResolvedContext.runtime`, binds `OdooInstance` with `_RuntimeBinding(owner_kind, owner_id, project_id, ...)`, and persists both owner kinds in the shared `runtime` table. The stop callback nevertheless calls `require_environment()`, while `_read_runtime_identity()` and conditional cleanup address only environment rows. The lower process boundary already supplies the required bounded terminate/escalate and exit verification behavior.

This change closes that asymmetry without changing storage or process ownership rules. The security boundary remains persisted identity plus independently re-read canonical owner inputs and live process evidence; a port is never ownership proof.

## Goals / Non-Goals

**Goals:**

- Make the existing `odcli stop` complete the detached lifecycle for both initialized main checkouts and registered environments.
- Preserve the full fail-closed identity validation and plan-versus-execution revalidation.
- Make conditional cleanup owner-neutral and prevent deletion of a replacement row.
- Keep bounded output and help accurate for either owner kind.
- Cover both owners with the same parameterized regression matrix.

**Non-Goals:**

- No new CLI command, daemon manager, supervisor, runtime table, migration, or dependency.
- No port-based discovery, adoption of unrecorded processes, broad process cleanup, or change to launch behavior.
- No redesign of project/environment resolution or the process execution boundary.
- No removal of the existing environment-oriented SDK compatibility surface unless implementation proves an additive alias is necessary.

## Decisions

### 1. Use `_RuntimeBinding` as the exact stop owner

The stop path will derive `(owner_kind, owner_id)` from the instance's existing binding and re-read only that row. The legacy `_environment_id` fallback remains bounded to environment instances for compatibility. This reuses the identity established by `from_project()` and `from_environment()` and prevents cross-owner lookup.

Alternative considered: infer the owner from cwd or scan runtime rows. Rejected because resolution already produced the authoritative owner and scanning weakens fail-closed behavior.

### 2. Generalize identity evidence, not termination

`_RuntimeIdentity` will carry owner kind/id and nullable environment identity. Environment expectations continue to use recorded environment runtime artifacts. Project expectations use the already resolved instance command prefix, default cwd, and effective config path/start configuration that detached launch used. After those inputs are normalized, both owners use the current live-process snapshot, comparison, POSIX/Windows checks, bounded termination, and exit verification.

Alternative considered: duplicate a project-specific stop implementation. Rejected because it would create two safety algorithms that can drift.

### 3. Add owner-neutral conditional cleanup to the existing catalogue seam

The catalogue operation will delete only where `owner_kind`, `owner_id`, `root_pid`, and `create_time` still match. The environment-specific helper may delegate to it for compatibility. Clearing a project runtime never deletes its `projects` registration.

Alternative considered: call `_clear_runtime(owner_kind, owner_id)` after process exit. Rejected because an intervening launch could replace the row and then be erased.

### 4. Keep one public CLI leaf and one bounded result contract

The callback will stop requiring an environment, call the generalized command on the resolved instance, and build context/result fields from `ResolvedContext.runtime`: `owner_kind`, `project_id`, nullable environment fields, worktree root, and status. Rich copy and command help will say “runtime” and identify the selected owner. `PUBLIC_LEAF_CASES` continues to contain one `stop` leaf.

Alternative considered: add `project stop` or a second top-level command. Rejected because selection and lifecycle are already shared.

### 5. Parameterize the existing safety suite by owner kind

The test fixture will construct otherwise equivalent project- and environment-owned instances/runtime rows. The existing match, mismatch, PID reuse, inaccessible evidence, plan/execution race, vanished process, conditional cleanup, output parity, and help assertions will run against both where behavior is common; owner-specific assertions cover nullable environment fields and preserved project registration.

Alternative considered: add only one happy-path project test. Rejected because the safety claim is that both owners share the complete fail-closed matrix.

## Risks / Trade-offs

- **[Risk] Project expectations accidentally differ from detached launch normalization** → Reuse the instance's canonical command prefix/config/cwd helpers and assert the planned argv/config path in tests rather than reconstructing them in the CLI.
- **[Risk] Generalizing `_RuntimeIdentity` weakens environment checks** → Keep the same comparison fields and execute the existing environment cases through the parameterized matrix.
- **[Risk] A new runtime replaces the row during termination** → Conditional cleanup includes owner, PID, and create time; mismatch is an error and the replacement row survives.
- **[Trade-off] Existing environment-named SDK methods may remain as compatibility wrappers** → Prefer the smallest additive/internal rename needed by the CLI; do not force unrelated public API churn into this bug fix.

## Migration Plan

No data migration is required. Ship the generalized read/conditional-clear path, callback/help projection, and regression tests together. Rollback is the single implementation commit; existing runtime rows remain schema-compatible, and project registration is never removed by stop.

## Open Questions

None. The owner source, identity checks, cleanup key, output fields, and compatibility boundary are fixed by the current runtime model and this change's requirements.
