## Context

The filesystem purge adapter proves the selected target itself is not a symlink
and is canonically contained, then applies the same nested-symlink rejection to
both source roots and smaller owned artifacts. That shared guard is too strict
for an OdCLI-created `.venv`, whose interpreter entries are ordinary symlinks.
`shutil.rmtree` on supported Python platforms unlinks such entries rather than
following them, but source-root removal still needs the stricter inventory rule.

Project removal captures database and Compose evidence before executing its
ordered mutations. Today `OdooClient.instance.from_project()` resolves the Odoo
runtime as part of construction, even when removal only needs PostgreSQL and
cluster primitives. If the primary `.venv` is already absent, observation fails
and the registered project cannot finish a full-id retry.

## Goals / Non-Goals

**Goals:**

- Purge an ownership-proven primary `.venv` containing normal nested interpreter
  symlinks without following or modifying their external targets.
- Preserve strict top-level target, containment, source provenance, unknown
  source content, and clean-worktree checks.
- Recover database and Compose observations from durable evidence when only the
  primary project runtime is missing, and remain fail-closed otherwise.
- Cover both the direct regression and the already-missing-venv retry path.

**Non-Goals:**

- No force option, general symlink-following deletion, source-root policy
  relaxation, catalogue repair, or legacy database ownership inference.
- No public SDK/CLI, output, schema, dependency, migration, or E2E framework
  change.

## Decisions

### Keep the strict policy at the source boundary

For a top-level target, the adapter continues to reject `path.is_symlink()` and
to require canonical containment. For a registered source root, the recursive
inventory continues to reject nested symlinks, unknown `.odcli` content, dirty
worktrees, and changed Git provenance. This is necessary because source deletion
has a broad ownership boundary. The existing alternative is the current shared
recursive guard; it is retained for source roots but removed from narrower
non-source targets because it rejects the SDK's own venv layout.

For an already-proven non-source directory, recursive deletion may encounter
nested file or directory symlinks. The adapter relies on the standard-library
non-following recursive delete behavior, verifies the directory postcondition,
and tests that each external target remains unchanged. Resolving every nested
link and maintaining an allowlist was rejected: it adds a second tree walker
without improving the top-level ownership proof.

### Separate removal observation from runtime execution readiness

The normal path remains `instance.from_project()`. If that path fails solely
because the configured primary runtime is absent or non-executable, removal may
construct an internal observation-capable instance from the already loaded
project manifest, generated Odoo configuration, active catalogue cluster claim,
owned password file, and existing `OdooInstance`/`PostgresCluster` primitives.
It does not create, repair, or execute a Python environment or Odoo process.

The fallback is necessary for a durable full-id retry. Recreating a temporary
venv was rejected because it mutates before the removal plan is proven. Treating
database and Compose evidence as optional was rejected because it would weaken
the existing ownership gate. Broadening `from_project()` was rejected because
normal runtime callers still require a valid executable.

### Preserve existing failure and ordering contracts

The recovered observation flows through the existing inventory, fingerprint,
adapter, lock, revalidation, and typed-error paths. Missing or mismatched
manifest, cluster, password, database identity, Docker identity, or exclusive-use
evidence still produces an incomplete projection and no mutation. No separate
retry ledger or new abstraction is introduced.

### Ponytail full gate

No new subsystem is introduced. Each needed behavior is served by an existing
filesystem adapter, removal observation path, inventory projection, or test seam;
the alternatives above either duplicate those facilities or weaken a safety
boundary, so they are omitted.

## Risks / Trade-offs

- **[A platform recursive delete could follow nested links]** → Keep the target
  and source guards, rely only on Python's supported symlink-safe directory
  semantics, and assert external file and directory targets survive in focused
  tests.
- **[The recovery path could accept incomplete ownership]** → Gate it on the
  existing manifest, active claim, owned credential, inventory, Docker identity,
  and revalidation checks; negative integration tests must remain inert.
- **[The normal path and recovery path could drift]** → Feed both into the same
  database inventory and project-removal projection instead of adding a second
  planner or result type.

## Migration Plan

No data migration is required. Ship the guarded behavior and regression tests in
one product change. Rollback is a code rollback; existing catalogue rows and
project files require no conversion.

## Open Questions

None. The required behavior and safety boundaries are fixed by GitHub #188 and
the existing project-removal contracts.
