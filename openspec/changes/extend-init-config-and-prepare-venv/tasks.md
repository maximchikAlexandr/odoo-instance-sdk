## 1. Extend stored init inputs and generated config

- [ ] 1.1 Add optional ordered `addon_roots` and `server_wide_modules` fields with TOML round-trip, path containment/deduplication, module-name validation, and focused compatibility/error tests.
- [ ] 1.2 Add repeated init option transport so omitted values retain stored tuples and supplied values replace them through the existing immutable plan, confirmation, dry-run, and output paths.
- [ ] 1.3 Overlay resolved addon paths and modules in the existing generated-config renderer and include them in independent stale-config repair without changing source preservation, secrets, permissions, external mode, or bootstrap ordering.
- [ ] 1.4 Extend init/generated-config tests and the existing real-Odoo CI fixture for ordered values, absent/present source config, repair after stored-value change, unchanged retries, and mutation-free dry-run.

## 2. Share complete dependency discovery

- [ ] 2.1 Complete one deterministic private discovery helper for recursive addon modules, module/immediate-parent requirements, Odoo-bin sibling requirements, explicit inputs, canonical first-occurrence deduplication, and typed missing-explicit failures.
- [ ] 2.2 Make managed checkout and sync invoke the helper after worktree rebasing while preserving hash-lock bypass, owned/reused install modes, pins, uv locks, and retained-lock failure behavior.
- [ ] 2.3 Add focused discovery and managed-environment tests for nesting, shared parents, absent candidates, explicit failures, symlink duplicates, deterministic order, rebasing, checkout/sync parity, and unchanged hash-lock behavior.

## 3. Prepare the primary checkout venv

- [ ] 3.1 Add one public immutable primary-venv command builder and convenience operation that resolve the venv/interpreter, reuse existing uv prepared steps and locking, jointly process shared inputs, and atomically persist the interpreter only after success.
- [ ] 3.2 Register SDK-first `odcli prepare-venv`, extend canonical CLI/output/dry-run inventories, and preserve current Rich/JSON/TOON, redaction, typed-error, confirmation, and package-startup contracts.
- [ ] 3.3 Add SDK/CLI tests for missing and existing venvs, empty and non-empty inputs, POSIX/Windows interpreter projection, step failures, stale manifest, plan/execution identity, redaction, and mutation-free dry-run.

## 4. Documentation and verification

- [ ] 4.1 Update user and SDK documentation for fields, init options, config precedence/repair, discovery order, `prepare-venv`, and the boundary that concrete test-repository paths remain caller inputs.
- [ ] 4.2 Add the planned combined test-repository E2E script and fixtures; keep runtime logs, diagnostic data, hashes, and images in Multica evidence rather than Git.
- [ ] 4.3 Run focused and repository unit/integration tests, real-Odoo CI coverage, canonical CLI/security/process inventories, formatting, Ruff, strict mypy, packaging checks, `git diff --check`, and strict OpenSpec validation.
