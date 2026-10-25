## Delivery contract

- Task key: `MYL-560`
- OpenSpec change: `fix-gitlab-credentials-disk-accounting`
- Approved base: `origin/main` at the planning base SHA recorded in the publication comment.
- Scope source: GitHub issue #176 plus the proposal, design, delta specs, and tasks in this change.
- Estimate source of truth: the planning issue custom properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; numeric estimate totals are intentionally not duplicated here.
- Estimate confidence: medium, uncalibrated for one experienced project developer; the implementation paths and local analogues are inspectable, while project-wide storage identity/de-duplication and generated-contract repair may add bounded friction.

## Delivery topology

`delivery_mode=single_wp_no_dag`

### WP-MYL-560-01 — Encrypted credentials and complete Disk accounting

- Task coverage: OpenSpec tasks 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 4.1, 4.2, and 4.3 exactly once.
- Deliverable: one reviewable implementation revision in which `odcli-multica` uses encrypted-store-first GitLab credentials with the exact legacy fallback, the monitor exposes a de-duplicated project disk aggregate and backup subtotal, and the dashboard renders honest Disk metrics for running and stopped cards.
- Owned responsibility scope: bridge credential composition and its package tests; monitor footprint models, planning/collection/storage logic and catalogue/measurement integration; OpenAPI and generated TypeScript contracts; dashboard card/style/component tests; directly related fixtures, snapshots, documentation, and verification files. Critical shared files include `packages/odcli-multica/src/odcli_multica/client.py`, `src/odoo_instance_sdk/internal/gitlab_credentials.py` (reuse contract, changed only if a narrowly necessary public adapter is required), `src/odoo_instance_sdk/models/footprint.py`, monitor collection modules, the checked OpenAPI/generated client, and `src/odoo_instance_sdk/web/src/App.tsx`.
- Contract surface: exact root-member/HTTPS-host credential precedence and fail-closed redaction; `ProjectStorageFootprint.backup_bytes` plus aggregate/completeness semantics; monitor snapshot/OpenAPI/codegen compatibility; Disk placement, project scope text, and stopped/incomplete presentation.
- Definition of done and evidence: every OpenSpec task is checked only after implementation; focused package/Python/dashboard/codegen tests cover every verification-matrix row; lint/types and applicable build/package checks meet the 10-minute local-suite policy; final diff contains no secrets, E2E scripts, evidence artifacts, new dependency, or duplicate subsystem; independent Tester reports the exact candidate SHA, commands, exit codes, assertions, and MR/commit/file links in Multica with successful read-back.
- Parallel-safety rationale: the credential, public DTO/collector, generated client, and shared card changes converge in common package and contract files and must ship atomically. A single WP avoids cross-package version skew and is mandatory under the estimate property threshold.

## E2E decision

`e2e_required=false`.

The root issue explicitly requests delivery without E2E tests because the user will perform the live check. The changed credential behavior terminates at existing private Git/GitLab boundaries and can be proven with injected encrypted-store records, captured Git/HTTP fakes, redaction assertions, and failure-path unit tests. The disk computation can be proven deterministically with catalogue, filesystem, PostgreSQL-size, and filestore fixtures. The visible card placement and stopped-state behavior can be proven with the existing React component test harness and generated-contract checks. These checks close the specified behavior gaps without a real GitLab mutation, Odoo instance, database server, browser walkthrough, or the project's GitLab E2E repository.

No local E2E script, disposable `odcli-N` slot, live GitLab push/MR, browser walkthrough, before/after image, terminal render, or E2E attempt is planned. This is an explicit scope decision, not an infrastructure waiver. The user's own later manual verification is outside the delivery acceptance evidence and SHALL NOT be reported as a planned or completed Tester result.

## Verification matrix

| Case ID | OpenSpec requirement/scenario | Verification gap / local E2E decision | OpenAPI operation/schema | Local test script | Independent Tester steps | CI disposition / test / job | Assertions | Artifacts / images |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CRED-01 | Host-scoped mapping: encrypted-only resolution and precedence | Lower-level coverage is sufficient; no live GitLab mutation is needed | None | None | Run focused bridge credential tests; inspect that the bridge delegates to the core resolver and that fixtures contain no real secret | Extend `packages/odcli-multica/tests/test_credentials.py`; run in existing package test job | Encrypted login/token wins without mapping/env; conflicting legacy values are ignored; returned public identity is secret-free | Multica command/exit-code and commit/file links; no image |
| CRED-02 | Exact-absence fallback, broken-store no-fallback, exact member/host rejection | Deterministic resolver failure branches fully expose the contract | None | None | Run focused negative matrix; inspect captured Git/GitLab calls remain empty on rejection | Extend package credential tests in the existing package test job | Exact absence uses current mapping/env precedence and overrides; corrupt/mismatched/ambiguous inputs fail before child/request | Multica command/exit-code and file links; no image |
| CRED-03 | Shared identity consumed by push/sync/passthrough/MR and redacted | Existing fake Git resource and fake GitLab transport are sufficient | None | None | Run focused bridge Git and MR tests; inspect captured argv, URL, environment projections, logs, and exceptions | Extend `packages/odcli-multica/tests/test_gitlab.py` and relevant CLI tests | Encrypted-only credential reaches private helper/authorization channel; token is absent from public surfaces | Multica command/exit-code and file links; no image |
| DISK-01 | Project aggregate arithmetic and de-duplication | Filesystem/catalogue/database fakes provide exact byte accounting; no live database is needed | `ProjectStorageFootprint` in monitor snapshot | None | Run focused monitor storage tests; inspect fixture identities and exact expected sums | Extend `tests/unit/test_monitor_project_storage.py` and monitor schema/contract tests in existing Python CI | Main/artifact, working environments, distinct DB/filestore and backups sum once; backup subtotal is exact; unrelated project data excluded | Multica test output summary and file links; no image |
| DISK-02 | Zero backups, missing components, stopped runtimes, cache/partial isolation | Deterministic failure injection and runtime models are sufficient | `ProjectStorageFootprint` in monitor snapshot | None | Run focused failure/cache tests and inspect stopped snapshot fixtures | Extend monitor/storage unit tests in existing Python CI | Zero is distinguished from unavailable; known totals survive failures with `complete=false`; stopped storage remains measurable; TTL cache is reused | Multica test output summary and file links; no image |
| API-01 | Additive backup subtotal reaches the dashboard client | Existing OpenAPI generation contract detects drift | Monitor snapshot response / `ProjectStorageFootprint` schema | None | Run codegen check and inspect generated diff is tool-produced | Existing dashboard codegen check and generated-contract test | OpenAPI and generated TypeScript include nullable `backup_bytes`; checked files match generator output | Multica command/exit-code plus generated-file links; no image |
| UI-01 | Disk metric row, project subtotal/scope, stopped and incomplete states | React component tests provide DOM-level visible-output evidence; user explicitly excluded E2E/screenshots | Consumes `ProjectStorageFootprint` | None | Run focused Vitest component test; inspect DOM assertions for project and worktree cards | Extend `src/odoo_instance_sdk/web/src/App.test.tsx` in existing dashboard test job | `Disk` shares metric row with CPU/RAM; stopped cards retain Disk; project subtotal/scope appear; `Resources` is absent; unavailable/incomplete stay honest | Multica command/exit-code and file links; no image by explicit E2E exclusion |

## Independent evidence and ownership

- Implementer owns every code, fixture, unit/component/contract test, generated OpenAPI/client file, and any necessary documentation change described by OpenSpec tasks 1.1–4.3.
- Tester does not edit implementation or test files. Tester independently runs the focused existing test commands on the exact candidate revision, reviews resolver/collector/public-schema diffs, checks the no-secret/no-generated-evidence constraints, and reports findings.
- The Tester final Multica comment SHALL record the exact product SHA, commands and exit codes, focused assertions, and GitHub MR/commit/file links, then read back the posted comment. Raw logs, diagnostics, hashes, and any incidental captures belong only in Multica and SHALL NOT be committed.
- There are no image artifacts for this plan. If implementation changes scope so deterministic package/unit/component checks no longer close a material cross-component gap, planning must be revised instead of silently adding a live run.

## Cleanup and publication

No live stand, external mutation, test branch in the GitLab scenario repository, or disposable database is created by this plan, so no E2E cleanup applies. Normal implementation publication preserves the evidence MR/branch/commits; merge, closure, or branch deletion requires separate authorization.
