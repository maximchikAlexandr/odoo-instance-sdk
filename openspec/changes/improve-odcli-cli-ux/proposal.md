## Why

OdCLI already has a typed SDK-first command boundary, but several ordinary input and filesystem failures still escape as tracebacks or pre-envelope Click output, while common discovery paths remain slower and less helpful than the underlying command model permits. The selected UX improvements close those concrete gaps without adding another parser, collector, registry, renderer, or runtime.

## What Changes

- Convert expected invalid-input and local-filesystem failures at the existing CLI boundary into sanitized human diagnostics or the existing v1 machine error envelope, with stable non-zero exits and no partial output.
- Extend the bounded-leaf contract so parameter parsing and validation failures produce `usage_error` JSON/TOON documents only after a valid supported machine format and leaf are known; preserve native Click usage output outside that boundary.
- Resolve `env show` selectors cheaply before the canonical monitor snapshot, then collect exactly one snapshot scoped to the selected project.
- Keep metadata commands lightweight by moving failure-context and other operation-only imports out of CLI startup paths while preserving public exports.
- Make human `doctor` findings readable, actionable, terminal-safe, and width-aware while retaining full facts in machine output.
- Add accurate examples and constraints to the existing `init`, `env create`, `db restore`, `run`, `exec`, and `test` help; support `-h` throughout the Click tree without consuming child arguments after `--`.
- Add side-effect-free Click completion for environment selectors, named remote sources, and schema-derived `--fields` values, including dotted and comma-separated prefixes.
- Suggest close schema-valid paths for unknown `--fields` values from the same typed schema source used by projection.
- Update compatibility documentation and release notes for the intentional machine usage-error contract change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-odcli`: Changes expected error rendering, supported machine usage errors, human doctor presentation, command help, `-h`, selector and field completion, and schema-derived field suggestions.
- `environment-monitor`: Adds the requirement that focused environment inspection resolve a catalogue selector before expensive collection and scope the one resulting snapshot to its owning project.
- `sdk-package-imports`: Strengthens installed metadata startup boundaries so `--help` and `--version` do not eagerly load database preparation, Alembic, or SQLAlchemy paths.

## Impact

Affected areas are the shared Click/Rich registration and output helpers, environment command/catalogue resolution, monitor snapshot scoping, doctor rendering, remote-source options, typed field projection, startup import boundaries, CLI characterization and packaging tests, help/reference text, and changelog. Successful machine envelopes, SDK APIs, command names and aliases, catalogue schema, process execution, confirmation, locking, rollback, cleanup, redaction, and native-stream semantics remain compatible. No runtime dependency or new framework is introduced. The active `refactor-cli-output-boundary` change remains the owner of shared envelope/serializer/inventory architecture; this change consumes that boundary and specifies only the nine user-visible outcomes.
