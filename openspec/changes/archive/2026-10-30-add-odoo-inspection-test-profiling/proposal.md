## Why

Developers can already resolve an Odoo project or environment and run tests through a typed, inspectable headless-shell command, but they cannot safely inspect a registry object or see which native Odoo tests consume time and SQL queries. Adding these two bounded diagnostics now closes that gap without introducing a general ORM client, another test runner, or a second output/planning layer.

## What Changes

- Add a public `OdooInspector` facade at `instance.inspector` with explicit model, field, and XML-ID operations plus matching immutable command siblings.
- Add `odcli inspect model`, `odcli inspect field`, and `odcli inspect ref` as bounded read-only SDK-first leaves using the existing project/environment resolution and Rich/JSON/TOON output pipeline.
- Extend the existing Odoo test operation with opt-in profiling that maps Odoo 19 native `_r.stats` entries into frozen typed profile values.
- Add `--profile` to the existing top-level test path while preserving selection, dry-run planning, exit semantics, and the ordinary unprofiled result shape.
- Extend focused unit, CLI contract, serializer/redaction, documentation, and real-Odoo integration coverage. No new dependency or persistent data migration is introduced.

## Capabilities

### New Capabilities

- `odoo-registry-inspection`: Bounded read-only model, field, and XML-ID inspection through public typed SDK results and immutable command objects.

### Modified Capabilities

- `local-odoo-testing`: Opt-in native Odoo 19 per-test duration and SQL-query profile data on the existing runner and result path.
- `cli-odcli`: New inspect leaves and the additive `test --profile` adapter/output contract, including canonical leaf inventory and format parity.

## Impact

- Public SDK: `OdooInstance`, one focused inspector facade, frozen inspection/profile result models, public exports, and command delegation contracts.
- Runtime: the existing rollback-only headless Odoo shell command and `_test_runner_source()` payload; no HTTP server, raw SQL inspector, second runner, or new subprocess boundary.
- CLI: one lazy `inspect` group, the existing `test` command, shared bounded output projection, help, and `PUBLIC_LEAF_CASES`.
- Verification: focused unit/contract tests, existing CI jobs and real-Odoo fixtures, plus an isolated numbered Odoo 19 walkthrough against the project test repository.
- Documentation: README and CLI help for the exact bounded operations.
