## Why

Batch translation exports put several encoded catalogues into one captured response. Whole-value assignment redaction retries an unbounded pattern across that response and can stall for minutes.

## What Changes

- Scan candidate assignment keys once while preserving existing secret/value boundaries.
- Add a large-response timeout regression and assignment boundary cases.
- Accept the exact pinned uv provenance URL in the hotfix installer, retaining repository and SHA checks, so the reviewed isolated tool can be installed.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `command-execution`: bounded whole-value redaction of large captured responses.

## Impact

Shared process redaction and its tests; private hotfix bootstrap and tests. No CLI syntax, catalogue schema, database transaction, or translation file format changes. Detached launch is outside scope.
