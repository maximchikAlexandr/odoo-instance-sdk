## Why

The CLI currently mixes Click parsing, context resolution, operation orchestration, inventory enrichment, and rendering, so the same environment operation cannot be reused consistently by the Python SDK and future HTTP transports. This change establishes a small transport boundary while delivering GitHub issue #23: stable Rich, JSON, and TOON output plus a live `env list` view. It preserves existing public imports and resource method names, with one explicit additive public-model migration: snapshot schema v2 adds required environment fields.

## What Changes

- Add characterization coverage for the existing command tree, help, context resolution, exit codes, stream routing, JSON envelope v1, `env list` filters, and passthrough commands before moving responsibilities.
- Keep `odoo_instance_sdk.cli:cli` as the stable Click composition/registration entry point while moving only the affected context, output, and `env` adapter responsibilities into `commands/`.
- Replace the untyped `ctx.obj` dictionary on the affected command paths with a small typed CLI context and native Click passing.
- Make `EnvironmentMonitor` return the complete typed environment inventory needed by every `env list` renderer, including requested removed rows and artifact/backup availability, without CLI-side catalog, Git, or Docker collection.
- Add one CLI-only `OutputMode` with `rich`, `json`, and `toon`; retain command-local `--json` as a backward-compatible alias for `--format json` and preserve envelope v1.
- Encode JSON and TOON from the same JSON-safe envelope, using pinned `python-toon==0.1.3` verified for the CLI-envelope-v1/snapshot-v2 boundary traced to TOON specification v4.1, rather than a custom encoder or Node subprocess; no full-library conformance is claimed.
- Render structured human output with Rich and add `odcli env list --watch` using `rich.live.Live`, the same inventory query, a positive configurable interval, retained filters/sort, last-successful-sample behavior, and clean interruption.
- Keep passthrough and interactive commands on native streams; do not apply document output modes to `run`, `shell`, or `logs --follow`.
- Do not migrate Click, alter FastAPI/React semantics, add generic application layers, or move unrelated command groups.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-odcli`: Define the lightweight CLI boundary, typed context, Rich/JSON/TOON format selection and envelope parity, canonical `env list` rendering, live watch behavior, stream policy, and compatibility guarantees.
- `environment-monitor`: Extend the canonical typed inventory query just enough to supply removed environments and artifact/backup availability to all CLI renderers without transport knowledge.
- `packaging`: Add the runtime dependencies required for Rich rendering and verify Python TOON encoding/strict decoding for the supported CLI-envelope-v1/snapshot-v2 fixtures traced to v4.1.

## Impact

- Affected implementation areas: `src/odoo_instance_sdk/cli.py`, new focused modules under `src/odoo_instance_sdk/commands/`, `src/odoo_instance_sdk/resources/monitor.py`, public snapshot models in `models.py`, and the existing internal catalog-backed collectors.
- Affected tests: CLI characterization/output/watch tests, monitor snapshot/inventory tests, packaging metadata tests, and full `make pr` validation.
- Public compatibility: existing Python resource imports and method set plus `odoo_instance_sdk.cli:cli` remain stable. The explicit exception is the additive snapshot-model/schema-v2 migration (`observed_port` and `artifacts` become required fields); command names, exit codes, JSON envelope v1, redaction, and passthrough streams remain observable-compatible.
- Dependencies: Rich becomes the human renderer; pinned `python-toon==0.1.3` is verified by semantic strict round trips of the CLI-envelope-v1/snapshot-v2 fixture boundary traced to TOON v4.1, without claiming full-library conformance.
