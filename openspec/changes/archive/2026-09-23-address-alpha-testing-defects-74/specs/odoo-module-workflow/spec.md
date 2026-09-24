## MODIFIED Requirements

### Requirement: Changed-module update selection

`module update --changed [--base REF]` SHALL be mutually exclusive with positional modules, SHALL reuse the `test --changed` merge-base and committed/staged/unstaged/untracked selector, SHALL fail closed on unmapped paths or stale HEAD, and SHALL attach base provenance, changed paths, selected installed modules, and `not_installed` to the shared immutable execution plan. Empty addon changes SHALL be a successful no-op; mutation SHALL use the existing exclusive update path and require `--yes`.

On a non-zero return code, `module update` SHALL prioritize a valid nonce-framed payload and use the `user_error` or `finalization_error` from the common shell wrapper. When the payload is missing or malformed, the command SHALL fall back to the last `_TIMEOUT_TAIL_BYTES = 8192` redacted bytes of `stderr`, not the first N characters. Full unlimited tracebacks SHALL NOT be emitted and existing redaction SHALL NOT be weakened. Rich, JSON, and TOON SHALL return the same stable error code and safe details. Secrets, terminal escapes, and sensitive paths SHALL remain sanitized. The common shell-error contract SHALL be reused; no separate parser for `module update` SHALL be added.

#### Scenario: Preview changed update

- **WHEN** a caller runs `module update --changed --dry-run` with a stable HEAD and resolvable base
- **THEN** no action or process executes and Rich, JSON, and TOON expose the same selection and exact shared execution plan

#### Scenario: long startup log does not hide the traceback

- **WHEN** `odcli module update <MODULE> --yes` fails with a startup log longer than the limit and a traceback at the end
- **THEN** the final error contains the exception type and the root cause

#### Scenario: framed payload has priority

- **WHEN** a valid nonce-framed `user_error` payload is present
- **THEN** it is used with priority over the general `stderr` tail

#### Scenario: fallback uses bounded tail

- **WHEN** the framed payload is missing or malformed
- **THEN** a bounded redacted tail of `stderr` is used and truncation is reported explicitly

#### Scenario: same error code across formats

- **WHEN** `module update` fails and is rendered in Rich, JSON, and TOON
- **THEN** all three return the same stable error code and safe details
