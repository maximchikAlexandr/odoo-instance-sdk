## Why

PR #137 contains a machine-operation registry, export/invoke/session commands, provider loading, and consumer-generation surface without a current product consumer. The owner selected the existing Python SDK and JSON CLI instead, so the remaining work is to delete that speculative layer while preserving useful code already present in the PR.

## What Changes

- **BREAKING** Remove the production operation inventory, stable operation IDs, descriptors/schemas, contract export, local invoke/session runtime, operation context, provider API, provider fixtures, machine-only public exports, and Go/consumer proof.
- Remove only the associated Click entrypoints, packaging metadata, tests, fixtures, documentation, generated traceability rows, and main-spec requirements; repair directly coupled shared files.
- Restore the exhaustive test-owned `PublicLeafCase`/`PUBLIC_LEAF_CASES` CLI matrix without creating a replacement runtime registry or second authority.
- Preserve the already implemented snapshot-v3 monitor, database observation/reconciliation, inert backup reads, catalogue migration, and structured replacement recovery. Run only regression checks whose imports, registrations, output contracts, or architecture assertions are directly touched by machine-layer removal.
- Keep required CI and the implementation-parent four-profile review as safety gates. Do not add live Moritora/database E2E, test-repository orchestration, new scripts, manifests, adapters, or evidence infrastructure for this deletion.
- Keep Supervisor integration, daemon/RPC dispatch, dynamic operation discovery, plugin lifecycle, and any future machine interface outside this change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `machine-operation-contracts`: Remove the complete capability.
- `cli-odcli`: Remove machine-operation entrypoints and stable-operation bindings while preserving ordinary CLI behavior.
- `command-execution`: Remove machine approval-session requirements while preserving the existing command execution boundary.
- `real-odoo-e2e-verification`: Restore test-owned CLI traceability after the production inventory is removed.

## Impact

- Delete `src/odoo_instance_sdk/operations/`, `commands/contract.py`, `commands/operation.py`, operation-only `OperationContext` code, provider fixtures/entry points, operation-only tests, `docs/machine-operation-contracts.md`, and their documentation/spec/traceability references.
- Update only direct callers and shared seams such as root exports, CLI registration/context/output, exceptions, architecture inventories, CLI characterization, command-matrix generation, packaging expectations, and documentation contracts.
- Preserve monitor/database/recovery implementation, migrations, OpenAPI/web generated surfaces, and their existing tests except for a minimal import or assertion repair caused by the deletion.
- The superseded graph-1 planning package is omitted from the final tree at the owner's request. This archived change records the graph-3 removal; PR #137 remains the delivery vehicle.
