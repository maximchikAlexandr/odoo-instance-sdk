## 1. Persist project configuration inputs

- [ ] 1.1 Add validated ordered `addon_roots` and `server_wide_modules` fields to `ProjectConfig`, including TOML load/write compatibility, canonical addon-root containment and deduplication, module-name validation, and focused round-trip/error tests.
- [ ] 1.2 Extend init option resolution so omitted lists inherit existing manifest values while explicit lists, including empty replacements at the SDK boundary, replace them without weakening current completeness, overwrite, or secret checks.

## 2. Generate the project Odoo configuration during init

- [ ] 2.1 Extend the existing config renderer with explicit ordered addon-path and server-wide-module overlays while preserving source settings, project-resolved database/network/runtime/secret precedence, atomic mode-`0600` writes, and worktree rebasing.
- [ ] 2.2 Add generated-config planning and execution to the public init command before Compose/database bootstrap, treating the manifest and config as one confirmed replacement set and preserving idempotent resource reuse and retry postconditions.
- [ ] 2.3 Add init/config unit and integration tests for absent and present source configs, value precedence, path order, modules, bootstrap step order, identical retries, non-identical confirmation/refusal, atomic failure behavior, and mutation-free dry-run.

## 3. Unify dependency input discovery

- [ ] 3.1 Implement one deterministic dependency-input discovery helper covering recursive addon modules, module and immediate-parent requirements, Odoo-bin sibling requirements, explicit inputs, canonical first-occurrence deduplication, and typed missing-explicit-input errors.
- [ ] 3.2 Rebase repository-local discovery inputs for managed worktrees and make `env checkout --create-venv` and `env sync` consume the shared result without changing owned/reused install modes, pin preservation/upgrade, locks, or retained-lock failure behavior.
- [ ] 3.3 Add focused discovery and managed-environment tests for arbitrary nesting, shared parents, absent candidates, explicit errors, symlink/canonical duplicates, deterministic order, worktree rebasing, and checkout/sync parity.

## 4. Prepare the primary checkout virtual environment

- [ ] 4.1 Add public immutable project-venv prepare and command-builder operations that resolve the venv/interpreter cross-platform, plan optional `uv venv`, compile all discovered inputs together, install into the selected venv, serialize uv writes, and publish a redacted JSON-safe plan.
- [ ] 4.2 Make successful preparation atomically persist the venv interpreter in `project.toml`; ensure every creation/compile/install/validation failure leaves the manifest unchanged and dry-run performs no filesystem or process mutation.
- [ ] 4.3 Add SDK tests for missing/existing venvs, empty/non-empty input sets, process failure at each step, manifest stale-plan/atomicity behavior, locking, plan/execution identity, redaction, and Windows/POSIX interpreter paths.

## 5. Expose SDK-first CLI workflows

- [ ] 5.1 Add repeatable ordered `init` addon-root and server-wide-module options and delegate them through the existing public init command and output/confirmation pipeline.
- [ ] 5.2 Register top-level `odcli prepare-venv` with required Python and venv inputs, current dry-run and Rich/JSON/TOON envelopes, typed error rendering, and public SDK delegation only.
- [ ] 5.3 Update the canonical CLI leaf/output/dry-run inventories and parameterized help, success, failure, redaction, confirmation, SDK-boundary, and package-startup tests for both surfaces.

## 6. Verification and documentation

- [ ] 6.1 Add or extend GitHub CI-executed unit/integration fixtures that cover every delta-spec scenario, including genuine subprocess-boundary fakes, generated file assertions, and regression coverage for existing init and managed environment sync behavior.
- [ ] 6.2 Update user and SDK documentation with manifest fields, init flags, generated-config precedence/location, dependency discovery order, `prepare-venv` examples, dry-run behavior, and the boundary that Binta-specific paths remain wrapper inputs.
- [ ] 6.3 Run focused tests, CLI characterization/security/process inventories, formatting, Ruff, strict mypy, packaging tests, the repository PR gate, `git diff --check`, and strict OpenSpec validation; record external Odoo/Docker prerequisites separately from regressions.
