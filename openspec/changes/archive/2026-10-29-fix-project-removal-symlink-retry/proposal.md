## Why

`odcli remove --purge` currently rejects ordinary interpreter symlinks inside an
OdCLI-created `.venv`, even though the recursive deletion primitive unlinks those
entries without following them. The failure can occur after earlier purge steps,
and a retry can then lose the runtime needed by the current observation path.

## What Changes

- Permit recursive deletion of a proven, contained, non-source filesystem target
  when it contains nested symlinks, while preserving the external link targets.
- Preserve the top-level symlink, containment, source-root provenance, unknown
  source content, and clean-worktree guards.
- Let project purge reconstruct the required database and Compose observations
  from durable project/catalogue evidence when the owned primary Python
  environment is already absent, so a full-id retry can finish safe remaining
  work.
- Add focused filesystem and project-removal regression coverage; keep the
  public SDK, CLI syntax, output schema, dependencies, and catalogue schema
  unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `local-resource-lifecycle`: Distinguish a forbidden top-level symlink target
  from nested symlink entries that may be unlinked inside a proven non-source
  directory without traversal.
- `project-removal`: Preserve retryability when the primary project Python
  environment is absent but durable database and Compose ownership evidence is
  still available.

## Impact

The change affects the internal filesystem purge adapter, project-removal
observation construction, and their unit/integration tests. It does not change
public commands, serialized results, storage, dependencies, or migrations.
