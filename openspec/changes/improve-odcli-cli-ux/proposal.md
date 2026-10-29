## Why

OdCLI already has a typed SDK-first command boundary, but several ordinary input and filesystem failures still escape as tracebacks or pre-envelope Click output, while focused environment inspection and metadata startup do more work than necessary. The selected UX improvements close those concrete gaps without adding another parser, collector, loader framework, or runtime.

## What Changes

- Convert expected invalid-input and local-filesystem failures at the existing CLI boundary into sanitized human diagnostics or the existing v1 machine error envelope, with stable non-zero exits and no partial output.
- Extend the bounded-leaf contract so parameter parsing and validation failures produce `usage_error` JSON/TOON documents only after a valid supported machine format and leaf are known; preserve native Click usage output outside that boundary.
- Resolve `env show` selectors cheaply before the canonical monitor snapshot, then collect exactly one snapshot scoped to the selected project.
- Keep metadata commands lightweight by moving failure-context and other operation-only imports out of CLI startup paths while preserving public exports.
- Add accurate examples and constraints to the existing `init`, `env create`, `db restore`, `run`, `exec`, and `test` help, including the now-integrated opt-in `init --venv` path and the distinct owned-checkout `env create --create-venv` path; support `-h` throughout the Click tree without consuming child arguments after `--`.
- Update compatibility documentation and release notes for the intentional machine usage-error contract change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-odcli`: Changes expected error rendering, supported machine usage errors, command help, and inherited `-h` handling.
- `environment-monitor`: Adds the requirement that focused environment inspection resolve a catalogue selector before expensive collection and scope the one resulting snapshot to its owning project.
- `sdk-package-imports`: Strengthens installed metadata startup boundaries so `--help` and `--version` do not eagerly load database preparation, Alembic, or SQLAlchemy paths.

## Impact

Affected areas are the shared Click/Rich registration and output helpers, environment command/catalogue resolution, the split monitor collection implementation, startup import boundaries, the expanded test-owned CLI leaf inventory, CLI characterization and packaging tests, help/reference text, and changelog. Unknown `--fields` remains an ordinary typed validation failure covered by the machine `usage_error` contract; suggestions and value completion are explicitly excluded. Successful machine envelopes, SDK APIs, command names and aliases, catalogue schema, process execution, confirmation, locking, rollback, cleanup, redaction, and native-stream semantics remain compatible. No runtime dependency or new framework is introduced. The active `refactor-cli-output-boundary` change remains the owner of shared envelope/serializer/inventory architecture; this change consumes that boundary and specifies only the six retained user-visible outcomes.
