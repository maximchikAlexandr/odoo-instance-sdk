## Why

Generated restore database names contain a redundant `_refresh_` label even though the timestamp, collision-resistant suffix, and restore catalog already identify each restored database. Removing the label makes names shorter without weakening collision protection or restore safety.

## What Changes

- Generate automatic restore targets as `<source>_<UTC timestamp>_<random suffix>`.
- Preserve PostgreSQL name validation, the 63-byte limit, collision rechecks, and the rule that existing databases are never reused or overwritten.
- Update regression coverage and user-facing specification examples.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `project-database-preparation`: Remove the literal refresh marker from automatically generated target database names while preserving their timestamped uniqueness and safety guarantees.

## Impact

This affects the private target-name generator, its focused unit tests, and the project database preparation specification. Explicitly supplied target names and existing restored databases are unchanged.
