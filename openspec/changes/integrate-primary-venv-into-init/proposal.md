## Why

Primary virtual-environment preparation is currently a separate required step after `odcli init`, so a nominally self-contained initialization can reach Compose and Odoo bootstrap while still using the source Python instead of the prepared project interpreter. The approved workflow makes that preparation an explicit opt-in part of the same inspectable initialization command without changing existing callers that omit the new option.

## What Changes

- Add one optional `--venv PATH` input to `odcli init`; omission preserves the current init plan and behavior.
- When `--venv` is present, use the existing resolved `--python` value as the creation selector, prepare or refresh the primary venv through the existing preparation implementation, persist its concrete interpreter, and use that interpreter for later Compose/Odoo bootstrap steps.
- Capture one immutable plan ordered as manifest persistence, primary-venv preparation/persistence, then Compose and Odoo bootstrap; keep dry-run, Rich/JSON/TOON, redaction, locks, path validation, and failure reporting truthful.
- Define retry and partial-success semantics for fresh and existing projects, Python-selector changes, valid or damaged venvs, empty dependency inputs, and failures at each preparation stage.
- Keep the public `prepare-venv` CLI and SDK operations as the explicit refresh/recovery path and preserve their atomic behavior.
- Add focused cross-platform tests, documentation, CI coverage, and independent local E2E evidence for the combined public workflow.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `project-init`: Integrate opt-in primary-venv preparation into the ordered initialization lifecycle and define its retry and failure state.
- `development-environment`: Reuse the single primary-venv preparation owner from both standalone preparation and initialization without weakening its persistence and provenance guarantees.
- `cli-odcli`: Add `init --venv PATH` while preserving SDK-first transport, output-mode, dry-run, and compatibility contracts.

## Impact

The change affects the public init SDK signature and immutable command composition, init CLI option binding and existing-manifest decisions, the private reusable primary-venv preparation executor, bootstrap Python selection, canonical CLI characterization, focused init/prepare tests, Windows CI coverage, and primary-checkout documentation. It adds no dependency, venv abstraction, resolver, runner, catalog record, background workflow, or replacement for `prepare-venv`.
