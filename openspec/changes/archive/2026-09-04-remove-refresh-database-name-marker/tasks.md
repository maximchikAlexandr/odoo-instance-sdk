## 1. Target Name Contract

- [x] 1.1 Remove the literal `_refresh_` segment from automatic target generation while preserving the UTC timestamp, collision-resistant suffix, validation, and 63-byte limit; verify focused generator tests pass.
- [x] 1.2 Update target-name regression assertions to require `<source>_<timestamp>_<suffix>` and verify collision retry coverage remains green.

## 2. Verification

- [x] 2.1 Run formatting, lint, strict type checking, focused database-preparation tests, and `openspec validate remove-refresh-database-name-marker --strict`.
