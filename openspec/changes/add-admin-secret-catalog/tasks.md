## 1. Encrypted store and administrator boundary

- [ ] 1.1 Add the optional dashboard secret-management dependencies, owner-only administrator/key/store path models, strict regular-file/mode validation, and core-import/package compatibility coverage.
- [ ] 1.2 Implement SQLCipher schema initialization for remote Odoo and GitLab identities, stable internal token keys, record revisions, atomic key/database creation, and fail-closed partial/wrong/corrupt store handling.
- [ ] 1.3 Implement typed secret create/update/delete commands with write-only fields, blank-on-edit retention, compare-and-swap conflicts, bounded SQLite busy handling, and complete redaction tests.
- [ ] 1.4 Mount Starlette Admin authentication/session handling in non-headless monitor composition, add explicit secret resource views and monitor navigation, and test login/logout, unsafe configuration, unauthorized access, proxy/Host behavior, and headless exclusion.

## 2. Credential resolver integration

- [ ] 2.1 Add one normalized-URL encrypted lookup before the existing remote Odoo named/legacy environment chain, preserving MYL-515 nullable-database discovery and all current fallback order.
- [ ] 2.2 Add one member-ID/normalized-host encrypted lookup before the existing GitLab mapping/environment chain while preserving the generated token-key contract and transport redaction.
- [ ] 2.3 Add public-boundary hit/absent/broken-store/invalid-record matrices for remote refresh/COPY and GitLab operations, proving that only absence falls back and no password/token/database leaks through plans, output, errors, logs, or repr.

## 3. Read-only catalogue Admin

- [ ] 3.1 Add typed bounded catalogue page queries and stable filters/order for backup summaries and allowlisted backup event details.
- [ ] 3.2 Add typed bounded projections for recorded incomplete operations/errors and environment history, with sanitizer-backed messages and no unsafe selected columns.
- [ ] 3.3 Register exactly three read-only Admin resources with pagination/filters and deny all catalogue create/edit/delete/import/export/restore actions.
- [ ] 3.4 Add query, HTTP/Admin, authorization, injection, pagination, mutation-denial, and unsafe-field regression tests with adversarial stored paths/JSON/messages.

## 4. Backup freshness in the canonical monitor

- [ ] 4.1 Add an exact current-database provenance query covering completed catalogue restore, replacement/drop/bootstrap/local-archive/incomplete/missing/null/mismatched-cluster cases.
- [ ] 4.2 Extend frozen monitor models, snapshot planning/collection, msgspec/OpenAPI bridge, and generated TypeScript with exact UTC download time, nonnegative age, and explicit unknown state.
- [ ] 4.3 Render download timestamp/age or the exact unknown label on project and environment database cards without a second request, and add Python schema plus React known/unknown/clock-skew regressions.

## 5. Packaging, documentation, and compatibility

- [ ] 5.1 Update lockfiles, wheel/sdist asset rules, dashboard isolated-install checks, supported-platform dependency checks, and optional-import inventories for Admin/auth/SQLCipher support.
- [ ] 5.2 Document administrator config creation, key/store paths and permissions, recovery/fallback behavior, secret identities, conflict handling, read-only screens, and backup-freshness meaning without publishing private URLs or secret examples.
- [ ] 5.3 Reconcile canonical CLI/dashboard characterization, architecture/line-limit inventories, and generated OpenAPI/frontend artifacts while keeping headless and core-only behavior unchanged.

## 6. Verification and evidence

- [ ] 6.1 Add `scripts/e2e_admin_secret_catalog.py` and only its required non-secret fixtures for repeatable numbered-slot browser/credential/provenance verification; keep captures, outputs, diagnostics, and raw evidence outside Git.
- [ ] 6.2 Run focused secret-store, resolver, catalogue, monitor HTTP/snapshot, React, package, compatibility, formatting, Ruff, strict typing, coverage/architecture, and applicable real-Odoo/GitLab checks.
- [ ] 6.3 Execute the independent numbered `odcli-N` E2E contract against the project test repository, capture genuine before/after browser images and required text/log evidence, perform failure-safe slot cleanup, and retain the evidence MR/branch/commits.
- [ ] 6.4 Run strict OpenSpec and repository validation and publish exact product/test revisions, commands, exit codes, assertions, MR/commit/file/CI links, image attachments, hashes, provenance, and attachment read-back in Multica.
