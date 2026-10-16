# Delivery plan

## Planning baseline

- Task key: `MYL-491`.
- OpenSpec change: `extend-init-config-and-prepare-venv`.
- Approved implementation base: `origin/main` at the base recorded by the planning publication commit.
- Test repository: resolve `test_repository_url` from the Multica project description at execution time; do not copy private hostnames into public Git content.
- Work packages are stable implementation ownership units, not live status or assignee records. Responsibility scopes include directly related tests, fixtures, snapshots, documentation, and service files unless an active sibling explicitly owns them.

## Estimate contract

- Authoritative totals are stored only in the planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; they are intentionally not duplicated here.
- Basis: remaining full scope for one experienced developer familiar with the Python/Click/uv stack, measured in active developer-hours including investigation, implementation, required tests, review fixes, and attended verification.
- Confidence: medium. The affected implementation and close local analogues were inspected, but init's multi-artifact atomicity, cross-platform venv behavior, and shared dependency discovery create bounded integration uncertainty.
- Calibration: uncalibrated; no comparable executor timing history was supplied. Tests were not run for the estimate.
- Evidence: current source revision; `ProjectConfig` manifest round-tripping; `init_project_command()` and Compose/bootstrap steps; `internal/generated_config.py`; `EnvironmentResource.sync_python_command()`; dependency, init, generated-config, environment-Python, CLI characterization, process/security, packaging, and CI paths.
- Main coupling risks are init retry/confirmation semantics, canonical rebasing across primary and managed worktrees, platform-specific interpreter paths, and the broad canonical CLI inventory. External review queues and unattended CI/Odoo startup waits are excluded from active effort.

## Delivery topology

The authoritative headline estimate property is above the multi-package threshold. The graph has a real parallel frontier at stage 2: init/config, managed-environment reuse, and primary-venv preparation consume the same completed foundation but own disjoint production and test concerns.

| Work package | Stage | Direct `depends_on` | Task coverage |
| --- | ---: | --- | --- |
| WP-01 | 1 | none | 1.1, 3.1 |
| WP-02 | 2 | WP-01 | 1.2, 2.1, 2.2, 2.3 |
| WP-03 | 2 | WP-01 | 3.2, 3.3 |
| WP-04 | 2 | WP-01 | 4.1, 4.2, 4.3 |
| WP-05 | 3 | WP-02, WP-04 | 5.1, 5.2, 5.3 |
| WP-06 | 4 | WP-03, WP-05 | 6.1, 6.2, 6.3 |

Stage mapping for child materialization: `WP-01 -> 1`, `WP-02 -> 2`, `WP-03 -> 2`, `WP-04 -> 2`, `WP-05 -> 3`, `WP-06 -> 4`. Stages are consecutive topological layers, not WIP limits.

### WP-01 — Project inputs and dependency-discovery foundation

- Stage: 1
- `depends_on`: none
- Independent deliverable: backward-compatible manifest fields plus one deterministic, typed dependency-input discovery contract reusable by every later caller.
- Owned responsibility scope: `ProjectConfig` parsing, validation, serialization, project-relative path helpers, the focused internal dependency discovery helper, and directly related fixtures/tests. Critical shared files include `src/odoo_instance_sdk/project.py` and the discovery module it establishes.
- Contract surface: ordered `addon_roots`; ordered `server_wide_modules`; omission/replacement-capable SDK values; immutable ordered canonical dependency inputs; typed missing-explicit-file error; execution-root rebasing input.
- Definition of done / evidence: existing manifests round-trip unchanged; containment, module validation, canonical dedupe, recursive ordering, source categories, and explicit failure cases pass focused unit tests; public plans can project the returned tuple without secrets.
- Parallel-safety rationale: this serial predecessor owns the shared data/discovery contracts. Stage-2 packages consume them read-only and do not revise their shape.

### WP-02 — Init and generated project config

- Stage: 2
- `depends_on`: WP-01
- Independent deliverable: init resolves stored/explicit settings and atomically plans/writes a complete project Odoo config before existing Compose/bootstrap follow-ups.
- Owned responsibility scope: public/internal project-init flow, generated-config renderer/writer, init config-source resolution, and init/generated-config tests. Critical shared files include `src/odoo_instance_sdk/project_init.py`, `src/odoo_instance_sdk/internal/project_init.py`, and `src/odoo_instance_sdk/internal/generated_config.py`.
- Contract surface: init config action ordering; source-option preservation and project overlay precedence; manifest/config replacement set; mode-`0600` target; idempotent lifecycle reuse; dry-run projection.
- Definition of done / evidence: absent/present source, addon/module order, secrets/runtime values, confirmation/refusal, identical retry, incomplete Compose retry, write failure, no mutation in dry-run, and bootstrap-before/after ordering tests pass.
- Parallel-safety rationale: owns init and config modules only. It consumes WP-01 contracts and is write-disjoint from managed environment and project-venv implementations.

### WP-03 — Managed environment dependency reuse

- Stage: 2
- `depends_on`: WP-01
- Independent deliverable: checkout-created and synchronized managed environments use the shared dependency set without changing ownership, locking, pin, or retained-lock semantics.
- Owned responsibility scope: environment checkout dependency planning, environment settings/sync, worktree rebasing adapters, and managed-environment dependency tests. Critical shared files include `resources/environment/checkout_*`, `resources/environment/settings.py`, and their focused tests.
- Contract surface: rebase-to-worktree discovery context; owned `uv pip sync`; reused `uv pip install`; normal/upgrade pin behavior; canonical Python lock; prior-lock fallback.
- Definition of done / evidence: checkout and later sync expose equal inputs for one snapshot; owned/reused modes retain current argv and cleanup behavior; missing explicit inputs fail before mutation; compile failure preserves a valid lock; regression matrices pass.
- Parallel-safety rationale: does not edit init/config, primary project-venv, or CLI registration files. Any WP-01 contract change requires predecessor revision rather than a sibling edit.

### WP-04 — Primary checkout venv SDK

- Stage: 2
- `depends_on`: WP-01
- Independent deliverable: public immutable plan/execute operations create or refresh the selected project venv and persist its interpreter only after success.
- Owned responsibility scope: new public project-venv operation, platform-aware interpreter selection, prepared uv steps, manifest stale-plan/update transaction, locking, exports, and SDK tests. Critical shared files are the new project-venv module and public package/client export points assigned exclusively here.
- Contract surface: `prepare_project_venv_command()` and convenience operation; selected Python/venv/interpreter/input projection; optional venv-create step; joint compile/install; post-success atomic manifest update.
- Definition of done / evidence: missing/existing/empty-input paths, POSIX/Windows interpreter resolution, all process failures, stale manifest, lock identity, redaction, plan/execution equality, and mutation-free dry-run pass focused tests.
- Parallel-safety rationale: owns a new public operation and export seam, treats discovery as read-only, and does not edit init or managed-environment modules.

### WP-05 — CLI surfaces and canonical inventories

- Stage: 3
- `depends_on`: WP-02, WP-04
- Independent deliverable: transport-only init flags and `prepare-venv` CLI surface with complete Rich/JSON/TOON, dry-run, error, help, SDK mapping, and package-startup contracts.
- Owned responsibility scope: init option registration/callback transport, top-level prepare command registration, canonical CLI leaf/output/dry-run inventories, and CLI/output/security/startup tests. Critical shared files include `commands/cli_parts/*`, CLI composition, and the canonical test-owned inventory.
- Contract surface: repeatable ordered flags; `prepare-venv --python --venv`; public SDK primitive mapping; output envelope and exit-code behavior; no CLI-local uv or discovery logic.
- Definition of done / evidence: help tree, invocation mapping, machine parity, no-prompt/no-ANSI contracts, typed errors, redaction, dry-run, confirmation, lazy startup, and installed-package tests pass.
- Parallel-safety rationale: starts after both public operations are stable and is the sole owner of shared CLI registration/inventory files.

### WP-06 — Integrated verification, CI fixtures, and documentation

- Stage: 4
- `depends_on`: WP-03, WP-05
- Independent deliverable: complete CI and documentation coverage plus a verified implementation candidate ready for independent local E2E execution under the contract below.
- Owned responsibility scope: cross-capability CI fixtures, documentation, repository-wide verification commands, coverage reconciliation, and planning-artifact implementation checkboxes/evidence updates when authorized. Product fixes discovered here return to the owning WP rather than expanding this package's write scope.
- Contract surface: complete scenario-to-test map; existing GitHub job integration; user/SDK docs; final static/type/package/security/process/OpenSpec gates; Tester handoff inputs.
- Definition of done / evidence: every delta scenario maps to a passing test or the selected local E2E case; focused and repository gates pass; docs match public surfaces; diff contains no new runner/resolver/catalog/config subsystem; command/exit ledger and external-prerequisite notes are ready for handoff.
- Parallel-safety rationale: sole convergence package after the stage-2 managed-environment path and stage-3 CLI path complete; no active sibling shares its integration files.

## Coverage proof

All tasks in `tasks.md` are assigned exactly once: WP-01 covers 1.1 and 3.1; WP-02 covers 1.2 and 2.1–2.3; WP-03 covers 3.2–3.3; WP-04 covers 4.1–4.3; WP-05 covers 5.1–5.3; WP-06 covers 6.1–6.3. The stage-2 frontier contains three independent packages, so the graph is not a linear chain.

## E2E decision and verification contract

`e2e_required=true`. Unit and integration tests can prove manifest serialization, traversal, argv, redaction, failure, and step ordering, but they cannot fully prove that a generated no-source project config is accepted at a real Odoo boundary or that a real uv-created venv jointly resolves and installs the discovered dependency set. Two focused local cases close only those gaps; the remaining scenarios stay in lower-level GitHub CI coverage.

Planning does not execute tests or create evidence. Tester SHALL use a dedicated `odcli-N` slot with its own checkout, database when needed, port, XDG/catalog roots, and venv; the reference checkout SHALL remain untouched.

| Case ID | OpenSpec requirement/scenario | Verification gap / local E2E decision | OpenAPI | Local script | Independent Tester steps | CI disposition | Assertions | Artifacts / images |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| E2E-01 | Project config generation: no source, preserved source settings, addon order, modules, re-init reuse | Required: only a real Odoo process proves the generated config is consumable and init artifacts precede bootstrap | N/A | `scripts/myl491_init_config_e2e.py` in the test repository | In a fresh dedicated slot, capture baseline help/error; initialize equivalent no-source and source-config fixtures through the public CLI; inspect `project.toml` and `odoo.conf`; start the candidate with the generated config on the slot port; repeat identical init; perform failure-safe shutdown/cleanup | GitHub unit/integration tests cover exact values, permissions, atomicity, ordering, confirmation, and dry-run; local case covers real config consumption only | Exit codes are expected; config mode is `0600`; addon/module order and resolved runtime values match; source custom key remains; Odoo reaches readiness; identical init does not recreate completed resources | Sanitized command/exit ledger, actual config excerpts, readiness observation, baseline/candidate text, `myl491-init-before.png`, `myl491-init-after.png` |
| E2E-02 | Primary venv preparation: missing/existing venv, shared discovery, plan inputs | Required: mocked process tests cannot prove real uv joint resolution, interpreter selection, and refresh in the test repository layout | N/A | `scripts/myl491_prepare_venv_e2e.py` in the test repository | Create nested addon modules with module/parent requirements plus Odoo-core and duplicate explicit inputs; run dry-run; run preparation into a fresh slot venv; verify imports/interpreter/manifest; change a fixture pin and rerun against the existing venv; cleanup the venv only after evidence capture | GitHub tests cover discovery order, missing explicit errors, canonical dedupe, worktree rebasing, locks, failures, ownership modes, and Windows/POSIX paths | Dry-run mutates nothing; plan lists each canonical input once in order; created interpreter is inside selected venv; installed versions/imports match the joint resolution; second run reuses venv and updates dependencies; manifest changes only after success | Sanitized plan/result text, interpreter and package assertions, file/commit links, `myl491-venv-before.png`, `myl491-venv-after.png` |
| CI-01 | Stored config inputs and all validation/re-init scenarios | No local gap: deterministic pure config/manifest behavior is sufficiently proven below the process boundary | N/A | None | Inspect focused test names and resulting manifest fixtures; report findings without editing them | Extend project/config/init unit matrix in the existing GitHub unit job | Round-trip, order, containment, omission vs replacement, typed failures, unchanged artifacts | Test/commit/file links in Multica only |
| CI-02 | Dependency discovery negative/dedup/rebase scenarios and managed sync parity | No local gap beyond E2E-02: deterministic filesystem fixtures and prepared-command assertions are sufficient | N/A | None | Inspect fixture topology and prepared plans for project and managed worktrees | Extend dependency-sync and environment Python tests in the existing GitHub unit job | Arbitrary depth, shared parent, absent candidate, explicit failure, symlink dedupe, deterministic order, rebase, parity, retained lock | Test/commit/file links in Multica only |
| CI-03 | SDK failure, dry-run, atomic persistence, locks, ownership, plan identity | No local gap: injected failures and fake executor provide stronger deterministic coverage | N/A | None | Review failure matrix and verify every planned mutation has a failing-step case | Extend SDK/process/security tests in existing GitHub jobs | No mutation on dry-run/failure, stale-plan rejection, lock identity, redaction, install/sync ownership semantics | Test/commit/file links in Multica only |
| CI-04 | CLI init options and `prepare-venv` output/error contracts | Visible output requires images, but behavior is otherwise sufficiently covered by characterization tests | N/A | Reuse E2E-01/E2E-02 capture paths | Compare baseline and candidate actual text at fixed locale and 120-column width; render captured text on macOS using bundled `scripts/render_terminal.swift` from `openspec-e2e-testing` | Extend canonical leaf/output/dry-run/help/package-startup matrices | Rich/JSON/TOON parity, truthful dry-run, no traceback/ANSI in machine output, SDK primitive mapping, correct exit codes | Raw actual text plus before/after PNG attachments; renders labelled as renders, with ANSI/TTY/progress limitations stated |

### Script, evidence, and cleanup contract

- The two scripts and necessary ordinary fixtures SHALL be committed to a dedicated branch/MR in `test_repository_url`; test-code commits SHALL be separate from any functional commit genuinely created by the utility under test. No synthetic second commit is required when the verified function creates none.
- Tester SHALL record candidate product SHA, test-repository test SHA, baseline SHA, exact commands, exit codes, assertions, and sanitized provenance. Actual logs, diagnostic JSON, hashes, images, and command output SHALL be attached only to the final Multica comment, not committed to GitLab, unless JSON is itself a functional product output under test.
- Baseline and candidate captures SHALL use equivalent fixtures, English tool-authored explanation, the tested output's original language, fixed locale, fixed width, and reset state between runs. Redirected text does not prove ANSI/TTY/progress behavior; unsupported states SHALL be reported explicitly.
- Tester SHALL publish MR/commit/file links and all required image attachments in the final Multica comment and perform read-back. Missing required capture or unreadable attachment prevents complete verification.
- Cleanup SHALL stop processes and remove the dedicated slot database, port binding, temporary XDG/catalog roots, and venv while preserving the evidence MR, branch, commits, and Multica attachments. The MR SHALL not be closed or merged and the branch SHALL not be deleted without separate authority.
