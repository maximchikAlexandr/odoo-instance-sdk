## Why

A checkout of an OdCLI-managed Compose project can currently reach `READY` while its generated `odoo.conf` omits `data_dir`. A shared database can then be paired with the wrong or unusable filestore even though shared mode promises that database and filestore remain shared with the project.

## What Changes

- Resolve the canonical project-owned filestore binding while building the immutable checkout plan for a self-contained Compose project.
- Apply that resolved `data_dir` when generating checkout configuration in both shared and copy modes, including legacy source configurations that omit it.
- Preserve an explicit source `data_dir` for external PostgreSQL projects and leave their checkout behavior unchanged.
- Fail before the environment reaches `READY` when a required self-contained filestore binding cannot be proven safe.
- Add focused public-resource regression coverage for successful Compose binding, external-source preservation, both database modes, and fail-closed lifecycle behavior.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `development-environment`: Require managed Compose checkouts to carry the canonical project-owned filestore binding into generated configuration and prevent `READY` when that binding is unsafe.

## Impact

- Affected implementation: immutable checkout planning, checkout configuration generation, and the existing project-owned filestore helpers.
- Affected tests: focused `EnvironmentResource.checkout(...)` unit tests around generated configuration and lifecycle state.
- Public API and CLI signatures remain unchanged; no new dependency, storage abstraction, migration, filestore copy, or ownership transfer is introduced.
