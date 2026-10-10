## Why

Concurrent agents can install different OdCLI revisions, but ordinary, numbered, and existing hot-fix launchers currently converge on canonical `~/.odcli`. A schema migration or write from either alternate revision can therefore corrupt another agent's state; every exact-revision launcher needs isolated OdCLI user state without replacing `HOME` or weakening canonical behavior.

## What Changes

- Add one explicit SDK/CLI user-root selector: ordinary `odcli` keeps `~/.odcli`, numbered `odcli-N` uses `~/.odcli-N`, and `odcli-fix-ISSUE` uses `~/.odcli-fix-ISSUE`.
- Add canonical numbered-slot install/replace/list/remove management with exact-SHA uv environments and verified provenance.
- Update the existing skill-local hot-fix installer, shim, compatibility review, reconciliation, and cleanup to use its deterministic isolated root and manager metadata outside canonical state.
- Preserve real `HOME`, so Git, SSH, uv, and unrelated settings remain shared and unchanged.
- Start every alternate root empty; never copy canonical, legacy, numbered, or neighboring hot-fix state, absolute paths, or migration history.
- Run legacy-storage adoption only for ordinary canonical `odcli`; alternate revisions migrate only their selected root.
- Reject direct `odcli-N update` and `odcli-fix-ISSUE update`. Numbered revision changes use the canonical slot manager; hot-fix retirement remains skill-managed and occurs only after the existing merge/ancestry/branch gates.
- Make removal/retirement delete only the selected launcher, tool environment, manager metadata, lock, and isolated root while preserving canonical and every neighbor.
- Document that user-root isolation does not isolate project checkouts, Odoo/PostgreSQL, databases, filestores, Docker resources, or ports.

## Capabilities

### New Capabilities

- `numbered-odcli-slots`: Exact-revision alternate launcher identity, isolated roots, lifecycle management, cross-root safety, and external-resource boundaries for numbered and hot-fix commands.

### Modified Capabilities

- `development-environment`: Every SDK-owned global path uses the selected root; noncanonical roots never adopt canonical or legacy storage.
- `self-update`: Direct self-update is forbidden for numbered and hot-fix launchers; canonical update stays isolated from them.
- `cli-odcli`: Canonical CLI exposes bounded numbered-slot management; alternate launchers preserve the ordinary surface except forbidden management/update paths.
- `bug-report`: Existing `odcli-fix-<issue>` installation, compatibility, invocation, reconciliation, and cleanup use isolated state rather than shared `~/.odcli`.

## Impact

The change affects the central path provider, startup storage migration, self-update selection, canonical slot CLI, uv provenance handling, `.agents/skills/odcli-autonomous-work` fix-tool workflow, its tests, packaging acceptance, and documentation. It adds no dependency and does not change repository-local `.odcli`, Odoo/PostgreSQL ownership, database/filestore formats, Docker naming, or port allocation.
