## Delivery basis

- **Task / change:** `MYL-650` / `complete-post-restore-without-reimport`.
- **Approved code baseline:** `f0918da2d36b59c35cf0a949efc58744d1efb7f9`.
- **Implementation boundary:** extend the existing catalogue migration/restore binding, database-preparation command, typed models, module/admin-reset/default-switch composition, `db restore` leaf, focused tests, existing real-Odoo tier, and documentation. No new service, daemon, workflow engine, dependency, restore backend, public leaf, implicit module selection, or evidence runner.
- **Estimate authority:** planning-issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` are the only numeric total authority. This document records basis, confidence, and topology without duplicating totals.
- **Test repository:** use `TEST_REPOSITORY_URL` from the Multica project description. Its private value SHALL remain outside public Git and committed fixtures.
- **Planning boundary:** no product tests or E2E scenarios were run while preparing or re-estimating this plan.

## E2E decision

`e2e_required=true`.

The only live gap is the cross-component lifecycle: a real archive import must leave the same PostgreSQL database and managed filestore available after an administrator-reset failure, completion must publish that exact target without a second import, and repeat must no-op. Focused catalogue/preparation/CLI tests prove transition ordering, refusal, serialization, and output contracts more precisely; the existing source-backed real-Odoo CI tier provides the repeatable automated lifecycle. One fresh numbered-slot agent walkthrough supplies independent public-boundary inspection and mandatory changed-output captures, reusing the same lifecycle instead of adding a second local runner.

The slot SHALL be a fresh `odcli-N` checkout created by the existing Binta wrapper with the numbered project profile, clean reference checkout, exact candidate SDK SHA, unique database, free HTTP port, isolated `ODCLI_USER_ROOT`, and the dedicated PostgreSQL endpoint. It SHALL NOT use the reference checkout as its working checkout. Readiness requires exact candidate installation, healthy SQL, clean test-repository checkout, a supported archive, prior-default/database sentinels, deterministic reset-only failure injection, focused tests green, `/usr/bin/xcrun --find swift` successful, and the renderer self-test successful. A failed readiness gate consumes no live attempt.

The walkthrough is manual agent verification, so the workspace automatic-test time limit does not cap it. Automatic suites predicted to exceed or reaching that limit run only in GitHub Actions at the published exact revision and SHALL NOT be split to evade the limit.

## Minimal verification matrix

| Case | Requirement / risk | Cheapest sufficient automatic proof | Independent action | Assertions and evidence |
|---|---|---|---|---|
| `CHK-STATE` | truthful checkpoint; null/import-failed ineligibility; reset/publication transitions | Focused catalogue migration and preparation tests | Inspect the live failure binding through public `db list --tracked --format json`; never mutate the catalogue directly | Closed checkpoint values, prior default unchanged, no duplicate restored event, no secrets; sanitized JSON attachment only |
| `COMP-HAPPY` | explicit/no-module completion, reset, publication, repeat no-op, same target | One focused node in `tests/integration/real_odoo/test_focused_auth_archive.py`, selected by existing `real-odoo-tier.yml` | Perform the sole numbered-slot lifecycle and inspect public output, database/filestore sentinels, restore-event cardinality, and owned config | Same database identity/data and filestore, no second import/event, prior default until success, synchronized binding/config, repeat no-op |
| `COMP-FAIL` | module failure, foreign/null/stale evidence, concurrency | Focused catalogue/preparation/CLI tests expose exact action ordering and absent downstream calls | Review the immutable plans and captured call order; no duplicate live foreign-target probe | No reset/switch after module failure; refusal before mutation; lock serialization; stale latest sequence rejected |
| `CLI-SURFACE` | exclusions, confirmation, dry-run, format parity, changed summaries | Parametrized CLI/output-contract tests in existing offline suite | Capture equivalent baseline/candidate help and failure output plus candidate dry-run/success from the sole walkthrough | Exit codes, one JSON/TOON document, Rich labels, no ANSI/prompts/secrets; labelled UTF-8 text and PNG renders |
| `REG-DOC` | ordinary restore/reimport and public docs remain valid | Existing focused offline regressions plus same-SHA real-Odoo CI; no second local archive run after sufficient CI | Review affected diff, same-SHA CI result, and execute documented sequence during `COMP-HAPPY` | Ordinary import still once; guarded reimport unchanged; docs contain no private URL, `-u all`, or internal-call workaround |

Every delta scenario maps to one matrix row. Negative transition permutations remain lower-level tests because a second live attempt would not expose more precise ordering. `COMP-HAPPY` is the sole local lifecycle and is reused for docs and captures.

## Automatic verification ownership

The Implementer owns production changes and all GitHub tests, fixtures, workflow selectors, docs, and migration artifacts required by `tasks.md`. The Tester SHALL not edit them. Focused commands SHALL select the affected tests from:

```text
tests/unit/storage/test_backup_catalog.py
tests/unit/storage/test_catalog_migration.py
tests/unit/internal/test_database_preparation.py
tests/integration/test_reconcile_incomplete_lifecycle.py
tests/unit/test_cli_database.py
tests/unit/test_cli_output_modes.py
tests/integration/test_database_lifecycle.py
tests/unit/test_documentation_contract.py
```

The Implementer SHALL run only the nodes covering changed contracts before consolidated repository lint, type, package/compatibility, and strict OpenSpec gates. A passing same-SHA result is reused when inputs are unchanged. The new source-backed real-Odoo node runs in the existing CI tier; no new CI framework or local duplicate runner is allowed.

## Independent numbered-slot walkthrough

Before capture, the Tester SHALL create and deliver `tests/e2e/render_terminal.swift` in the Tester-owned GitLab MR. It SHALL use only macOS Command Line Tools Swift plus AppKit/CoreGraphics; accept `--input`, `--output`, `--label`, `--locale`, and `--columns`; require UTF-8 regular-file input; draw exact sanitized text with fixed monospaced font, colors, padding, and line metrics; emit one PNG; and fail rather than substitute or truncate. `--self-test ARTIFACT_DIRECTORY` SHALL render a built-in non-secret sample twice and check byte equality, PNG signature, nonzero dimensions, and fixed geometry. Readiness commands are `/usr/bin/xcrun --find swift` and `/usr/bin/xcrun swift tests/e2e/render_terminal.swift --self-test ARTIFACT_DIRECTORY`. Self-test outputs are disposable Multica evidence, not commits.

No lifecycle helper is planned: the existing Binta wrapper, public CLI, public tracked inventory, existing XML-RPC probe fixture, and direct sentinel inspection are sufficient. The renderer is Tester evidence tooling, not a product/WP deliverable.

The Tester performs one sequence:

1. Prove the prior default and create unique database and managed-filestore sentinels through supported fixture/public paths.
2. Run ordinary public restore with deterministic administrator-reset failure after completed import; confirm nonzero status, exact target, `reset_pending`, `admin_reset`, retained sentinels, and unchanged prior default.
3. Inspect `db restore --complete TARGET --update-module MODULE --dry-run`; confirm no restore/drop/archive action and exact module/reset/checkpoint/publication order.
4. Remove only the fixture compatibility fault and run completion with `--yes`; confirm the same target and sentinels, unchanged database identity and restore-event cardinality, completed reset, and synchronized manifest/generated config.
5. Repeat completion and confirm explicit no-op with no module, reset, import, event, or binding rewrite.

A pytest rerun is not the independent action. PASS records exact product/test revisions and material fixtures. A new candidate requires impact assessment and reruns only affected cases; unresolved impact remains unverified.

## Captures, publication, and cleanup

Mac evidence SHALL include actual UTF-8 text and PNG renders for changed help, restore failure, completion dry-run, and final summary. Baseline and candidate use equivalent sanitized fixtures, `LC_ALL=C`, width 120, explicit revision labels, and the same renderer commit. Render with `/usr/bin/xcrun swift tests/e2e/render_terminal.swift --input CAPTURE.txt --output RENDER.png --label REVISION --locale C --columns 120`; describe outputs as text renders, not terminal screenshots. State honestly that redirection does not prove ANSI color, TTY-only confirmation, spinners, animation, or cursor behavior.

The final Multica comment SHALL link the implementation PR, exact product/test commits, Tester MR/renderer file, relevant CI/files, and attach required images; record commands, exit codes, hashes, provenance, assertions, and manual observations; then read back comment and attachments. Text, PNGs, logs, diagnostic JSON, hashes, and assertion results are Multica-only evidence. No private hostname, credential, personal data, or complete config may enter public Git.

Cleanup uses the same Binta wrapper/profile to remove only the disposable slot and database while preserving the PostgreSQL cluster, implementation PR/branch/commits, Tester MR/branch/commits, and Multica evidence. It SHALL NOT stop Colima, alter reference checkouts, close/merge MRs, or delete branches without separate authority.

## Re-estimate basis and delivery topology

The repeated `ponytail full` audit found no removable product subsystem: the checkpoint closes proof of completed import; compare-and-append closes stale/concurrent mutation; the typed command reuses existing operations; and the existing CLI leaf is the smallest public surface. It removed duplicated verification work: no separate live foreign-target probe, no local archive regression after sufficient same-SHA CI, and no optional lifecycle helper.

The primary estimate is remaining active developer-hours for one experienced developer familiar with Python, Click, SQLite/Alembic, pytest, and this repository, without AI acceleration. It has two non-overlapping baskets:

- **Minimal implementation plus focused tests:** catalogue column/migration and append-only transition tests; truthful checkpoint capture and failure projection; typed completion planning/execution/revalidation with focused tests; existing CLI/output/docs changes with focused tests.
- **Mandatory developer delivery overhead:** one existing-tier real-Odoo regression and selector update; consolidated repository/OpenSpec gates; likely review repairs; exact-revision implementation evidence. Shared fixtures, investigation, and focused tests are counted only in their owning row, with no blanket test/review percentage.

Independent Tester renderer creation, numbered-slot walkthrough, captures, publication, cleanup, human review, approval queues, unattended CI time, and external outages are required by delivery but are separate-executor effort and are not duplicated in the developer-hour properties. The available Orbitalis comparison table is used only as qualitative calibration evidence that estimates must avoid repeated seam-design, test-matrix, evidence, and orchestration charges; its historical estimates are not treated as actual effort or quantitative calibration because confirmed actuals were not supplied. Confidence remains medium and quantitative calibration unavailable: the reuse seams and test analogues are inspectable, while catalogue transition composition and deterministic real-Odoo failure injection retain bounded uncertainty. Tests were not run for estimation. Changes to checkpoint semantics, arbitrary database selectors, automatic module selection, or partial-import resume invalidate the estimate.

The authoritative weighted property is at or below the single-package threshold. Therefore `delivery_mode=single_wp_no_dag`.

### `WP-MYL-650-01` — Complete post-restore without reimport

- **Task coverage:** `1.1`, `1.2`, `1.3`, `2.1`, `2.2`, `2.3`, `2.4`, `2.5`, `3.1`, `3.2`, `3.3`, `3.4`, `4.1`, `4.2`, and `4.3`, exactly once.
- **Deliverable:** one integrated change that records truthful post-import checkpoints, safely completes the exact proven target through existing operations and the existing `db restore` leaf, preserves ordinary restore/reimport behavior, and supplies focused, real-Odoo, documentation, and repository-gate evidence.
- **Owned responsibility scope:** catalogue schema/migration/restore binding; completion/checkpoint models and public environment command; dbprep checkpoint capture, validation, execution, module/reset/default-switch composition; CLI parsing/rendering and docs; all directly related unit/integration/real-Odoo tests, fixtures, snapshots, workflow selector, migration artifacts, and narrow integration repairs. Tester-owned renderer and runtime evidence are excluded.
- **Critical shared files:** `src/odoo_instance_sdk/storage/catalog/restore.py`, catalogue schema/migrations, dbprep materialization/coordinator/steps/models, public resource/model exports, `src/odoo_instance_sdk/commands/db.py`, affected tests, `README.md`, `docs/python-sdk.md`, `tests/integration/real_odoo/test_focused_auth_archive.py`, and `.github/workflows/real-odoo-tier.yml`.
- **Contract surface:** nullable closed checkpoint; exact latest-sequence/provenance compare-and-append; no duplicate restored event; immutable typed completion plan/result/failure; preparation-lock revalidation; ordered explicit modules; conditional reset; atomic publication; exact no-op; fixed CLI exclusions/confirmation/dry-run/output; no archive read, restore, drop, recreation, implicit modules, or secret leakage.
- **Definition of done / evidence:** all delta scenarios have focused coverage or the single existing-tier real-Odoo node; ordinary restore/reimport regressions pass; docs and migration contracts pass; lint, types, applicable package/compatibility checks, and strict OpenSpec pass on the consolidated exact revision; implementation PR/SHA, commands, exit codes, CI/file links, and independent numbered-slot observations/images are published and read back without runtime evidence in Git.

There are no `depends_on`, stages, parallel frontiers, or DAG orchestration. This single WP is the sole execution and WP-verification unit; full integrated review and E2E follow its accepted exact revision.
