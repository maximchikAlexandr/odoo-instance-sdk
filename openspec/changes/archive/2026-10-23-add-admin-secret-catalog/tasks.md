## 1. Encrypted store and administrator boundary

- [x] 1.1 Add the optional dashboard secret-management dependencies and the two required administrator/key/store paths by reusing existing owner-only `O_NOFOLLOW` and atomic-file patterns, with core-import/package compatibility coverage and no generic filesystem/config framework.
- [x] 1.2 Implement one direct SQLCipher DBAPI adapter with schema initialization for remote Odoo and GitLab identities, stable internal token keys, record revisions, atomic key/database creation, and fail-closed partial/wrong/corrupt store handling.
- [x] 1.3 Implement minimal create/update/delete methods on that adapter for direct Admin use, with write-only fields, blank-on-edit retention, compare-and-swap conflicts, bounded SQLite busy handling, complete redaction tests, and no separate Query/Command/service layer.
- [x] 1.4 Mount Starlette Admin authentication/session handling in non-headless monitor composition, add explicit secret resource views, and test login/logout, unsafe configuration, unauthorized access, proxy/Host behavior, and headless exclusion without editing the monitor React application.

## 2. Credential resolver integration

- [x] 2.1 Add one normalized-URL encrypted lookup before the existing remote Odoo named/legacy environment chain, preserving MYL-515 nullable-database discovery and all current fallback order.
- [x] 2.2 Add one member-ID/normalized-host encrypted lookup before the existing GitLab mapping/environment chain while preserving the generated token-key contract and transport redaction.
- [x] 2.3 Add public-boundary hit/absent/broken-store/invalid-record matrices for remote refresh/COPY and GitLab operations, proving that only absence falls back and no password/token/database leaks through plans, output, errors, logs, or repr.

## 3. Read-only catalogue Admin

- [x] 3.1 Extend the existing `BackupProjection`/keyset page and event reads with only the additional safe fields, typed filters, and stable order required for backup summaries and event details.
- [x] 3.2 Extend existing environment/event reads with one shared allowlisted projection per remaining screen, sanitizer-backed messages, and no unsafe selected columns or duplicate DTO/query layer.
- [x] 3.3 Register exactly three read-only Admin resources with pagination/filters and deny all catalogue create/edit/delete/import/export/restore actions.
- [x] 3.4 Add query, HTTP/Admin, authorization, injection, pagination, mutation-denial, and unsafe-field regression tests with adversarial stored paths/JSON/messages.

## 4. Backup freshness in the canonical monitor

- [x] 4.1 Add an exact current-database provenance query covering completed catalogue restore, replacement/drop/bootstrap/local-archive/incomplete/missing/null/mismatched-cluster cases.
- [x] 4.2 Extend frozen monitor models, snapshot planning/collection, msgspec/OpenAPI bridge, and generated TypeScript with exact UTC download time, nonnegative age, and explicit unknown state.
- [x] 4.3 Add the same-origin Admin navigation link and render download timestamp/age or the exact unknown label on project and environment database cards without a second request; own all shared monitor React application/styles/tests and add Python schema plus React navigation/known/unknown/clock-skew regressions.

## 5. Packaging, documentation, and compatibility

- [x] 5.1 Update lockfiles, wheel/sdist asset rules, dashboard isolated-install checks, supported-platform dependency checks, and optional-import inventories for Admin/auth/SQLCipher support.
- [x] 5.2 Document administrator config creation, key/store paths and permissions, recovery/fallback behavior, secret identities, conflict handling, read-only screens, and backup-freshness meaning without publishing private URLs or secret examples.
- [x] 5.3 Reconcile canonical CLI/dashboard characterization, architecture/line-limit inventories, and generated OpenAPI/frontend artifacts while keeping headless and core-only behavior unchanged.

## 6. Verification and evidence

- [x] 6.1 Extend only the existing dashboard/package, real-Odoo, GitLab workflow, and test-repository numbered-slot scripts/fixtures needed by the accepted cases; keep browser steps explicit and do not add an umbrella coordinator, parallel validator, captures, outputs, diagnostics, or raw evidence to Git.
- [x] 6.2 Run focused secret-store, resolver, catalogue, monitor HTTP/snapshot, React, package, compatibility, formatting, Ruff, strict typing, coverage/architecture, and applicable real-Odoo/GitLab checks.
- [x] 6.3 Execute the independent numbered `odcli-N` E2E contract against the project test repository, capture genuine before/after browser images and required text/log evidence, perform failure-safe slot cleanup, and retain the evidence MR/branch/commits.
- [x] 6.4 Run strict OpenSpec and repository validation and publish exact product/test revisions, commands, exit codes, assertions, MR/commit/file/CI links, image attachments, hashes, provenance, and attachment read-back in Multica.

## Completion evidence and remaining gates

Completion records implementation and executed automated checks; it does not waive independent review, test re-grading, or live acceptance.

- Tasks 1.1–1.4: accepted encrypted-store/Admin work packages and consolidated fixes implement private configuration/key/store lifecycle, write-only CAS mutations, authentication/session/proxy/headless boundaries, bounded login attempts, and owner-only bootstrap. Dashboard and packaging jobs pass.
- Tasks 2.1–2.3: accepted resolver work package and explicit public credential-boundary regression cases preserve encrypted-first lookup, absence-only fallback, database selection, and redaction. Core-only and dashboard CI jobs pass with their intended dependency scopes.
- Tasks 3.1–3.4 and 4.1: accepted safe catalogue/provenance work packages and focused query/HTTP regression cases cover bounded projections, filters, pagination, mutation denial, completed-restore identity, replacement and unknown states. Catalogue reads and timestamp decoding retain focused shared boundaries.
- Tasks 4.2–4.3: accepted canonical snapshot/generated client/monitor work package implements freshness and same-origin Admin navigation. Generated-contract, React and dashboard CI checks pass; genuine browser evidence is accepted under task 6.3.
- Tasks 5.1–5.3: optional dependencies, isolated core/dashboard packages, bootstrap/recovery documentation, targeted lock security fixes, module line limits, formatting, architecture inventories, strict types and generated contracts are implemented and validated. All four R3 profiles are CLEAN at reviewed executable SHA 32cd5ff41224754373a7e1090a515008a63b0a8e.
- Task 6.1: the accepted packaging/integration work package extends existing dashboard/package verification and the update script; no umbrella runner or raw evidence is added to Git. Live private-repository setup/fixtures and browser actions remain subject to task 6.3 readiness and acceptance.
- Task 6.2: automated offline, dashboard, package, lint, strict types, Ubuntu/macOS compatibility, Windows preparation and cold/warm Odoo smoke jobs passed in GitHub Actions run 39830440383 for product commit 38662dfe8cfe186a4d1b779e866e5c19b493acd1. Original-production/original-test baseline 5ed09507e6b892d28804f6aef63720dfdc5675e8 and that candidate use identical 11 test-file paths, configuration, prerequisites and production roots: line coverage 4674/9762 to 4858/9887; branch coverage 1041/3100 to 1085/3128. Baseline has three recorded pre-fix failures and no skips; candidate has 313 passing cases and no skips. Public OpenAPI command fails before the fix and passes with canonical output afterwards. Long suites and coverage run only in GitHub Actions under the owner execution policy. Live private Odoo/GitLab case evidence is still required by task 6.3.
- Task 6.3: independent E2E Run5 accepted on executable SHA 32cd5ff41224754373a7e1090a515008a63b0a8e and test/helper SHA 09522ab62cfde601a3db4918f150652ed8bf2499, carrying verified original Run2–4 results. E2E-01..08, malformed persisted GitLab record, genuine baseline/candidate known-provenance cards and final task-owned cleanup passed. Required images, safe snapshot projections, hashes and attachment read-back are retained in Multica; no media or raw/private responses are committed. Counters: E2E 5/8, fix 2/6, review round 3.
- Task 6.4: exact reviewed/tested revisions, commands, outcomes, scenario ledger, genuine image attachments, provenance and download/read-back are published in Multica. Exact executable CI run 39838651177 has all 10 canonical jobs green. Strict OpenSpec validation and documentation-only spec synchronization/archive complete this record; final history/public PR readiness remains a Manager publication gate, without any claim of a new executable test run.
