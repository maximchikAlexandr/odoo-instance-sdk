## Delivery basis

- Task: `MYL-632`; OpenSpec change: `show-linked-multica-task-monitor`.
- Approved implementation base: `origin/main` at `f0918da2d36b59c35cf0a949efc58744d1efb7f9`.
- Scope authority: proposal, design, delta specs, and tasks in this change directory. GitHub #105 supplies the product intent; OpenSpec supplies the implementation contract.
- Estimation authority: the root planning issue custom properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`. Numeric totals SHALL be read from those properties and SHALL NOT be duplicated here.
- Estimate basis: remaining work from the approved base for one experienced developer familiar with Python/msgspec/FastAPI/React, without AI acceleration. Confidence, calibration, assumptions, and evidence are recorded below after property publication.

## E2E decision

`e2e_required=true`.

Unit/integration/contract tests can prove persistence, status mapping, deduplication, pagination, schema, and serializer behavior, but they cannot prove the material visible results in a real terminal and browser: the added `env list` provider cell, the `ps` Details annotation, the existing card layout, keyboard-focus tooltip, new-tab link behavior, and activity transitions. Local E2E is therefore limited to those remaining public-boundary gaps. It SHALL not duplicate the full lower-level matrix or run a whole-product Odoo sweep.

The project test repository is `https://gitlab.icode.by/a.maksimchyk/binta-test-repo`. Any Odoo-backed walkthrough SHALL use a fresh Tester-owned numbered `odcli-N` slot created by that repository's `scripts/agent_checkout.py create` with all of `--project ~/e2e/odcli/numbered-project-e2e`, `--reference-checkout ~/e2e/odcli/binta-e2e-reference`, a free `--http-port`, unique `--target-database`, and the exact candidate `--sdk-sha`. It SHALL use its own checkout/database/port and SHALL NOT use or modify the reference checkout, legacy `5468` profile, permanent source database, or another agent's slot.

## Verification matrix

| Case | OpenSpec requirement/scenario | Verification gap / local E2E decision | OpenAPI operation/schema | Local script | Independent Tester actions | CI disposition | Assertions | Artifacts/images |
|---|---|---|---|---|---|---|---|---|
| LT-01 | Proven binding survives restart; conflict/legacy no-guess | Lower-level integration is sufficient; no separate live case | `Snapshot` only after restart | Focused pytest via repository tests | Inspect a fresh candidate monitor process against the prepared slot and confirm the task remains after the originating prepare process exits | Extend existing `offline-tests` with migration/adoption/restart cases | Binding written only on success; exact conflict refuses; legacy/unbound absent | Sanitized command/exit summary in Multica; no image because storage behavior is not visible |
| LT-02 | Activity mapping fails closed; deduplication; timeout/pagination | Deterministic unit/integration matrix is sufficient for every upstream state; the live walkthrough exercises active→unknown only | `GET /api/v1/snapshot` / `ExternalTaskSummary` | `scripts/e2e_linked_task_monitor.py` for the visible transition | Independently inspect snapshot JSON before and after the controlled provider becomes unavailable; do not rerun the automated assertions as the manual check | Extend existing Python tests in `offline-tests` | Exact `running` active; queue-only inactive; incomplete/unknown/error unknown; one read per unique issue; no stale green | Sanitized selected JSON assertions and revision hashes in Multica only; raw JSON not committed |
| LT-03 | `env list` Rich/JSON/TOON task projection and watch replacement | Local E2E required because CLI table output changes visibly | Not applicable | Harness prepares equivalent isolated baseline/candidate catalogues and captures public CLI output at fixed locale/width | Run one-shot Rich and JSON on the exact candidate, inspect identifier/status/activity, then observe one watch transition from active to unknown | Existing CLI/unit/output-contract tests; no new CI framework | One provider call/sample; machine parity; unbound row has no fabricated summary; later frame replaces active | `env-list-before.txt`, `env-list-after.txt`, `env-list-before.png`, `env-list-after.png`; PNGs are renders of actual captured text |
| LT-04 | `ps` compact annotation without process group | Local E2E required because the existing Details output changes visibly | Not applicable | Same harness and isolated sample; no second collector | Run candidate `odcli ps`, inspect the existing environment section and confirm no new row/group/PID is created | Extend existing process-inventory/CLI tests | Exactly one compact task annotation; JSON/TOON nested value equals snapshot; shared issue does not imply process attribution | `ps-before.txt`, `ps-after.txt`, `ps-before.png`, `ps-after.png`; PNGs are renders of actual captured text |
| LT-05 | Snapshot/OpenAPI/generated client carries safe schema-v5 facts | Contract tests are sufficient for schema; local HTTP probe is retained as readiness for UI | `GET /api/v1/snapshot` / `Snapshot`, `EnvironmentFactsSummary`, `ExternalTaskSummary` | Harness starts exact candidate monitor on an isolated loopback port and probes health/OpenAPI/snapshot | Inspect browser network response and confirm no request targets Multica and no private binding field appears | Extend current HTTP/OpenAPI/generated-client tests and existing frontend build/test jobs | Direct typed response, exact enums/nullability, no CLI envelope, no server/workspace/path/runtime/daemon/credential fields | Sanitized schema/assertion summary in Multica only; no committed OpenAPI diff evidence beyond required generated source |
| LT-06 | Existing RuntimeCard accessible task block; stopped visibility; active/inactive/unknown | Local browser E2E required for layout, focus, tooltip, link, and visible transitions | Consumes `GET /api/v1/snapshot` | Harness supplies deterministic active/inactive/unknown snapshots to the real built candidate SPA | In a real browser, inspect stopped and running cards; focus the activity help; read tooltip/accessibility text; open the link and verify new-tab/no opener behavior; switch to unavailable and confirm gray unknown with link retained | Extend `App.test.tsx`, generated contract test, frontend test/build | Text independent of color; exact help copy; safe URL; `noopener,noreferrer`; no empty block; runtime controls unchanged | `card-before.png`, `card-active-after.png`, `card-unknown-after.png` actual browser screenshots, plus concise observations in Multica |
| LT-07 | Optional co-installation/core-only behavior | Packaging/import tests are sufficient; candidate walkthrough confirms installed provider discovery | Snapshot facts empty or populated | Harness reports installed distribution/entry-point identity without environment dump | Start one core-only monitor and one co-installed candidate monitor; confirm core-only card/CLI have no placeholder and candidate discovers the provider | Extend packaging artifact/import tests | No `multica-py` core dependency/import; PATH-only executable ignored; same-interpreter entry point works | Sanitized package/revision assertions in Multica; no image beyond LT-06 before view |

## Implementer-owned verification assets

- Implementer owns task 6.3/6.4 and SHALL add `scripts/e2e_linked_task_monitor.py` plus only the minimal deterministic fixtures needed to repeat LT-02 through LT-07. The script accepts explicit `--baseline-sha`, `--candidate-sha`, isolated roots/catalogues/profiles, ports, locale, and output directory; it records product revision separately from the test-script revision.
- The harness SHALL verify readiness before consuming a live attempt: exact candidate SHA, clean candidate checkout, provider entry point in the monitor interpreter, healthy dedicated PostgreSQL cluster when an Odoo-backed slot is used, isolated port/database/root, fixture determinism, built SPA assets, and focused CI/regression results.
- The harness SHALL stop only processes it started. Before terminating any candidate PID it SHALL compare the PID with `multica daemon status --output json` and refuse to terminate the daemon. Failure-safe cleanup SHALL remove the numbered slot through the Binta wrapper with the same `--project`, its test database/port/processes, isolated catalog/profile/temp roots, and local raw captures after publication.
- Tests, fixtures, generated client code, documentation, and the harness are normal development artifacts committed to the implementation branch. Raw logs, diagnostic JSON, screenshots, rendered PNGs, command transcripts, machine/private paths, hashes/SHA evidence, and assertion results are evidence and SHALL be attached only to Multica.

## Independent Tester procedure

1. Confirm the exact candidate SHA and test-script SHA, clean checkouts, approved baseline SHA, property-backed OpenSpec change, and focused automated results. Do not use a live attempt to debug setup or the harness.
2. Allocate an isolated root, candidate monitor port, and—only where the public prepare path needs Odoo state—a fresh numbered slot using the prescribed project/reference/port/database/sdk-sha arguments. Record sanitized identities, never secrets or private hostnames.
3. Run the harness synchronously for one bounded acceptance attempt. Automatic suites/harness execution obey the ten-minute local limit; stop only that run's owned test process at the limit and continue the exact published revision in GitHub Actions rather than splitting the suite. Manual browser/terminal inspection may take longer.
4. Independently run the public candidate commands and browser actions in LT-03, LT-04, LT-05, LT-06, and LT-07. A second invocation of the harness is not the independent check.
5. For terminal evidence, preserve actual baseline/candidate text in its original language at the same locale, width, and output mode. Render those captured text files with the bundled `openspec-e2e-testing/scripts/render_terminal.swift`; label PNGs as renders, not terminal-window screenshots. Record honestly that redirection/PTY differences may limit ANSI, interactive progress, or tooltip capture; do not claim unsupported states.
6. For browser evidence, take actual screenshots of equivalent baseline/candidate fixtures. Verify keyboard focus and accessible names separately from pixels. Never manufacture a baseline or intermediate state.
7. Publish one final Multica Tester comment with case-level PASS/FAIL, exact product/test revisions, commands and exit codes, assertions, sanitized provenance, cleanup result, MR/commit/file links, and all required PNG attachments. Read the comment back and verify attachment metadata. Missing mandatory capture or read-back prevents complete verification.

## CI and evidence disposition

- Focused Python tests belong in the existing `offline-tests` path in `.github/workflows/ci.yml`; frontend tests/build use the existing `npm test`/build integration. No new runner or CI framework is planned.
- Required GitHub tests/fixtures are Implementer-owned OpenSpec tasks. Tester does not edit them; Tester verifies their presence/coverage and reports findings.
- If an automated local suite is expected to exceed ten minutes, publish the exact revision and use GitHub Actions. Do not split a long suite to evade the limit. CI is evidence, not a reason to wait after a successful push unless the triggering acceptance criteria explicitly require the result.
- The test GitLab MR/branch/commits and any real commits produced by the tested Git utility are preserved and linked. They are not automatically merged, closed, renamed, or deleted. No functional Git commit is expected from this read-only monitor verification; therefore no second result commit is required. Test-code commits remain separate from product changes during implementation history review.
- Cleanup removes the live stand and local evidence staging but preserves the evidence MR/branch/commits and the Multica comment attachments. Private URLs/hostnames, personal data, credentials, and local paths SHALL not enter public Git.

## Estimate evidence and confidence

The estimate is property-backed and calibrated qualitatively from the inspected source at the approved base: environment catalogue migrations and adoption evidence already exist; provider discovery/process isolation and generic summaries already exist; monitor/checkout/process projections are established; HTTP derives OpenAPI from msgspec; the React change is confined to the existing `RuntimeCard`; and `odcli-multica` already has verified task context, public issue/run access, and pinned compatibility. Material uncertainty remains in moving facts into the canonical snapshot without duplicate collection, catalogue/adoption migration compatibility, and end-to-end optional-package installation. Historical elapsed implementation data is unavailable, so the estimate is uncalibrated with medium confidence. Tests were not run to produce the estimate.

## Delivery topology

`delivery_mode=dag` because the authoritative `Estimate, hours` property exceeds the multi-WP threshold and the graph contains a real three-way independent frontier after the shared model/snapshot foundation. Stages are topological layers, not WIP limits. Stage numbers are consecutive and every dependency points to a lower stage.

Stage mapping:

- `WP-MYL-632-01 -> 1`
- `WP-MYL-632-02 -> 2`
- `WP-MYL-632-03 -> 3`
- `WP-MYL-632-04 -> 3`
- `WP-MYL-632-05 -> 3`
- `WP-MYL-632-06 -> 4`
- `WP-MYL-632-07 -> 5`

### WP-MYL-632-01 — Binding and catalogue foundation

- **Stage:** 1
- **OpenSpec task coverage:** 1.1, 1.2, 1.3.
- **Deliverable:** Core has validated frozen binding/summary types and an additive catalogue/adoption path that atomically records complete proof, safely fills an absent binding on verified reuse, and refuses conflicts.
- **Direct `depends_on`:** none.
- **Owned responsibility scope:** public model exports and validators; environment catalogue schema/migrations/row conversion; caller-owned adoption plan/reuse transaction; directly related unit/migration/adoption fixtures and docs. Critical shared files include `models/backup.py`, model exports, `storage/catalog_schema.py`, `storage/catalog_migrations/`, `storage/catalog/environment.py`, and environment adoption code.
- **Contract surface:** exact private/public field separation; nullable legacy migration; immutable plan input; successful-write and conflict semantics; no binding CRUD.
- **DoD/evidence:** migration parity and legacy round-trip tests pass; create/reuse/rollback/conflict tests prove atomic behavior; serialized public models contain no private identity; focused lint/type checks pass.
- **Parallel-safety rationale:** predecessor owns all new shared data contracts before any consumer work begins, preventing siblings from competing over schema or model definitions.

### WP-MYL-632-02 — Canonical snapshot enrichment

- **Stage:** 2
- **OpenSpec task coverage:** 2.1, 2.2, 2.3.
- **Deliverable:** Environment facts run once inside schema-v5 monitor sampling, remain isolated/bounded, attach to project/environment summaries, and flow into `CheckoutInventory` without a second provider call.
- **Direct `depends_on`:** `WP-MYL-632-01`.
- **Owned responsibility scope:** public facts protocol/summary integration, provider process boundary, monitor collection, snapshot schema/version and checkout projection, plus their direct tests/fixtures. Critical shared files include `models/monitor.py`, `internal/checkout_inventory.py`, `resources/monitor/collection_parts/collect.py`, monitor collection helpers, and `tests/unit/test_checkout_inventory.py`.
- **Contract surface:** `odoo_instance_sdk.environment_facts`; schema-v5 `facts`; private binding input versus safe summary output; per-sample invocation; deadline/error/large-response/duplicate behavior.
- **DoD/evidence:** strict model and snapshot tests cover empty/provider/timeout/duplicate/removed/filter cases; one-shot and watch prove one invocation per sample; CheckoutInventory serialization/order remains deterministic; no optional package becomes a core dependency.
- **Parallel-safety rationale:** this is the last shared foundation. It finishes the typed snapshot contract consumed read-only by all stage-3 siblings.

### WP-MYL-632-03 — Multica binding producer and provider

- **Stage:** 3
- **OpenSpec task coverage:** 3.1, 3.2, 3.3, 3.4.
- **Deliverable:** `odcli-multica` writes safe proof through public adoption and supplies deduplicated, conservative, bounded task/status/activity facts through public `multica-py`.
- **Direct `depends_on`:** `WP-MYL-632-02`.
- **Owned responsibility scope:** `packages/odcli-multica` models/client/provider/entry implementation and package-local tests/fixtures, including public permalink, daemon/path revalidation, status mapping, pagination, timeout, and deduplication. Critical shared files are confined to `packages/odcli-multica/src/`, its tests, and its package metadata where needed for implementation.
- **Contract surface:** core binding/facts protocol from predecessors; pinned `multica-py` Issue/TaskRun/Page/daemon APIs; closed upstream run-status mapping; safe URL and sanitized reason categories.
- **DoD/evidence:** package tests prove all known/future/incomplete/error states, grouping, restart reconstruction, mismatches, conflict, and public-boundary imports; no raw CLI parsing, private core import, credential output, or browser API call appears.
- **Parallel-safety rationale:** writes only the optional package while stage peers own core CLI/process and HTTP/web surfaces; it consumes but does not change the shared snapshot contract.

### WP-MYL-632-04 — CLI and process projections

- **Stage:** 3
- **OpenSpec task coverage:** 4.1, 4.2, 4.3.
- **Deliverable:** `env list`/watch and `ps` Rich/JSON/TOON project the captured summary consistently, with no duplicate collection or invented process attribution.
- **Direct `depends_on`:** `WP-MYL-632-02`.
- **Owned responsibility scope:** core CLI environment display, process-inventory typed projection and renderer, field-schema/machine-output characterization, and directly related CLI/process tests and snapshots. Critical shared files include `commands/env/checkout.py`, `commands/env/display.py`, `models/process_inventory.py`, `internal/process_inventory.py`, and `commands/ps.py`.
- **Contract surface:** schema-v5 snapshot facts and existing CheckoutInventory/ProcessInventory output envelopes; provider column rules; existing Details cell and seven-column process table.
- **DoD/evidence:** compact/medium/wide/watch tests, snapshot-count assertions, JSON/TOON equality, process block annotation, unbound behavior, and redaction/field-schema gates pass without a new row/group/collector.
- **Parallel-safety rationale:** does not write optional-package or HTTP/web files owned by its stage peers and treats predecessor models as fixed.

### WP-MYL-632-05 — HTTP schema and accessible RuntimeCard

- **Stage:** 3
- **OpenSpec task coverage:** 5.1, 5.2, 5.3.
- **Deliverable:** Typed schema-v5 facts reach the generated client and existing card renders a secure, accessible task block with honest active/inactive/unknown transitions.
- **Direct `depends_on`:** `WP-MYL-632-02`.
- **Owned responsibility scope:** FastAPI/OpenAPI schema bridge tests, generated TypeScript, `App.tsx`, monitor CSS, frontend tests/build/package artifact assertions, and directly related HTTP fixtures. Critical shared files include `http/monitor.py`, `openapi.json`, `web/src/generated/`, `web/src/App.tsx`, `web/src/monitor.css`, and `web/src/App.test.tsx`.
- **Contract surface:** `GET /api/v1/snapshot` named schemas; generated `ExternalTaskSummary`; safe new-tab link; visible text/color/focus tooltip and accessible names; existing runtime controls.
- **DoD/evidence:** Python response/schema validation, generated-client contract, Vitest accessibility/state tests, TypeScript build, CSS/artifact checks, and private-field absence pass; browser makes no Multica request.
- **Parallel-safety rationale:** web/HTTP ownership is disjoint from the optional-package and CLI/process siblings; it consumes the frozen snapshot contract only.

### WP-MYL-632-06 — Packaging, repeatable E2E assets, and documentation

- **Stage:** 4
- **OpenSpec task coverage:** 6.1, 6.2, 6.3, 6.4, 6.5.
- **Deliverable:** Optional co-installation is packaged/documented, focused checks are wired into existing CI, and the repeatable isolated evidence harness/fixtures cover the full public path without committing evidence.
- **Direct `depends_on`:** `WP-MYL-632-03`, `WP-MYL-632-04`, `WP-MYL-632-05`.
- **Owned responsibility scope:** repository/package metadata consolidation, existing CI job integration, `scripts/e2e_linked_task_monitor.py`, minimal E2E fixtures, public SDK/CLI/monitor docs/examples, and directly related packaging tests. Critical shared files include root/package `pyproject.toml` as applicable, `.github/workflows/ci.yml`, `scripts/`, `README.md`, and `packages/odcli-multica/README.md`.
- **Contract surface:** same-interpreter entry-point installation; explicit baseline/candidate revision parameters; isolated catalog/profile/ports; evidence-versus-Git disposition; existing CI runners only.
- **DoD/evidence:** packaging tests prove co-installed/PATH-only/core-only cases; harness self-check/readiness/cleanup tests pass; docs match the three-value contract/non-goals; CI changes invoke focused existing suites; no raw evidence/private value is tracked.
- **Parallel-safety rationale:** runs after all feature siblings, so it can integrate their stable public paths without overlapping their active write zones.

### WP-MYL-632-07 — Consolidated acceptance and evidence gate

- **Stage:** 5
- **OpenSpec task coverage:** 7.1, 7.2, 7.3.
- **Deliverable:** One consolidated exact-revision acceptance record proves automated gates, independent public-boundary E2E, mandatory images, cleanup, and absence of secret/private/evidence artifacts in Git.
- **Direct `depends_on`:** `WP-MYL-632-06`.
- **Owned responsibility scope:** final focused/full repository checks, independent Tester coordination and findings, necessary consolidated-revision fixes, evidence publication/read-back, and safety audit across all directly affected tests/fixtures/docs. No standing production-code write zone is reserved; any repair follows the finding and triggers impact-based re-verification.
- **Contract surface:** exact candidate SHA; delivery matrix LT-01 through LT-07; local ten-minute automated-test limit; required Multica attachments/read-back; cleanup and preserved evidence branch/MR links.
- **DoD/evidence:** repository/OpenSpec/lint/type/test/build gates recorded with exit codes; independent Tester publishes case-level results and all mandatory PNGs with read-back; exact SHA and cleanup are proven; secret/private-path/evidence-file scan is clean; unresolved findings keep the WP incomplete.
- **Parallel-safety rationale:** terminal integration gate starts only after every implementation and verification asset is consolidated; it has no sibling.

## Coverage and frontier audit

Every OpenSpec task from 1.1 through 7.3 appears exactly once above. Direct dependency edges are minimal: shared contracts flow through WP-01/WP-02; the stage-3 frontier (`WP-MYL-632-03`, `WP-MYL-632-04`, `WP-MYL-632-05`) is genuinely parallel with disjoint write zones; integration and acceptance then converge. Related tests, fixtures, snapshots, generated code, docs, and service files are included in each owner's responsibility unless explicitly owned by an active sibling; the listed critical files are not exhaustive allowlists.
