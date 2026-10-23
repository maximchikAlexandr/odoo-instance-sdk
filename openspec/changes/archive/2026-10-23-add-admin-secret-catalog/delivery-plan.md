# Delivery plan

## Planning baseline

- Task key: `MYL-538`.
- OpenSpec change: `add-admin-secret-catalog`.
- Approved implementation base: `origin/main` at `23dd4ea390393bb0f902f2e057fc2ca56483dd94`.
- Planning branch/worktree: `feat/MYL-538-admin-secret-catalog` in the dedicated managed checkout; production code is unchanged.
- Related active change: MYL-515 `fix-named-remote-and-init-contracts` at planning commit `db9515f1` was inspected read-only. Its nullable named-database and singular refresh resolver contracts are prerequisites for overlapping resolver work; this change owns only URL-keyed credential lookup and SHALL rebase/reconcile without copying or weakening MYL-515 scope.
- Test repository: resolve the URL from the current Multica project description at execution time. Treat it as a private parameter and never place its hostname, credentials, personal data, or full URL in public Git artifacts.

## Estimate contract

- Authoritative totals exist only in root planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; they are intentionally not duplicated here.
- Basis: remaining work from the approved base to all acceptance criteria for one experienced developer familiar with Python, FastAPI/Starlette, SQLite/SQLCipher, React, and this repository, without AI acceleration.
- Confidence: medium. Requirements and the affected monitor, catalogue, resolver, packaging, and test seams were inspected, but SQLCipher platform packaging, Starlette Admin customization, and the private-repository walkthrough retain bounded uncertainty.
- Calibration: uncalibrated because no comparable human timing history was supplied. The property values use a PERT-style aggregation of non-overlapping implementation/test/documentation/verification units, not commit timestamps, line counts, or a blanket multiplier.
- Evidence: reusable foundations were credited before estimating: Starlette Admin supplies authentication/session/forms/views; the SQLCipher DBAPI supplies encryption; repository `O_NOFOLLOW`/atomic-file helpers supply the file-safety pattern; `BackupProjectionPage`, `BackupCatalog`, restore/environment events, and typed SDK boundaries supply catalogue reads; `sanitize_last_error` supplies safe errors; normalized URL and GitLab credential seams supply identities/fallback; canonical snapshot/msgspec/OpenAPI/codegen and React card tests supply the monitor contract; existing dashboard/package/real-Odoo/GitLab scripts and CI supply verification infrastructure. Net-new effort is limited to the small encrypted adapter/key lifecycle, CAS/write-only policy, narrow wiring/projections, Admin composition, freshness fields/rendering, dependency compatibility, and focused live evidence.
- Non-duplicated line-item calibration: store lifecycle/CAS/security tests remain the largest unit; Admin composition and SQLCipher packaging are medium units; resolver wiring, catalogue extensions, freshness/codegen/React, docs/package edits, and focused E2E extensions are smaller units. Unit/integration/HTTP/React/package checks are included with their owning change, while final gates and independent evidence count only the remaining integration/walkthrough effort. No blanket test/review multiplier is added.
- Material assumptions: one supported SQLCipher DBAPI can be pinned for the declared platforms; Starlette Admin supports the specified provider/session/custom-view hooks; MYL-515 lands or is reconciled before overlapping resolver integration; the private test repository and numbered-slot infrastructure remain available. A failure of any assumption requires planning revision and re-estimation.
- Tests were not run for the estimate or planning phase.

### Qualitative net-new breakdown

| Task scope | Existing implementation credited | Net-new work counted once | Included verification / uncertainty |
|---|---|---|---|
| `1.1`–`1.3` | user-root paths, owner-only `O_NOFOLLOW` reads, atomic writes, SQLite transaction/error patterns, SQLCipher DBAPI encryption | one small adapter/schema, key/store state machine, two identities, direct mutation methods, revision CAS, redacted failures | adapter security/concurrency matrices live in this line; SQLCipher wheel behavior is the main uncertainty |
| `2.1`–`2.3` | URL normalization, remote password chain, GitLab member/host mapping, token-key and askpass redaction | one lookup-before-legacy branch at each existing boundary and typed absence/error mapping | public hit/absence/failure matrices are owned here, not repeated in final review |
| `3.1`–`3.4` | `BackupProjectionPage`, keyset cursor, backup/event/environment reads, sanitizer, typed SDK models | safe field additions, two small shared projections, typed filters, three read-only Admin registrations | catalogue and Admin HTTP adversarial cases are included with these changes |
| `1.4` | current FastAPI composition/Host/CSRF boundary plus Starlette Admin auth/session/forms/views | owner config load, Admin mount, direct secret forms and headless exclusion wiring | auth/session/proxy/headless tests are included; no custom auth framework is counted |
| `4.1`–`4.3` | restore/database events, monitor planning/collection, canonical msgspec/OpenAPI/codegen, existing React cards/tests | exact-current provenance selection, additive freshness field, navigation and card text | provenance/model/schema/codegen/React cases are included with this line |
| `5.1`–`5.3` | dashboard extra, build backend, package/compatibility jobs, generated-contract checks and documentation structure | dependency pins/lock, optional import inventories, package assets and focused operator docs | isolated package compatibility is included; platform-specific SQLCipher friction widens this line |
| `6.1` | existing dashboard/package scripts, real-Odoo scenarios, GitLab workflow and test-repository slot wrapper | only focused fixture/assertion extensions where current scripts cannot express an accepted case | no new coordinator, validator, evidence packager, or duplicate local runner is counted |
| `6.2`–`6.4` | feature-owned lower-level tests and canonical repository gates above | one integration repair allowance, independent live walkthrough, captures, cleanup and Multica read-back | gates run once; review fixes are assigned to the affected line rather than added as a blanket percentage |

## E2E decision

`e2e_required=true`.

Lower-level tests can prove SQLCipher state machines, query projections, resolver precedence, typed schemas, and React rendering, but they cannot by themselves prove the packaged browser authentication/session flow, encrypted-store access by the running monitor and a separate agent process, a real remote refresh using the stored Odoo password, or genuine monitor/Admin visual output. A focused local walkthrough closes those integration and capture gaps. It SHALL use a fresh numbered `odcli-N` slot and the dedicated numbered project/reference infrastructure from project context with the candidate SDK SHA, a unique HTTP port/database, and the private test repository URL supplied at execution time. It SHALL NOT use or modify the reference checkout.

The implementer owns only necessary extensions to existing E2E scripts/fixtures, CI tests, generated OpenAPI/client code, and functional commits. The independent Tester SHALL not edit those assets; it runs the accepted existing scripts and explicit manual actions, reports findings, and publishes evidence only in Multica. No planning-stage test is executed and no umbrella product E2E coordinator is created.

### Verification matrix

| Case | OpenSpec requirement/scenario | Verification gap / local decision | Public boundary | Tracked script and CI disposition | Independent Tester actions | Assertions | Multica-only artifacts/images |
|---|---|---|---|---|---|---|---|
| E2E-01 | single-admin config; authenticated Admin; headless exclusion | Packaged session/login flow and the new monitor navigation need a real browser and approved-base comparison; local E2E | `/admin`, `/healthz`, monitor navigation | extend existing dashboard HTTP/package tests and `dashboard-tests`; browser actions remain Tester steps | Capture the approved-base monitor navbar and `/admin` unavailability; reset into the equivalent candidate fixture, verify the new link, redirect/login/logout/unauthorized access, and inspect modes without printing contents | approved base has no Admin link/route; candidate has one same-origin link and correct auth transitions; invalid login denied; session logout invalidated; headless has no Admin | `e2e01-base-monitor-navbar.png`, `e2e01-base-admin-unavailable.png`, `e2e01-candidate-monitor-navbar.png`, `e2e01-candidate-login.png`, `e2e01-candidate-admin-home.png`, `e2e01-candidate-logout-denied.png`; sanitized command/exit log |
| E2E-02 | first-write SQLCipher initialization; write-only values; stale conflict | The approved base has no secret surface; browser plus encrypted DB/key state and two candidate sessions require live evidence | Admin secret forms | focused store/concurrency tests plus existing dashboard/package jobs; no separate local script | Reuse the approved-base `/admin` unavailable capture from E2E-01; on the equivalent candidate fixture create both record types, reopen forms, submit two-session stale update, inspect filesystem metadata and query only schema/identity through safe helper | candidate files are private/distinct; plaintext secret absent from UI/log/evidence; blank edit retains; stale edit rejected; different-record edits persist | `e2e02-candidate-odoo-secret-form.png`, `e2e02-candidate-gitlab-secret-form.png`, `e2e02-candidate-stale-conflict.png`; safe file metadata/hash JSON and redacted transcript, never database/key contents |
| E2E-03 | SQLCipher-first remote Odoo lookup; absence-only legacy fallback; MYL-515 database resolution | Requires separate agent process plus a real remote backup/restore path; local E2E | public refresh/checkout CLI and remote Odoo HTTP | extend the existing real-Odoo scenario/fixture only for encrypted credential injection and assertions | In fresh slot, store test-server password by normalized URL, omit/poison legacy value, refresh the named source and restore; then remove record and prove legacy fallback in a reset fixture; inject wrong-key copy only in disposable user root | encrypted hit succeeds without DB in secret; chosen DB follows MYL-515; absent record uses legacy; wrong key fails before remote request and never falls through | exact sanitized CLI text/exit codes, resulting functional commit/MR links if created, redacted request assertions; no secret/env dump |
| E2E-04 | SQLCipher-first GitLab member/host lookup and stable token key | Real Git transport and separate process access need the project repository; local E2E | existing public GitLab/Git workflow CLI | extend `scripts/e2e_gitlab_member_workflow.py` and existing CI fakes; local-only real credential use | Store a task-scoped test credential for the authorized member/host, run a read-only public Git operation against the private test repo, verify host/member miss uses legacy fixture and invalid record fails closed | correct host routing, generated key stability, no token in process args/output/evidence, only absence falls back | sanitized transport summary, commit/MR/file links if functional action creates them, command exits; no hostname/token in public Git |
| E2E-05 | three read-only catalogue screens; safe errors; bounded pagination/filters | The approved base has no catalogue Admin; real candidate UI and adversarial persisted rows need browser inspection | `/admin` catalogue resources | extend existing catalogue and dashboard HTTP tests; browser actions remain Tester steps | Reuse `e2e01-base-admin-unavailable.png`; reset to the equivalent candidate catalogue fixture, paginate/filter each screen, and try mutation URLs/actions | candidate exposes exactly three screens; deterministic pages; filters work; raw path/JSON/secret-like text absent; running is recorded/incomplete, not live; mutations denied | `e2e05-candidate-backups.png`, `e2e05-candidate-backup-events.png`, `e2e05-candidate-incomplete-errors.png`, `e2e05-candidate-environment-history.png`, `e2e05-candidate-pagination-filter.png`; redacted assertion manifest |
| E2E-06 | completed current backup freshness; superseded/unknown cases | Exact visible regression requires approved-base and candidate revisions with equivalent known/unknown provenance fixtures; local E2E | public refresh/restore CLI and `/api/v1/snapshot`/monitor SPA | extend existing restore/provenance, monitor snapshot/OpenAPI, and React tests; browser actions remain Tester steps | On approved base, capture an exact database card after seeding a completed catalogue restore; fully reset and reproduce the same seed on candidate, capture the added timestamp/age, then reset candidate to the specified unknown-origin fixture and capture its explicit unknown label | base card contains no freshness row; candidate equivalent known fixture shows exact UTC timestamp/nonnegative age tied to the same logical backup; candidate unknown fixture shows the exact unknown label and no stale date | `e2e06-base-known-provenance-card.png`, `e2e06-candidate-known-provenance-card.png`, `e2e06-candidate-unknown-provenance-card.png`; `e2e06-base-snapshot.txt`, `e2e06-candidate-known-snapshot.txt`, `e2e06-candidate-unknown-snapshot.txt` |
| E2E-07 | core optional imports and packaged dashboard support | Isolated install is adequately automated; no local browser duplication beyond E2E-01 | package import/CLI | extend existing package/compatibility jobs; no separate local script | Inspect installed wheel metadata and run documented imports in isolated environment | core-only import has no dashboard/SQLCipher import; dashboard extra composes Admin from wheel assets | command/exit summary and package SHA only; no image |
| E2E-08 | regression/security/recovery aggregate | Cleanup and cross-process persistence require one end-to-end audit | numbered-slot wrapper and public CLI | compose the accepted existing scripts and explicit Tester actions; no aggregate product script | Stop monitor, restart with same user root, verify records remain usable; delete disposable slot using the same numbered project wrapper while preserving evidence branch/MR/commits | no secret in gathered evidence, no lingering candidate process/slot/database, persistent files remain only where scenario specifies | cleanup transcript, process/DB absence assertions, evidence index with candidate/test SHAs |

### Script and fixture contract

- No new `e2e_admin_secret_catalog.py`, `all` coordinator, evidence packager, or parallel validator SHALL be added. Implementation MAY make focused additions to `scripts/e2e_gitlab_member_workflow.py`, the existing real-Odoo scenarios, `scripts/e2e_update_dashboard.py`, and their current fixtures only where a case's public action is otherwise unrepeatable. Browser-only checks remain explicit Tester actions. Secret values SHALL enter only through private files, never argv.
- At execution time the Tester SHALL read the current Multica project description, obtain the declared private test-repository URL as a secret parameter, and clone/fetch it into a new task-owned checkout beneath the Tester's work directory. The Tester SHALL record its clean starting SHA, create a dedicated evidence branch there, read its `README.md` and `docs/stand.md`, resolve `<test-repo-checkout>/scripts/agent_checkout.py`, and verify that path is a tracked regular file before use. No command SHALL depend on an undeclared current directory or a pre-existing mutable test checkout.
- From the test-repository checkout, the Tester SHALL provision separate fresh approved-base and candidate slots by invoking that resolved wrapper with both `--project <numbered-project-profile>` and `--reference-checkout <clean-reference-checkout>`, plus a free `--http-port`, unique `--target-database`, and the exact `--sdk-sha` for the respective approved-base or candidate revision. The wrapper SHALL be invoked from the test-repository checkout or by its absolute verified path. Each slot SHALL use its own checkout/database/port and SHALL be inspected to confirm the requested SDK SHA before the product script runs.
- Tracked fixtures MAY include deterministic non-secret catalogue seeds, identities, timestamps, and browser selectors. They SHALL NOT contain credentials, private URLs/hosts, local absolute paths, raw output, screenshots, or environment dumps.
- GitHub CI SHALL extend existing offline/dashboard/package jobs for deterministic lower-level cases. Real GitLab/private-repository operations remain local because CI has no declared safe credential fixture. Applicable existing real-Odoo CI SHALL cover only behavior reproducible with its disposable public fixture.
- If the checked function creates a real Git commit, retain it unchanged and link it to the task; keep test changes and functional result in distinct commits when both exist. Do not manufacture or rename a commit merely as evidence.

### Independent walkthrough and image contract

- Approved-base visual revision is exactly `23dd4ea390393bb0f902f2e057fc2ca56483dd94`; candidate visual revision is the exact reviewed implementation SHA supplied by Manager. Tester SHALL record both before capture and SHALL reject a slot whose inspected SDK SHA differs. A missing runnable approved-base revision is an evidence blocker, not permission to substitute a candidate pre-action state.
- Tester SHALL use separate fresh numbered slots for approved base and candidate, each with its own checkout/database/filestore/port. Both SHALL be seeded from the same versioned, non-secret logical fixture manifest: identical project/source/database labels, catalogue rows, timestamps, error payload classes, browser account hash, and viewport-visible data. Environment-specific paths, ports, database IDs, session cookies, and random keys differ by necessity and SHALL be excluded from visual comparison.
- Between revisions Tester SHALL close the browser context, stop the exact monitor child, delete the owned slot/database through the same verified test-repository wrapper and `--project`, recreate from the clean reference, reapply the fixture manifest through the documented script/public actions, and verify the expected pre-capture row counts/state. A reset mismatch invalidates the pair.
- All genuine browser screenshots SHALL use Chromium at `1440×900` CSS pixels, device scale factor `1`, zoom `100%`, locale `en-US`, timezone `UTC`, and light color scheme. The viewport, locale, timezone, theme, approved-base SHA, candidate SHA, fixture-manifest SHA, route, and expected observation SHALL be recorded beside every filename. No terminal-rendered image substitutes for browser screenshots.
- Required approved-base versus candidate pairs are: monitor navbar (`e2e01-base-monitor-navbar.png` / `e2e01-candidate-monitor-navbar.png`), Admin availability (`e2e01-base-admin-unavailable.png` / `e2e01-candidate-login.png`), and known-provenance database card (`e2e06-base-known-provenance-card.png` / `e2e06-candidate-known-provenance-card.png`). Candidate-only state-transition evidence is additional: authenticated home/logout denial, both secret forms/stale conflict, the three catalogue resources plus event/filter views, and explicit unknown provenance, using the exact filenames in the matrix.
- Expected visual observations: base navbar has no Admin link while candidate has one; base `/admin` has no authenticated Admin surface while candidate shows the package login; base known-provenance card has no freshness row while candidate shows exact UTC download time and age; candidate secret inputs are empty/write-only on edit; candidate catalogue screens contain only allowlisted fields; candidate unknown provenance shows the exact unknown label.
- Failure criteria: either revision/fixture/reset metadata is missing or mismatched; a required filename is absent; viewport/locale/timezone/theme differs; base and candidate fixtures are not logically equivalent; the approved-base capture is replaced by candidate pre-action state; a secret/private path appears; the expected observation is absent; or any attachment cannot be read back. Any criterion prevents complete verification.
- If CLI text changes during implementation, capture actual baseline/candidate text and render PNGs on Mac with bundled `scripts/render_terminal.swift`; record ANSI/TTY/progress limitations honestly. If CLI text does not change, no decorative terminal image is required.
- Every artifact SHALL name the scenario, candidate/test revision, command or browser action, exit status/assertion result, and sanitization performed. Images and source output retain the tested language; Tester explanations are English.

### Evidence publication and cleanup

- Tester SHALL publish in the final Multica comment: candidate product SHA, test-code SHA, exact commands/exit codes, assertion summary, MR/branch/commit/file/CI links, sanitized text/log attachments, required PNG attachments, hashes/provenance, and attachment read-back. Raw logs, diagnostics JSON, screenshots, secrets, URLs/hosts, and personal data SHALL not enter GitLab.
- Cleanup SHALL stop only the exact approved-base/candidate child processes, compare each PID with `multica daemon status` before termination, and remove each owned slot/database through the verified wrapper from the task-owned test-repository checkout using the same numbered `--project`. It SHALL leave the clean reference checkout untouched and preserve the task-owned test-repository checkout, evidence branch, MR, commits, and linked files after runtime cleanup. It SHALL not reset the reference, stop Colima/shared PostgreSQL, close/merge the MR, delete the branch, or delete the evidence checkout without separate authority.
- Missing mandatory screenshots, candidate/test revision mismatch, secret-bearing evidence, failed cleanup assertions, or an inaccessible attachment SHALL prevent a complete verification result.

## Delivery topology

Delivery mode: `dag`.

The authoritative `Estimate, hours` property selects multi-package delivery. The graph has two real parallel execution frontiers: the encrypted-store foundation and catalogue-query foundation at stage 1, followed by resolver, Admin, and snapshot/frontend integration at stage 2. Stages are consecutive topological layers; every dependency points to a lower stage. Manager SHALL materialize each WP child with the listed integer `stage` and only the direct `depends_on` edges below.

Stage map: `WP-01 -> 1`, `WP-02 -> 1`, `WP-03 -> 2`, `WP-04 -> 2`, `WP-05 -> 2`, `WP-06 -> 3`.

### WP-01 — Encrypted secret and administrator foundation

- Stage: `1`.
- Direct `depends_on`: none.
- Task coverage: `1.1`–`1.3`, each covered exactly once.
- Independent deliverable: optional dependency/path contracts, one direct SQLCipher adapter with schema/key lifecycle and conflict-safe mutation methods, and focused security/concurrency tests without any UI or resolver integration.
- Owned responsibility scope: dependency declarations/lockfile portions for secret primitives; reused user-root/owner-only/atomic-file patterns; the minimal encrypted-store adapter/schema/methods; directly related tests, fixtures, docs, and inventories. It SHALL NOT add a generic repository, Query/Command/service, or filesystem framework. This is a responsibility scope, not an exhaustive file allowlist.
- Critical shared files: owns new secret modules and relevant `pyproject.toml` dependency entries during stage 1. It SHALL NOT edit monitor app/routes/React, catalogue query modules, remote/GitLab resolver call sites, or generated HTTP clients.
- Contract surface: owner-only regular files; atomic key/database initialization; two exact identities; write-only values; compare-and-swap revisions; bounded failures/redaction; optional import behavior.
- Definition of done / evidence: hit/invalid/lost-key/partial-store/schema/concurrency matrices pass; plaintext driver opening is impossible; core import remains dependency-neutral; no secret-bearing output exists.
- Parallel-safety rationale: its writes are confined to new secret/config/dependency seams while WP-02 owns existing catalogue queries and provenance.

### WP-02 — Safe catalogue queries and current provenance

- Stage: `1`.
- Direct `depends_on`: none.
- Task coverage: `3.1`, `3.2`, and `4.1`, each covered exactly once.
- Independent deliverable: minimal extensions to existing bounded catalogue projections/reads for all three Admin datasets plus an exact current-database backup-freshness query.
- Owned responsibility scope: existing `BackupProjection`/keyset page, `BackupCatalog` environment/event reads, one shared safe projection per screen, sanitizer-backed fields, restore/database-event provenance selection, and their direct tests/fixtures/migrations compatibility. No parallel DTO/query layer or schema migration is expected; directly related support files remain in scope if required.
- Critical shared files: owns `storage/backup_catalog.py`, `storage/catalog/*`, and catalogue-focused tests during stage 1. It SHALL NOT edit HTTP/Admin composition, monitor snapshot models/React, or secret modules.
- Contract surface: stable bounded pagination, typed filters, allowlisted safe fields, incomplete-state semantics, and exact cluster/database completed-restore provenance with explicit unknown.
- Definition of done / evidence: adversarial filter/unsafe-row tests, deterministic equal-timestamp pagination, no mutation, and all replacement/drop/bootstrap/local/incomplete/mismatch provenance cases pass.
- Parallel-safety rationale: no write overlap with WP-01; its outputs are typed read interfaces consumed later by WP-04 and WP-05.

### WP-03 — Credential resolver integration

- Stage: `2`.
- Direct `depends_on`: `WP-01`.
- Task coverage: `2.1`–`2.3`, each covered exactly once.
- Independent deliverable: SQLCipher-first remote Odoo and GitLab credential resolution with exact absence-only legacy fallback and public-boundary regressions.
- Owned responsibility scope: remote preparation/source credential lookup call sites, GitLab credential mapping/transport call sites, adapter wiring to WP-01, and related plans/output/redaction tests. MYL-515 nullable-database/discovery code is reconciled but not redesigned.
- Critical shared files: owns `internal/dbprep/source.py`/materialization credential seams and `internal/gitlab_credentials.py` integration for this stage. It SHALL NOT edit Admin routes, catalogue queries, monitor models, or package-generated frontend files.
- Contract surface: normalized URL identity; member/host identity; authoritative record presence; unchanged legacy order on absence; fail-closed invalid/unreadable record; no database selection from secrets.
- Definition of done / evidence: public remote/GitLab hit/absence/error matrices prove no fallback on failure, no secret projection, and MYL-515 explicit/single/zero/multiple database behavior remains intact.
- Parallel-safety rationale: consumes WP-01's stable adapter and has no write overlap with stage-2 Admin or monitor/frontend siblings.

### WP-04 — Authenticated Admin and read-only screens

- Stage: `2`.
- Direct `depends_on`: `WP-01`, `WP-02`.
- Task coverage: `1.4`, `3.3`, and `3.4`, each covered exactly once.
- Independent deliverable: mounted single-admin authentication/session flow, secret forms, three read-only catalogue resources, and backend/Admin-template HTTP/browser contract tests.
- Owned responsibility scope: FastAPI/Starlette Admin composition, its package `AuthProvider`/session/forms, custom views/templates/assets consuming WP-01 methods and WP-02 shared safe projections directly, and direct authorization/security tests. It SHALL NOT add custom authentication, duplicate DTO conversion, or edit the monitor React application.
- Critical shared files: owns `http/app.py`, new Admin modules/templates, and Admin-focused Python/template test files. It SHALL NOT edit `src/odoo_instance_sdk/web/src/App.tsx`, `App.test.tsx`, `monitor.css`, resolver call sites, or canonical snapshot/generated schemas owned by siblings.
- Contract surface: `/admin` authentication; Host/proxy/headless boundaries; write-only secret controls; stale conflict UX; exactly three catalogue screens; mutation denial and safe error projection.
- Definition of done / evidence: login/logout/unauthorized/headless/proxy tests, secret-form leak checks, three-screen pagination/filter tests, mutation route denial, and adversarial persisted-field coverage pass.
- Parallel-safety rationale: predecessor interfaces are stable; its Python/Admin-template write zone is disjoint from WP-03 resolvers and from every shared React file owned exclusively by WP-05.

### WP-05 — Canonical snapshot and freshness cards

- Stage: `2`.
- Direct `depends_on`: `WP-02`.
- Task coverage: `4.2`–`4.3`, each covered exactly once.
- Independent deliverable: additive typed backup freshness in the canonical snapshot/OpenAPI/generated client, the monitor's Admin navigation link, and visible project/environment card rendering.
- Owned responsibility scope: monitor msgspec models, snapshot planning/collection, section outcomes, OpenAPI bridge/schema fixtures, generated TypeScript, and all changes to the shared monitor React application/styles/tests, including navigation and card rendering.
- Critical shared files: exclusively owns `models/monitor.py`, `resources/monitor/*`, generated HTTP schema/client outputs, `src/odoo_instance_sdk/web/src/App.tsx`, `App.test.tsx`, `monitor.css`, and directly related frontend snapshots for this stage. It SHALL NOT edit Admin backend composition/catalogue custom views or resolver code.
- Contract surface: exact UTC `downloaded_at`, nonnegative age from snapshot time, explicit unknown, degraded freshness without hiding runtime, no second browser request.
- Definition of done / evidence: Python model/schema/runtime tests and React navigation/known/unknown/replacement/clock-skew tests pass; generated contract is current; existing monitor/runtime behavior remains compatible.
- Parallel-safety rationale: consumes WP-02's provenance query; its snapshot/generated/React write zone is exclusive and does not overlap WP-03 or WP-04.

### WP-06 — Packaging, documentation, integration verification, and evidence

- Stage: `3`.
- Direct `depends_on`: `WP-03`, `WP-04`, `WP-05`.
- Task coverage: `5.1`–`6.4`, each covered exactly once.
- Independent deliverable: shippable optional package/docs/generated artifacts plus one non-duplicated integration pass, independent numbered-slot walkthrough, mandatory captures, cleanup proof, and exact-revision publication.
- Owned responsibility scope: final lock/package/build rules, isolated install checks, docs/inventories, focused extensions to existing E2E scripts/fixtures, repository gates, Tester execution contract, Multica evidence/read-back, and repair of integration defects returned to their owning feature scope. It SHALL not create an umbrella harness, repeat lower-level suites as a separate effort layer, broaden scope, or replace required live failures with assertions.
- Critical shared files: owns final `pyproject.toml`/`uv.lock`, build/package metadata, README/inventories, existing applicable verification scripts, and directly generated documentation/code. Raw logs, JSON diagnostics, hashes, screenshots, and command output remain outside Git and are published only to Multica.
- Contract surface: wheel/sdist dashboard support; core-only imports; documented secure setup/recovery; all OpenSpec/E2E cases; candidate/test revision separation; failure-safe numbered-slot lifecycle; MR/commit/file/CI links and safe image attachments.
- Definition of done / evidence: package/docs contracts and repository gates pass once at their canonical surfaces; independent live cases record commands/exits/assertions; mandatory images are attached/read back; candidate revision and cleanup are exact; strict OpenSpec validation succeeds.
- Parallel-safety rationale: it begins only after all product surfaces converge and is the sole integration/evidence package; no sibling writes remain and lower-level verification already belongs to WP-01–WP-05.

## Frontier audit

- Stage 1 frontier: `WP-01` and `WP-02` are independent and have disjoint critical write zones.
- Stage 2 frontier: `WP-03`, `WP-04`, and `WP-05` are independent after their direct predecessors and reserve disjoint resolver, Python/Admin-template, and snapshot/generated/shared-React zones; WP-05 exclusively owns `App.tsx`, `App.test.tsx`, and `monitor.css`.
- Stage 3 is the single packaging/integration/evidence convergence point; this does not erase the two earlier parallel frontiers or re-count verification already owned by feature WPs.
- Every OpenSpec task is covered exactly once. Shared foundations are predecessors, direct dependencies are minimal, and no same-stage siblings own conflicting critical files.
