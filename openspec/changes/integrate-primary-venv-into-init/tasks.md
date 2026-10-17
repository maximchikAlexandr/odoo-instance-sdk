## 1. Share the existing primary-venv preparation owner

- [x] 1.1 Extract the minimum private primary-venv planning/execution seam from `prepare_venv_command` so it accepts a captured config/project identity and preserves the existing create, validate, discovery, compile/sync, locking, provenance, staged-lock, rollback, redaction, and immutable-step behavior.
- [x] 1.2 Keep `prepare_venv_command` and `prepare_venv` source-compatible by delegating through that seam with the existing manifest snapshot/staleness checks and result schema.
- [x] 1.3 Add focused preparation tests proving standalone parity, init/standalone step parity, empty inputs, damaged venv rejection, previous-byte restoration at every persistence failure, and POSIX/Windows interpreter projection.

## 2. Compose primary preparation into initialization

- [x] 2.1 Add the optional public init venv parameter and construct its complete preparation steps from the effective config before execution, failing before mutation when no effective Python selection exists.
- [x] 2.2 Order manifest write/verification, preparation processes and persistence, Compose startup, and Odoo bootstrap in one command, and capture the concrete venv interpreter in the bootstrap prefix without rebuilding any step during execution.
- [x] 2.3 Preserve successful phase boundaries and recovery state across create, validation, compile, sync, origin/manifest/lock publication, Compose, and bootstrap failures, including truthful retry behavior and no dependency steps for empty inputs.
- [x] 2.4 Add SDK/init tests for fresh and existing projects, identical no-op/repair/resume, changed selector with confirmation, missing or damaged venv, empty inputs, failure injection at every phase, later bootstrap failure, retry, immutable plan/execution identity, redaction, and mutation-free dry-run.

## 3. Expose the opt-in CLI contract

- [x] 3.1 Add `init --venv PATH` to option binding and SDK delegation, route opted-in identical manifests through the composite command, and preserve every early-return and output path when the option is absent.
- [x] 3.2 Render requested selector, selected venv, concrete interpreter, ordered plan, refresh/recovery state, and sanitized failures through existing Rich/JSON/TOON envelopes without moving domain logic or process construction into the callback.
- [x] 3.3 Extend canonical CLI leaf/output/dry-run/security/process inventories and focused CLI matrices for omission compatibility, headless `--yes`, effective-Python errors, external and Compose modes, no-op refresh, repair, overwrite, all output transports, and standalone recovery availability.

## 4. Documentation and verification

- [x] 4.1 Update CLI-first and public SDK documentation so integrated init is the primary agent bootstrap, `--python` creation-selector versus actual-interpreter behavior is explicit, partial-success states are honest, and `prepare-venv` remains the refresh/recovery path.
- [x] 4.2 Extend the existing GitHub Windows preparation job and appropriate unit/integration fixtures to cover integrated init on Windows and POSIX without adding a new CI framework; keep Tester ownership read-only for these Implementer-authored checks.
- [x] 4.3 Add the focused test-repository E2E script and fixtures defined by `delivery-plan.md`, then run focused and repository tests, CLI/security/process inventories, formatting, Ruff, strict mypy, packaging checks, `git diff --check`, and strict OpenSpec validation while publishing runtime evidence only through Multica.
