## Delivery basis

- **Task / change:** `MYL-565` / `fix-native-identity-user-env-mr-admin`.
- **Approved base:** `origin/main` at `12607a501bd85c455b8bbf7f4e857834d5b12347`.
- **Implementation boundary:** existing core SDK plus `packages/odcli-multica`; no production Odoo schema, external service, new dependency, second database, or custom frontend.
- **Estimate authority:** the planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` are the only numeric total authority. This document records confidence, assumptions, evidence, and topology without duplicating those totals.
- **Calibration/confidence:** medium confidence for one experienced developer familiar with Python, SQLite/Alembic, Starlette Admin, and GitLab APIs, without AI acceleration. The affected paths and close local analogues are inspectable; coupled uncertainty remains in catalog schema fingerprints, Starlette Admin route behavior after primary-key edits, and GitLab self-managed permission/response variants. No comparable completed-work timing series was supplied, so the estimate is uncalibrated.

## E2E decision

`e2e_required=true`.

The requester prefers to verify E2E personally, and no real Odoo or production GitLab mutation is justified: native commit identity, dotenv precedence, catalog migration, provider payloads, and encrypted-store atomicity have sufficient deterministic offline/component coverage. One residual gap remains: Starlette Admin generates its browser forms and post-edit navigation from view metadata, so Python unit assertions alone do not prove that identity fields are actually editable, technical revision is absent, blank secret controls remain safe, and a changed identity redirects to its new route. A single isolated baseline/candidate browser walkthrough closes that gap and supplies the mandatory before/after images for the visible UI change.

The project description identifies `TEST_REPOSITORY_URL` as the private Odoo scenario repository. This verification does not clone, modify, or publish to that repository because it starts no Odoo process and needs no Odoo data. No numbered `odcli-N` slot, PostgreSQL database, GitLab test branch, or test-repository MR is required. Infrastructure absence would block the selected Admin walkthrough rather than silently change `e2e_required`.

## Verification matrix

| Case ID | OpenSpec requirement/scenario coverage | Verification gap / local E2E decision | Public boundary | Test path and command | Independent Tester action | CI disposition | Assertions and evidence |
|---|---|---|---|---|---|---|---|
| `CAT-SET` | `catalog-settings`: canonical JSON, invalid/secret-like values, Admin CRUD/CAS, scoped MR key and validation | Lower-level checks prove storage and HTTP behavior; the settings page participates in `ADMIN-LIVE` only to prove generated form availability | `BackupCatalog` APIs and authenticated `/admin/settings/*` | New `tests/unit/storage/test_catalog_settings.py`, `tests/unit/test_http_admin_settings.py`; `uv run pytest -q -o addopts='' ...` | Inspect a fresh upgraded catalog, create/edit/delete scalar and structured values through Admin, and confirm stale route rejection | `offline-tests` for catalog model/migration; `dashboard-tests` for Admin | Revision, canonical stored JSON, exact type, timestamps, field errors, route ID, and no submitted value in diagnostics; focused command/exit output attached in Multica |
| `ENV-ONE` | `project-init`, `database-restore`, `gitlab-member-credentials`: grammar, owner-only files, process/project/user precedence, `ODCLI_USER_ROOT`, purpose limitation, admin reset flows, old-path transition/conflict | No live gap: pure file/selection behavior and CLI coordination are fully observable with isolated fixtures and mocked reset boundaries | User/project dotenv loaders and reset/credential resolvers | Extend `tests/unit/internal/test_project_env.py`, `test_admin_password_secret.py`, `test_gitlab_foundation.py`, and focused DB CLI tests; `uv run pytest -q -o addopts='' ...` | In an isolated root, inspect the three precedence tiers and sanitized failure messages; do not print secret values or run Odoo | `offline-tests`; existing real-Odoo jobs are regression-only and receive no new required scenario | Source provenance, selected value only inside private test assertions, no child/public leakage, old-only diagnostic, unequal-file failure before transport |
| `GIT-NATIVE` | `odoo-git-workflow` and `cli-odcli`: registered manifest-free checkout, tag/ticket policy, verified author/committer, stale/foreign evidence, safe dry-run, no-member compatibility | No local live E2E: temporary real Git repositories plus fake catalog/GitLab identity exercise every component interaction without private credentials | `odcli --project ... --env ... git commit` and `GitResource.commit_command()` | Extend `tests/unit/test_git_resource.py`, `tests/unit/test_cli_git.py`, and companion client tests; focused pytest command | Create a disposable local repository/worktree, stage one public placeholder file, inspect dry-run, execute one candidate commit, inspect `%an/%ae/%cn/%ce/%s`, then discard the repository | `offline-tests` and `compatibility` | Exact subject/Jira URL, equal verified author/committer, unchanged `HEAD` for every rejection, no manifest creation, no ambient identity; text/command/exit/SHA evidence in Multica, no PNG because output schema is unchanged |
| `MR-ASSIGN` | `gitlab-merge-request`: absent/default integer/default username/override, exact member, create/update, reviewer separation, failures, response verification | Real MR explicitly unnecessary and unsafe; fake HTTP transport and CLI component tests prove request order/payload/result deterministically | `odcli-multica gitlab mr publish`, GitLab Users/Project Members/MR API shapes | Extend `packages/odcli-multica/tests` and MR transport tests; `uv run pytest -q -o addopts='' packages/odcli-multica/tests ...` | Inspect captured GET/POST/PUT requests for the exact project and user; confirm no provider mutation call follows any invalid preflight | `offline-tests`; no new CI framework or credential | Exact `assignee_id`, no `reviewer_ids`, override provenance, membership preflight, create/update parity, response mismatch failure, redacted token; command/exit and relevant implementation file links in Multica |
| `SECRET-CAS` | `secret-store`: every business field, blank/replaced secrets, identity collision, stale tab/route, timestamps, new route, rollback | Store/component checks are sufficient for atomicity and secrecy; generated browser behavior is the selected `ADMIN-LIVE` gap | SQLCipher `SecretStore` and authenticated Admin secret routes | Extend `tests/unit/test_http_admin.py` plus focused store tests; `uv run pytest -q -o addopts='' -m dashboard ...` | Inspect old/new safe projections and encrypted-store lookups without exposing secret content; perform the live form walkthrough below | `dashboard-tests` | One row after key change, secret retained/replaced as requested, token key/created time stable, revision advanced, collision/stale rollback, old route refuses, no secret in HTML/logs; screenshots from `ADMIN-LIVE` |
| `CLI-DOC` | `cli-odcli`: unified path/precedence, native identity help, exact Multica `user_id` meaning for `--member-id`, assignee not reviewer, safe Rich/JSON/TOON | No live gap beyond `ADMIN-LIVE`; snapshot/help tests and review prove static text/output | CLI help, README, package README, frozen output models | Existing CLI help/output suites plus documentation review; focused pytest and `make lint` | Compare help and examples to spec, verify every `--member-id` surface names `multica workspace member list --output json` and distinguishes `user_id` from membership-row `id`/agent/squad IDs, scan for private hosts/personal data/secret literals, inspect three output modes | `offline-tests`, `lint`, `types` | Exact public placeholders, correct roster-field wording, consistent fields/provenance, no duplicate-secret recommendation, no reviewer wording, links to changed docs/files |
| `ADMIN-LIVE` | Visible portions of `catalog-settings` and `secret-store`: generated settings/secret edit forms and identity-change redirect | **Selected local E2E:** browser-rendered field/edit/navigation behavior is not fully proved by view-unit tests | Non-headless `odcli monitor` at loopback `/admin/` | No new script. Use existing `scripts/bootstrap_admin_config.py`, `uv run odcli monitor --host 127.0.0.1 --port PORT --no-open`, and browser actions | Run equivalent baseline and candidate instances with isolated roots; log in, open both secret edit forms, then on candidate edit every business field with blank secret, follow redirect, revisit old URL, and create/edit/delete one MR-assignee setting | Existing `dashboard-tests` remains automated coverage; live walkthrough is local-only because screenshots and generated navigation are the remaining gap | Baseline shows locked identity/editable revision defect; candidate shows editable business fields, no editable revision, empty secret control, successful new route, old route rejection, settings validation; actual PNGs and observations attached only to Multica |

## Automated verification ownership

The Implementer owns all test and fixture changes in the GitHub implementation branch: catalog migration/schema fixtures; dotenv/resolver tests; temporary-Git identity tests; fake GitLab HTTP/component tests; SQLCipher/Admin tests; CLI help/output tests; and any generated documentation or client artifact required by the repository checks. The Tester SHALL not edit those tests. The focused pre-review commands are:

```text
uv run pytest -q -o addopts='' tests/unit/storage/test_catalog_migration.py tests/unit/storage/test_catalog_settings.py
uv run pytest -q -o addopts='' tests/unit/internal/test_project_env.py tests/unit/internal/test_admin_password_secret.py tests/unit/test_gitlab_foundation.py tests/unit/test_git_resource.py tests/unit/test_cli_git.py
uv run pytest -q -o addopts='' packages/odcli-multica/tests
uv run pytest -q -o addopts='' -m dashboard tests/unit/test_http_admin.py tests/unit/test_http_admin_settings.py
make lint
make types
openspec validate fix-native-identity-user-env-mr-admin --strict
```

`offline-tests`, `dashboard-tests`, `compatibility`, `lint`, and `types` in `.github/workflows/ci.yml` are the existing CI owners. If the full local automatic suite is predicted to exceed ten minutes, or reaches ten minutes, stop only that owned test process and rely on those CI jobs at the published exact implementation revision; do not split the suite to bypass the limit. The planning phase runs none of these tests.

## `ADMIN-LIVE` walkthrough and images

### Readiness gate

1. Record baseline SHA `12607a501bd85c455b8bbf7f4e857834d5b12347`, candidate SHA, GitHub PR URL, clean status, Python/uv/browser versions, and two free loopback ports.
2. Build the existing dashboard assets for each exact checkout and install only the repository's dashboard/test groups. Complete focused automated results/review before consuming the live attempt.
3. Allocate separate owner-only `ODCLI_USER_ROOT` directories for baseline and candidate. Interactively create each `config/admin.toml` with the existing bootstrap script; do not record the test password. Create equivalent Remote Odoo and GitLab member records through Admin using public placeholder origins/member UUIDs and non-production secrets.
4. Start each monitor as a foreground-owned process or with a recorded exact PID and durable local log. Confirm loopback readiness and `/admin/` login before the walkthrough. Never terminate by executable name and never touch the Multica daemon PID.

### Manual actions and assertions

1. At fixed `1440x1000` viewport, English locale, default light color scheme, and browser zoom 100%, open the baseline Remote Odoo and GitLab member edit forms. Capture the identity fields and visible revision defect without displaying secret values.
2. Reset browser/session state, open the equivalent candidate forms, and confirm `base_url`, `member_id`, `host`, and `login` are editable as applicable; `revision`, `admin_id`, and timestamps are not editable; password/token controls are empty.
3. Submit changed identity/business fields with blank secret controls. Confirm success, the browser's new detail/edit URL, persisted safe values, unchanged secret behavior through a subsequent authenticated operation, and predictable refusal from the copied old URL.
4. In candidate Settings, create the exact public-placeholder MR-assignee key with a JSON username, provoke one invalid JSON/type error beside the value field, correct it, edit it to a positive integer, and delete it. Confirm no settings view exists on baseline.
5. Treat any missing field, visible technical revision input, secret placeholder/value, wrong redirect, accepted invalid value, stale old URL success, or console/server error as FAIL.

### Required captures

- `admin-remote-edit-baseline.png` and `admin-remote-edit-candidate.png` — equivalent Remote Odoo edit forms.
- `admin-gitlab-edit-baseline.png` and `admin-gitlab-edit-candidate.png` — equivalent GitLab member edit forms.
- `admin-settings-candidate.png` — valid candidate settings form/list result.
- `admin-settings-validation-candidate.png` — field-local invalid assignee value error.
- `admin-secret-route-candidate.png` — safe new route result after identity change, with private paths/session data cropped or redacted.

These are genuine browser screenshots, not model-generated images or terminal renders. File names, baseline/candidate SHA, viewport, action, result, and observation accompany each attachment. No image or raw log is committed.

## Evidence, attempts, and cleanup

- Budget one consolidated baseline/candidate walkthrough; one additional consolidated attempt is allowed only after a product/test-environment repair, with the first failure evidence retained. Do not spend attempts debugging setup before readiness.
- A case-level PASS records exact product SHA, fixture identities, commands/actions, exit statuses, and assertions. A new SHA requires impact assessment and rerun of affected cases; unchanged evidence remains valid.
- The Tester final Multica comment SHALL include the GitHub PR, exact candidate commit and relevant file links, CI/focused-test results, manual observations, commands/exit codes, sanitized server-log excerpts, and every required PNG as actual attachments with attachment read-back. It SHALL state `GitLab test MR: not applicable — no durable test-repository artifact was required` rather than inventing a branch or committing evidence.
- Raw outputs, screenshots, diagnostic JSON, hashes, SHAs, provenance, and assertion results live only in Multica. Git contains only ordinary implementation tests/fixtures/docs/generated product artifacts.
- Stop only the two recorded monitor PIDs, verify ports are released, and remove isolated user roots and browser profiles. Preserve the implementation PR/branch/commits and Multica evidence; do not close/merge the PR or delete its branch without a separate instruction.

## Delivery topology

`delivery_mode=dag`. The authoritative `Estimate, hours` planning-issue property is above the multi-WP threshold, and stage 1 contains four independently executable packages with non-conflicting critical write zones. Task coverage is complete and one-time: every `tasks.md` ID appears in exactly one package.

### `WP-01` — Catalog settings foundation

- **Task coverage:** `1.1`, `1.2`, `1.3`.
- **Deliverable:** an upgraded catalog with canonical typed settings APIs and authenticated revision-safe Admin CRUD, including MR-assignee key validation.
- **depends_on:** none.
- **Owned responsibility scope:** catalog SQLAlchemy metadata, Alembic revision and revision/fingerprint/fixture compatibility, a settings catalog mixin/projections, the new Admin settings view and its mount/export, and focused catalog/settings Admin tests. Critical shared files include `storage/catalog_schema.py`, `storage/catalog_migrate.py`, `storage/catalog_migrations/`, `storage/catalog/__init__.py`, `http/admin.py`, and new settings-specific tests/fixtures. Directly related fixtures, docs comments, generated schema metadata, and helper files are included.
- **Contract surface:** canonical JSON value types/serialization; key validation; revision-bound catalog CRUD; exact `gitlab.mr.default_assignee:https://<host>/<project>` validation; public catalog lookup consumed later by `WP-05`.
- **DoD/evidence:** fresh/legacy upgrade and downgrade checks, schema-equivalence checks, typed CRUD/CAS tests, authenticated Admin create/edit/delete/validation tests, secret-like-key rejection, and focused lint/type results pass with no existing-row loss.
- **Parallel safety:** it does not edit dotenv, Git commit, SQLCipher secret-view, or companion MR publisher files. It exclusively owns `http/admin.py` during stage 1; settings tests use a new test module rather than `test_http_admin.py`.
- **stage:** `1`.

### `WP-02` — Unified user dotenv and secret resolution

- **Task coverage:** `2.1`, `2.2`, `2.3`.
- **Deliverable:** one strict owner-level dotenv source with fixed precedence, purpose-limited administrator-password consumption, and conflict-safe legacy GitLab-token fallback.
- **depends_on:** none.
- **Owned responsibility scope:** shared/project/user dotenv parsing/loading, user-root path helpers, administrator-password resolver and applicable DB/init/restore/refresh/replacement entry points, legacy GitLab credential resolver, and their focused unit/CLI tests. Critical shared files include `internal/project_env.py`, `internal/admin_password.py`, `internal/gitlab_credentials.py`, `internal/paths.py`, applicable `commands/db.py`/preparation call sites, and the named tests in `ENV-ONE`.
- **Contract surface:** immutable process > project > user selection; strict owner-only grammar and errors; encrypted-store priority; explicit environment-path override; old-only/equal/conflicting legacy file rules; private-only secret propagation.
- **DoD/evidence:** precedence, unsafe-file, `ODCLI_USER_ROOT`, missing/malformed, old-path transition/conflict, all applicable reset coordinators, child isolation, and public-redaction regressions pass in focused offline tests.
- **Parallel safety:** it does not edit catalog schema/Admin mounts, Git member-commit files, SQLCipher update/Admin secret views, or companion MR publisher files.
- **stage:** `1`.

### `WP-03` — Registered native checkout commit identity

- **Task coverage:** `3.1`, `3.2`.
- **Deliverable:** manifest-free registered native checkouts retain project-aware Odoo commit policy and exact verified member author/committer identity, with binding drift rejected before commit.
- **depends_on:** none.
- **Owned responsibility scope:** project-aware member-commit classification/binding capture and revalidation, repository snapshot integration, related core/companion commit adapters, and temporary-Git SDK/CLI tests. Critical shared files include `resources/git_member_commit.py`, narrowly related `resources/git.py`/runtime or catalog-binding helpers, `commands/git.py` only where needed for native behavior, and Git-focused tests.
- **Contract surface:** selected registered project/environment binding; canonical worktree/Git-common-dir/origin proof; immutable `GIT_AUTHOR_*`/`GIT_COMMITTER_*`; tag/ticket/module policy; unchanged generic/no-member compatibility.
- **DoD/evidence:** manifest-free dry-run and real temporary commit, exact subject/link/identity, no manifest creation, stale/foreign/host/member failures with unchanged `HEAD`, safe preview, and compatibility tests pass.
- **Parallel safety:** it does not edit dotenv resolution, catalog settings/Admin files, SQLCipher secret views, or companion MR publication. `commands/git.py` help-only changes are reserved for `WP-06` after stage 1.
- **stage:** `1`.

### `WP-04` — Atomic encrypted-secret identity editing

- **Task coverage:** `5.1`, `5.2`, `5.3`.
- **Deliverable:** both existing secret views edit every business field through atomic identity-changing compare-and-swap updates while retaining blank secrets and hiding technical metadata.
- **depends_on:** none.
- **Owned responsibility scope:** SQLCipher update methods/projections, opaque secret route decoding/use, Remote Odoo and GitLab member Admin views/validation, and encrypted-store/authenticated Admin tests. Critical shared files include `internal/secret_store.py`, `http/admin_secrets.py`, and `tests/unit/test_http_admin.py`; `http/admin.py` is excluded because `WP-01` owns the shared mount and existing secret views require no remount.
- **Contract surface:** old identity plus proposed normalized identity; route revision as CAS token; blank-secret retention; stable token key/created time; new route projection; field-local collision/validation and stale conflict.
- **DoD/evidence:** individual/combined field edits, blank/replaced secrets, normalized collision, stale tabs/routes, timestamps/revisions, redirect/new and refusal/old routes, authorization, atomic rollback, and response/log/OpenAPI redaction pass under dashboard tests.
- **Parallel safety:** it does not edit catalog metadata/settings modules, dotenv/Git credential resolver, native commit files, or companion MR publisher files; its Admin test module is distinct from `WP-01`'s new settings test module.
- **stage:** `1`.

### `WP-05` — Configured MR assignment

- **Task coverage:** `4.1`, `4.2`, `4.3`.
- **Deliverable:** existing `odcli-multica gitlab mr publish` consumes the exact catalog default or explicit override, preflights one project member, applies `assignee_id` on create/update, and verifies safe response identity.
- **depends_on:** `WP-01`.
- **Owned responsibility scope:** companion MR command/client/transport/models and tests plus read-only consumption of the completed core settings API. Critical shared files include `packages/odcli-multica/src/odcli_multica/{cli,client,gitlab,models}.py` as applicable and companion/provider tests. It SHALL not revise the `WP-01` catalog contract except through a planning revision.
- **Contract surface:** exact derived setting key; integer-or-username value; explicit override; absent behavior; exact Users and Project Members lookups; `assignee_id` rather than `reviewer_ids`; returned ID/username/source; provider-consistency failure.
- **DoD/evidence:** fake-HTTP create/update parity, absent/default/override, host/project isolation, numeric and exact username paths, inactive/non-member/ambiguous/permission errors before mutation, reviewer separation, response mismatch, dry-run, and secret-free Rich/JSON/TOON tests pass.
- **Parallel safety:** it starts only after the settings API stabilizes. It does not edit core catalog implementation, dotenv, native commit, or secret Admin files.
- **stage:** `2`.

### `WP-06` — Public guidance and consolidated verification

- **Task coverage:** `6.1`, `6.2`.
- **Deliverable:** coherent public help/docs plus one consolidated checked implementation revision ready for independent `ADMIN-LIVE` verification and ordinary CI.
- **depends_on:** `WP-02`, `WP-03`, `WP-04`, `WP-05`.
- **Owned responsibility scope:** README/package/docs examples, CLI help strings and frozen safe projection documentation across completed components, OpenSpec consistency fixes if implementation reveals no scope change, cross-component integration glue, and repository lint/type/focused verification. Critical shared files are documentation and any final help/output registration files; predecessor code is touched only for integration defects discovered by the consolidated checks, with the full affected focused suite rerun.
- **Contract surface:** one recommended dotenv and precedence; compatibility diagnostic; native commit help; `--member-id` means the person's Multica account `user_id` from the documented roster command rather than membership-row/agent/squad IDs; MR assignee-versus-reviewer wording and output; Admin field behavior; implementation SHA/evidence handoff.
- **DoD/evidence:** every `--member-id` help/documentation surface names the lookup command and correct `user_id` field and has focused help/snapshot coverage; all commands in Automated verification ownership complete within the local-time rule or are delegated to CI at the exact published SHA; strict OpenSpec, lint, type, offline/dashboard focused tests and public-placeholder/privacy review succeed; no numeric estimate totals or private data enter Git/docs.
- **Parallel safety:** all feature predecessors are complete before it begins, so documentation/help and any cross-component repair cannot race sibling writes.
- **stage:** `3`.

### Stage map and execution frontiers

| WP | Stage | Direct predecessors |
|---|---:|---|
| `WP-01` | 1 | none |
| `WP-02` | 1 | none |
| `WP-03` | 1 | none |
| `WP-04` | 1 | none |
| `WP-05` | 2 | `WP-01` |
| `WP-06` | 3 | `WP-02`, `WP-03`, `WP-04`, `WP-05` |

Stage numbers are consecutive topological levels. The first frontier contains four independent packages; after `WP-01`, `WP-05` may proceed while remaining stage-1 packages finish, and `WP-06` begins only after every direct predecessor completes. WIP limits are operational and are not encoded by additional dependencies.
