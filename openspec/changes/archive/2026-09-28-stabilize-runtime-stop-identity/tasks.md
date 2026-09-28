## 1. Catalogue and launch identity

- [x] 1.1 Add Alembic revision `0004` for nullable `runtime.launch_identity_json` after the existing catalogue `0003` revision, update the canonical SQLAlchemy schema/revision checks, and cover fresh, upgraded, idempotent, and downgrade catalogue behavior.
- [x] 1.2 Define the strict versioned launch-identity codec/value using JSON-safe concrete types, canonical paths, bounded prefix/index validation, and the existing common argv redaction projection; reject missing, malformed, or unsupported evidence without exposing its contents.
- [x] 1.3 Capture identity from the exact immutable foreground/detached `PreparedStep` and require every new owner-neutral runtime upsert to persist it before wait/return, without rebuilding argv or changing public plans and results.

## 2. Stop-time validation and diagnostics

- [x] 2.1 Replace project/environment stop-time argv reconstruction with decoding the persisted captured identity and projecting the live process through the same redaction boundary.
- [x] 2.2 Compare the captured executable prefix and protected bindings while preserving PID/create-time, executable, cwd, config, owner, POSIX process-group, bounded termination, exit verification, and conditional-clear checks.
- [x] 2.3 Return deterministic value-free component labels for prefix/protected-option mismatches and sanitized unavailable/unreadable snapshot failures; retain the row and send no signal on every validation failure.

## 3. Regression and security coverage

- [x] 3.1 Extend existing runtime-stop parameterized tests for unchanged owned runtimes after project configuration/branch-source drift, protected binding and prefix mismatches, PID reuse, snapshot/owner TOCTOU, Windows/POSIX behavior, and absent/malformed/unsupported legacy evidence.
- [x] 3.2 Extend existing foreground/detached persistence and catalogue tests to prove both owner kinds record the exact executed secret-free identity, argument boundaries and sensitive indices survive round trip, and legacy rows migrate to null without fabricated evidence.
- [x] 3.3 Extend the existing CLI leaf/security matrices for Rich, JSON, and TOON to prove actionable field labels, stable success payloads/exit codes, no signal or row clear on rejection, and no secret, attacker-controlled control text, unsafe path, or full argv leakage.

## 4. Verification and documentation

- [x] 4.1 Update user-facing stop/runtime documentation and changelog with captured-identity behavior, legacy live-row recovery constraint, and sanitized diagnostics without publishing internal paths or secret examples.
- [x] 4.2 Run strict OpenSpec validation, focused catalogue/runtime-stop/detached/CLI security suites, catalogue migration tests, architecture inventory, Ruff, strict mypy, and the repository's required full verification gates; fix failures without weakening redaction or inventories.
