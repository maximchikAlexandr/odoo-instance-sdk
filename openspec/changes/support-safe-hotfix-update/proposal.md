## Why

An active `odcli-fix-N` installation cannot advance to a newly reviewed immutable revision without deleting its isolated user state. Iterative fixes therefore need a fail-closed update path that preserves populated state, keeps canonical OdCLI untouched, and never exposes preserved state to unvalidated candidate code.

## What Changes

- Add a supported developer-helper update command for one existing hot-fix identity, issue, and pull request.
- Require fresh exact-SHA review evidence and matching installed identity/provenance before any mutation.
- Reuse the alternate-tool lifecycle lock and staged installation transaction, adding a pre-activation compatibility probe that cannot access preserved state.
- Atomically switch the hot-fix executable, launcher, and manifest while retaining the existing `ODCLI_USER_ROOT`; roll back the old installation on preparation, validation, or activation failure.
- Reject lifecycle conflicts, unsafe self-replacement, foreign/malformed installations, stale review evidence, and identity mismatches without changing the installed tool or state.
- Extend unit, packaging/integration, architecture-inventory, and developer-tool documentation coverage for the public developer-helper boundary.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `numbered-odcli-slots`: Extend the existing isolated alternate-tool lifecycle contract with safe in-place updates for a reviewed hot-fix identity while preserving its state and canonical isolation.

## Impact

- Affected implementation: `src/odoo_instance_sdk/internal/alternate_tool.py`, `scripts/odcli_agent_tools/fix_tool.py`, and `scripts/odcli_agent_tools/reviewer.py`.
- Affected tests and inventories: alternate-tool unit, packaging/integration acceptance, autonomous developer-helper, and execution-architecture inventory coverage.
- User-visible surface: the developer-only `fix_tool.py` command set and JSON-safe result/error output; the public product `odcli` command tree remains unchanged.
- No new runtime dependency, state-root format, database migration, public HTTP/OpenAPI operation, or parallel installer is introduced.
