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
- Evidence: reusable foundations include owner-root paths, `BackupCatalog`/Alembic schema, exact restore provenance, snapshot/msgspec/OpenAPI generation, React monitor tests, URL normalization, legacy remote/GitLab resolvers, sanitizer/redaction, and existing dashboard/package CI. New effort is concentrated in encrypted storage, Admin composition, safe page DTOs, cross-process lookup integration, and live evidence.
- Material assumptions: one supported SQLCipher DBAPI can be pinned for the declared platforms; Starlette Admin supports the specified provider/session/custom-view hooks; MYL-515 lands or is reconciled before overlapping resolver integration; the private test repository and numbered-slot infrastructure remain available. A failure of any assumption requires planning revision and re-estimation.
- Tests were not run for the estimate or planning phase.

## E2E decision

`e2e_required=true`.

Lower-level tests can prove SQLCipher state machines, query projections, resolver precedence, typed schemas, and React rendering, but they cannot by themselves prove the packaged browser authentication/session flow, encrypted-store access by the running monitor and a separate agent process, a real remote refresh using the stored Odoo password, or genuine monitor/Admin visual output. A focused local walkthrough closes those integration and capture gaps. It SHALL use a fresh numbered `odcli-N` slot and the dedicated numbered project/reference infrastructure from project context with the candidate SDK SHA, a unique HTTP port/database, and the private test repository URL supplied at execution time. It SHALL NOT use or modify the reference checkout.

The implementer owns the tracked E2E script, fixtures, CI tests, generated OpenAPI/client code, and functional commits. The independent Tester SHALL not edit those assets; it runs the accepted script and manual actions, reports findings, and publishes evidence only in Multica. No planning-stage test is executed.

### Verification matrix

| Case | OpenSpec requirement/scenario | Verification gap / local decision | Public boundary | Tracked script and CI disposition | Independent Tester actions | Assertions | Multica-only artifacts/images |
|---|---|---|---|---|---|---|---|
| E2E-01 | single-admin config; authenticated Admin; headless exclusion | Packaged session/login flow and owner-only config need a real browser process; local E2E | `/admin`, `/healthz`, monitor navigation | `scripts/e2e_admin_secret_catalog.py auth`; extend dashboard package/HTTP tests and `dashboard-tests` | Create owner-only config with a precomputed non-secret test hash/key, start candidate monitor on loopback, verify redirect/login/logout/unauthorized access; inspect modes without printing contents | correct auth transition, invalid login denied, session logout invalidated, no Admin route in headless check | browser login-before, authenticated-Admin-after, logout-denied PNG; sanitized command/exit log |
| E2E-02 | first-write SQLCipher initialization; write-only values; stale conflict | Browser plus encrypted DB/key state and two sessions are not fully proven by unit tests; local E2E | Admin secret forms | script `secrets`; add unit/concurrency/package tests to offline/dashboard CI | Create both record types, reopen forms, submit two-session stale update, inspect filesystem metadata and query only schema/identity through safe helper | files are private and distinct; plaintext secret absent from UI/log/evidence; blank edit retains; stale edit rejected; different-record edits persist | safe file metadata/hash JSON, redacted text transcript, form/conflict PNG; never database/key contents |
| E2E-03 | SQLCipher-first remote Odoo lookup; absence-only legacy fallback; MYL-515 database resolution | Requires separate agent process plus a real remote backup/restore path; local E2E | public refresh/checkout CLI and remote Odoo HTTP | script `remote-odoo`; add resolver matrices to offline CI and applicable real-Odoo test | In fresh slot, store test-server password by normalized URL, omit/poison legacy value, refresh the named source and restore; then remove record and prove legacy fallback in a reset fixture; inject wrong-key copy only in disposable user root | encrypted hit succeeds without DB in secret; chosen DB follows MYL-515; absent record uses legacy; wrong key fails before remote request and never falls through | exact sanitized CLI text/exit codes, resulting functional commit/MR links if created, redacted request assertions; no secret/env dump |
| E2E-04 | SQLCipher-first GitLab member/host lookup and stable token key | Real Git transport and separate process access need the project repository; local E2E | existing public GitLab/Git workflow CLI | script `gitlab`; retain/extend existing GitLab CI fakes, local-only real credential use | Store a task-scoped test credential for the authorized member/host, run a read-only public Git operation against the private test repo, verify host/member miss uses legacy fixture and invalid record fails closed | correct host routing, generated key stability, no token in process args/output/evidence, only absence falls back | sanitized transport summary, commit/MR/file links if functional action creates them, command exits; no hostname/token in public Git |
| E2E-05 | three read-only catalogue screens; safe errors; bounded pagination/filters | Real packaged UI and adversarial persisted rows need browser inspection; local E2E | `/admin` catalogue resources | script `catalog`; add catalogue query and Admin HTTP tests to offline/dashboard CI | Seed lifecycle rows via public candidate operations/fixtures, including safe and secret-like errors, paginate/filter each screen, try mutation URLs/actions | exactly three screens; deterministic pages; filters work; raw path/JSON/secret-like text absent; running is labeled recorded/incomplete; mutations denied | one PNG per screen plus pagination/filter PNG, redacted row/assertion manifest |
| E2E-06 | completed current backup freshness; superseded/unknown cases | Actual snapshot-to-card result and visible output need genuine states; local E2E | public refresh/restore CLI and `/api/v1/snapshot`/monitor SPA | script `freshness`; add provenance/model/OpenAPI/React tests to CI | Capture baseline candidate card with unknown origin, perform catalogue refresh/restore in fresh slot, reload card, then create/reset an unknown-origin database fixture | before says exact unknown label; after shows exact UTC download timestamp and nonnegative age tied to current backup; no older timestamp after reset | mandatory before/after browser PNG, corresponding sanitized snapshot excerpt and exact displayed text |
| E2E-07 | core optional imports and packaged dashboard support | Isolated install is adequately automated; no local browser duplication beyond E2E-01 | package import/CLI | extend existing package/compatibility jobs; no separate local script | Inspect installed wheel metadata and run documented imports in isolated environment | core-only import has no dashboard/SQLCipher import; dashboard extra composes Admin from wheel assets | command/exit summary and package SHA only; no image |
| E2E-08 | regression/security/recovery aggregate | Cleanup and cross-process persistence require one end-to-end audit | numbered-slot wrapper and public CLI | script `audit`; CI retains focused suites, local-only infrastructure audit | Stop monitor, restart with same user root, verify records remain usable; delete disposable slot using the same numbered project wrapper while preserving evidence branch/MR/commits | no secret in gathered evidence, no lingering candidate process/slot/database, persistent files remain only where scenario specifies | cleanup transcript, process/DB absence assertions, evidence index with candidate/test SHAs |

### Script and fixture contract

- Tracked script: `scripts/e2e_admin_secret_catalog.py` with explicit subcommands above and an `all` coordinator; it SHALL accept candidate SDK SHA, numbered slot identity, project/reference paths, test-repository parameter, unique port/database, evidence directory, and secret paths through private files—not secret values on argv.
- The script SHALL call `scripts/agent_checkout.py create` with both required numbered project/reference arguments and the candidate SDK SHA, record the product revision separately from its own test-code revision, and use the same `--project` for failure-safe deletion.
- Tracked fixtures MAY include deterministic non-secret catalogue seeds, identities, timestamps, and browser selectors. They SHALL NOT contain credentials, private URLs/hosts, local absolute paths, raw output, screenshots, or environment dumps.
- GitHub CI SHALL extend existing offline/dashboard/package jobs for deterministic lower-level cases. Real GitLab/private-repository operations remain local because CI has no declared safe credential fixture. Applicable existing real-Odoo CI SHALL cover only behavior reproducible with its disposable public fixture.
- If the checked function creates a real Git commit, retain it unchanged and link it to the task; keep test changes and functional result in distinct commits when both exist. Do not manufacture or rename a commit merely as evidence.

### Independent walkthrough and image contract

- Tester SHALL use a fresh numbered slot, its own checkout/database/port, the candidate SHA, and the private test repository parameter from project context. It SHALL not reuse the reference checkout or another agent's database.
- Browser captures SHALL be genuine screenshots at the same desktop viewport, locale, and theme. Required captures are E2E-01 login/auth/logout, E2E-02 safe forms/conflict, E2E-05 three catalogue screens, and E2E-06 before/after database card.
- E2E-06 baseline and candidate SHALL use equivalent fixtures except for the provenance-producing action. Preserve exact displayed text alongside PNGs. No terminal-rendered image substitutes for browser screenshots.
- If CLI text changes during implementation, capture actual baseline/candidate text and render PNGs on Mac with bundled `scripts/render_terminal.swift`; record ANSI/TTY/progress limitations honestly. If CLI text does not change, no decorative terminal image is required.
- Every artifact SHALL name the scenario, candidate/test revision, command or browser action, exit status/assertion result, and sanitization performed. Images and source output retain the tested language; Tester explanations are English.

### Evidence publication and cleanup

- Tester SHALL publish in the final Multica comment: candidate product SHA, test-code SHA, exact commands/exit codes, assertion summary, MR/branch/commit/file/CI links, sanitized text/log attachments, required PNG attachments, hashes/provenance, and attachment read-back. Raw logs, diagnostics JSON, screenshots, secrets, URLs/hosts, and personal data SHALL not enter GitLab.
- Cleanup SHALL stop the exact candidate processes, compare any child PID with `multica daemon status` before termination, and remove the numbered slot/database through the wrapper while preserving the evidence MR/branch/commits. It SHALL not stop Colima or shared PostgreSQL, close/merge the MR, or delete the evidence branch without separate authority.
- Missing mandatory screenshots, candidate/test revision mismatch, secret-bearing evidence, failed cleanup assertions, or an inaccessible attachment SHALL prevent a complete verification result.

## Delivery topology

Delivery mode: `dag`.

The authoritative `Estimate, hours` property selects multi-package delivery. The graph has two real parallel execution frontiers: the encrypted-store foundation and catalogue-query foundation at stage 1, followed by resolver, Admin, and snapshot/frontend integration at stage 2. Stages are consecutive topological layers; every dependency points to a lower stage. Manager SHALL materialize each WP child with the listed integer `stage` and only the direct `depends_on` edges below.

Stage map: `WP-01 -> 1`, `WP-02 -> 1`, `WP-03 -> 2`, `WP-04 -> 2`, `WP-05 -> 2`, `WP-06 -> 3`, `WP-07 -> 4`.

### WP-01 — Encrypted secret and administrator foundation

- Stage: `1`.
- Direct `depends_on`: none.
- Task coverage: `1.1`–`1.3`, each covered exactly once.
- Independent deliverable: optional dependency/path contracts, SQLCipher schema/key lifecycle, typed conflict-safe secret mutations, and focused security/concurrency tests without any UI or resolver integration.
- Owned responsibility scope: dependency declarations/lockfile portions for secret primitives; user config/data paths; new encrypted-store models/adapters/schema; secret mutation commands; directly related tests, fixtures, docs, and inventories. This is a responsibility scope, not an exhaustive file allowlist.
- Critical shared files: owns new secret modules and relevant `pyproject.toml` dependency entries during stage 1. It SHALL NOT edit monitor app/routes/React, catalogue query modules, remote/GitLab resolver call sites, or generated HTTP clients.
- Contract surface: owner-only regular files; atomic key/database initialization; two exact identities; write-only values; compare-and-swap revisions; bounded failures/redaction; optional import behavior.
- Definition of done / evidence: hit/invalid/lost-key/partial-store/schema/concurrency matrices pass; plaintext driver opening is impossible; core import remains dependency-neutral; no secret-bearing output exists.
- Parallel-safety rationale: its writes are confined to new secret/config/dependency seams while WP-02 owns existing catalogue queries and provenance.

### WP-02 — Safe catalogue queries and current provenance

- Stage: `1`.
- Direct `depends_on`: none.
- Task coverage: `3.1`, `3.2`, and `4.1`, each covered exactly once.
- Independent deliverable: typed bounded read models/queries for all three Admin datasets plus an exact current-database backup-freshness query.
- Owned responsibility scope: `BackupCatalog`, catalogue query mixins/models, sanitizer-backed projections, restore/database-event provenance selection, and their direct tests/fixtures/migrations compatibility. No schema migration is expected; directly related support files remain in scope if required.
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
- Independent deliverable: mounted single-admin authentication/session flow, secret forms, three read-only catalogue resources, monitor link, and HTTP/browser contract tests.
- Owned responsibility scope: FastAPI/Starlette Admin composition, auth provider/session adapter, custom Admin DTO views/templates/assets, monitor navigation link, and direct authorization/security/UI tests. It consumes rather than rewrites both predecessor interfaces.
- Critical shared files: owns `http/app.py`, new Admin modules/templates, and Admin-focused test files; coordinates only additive navigation edits in React. It SHALL NOT edit resolver call sites or canonical snapshot/generated schemas owned by siblings.
- Contract surface: `/admin` authentication; Host/proxy/headless boundaries; write-only secret controls; stale conflict UX; exactly three catalogue screens; mutation denial and safe error projection.
- Definition of done / evidence: login/logout/unauthorized/headless/proxy tests, secret-form leak checks, three-screen pagination/filter tests, mutation route denial, and adversarial persisted-field coverage pass.
- Parallel-safety rationale: predecessor interfaces are stable; its HTTP/Admin write zone is distinct from WP-03 resolvers and WP-05 canonical snapshot/frontend card files.

### WP-05 — Canonical snapshot and freshness cards

- Stage: `2`.
- Direct `depends_on`: `WP-02`.
- Task coverage: `4.2`–`4.3`, each covered exactly once.
- Independent deliverable: additive typed backup freshness in the canonical snapshot/OpenAPI/generated client and visible project/environment card rendering.
- Owned responsibility scope: monitor msgspec models, snapshot planning/collection, section outcomes, OpenAPI bridge/schema fixtures, generated TypeScript, React card rendering/styles/tests, and directly related snapshots.
- Critical shared files: owns `models/monitor.py`, `resources/monitor/*`, generated HTTP schema/client outputs, and monitor card files for this stage. It SHALL NOT edit Admin composition/catalogue custom views or resolver code.
- Contract surface: exact UTC `downloaded_at`, nonnegative age from snapshot time, explicit unknown, degraded freshness without hiding runtime, no second browser request.
- Definition of done / evidence: Python model/schema/runtime tests and React known/unknown/replacement/clock-skew tests pass; generated contract is current; existing monitor/runtime behavior remains compatible.
- Parallel-safety rationale: consumes WP-02's provenance query; its files do not overlap WP-03 and only a small navigation seam is reserved to WP-04.

### WP-06 — Packaging, documentation, and repeatable E2E harness

- Stage: `3`.
- Direct `depends_on`: `WP-03`, `WP-04`, `WP-05`.
- Task coverage: `5.1`–`5.3` and `6.1`, each covered exactly once.
- Independent deliverable: shippable optional dependency/assets package, reconciled docs/inventories/generated artifacts, and one repeatable non-secret numbered-slot E2E script/fixture set.
- Owned responsibility scope: final dependency lock/package/build rules, isolated install and compatibility tests, README/help/security recovery docs, architecture/characterization inventories, E2E script and non-secret fixtures, and directly generated documentation/code.
- Critical shared files: owns final `pyproject.toml`/`uv.lock`, build/package metadata, README/inventories, and `scripts/e2e_admin_secret_catalog.py`; production feature files are changed only for integration defects returned to their owning predecessor WP.
- Contract surface: wheel/sdist dashboard support; core-only imports; documented secure setup/recovery; parameterized candidate/test revisions; private inputs via files; failure-safe numbered-slot lifecycle.
- Definition of done / evidence: isolated core/dashboard installs and declared platform checks pass; docs match contracts; script has deterministic subcommands, provenance, sanitization, and cleanup without committed raw evidence.
- Parallel-safety rationale: it begins only after all product surfaces converge, avoiding lock/generated-file conflicts and preventing the E2E harness from encoding unstable interfaces.

### WP-07 — Integrated verification and publication evidence

- Stage: `4`.
- Direct `depends_on`: `WP-06`.
- Task coverage: `6.2`–`6.4`, each covered exactly once.
- Independent deliverable: focused/full repository verification, independent numbered-slot walkthrough with mandatory captures, cleanup proof, strict OpenSpec validation, and exact-revision evidence publication.
- Owned responsibility scope: verification and repair across the integrated change, Tester execution contract, Multica evidence/attachments/read-back, and directly related test fixes. It SHALL not broaden product scope or replace required live failures with assertions.
- Critical shared files: may repair integration defects across completed scopes with coordination; raw logs, JSON diagnostics, hashes, screenshots, and command output remain outside Git and are published only to Multica.
- Contract surface: all OpenSpec scenarios and E2E matrix cases; candidate/test SHA separation; GitLab MR/commit/file/CI links; image attachments; no secrets/private URLs; exact cleanup.
- Definition of done / evidence: required repository gates and selected live cases have recorded commands/exits/assertions, mandatory genuine images are attached/read back, candidate revision is exact, cleanup passes, and strict OpenSpec validation succeeds.
- Parallel-safety rationale: this is the sole final integration/evidence package after the product and harness are complete; no sibling writes remain.

## Frontier audit

- Stage 1 frontier: `WP-01` and `WP-02` are independent and have disjoint critical write zones.
- Stage 2 frontier: `WP-03`, `WP-04`, and `WP-05` are independent after their direct predecessors and reserve disjoint resolver, Admin, and snapshot/frontend zones.
- Stage 3 and 4 serialize generated packaging/harness convergence and final evidence; this does not erase the two earlier parallel frontiers.
- Every OpenSpec task is covered exactly once. Shared foundations are predecessors, direct dependencies are minimal, and no same-stage siblings own conflicting critical files.
