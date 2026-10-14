## Why

PR #137 added a versioned machine-operation registry, contract export, local invoke/session transport, provider loading, and external-consumer proof without a concrete product consumer. The owner selected the existing Python SDK and JSON CLI for automation and Supervisor for Odoo process ownership, so retaining that parallel machine layer would add drift and maintenance risk without a current need.

## What Changes

- **BREAKING** Remove the `machine-operation-contracts` capability, production operation inventory, stable operation IDs, contract bundle/export, `operation invoke`, approval session, operation context, installed-operation provider API, provider fixtures, and external/Go consumer proof from PR #137.
- Restore the repository's test-owned `PublicLeafCase`/`PUBLIC_LEAF_CASES` CLI characterization and E2E disposition matrix; it SHALL remain test evidence and SHALL NOT become a production runtime registry.
- Remove machine-only public exports, Click entrypoints, documentation, packaging checks, schemas, tests, and E2E cases while retaining shared bounded CLI output and SDK-first delegation used by ordinary commands.
- Retain selective snapshot-v3 observation/CPU semantics, inert database and backup reads, explicit database reconciliation, and structured database-replacement recovery.
- Add a fail-closed compatibility gate for the currently deployed Moritora consumer against the additive snapshot observation field; embedded dashboard success alone SHALL NOT prove external Moritora compatibility.
- Re-run review, CI, and selected live E2E against the graph-2 product composition. Graph-1 WP, review, CI, and E2E evidence SHALL NOT be reused as proof that graph-2 passed.
- Keep Supervisor integration, a daemon/RPC boundary, dynamic operation discovery, language-specific client generation, and a replacement machine interface out of this change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `machine-operation-contracts`: Remove the complete capability because no current product consumer requires registry/export/invoke/session/provider contracts.
- `cli-odcli`: Remove machine-operation entrypoints and stable-operation bindings while preserving existing friendly CLI commands and bounded Rich/JSON/TOON behavior.
- `command-execution`: Remove machine approval-session requirements while preserving the existing private `Command`, preview, execution, rollback, and cleanup boundary.
- `environment-monitor`: Retain snapshot-v3 observation truth and require compatibility evidence for embedded and separately deployed Moritora consumers.
- `real-odoo-e2e-verification`: Restore test-owned CLI traceability and replace machine-contract live evidence with graph-2 checks for the retained monitor and database boundaries.

## Impact

- Machine-only removal reaches `src/odoo_instance_sdk/operations/`, `commands/contract.py`, `commands/operation.py`, machine-only `OperationContext` and public exports, Click registration, provider fixtures, operation packaging/unit/integration tests, machine-operation documentation, and the archived change's contract-specific E2E matrix.
- Retained implementation reaches monitor DTO/planning/collectors, OpenAPI/generated TypeScript/dashboard consumption, database observation/reconciliation, backup catalogue migration, and database-replacement recovery. Coupled tests, fixtures, docs, and architecture inventories remain in scope.
- `openspec/changes/archive/2026-10-10-add-typed-machine-operation-contracts` remains immutable historical context. This active change is the sole planning package for graph revision 2.
- PR #137 remains the delivery vehicle and draft until graph-2 implementation, independent review, required CI, and live compatibility evidence complete.
