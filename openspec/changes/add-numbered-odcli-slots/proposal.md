## Why

Concurrent agents can install different OdCLI revisions, but every revision currently resolves global state to `~/.odcli`. A schema migration or update from one revision can therefore corrupt or make another revision unusable; numbered, exact-SHA slots need isolated user state without changing the process `HOME` or the canonical `odcli` installation.

## What Changes

- Add a single explicit SDK/CLI user-root selector whose default remains `~/.odcli` and whose numbered-slot values resolve to `~/.odcli-N`.
- Add canonical CLI management for installing or replacing `odcli-N` from a full Git SHA in a dedicated uv tool environment, verifying installed provenance, listing slots, and safely removing only the selected slot's launcher, tool environment, and state.
- Launch every numbered command with its selected root while preserving the real `HOME`, so Git, SSH, uv, and unrelated user configuration remain shared and unchanged.
- Keep a new slot empty instead of copying canonical or legacy state; disable legacy-storage adoption and deletion for numbered roots.
- Reject direct `odcli-N update`; changing a slot revision is an explicit exact-SHA replacement through the canonical slot manager and never updates ordinary `odcli`.
- Document that slot state isolation does not isolate Odoo, PostgreSQL, databases, filestores, Docker resources, or ports, and require separate project copies or explicitly disjoint external resources for concurrent use.
- Preserve the existing canonical `odcli`/`~/.odcli` behavior and the separate shared-state `odcli-fix-<issue>` workflow.

## Capabilities

### New Capabilities

- `numbered-odcli-slots`: Exact-SHA numbered installation, invocation, provenance verification, state isolation, replacement, removal, and external-resource boundaries.

### Modified Capabilities

- `development-environment`: Global SDK-owned paths use the selected user root and numbered roots never adopt canonical or legacy storage.
- `self-update`: Direct self-update is forbidden for numbered commands; slot replacement is owned by the canonical manager.
- `cli-odcli`: The canonical CLI exposes bounded numbered-slot management commands while numbered launchers expose the ordinary command surface except direct update.

## Impact

The change affects the central path provider, startup storage migration, self-update selection, CLI registration/output inventory, uv-tool installation/provenance handling, and packaging/unit tests. It adds no dependency and does not change repository-local `.odcli`, Odoo/PostgreSQL ownership, database or filestore formats, Docker naming, or port allocation.
