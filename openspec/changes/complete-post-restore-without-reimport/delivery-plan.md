## Delivery basis

- **Task / change:** `MYL-650` / `complete-post-restore-without-reimport`.
- **Approved code baseline:** `f0918da2d36b59c35cf0a949efc58744d1efb7f9` (`origin/main` at planning start).
- **Implementation boundary:** existing backup catalogue/Alembic migrations, database preparation, typed SDK command/result models, module/admin-reset/default-switch composition, the existing `db restore` CLI leaf, documentation, and focused tests. No new service, daemon, workflow engine, dependency, restore backend, implicit module selection, or Supervisor integration.
- **Estimate authority:** planning-issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` are the only numeric total authority. This document records confidence, calibration, assumptions, evidence, and topology without duplicating those totals.
- **Test repository:** use the `TEST_REPOSITORY_URL` supplied by the Multica project description. Its private hostname/value SHALL remain outside the public GitHub repository, command captures, and committed fixtures.
- **Planning boundary:** no product tests or E2E scenarios were run while preparing this plan.

## E2E decision

`e2e_required=true`.

The remaining verification gap is the cross-component lifecycle: an Odoo archive must import into PostgreSQL and the managed filestore, administrator reset must fail after that boundary, the prior project default must remain active, and public completion must repair and publish that exact target without a second import. Mocked catalogue/process tests can prove ordering and refusal rules but cannot prove Odoo schema interaction, filestore continuity, generated config behavior, or absence of a second live restore. One focused local agent-driven walkthrough closes that gap; the existing source-backed real-Odoo CI tier supplies the repeatable automatic regression.

The live case uses a fresh numbered `odcli-N` slot created by the Binta wrapper with the numbered-project profile, clean reference checkout, candidate SDK SHA, unique database, free HTTP port, isolated `ODCLI_USER_ROOT`, and the persistent dedicated PostgreSQL endpoint. It SHALL NOT use the reference checkout as its working checkout. The readiness gate is: exact candidate SHA installed, cluster healthy and SQL-ready, clean test-repository checkout, captured prior default/database sentinel, supported archive, failure injection verified to affect only administrator reset, and focused offline tests green. A failed readiness gate consumes no live attempt.

The walkthrough is agent-driven manual verification, not an unattended automatic suite, so the workspace ten-minute automatic-test limit does not cap it. The new GitHub source-backed real-Odoo pytest node is automatic; it runs only in published CI because the applicable tier exceeds the local limit. No automatic suite is split to evade that limit.

## Verification matrix

| Case ID | OpenSpec requirement / scenario coverage | Verification gap / local E2E decision | Public boundary | Local script / independent agent steps | CI disposition | Assertions | Artifacts / images |
|---|---|---|---|---|---|---|---|
| `CHK-STATE` | truthful checkpoint: reset failure, publication failure with/without reset, incomplete import | Lower-level catalogue/preparation tests are sufficient for all transition branches; live case also observes `reset_pending` | Catalogue migration and typed preparation failure | No standalone local script; inspect isolated catalogue through public `db list --tracked --format json` plus bounded test helpers, never direct mutation | Extend offline catalogue/migration/preparation tests | Null legacy/import-failed rows are ineligible; closed values only; no duplicate restored event; prior default unchanged; no secrets | Focused command results and exact SHA in Multica; diagnostic JSON remains attachment-only |
| `COMP-HAPPY` | explicit modules, no-module path, reset completion, publication, repeat no-op | Live gap: same DB/filestore and no second import across a real failed reset | `odcli db restore --complete` | In fresh `odcli-N`, create sentinel DB/file state; invoke ordinary restore with deterministic reset failure; capture target/checkpoint; fix compatibility with one explicit module if fixture requires it; run completion; query sentinel through public Odoo/XML-RPC or existing fixture probe; rerun completion | Add focused node to `tests/integration/real_odoo/test_focused_auth_archive.py` and include it in `real-odoo-tier.yml` targeted selection | Target OID/name and sentinel survive; managed filestore sentinel survives; restore-call/event count does not increase; prior default remains until completion; final manifest/generated config select target; repeat is no-op | `comp-failure.txt`, `comp-success.txt`, `comp-repeat.txt`, sanitized state projection, MR/commit/file links; PNG renders for changed summaries |
| `COMP-FAIL` | module failure, foreign/changed evidence refusal, concurrency serialization | Deterministic unit/integration commands expose ordering and missing calls more precisely; one manual foreign-target probe confirms public refusal | Public SDK completion command and CLI | No new runner; independent Tester inspects captured immutable plan, process/action order, catalogue calls, and absence of downstream calls for each refusal fixture | Existing offline tests; focused tests in database preparation, catalogue, and CLI modules | Module failure runs neither reset nor switch; null/foreign/stale identity mutates nothing; lock serializes; changed latest sequence fails stale | Sanitized focused outputs and call/order observations in Multica only; no decorative images |
| `CLI-SURFACE` | option exclusivity, dry-run, actionable failure, format parity | Visible help/failure/success summaries change; actual Mac captures are required | Existing `db restore` Click leaf | On baseline and candidate, fixed `LC_ALL=C`, width 120: capture `db restore --help`; on equivalent failure fixture capture ordinary restore failure; invoke candidate completion dry-run/success and baseline same argv usage failure; render actual captured text with bundled `openspec-e2e-testing/scripts/render_terminal.swift` | Parametrized CLI/output-contract tests in `offline-tests`; no new job | Mutual exclusions exit 2 before access; machine confirmation precedes resolution; JSON/TOON decode equally; one stdout document; no ANSI/prompts/secrets; Rich labels match checkpoint/stage | Baseline/candidate `.txt` plus `help-*.png`, `failure-*.png`, and `completion-*.png`, labelled as text renders with revision/locale/width; state ANSI/TTY/progress limits honestly |
| `REG-RESTORE` | ordinary restore/reimport behavior unchanged | Existing real-Odoo archive tests plus focused offline regressions are sufficient | Ordinary UUID/file restore and guarded incomplete retry | Independently inspect affected diff and run existing focused archive nodes on exact candidate when within limit; do not repeat a passing live case on unchanged revision | Existing `real-odoo-smoke`, targeted/full real-Odoo tier, offline tests | Normal success still imports once and switches after postconditions; incomplete import remains guarded-reimport only; source-neutral provenance works | CI links and exact test node results in Multica |
| `DOC-GATES` | documentation and repository delivery tasks | Review/check gap only; no live system needed | README, SDK reference, OpenSpec, repository gates | Tester follows documented sequence against the live slot, then reviews docs for private URLs, raw secrets, `-u all`, or internal-function instructions | Existing lint, types, offline, compatibility, package, and strict OpenSpec checks | Public sequence is executable; docs describe legacy null limitation and safe cleanup; all checks tied to exact SHA | PR/SHA/file links, commands and exit codes in Multica; no generated logs committed |

Every affected delta scenario maps to a case above. `COMP-HAPPY` is the sole local live lifecycle. Publication-failure variants, malformed evidence, and concurrency use focused lower-level tests because they expose exact mutation ordering without spending another Odoo attempt. Existing successful restore and guarded reimport remain regressions, not duplicated live scenarios.

## Automatic verification ownership

The Implementer owns production changes and all GitHub tests, fixtures, workflow selectors, documentation, and generated migration artifacts required by `tasks.md`. The Tester SHALL not edit those files. Required focused coverage includes:

```text
uv run pytest -q -o addopts='' tests/unit/storage/test_backup_catalog.py tests/unit/storage/test_catalog_migration.py
uv run pytest -q -o addopts='' tests/unit/internal/test_database_preparation.py tests/integration/test_reconcile_incomplete_lifecycle.py
uv run pytest -q -o addopts='' tests/unit/test_cli_database.py tests/unit/test_cli_output_modes.py tests/integration/test_database_lifecycle.py
uv run pytest -q -o addopts='' tests/unit/test_documentation_contract.py
make lint
make types
openspec validate complete-post-restore-without-reimport --strict
```

The exact focused nodes may be narrowed by the Implementer while preserving all task/scenario coverage. If an automatic local run is predicted to exceed ten minutes or reaches ten minutes, stop only that run-owned test process, record the partial result, and use the corresponding GitHub Actions job at the published exact revision. The source-backed real-Odoo scenario runs in existing CI and produces the repository's normal bounded secret-safe evidence archive; raw CI evidence is not copied into Git.

## Independent numbered-slot walkthrough

The Tester uses the project-described Binta test repository and existing `scripts/agent_checkout.py create` with both `--project NUMBERED_PROJECT_PROFILE` and `--reference-checkout CLEAN_REFERENCE_CHECKOUT`, plus a free `--http-port`, unique `--target-database`, and exact `--sdk-sha`. `NUMBERED_PROJECT_PROFILE` resolves to the numbered profile supplied by the runtime; the clean reference resolves to its dedicated reference checkout. The Tester records the allocated `odcli-N`, checkout path, database, port, test-repository SHA, and product SHA only in Multica evidence.

No new local orchestration framework is required. The necessary scripts are the existing Binta slot wrapper, the repository's existing XML-RPC probe fixture for restored state, and bundled `openspec-e2e-testing/scripts/render_terminal.swift`. If a small assertion helper is needed for reproducibility, it SHALL be added in the Tester-owned GitLab MR as `tests/e2e/myl650_post_restore_completion.py` with isolated fixtures and no secrets/private hostnames; its functional result and script changes remain ordinary MR commits. It SHALL accept slot paths, SHAs, port, database, artifact directory, locale, width, and renderer path as arguments rather than embedding environment values.

The walkthrough performs these independent actions:

1. Prove the prior default and write a unique database sentinel plus managed-filestore sentinel through supported fixture/public paths.
2. Start ordinary public restore with a deterministic test-only administrator-reset failure after completed import; confirm nonzero status, exact target, `reset_pending`, `admin_reset`, retained sentinels, and unchanged prior default.
3. Inspect `db restore --complete TARGET --update-module MODULE --dry-run`; confirm no restore/drop/archive step and exact explicit module/reset/checkpoint/publication order.
4. Remove only the fixture's compatibility fault and run the same public command with `--yes`; confirm the exact target and sentinels, unchanged OID/restore event cardinality, completed reset, and synchronized manifest/generated config.
5. Repeat completion and confirm explicit no-op with no module, reset, import, event, or binding rewrite.
6. Attempt completion of one unproven database name and confirm refusal before process/config mutation.

A second invocation of pytest is not the independent check. The Tester SHALL inspect actual public output, target/database/filestore state, restore-event cardinality, and owned config bytes. PASS is recorded only with exact product revision and fixtures. On a new candidate revision, assess impact and rerun only affected cases; unresolved impact leaves the case unverified.

## Captures and publication

Because CLI help, restore failure details, completion dry-run, and final summary change, Mac evidence SHALL include actual UTF-8 text and PNG renders. Baseline and candidate use equivalent sanitized fixtures, `LC_ALL=C`, width 120, and explicit revision labels. Render text with bundled `scripts/render_terminal.swift`; describe the PNGs as renders, not terminal-window screenshots. Redirection does not prove ANSI color, TTY-only confirmation, spinners, progress animation, or cursor behavior, and the Tester SHALL state those limits.

The Tester-owned GitLab MR remains open and preserves any assertion helper/fixture commits. The final Multica comment SHALL link that MR when created, the implementation PR, exact product and test commits, relevant files, and image attachments; record commands, exit codes, CI/focused results, hashes, provenance, and manual observations; then read back the comment and attachments. Actual text, PNGs, logs, diagnostic JSON, hashes, and assertion results are Multica-only evidence and SHALL NOT be committed to GitHub or GitLab. No private hostname, credential, personal data, or complete config content may appear in public Git.

Cleanup uses the same Binta wrapper and numbered project profile to remove the disposable slot and its database while preserving the dedicated PostgreSQL cluster, implementation PR/branch/commits, Tester MR/branch/commits, and Multica evidence. Cleanup SHALL NOT stop Colima, touch reference checkouts, close/merge MRs, or delete branches without separate authority.

## Estimate basis and delivery topology

The authoritative weighted, optimistic, and pessimistic totals are stored only in the planning-issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`. Property read-back was verified after estimation. The weighted property is above the multi-package threshold.

The estimate covers remaining work from approved base `f0918da2d36b59c35cf0a949efc58744d1efb7f9` to all OpenSpec acceptance criteria for one experienced developer familiar with Python, Click, SQLite/Alembic, pytest, and the repository's command boundary, without AI acceleration. It includes catalogue/migration work, checkpoint integration, typed SDK planning/execution, CLI/output contracts, focused and real-Odoo tests, documentation, review repairs, and repository gates. Unattended CI time, approval queues, external outages, and independent human review time are excluded from developer-hours.

Confidence is medium: the affected source, tests, migration patterns, and close restore/module/default-switch analogues are directly inspectable, but safe append-only transition composition and deterministic real-Odoo failure injection carry bounded integration uncertainty. Calibration is unavailable because no comparable historical timing series was supplied. Material coupled risks are catalogue migration compatibility, nested lock/process composition, stale binding checks across append-only rows, and real-Odoo fixture stability. A change to checkpoint semantics, permission for arbitrary database selectors, automatic module selection, or a requirement to resume partial imports invalidates the estimate. Tests were not run for estimation.

`delivery_mode=dag`.

The graph has one genuine parallel frontier at stage 2. `WP-MYL-650-02` and `WP-MYL-650-03` consume the stable typed/catalogue contract from stage 1 but own disjoint production and test write-zones. Integration and live regression wait for both. Stages are contiguous topological layers, not WIP limits.

### Stage mapping

| Work package | Stage | Direct `depends_on` |
|---|---:|---|
| `WP-MYL-650-01` | 1 | none |
| `WP-MYL-650-02` | 2 | `WP-MYL-650-01` |
| `WP-MYL-650-03` | 2 | `WP-MYL-650-01` |
| `WP-MYL-650-04` | 3 | `WP-MYL-650-02`, `WP-MYL-650-03` |

### `WP-MYL-650-01` — Catalogue and typed completion foundation

- **Task coverage:** `1.1`, `1.2`, `1.3`, `2.2`.
- **Deliverable:** additive catalogue migration and closed checkpoint model; latest-binding projection and compare-and-append transitions; typed completion plan/result/failure models; public inspectable `complete_restore_command()` contract with inert planning and stable child-operation seams.
- **Owned responsibility scope:** catalogue migration/schema definitions and restore mixin; restore/checkpoint enums and completion model types; public environment/database-preparation resource interface and command construction contract; focused catalogue/migration/model tests; directly related fixtures and migration snapshots. It does not implement checkpoint capture in the restore callback, final completion execution, CLI parsing/rendering, or live tests.
- **Critical shared files:** `src/odoo_instance_sdk/storage/catalog/restore.py`, catalogue migration modules/schema assertions, restore/preparation model modules, and the public environment resource exposing preparation commands. Later packages consume these files but SHALL not revise the checkpoint values or public method signature without a planning revision.
- **Contract surface:** nullable `post_restore_checkpoint`; closed values `reset_pending`, `reset_complete`, `reset_not_requested`; null legacy rows; append-only compare-and-append using exact latest sequence/provenance; no duplicate database-restored event; typed completion command/result/failure fields; frozen JSON-safe inspectable plan.
- **Definition of done / evidence:** migration upgrade and current-schema assertions pass; invalid checkpoint values and stale/latest transitions fail; source-neutral catalogue/local-archive provenance remains exact; completion plan construction is mutation-free; public signature and typed projections are covered; focused commands/exit codes and exact SHA are reported without committing runtime evidence.
- **Parallel-safety rationale:** stage-1 foundation is the sole writer of shared schema/types/public contract, eliminating contract races for stage-2 siblings.

### `WP-MYL-650-02` — Restore checkpointing and completion execution

- **Task coverage:** `2.1`, `2.3`, `2.4`, `2.5`.
- **Deliverable:** existing restore records truthful checkpoints at the post-import boundary, and the public SDK command safely revalidates, repairs, resets, advances state, publishes, and no-ops against the exact proven target without reimport.
- **Owned responsibility scope:** internal database-preparation materialization/coordinator/step composition; target-instance and project-binding revalidation; module/admin-reset/default-switch child execution; SDK-focused preparation/integration tests; directly related executor fixtures. It may consume but SHALL not redesign stage-1 catalogue/model APIs. It does not edit `commands/db.py`, CLI characterization/output tests, README/SDK prose, or real-Odoo workflow files owned by active siblings.
- **Critical shared files:** `src/odoo_instance_sdk/internal/dbprep/materialize.py` and adjacent preparation coordinator/source/result modules, project default-switch helpers only where reuse requires a narrow correction, and preparation/integration tests.
- **Contract surface:** checkpoint capture occurs only after import/verify/filestore/neutralization; exact active claim/endpoint/database/data-directory/source/checkpoint/filestore/project-binding revalidation; preparation lock; explicit ordered module tuple; reset only when pending; append-only transition; atomic publication; complete-binding no-op; no archive read, restore, drop, recreation, or duplicate restored event.
- **Definition of done / evidence:** focused tests cover every project-database-preparation delta scenario, failure ordering and missing downstream calls, stale/concurrent refusal, crash-safe repeat, no-module path, successful and repeated completion, legacy guarded reimport, and ordinary restore regressions; lint/types for touched modules pass; exact commands and SHA are reported.
- **Parallel-safety rationale:** after stage 1 this package owns internal SDK execution and its non-CLI tests, while its stage-2 sibling owns CLI/docs files; neither writes the other's zone.

### `WP-MYL-650-03` — Existing CLI completion surface and docs

- **Task coverage:** `3.1`, `3.2`, `3.3`, `3.4`.
- **Deliverable:** the existing `db restore` leaf exposes the fixed completion syntax, exclusivity, confirmation, dry-run, bounded output, and user documentation against the stage-1 typed public command.
- **Owned responsibility scope:** `src/odoo_instance_sdk/commands/db.py`, adjacent output projection only if required, CLI characterization/output/database tests, `PUBLIC_LEAF_CASES` rationale without adding a row, README and SDK/reference documentation, directly related help snapshots/contracts. It SHALL mock/consume the stage-1 command boundary and SHALL not edit internal dbprep execution, catalogue migrations, or real-Odoo scenario/workflow files owned by siblings.
- **Critical shared files:** database command callback/renderers, CLI test matrices, documentation contract tests, `README.md`, and `docs/python-sdk.md`.
- **Contract surface:** `--complete DATABASE`; repeatable `--update-module MODULE`; all fixed mutual exclusions; deterministic duplicate removal/order; Rich confirmation and machine `--yes`; inert dry-run; one envelope; exact secret-free checkpoint/stage/module/default fields; one existing public-leaf inventory row.
- **Definition of done / evidence:** full parsing matrix exits before context access where required; dry-run contains no restore/drop/archive action; confirmation ordering and interrupt behavior are stable; JSON/TOON equality, Rich text, secret/ANSI/prompt exclusion, and error codes pass; docs contain an executable public sequence and no private URL/internal-call workaround; focused checks and exact SHA are reported.
- **Parallel-safety rationale:** CLI callback/tests/docs are disjoint from stage-2 internal execution files; stage-1 freezes the mocked command and typed result contract before both packages start.

### `WP-MYL-650-04` — Consolidated regression, CI, and delivery gates

- **Task coverage:** `4.1`, `4.2`, `4.3`.
- **Deliverable:** integrated backend plus CLI behavior is proven at real Odoo boundaries, added to the existing targeted CI tier, and passes applicable repository/OpenSpec gates on one consolidated exact revision.
- **Owned responsibility scope:** focused real-Odoo scenario and fixtures, existing real-Odoo targeted workflow selector, cross-package integration repairs, documentation/migration test gate fixes, and directly related generated artifacts required by repository checks. It SHALL not create a new E2E framework or redesign stable stage-1 contracts; any material contract change returns to planning.
- **Critical shared files:** `tests/integration/real_odoo/test_focused_auth_archive.py`, its existing fixtures/contracts, `.github/workflows/real-odoo-tier.yml`, and only the narrow cross-package files necessary to resolve integration findings after both predecessors merge.
- **Contract surface:** deterministic post-import administrator-reset failure; same target/database/filestore continuity; previous default until success; public completion and repeat no-op; no second restore/event; targeted CI selection; secret-safe bounded evidence; automatic-test time-limit handling.
- **Definition of done / evidence:** every verification-matrix row has a passing test, sufficient existing coverage, or documented manual evidence; focused local gates and strict OpenSpec validation pass; over-limit suites run in GitHub Actions at the published exact SHA; implementation PR/SHA, commands/exit codes, CI links, file links, and independent numbered-slot observations/images are posted and read back in Multica; no runtime evidence is committed.
- **Parallel-safety rationale:** stage 3 starts only after backend and CLI siblings converge, so it owns consolidated integration/real-Odoo changes without active sibling write conflicts.

### Coverage and frontier audit

Every `tasks.md` item appears exactly once: stage 1 covers catalogue/types/public command contract; the two stage-2 siblings cover internal execution and CLI/docs independently; stage 3 covers real-boundary integration and gates. Direct dependencies contain no transitive-only edges. The only simultaneous frontier is `{WP-MYL-650-02, WP-MYL-650-03}`; their critical write-zones do not overlap. Tests, fixtures, snapshots, docs, and generated service files directly related to each scope belong to that package unless explicitly owned by its active sibling.
