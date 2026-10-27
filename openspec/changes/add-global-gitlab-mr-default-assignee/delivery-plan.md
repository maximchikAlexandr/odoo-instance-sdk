## Delivery basis

- **Task / change:** `MYL-565` follow-up / `add-global-gitlab-mr-default-assignee`.
- **Approved code baseline:** implemented MYL-565 head `f5c6a03f77d74cd462fa9532e5ac8fe710fd92c5`, or a descendant containing the same catalog and MR-assignment contracts. The completed MYL-565 ledger is read-only.
- **Implementation boundary:** the existing catalog setting validator and Admin surface, `packages/odcli-multica` MR command/client, focused tests, CLI help, and package documentation. No schema migration, new dependency, provider, service, or production-code work outside this follow-up.
- **Estimate authority:** planning-issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` are the only numeric total authority. This document records confidence, assumptions, evidence, and topology without duplicating those totals.
- **Estimate basis:** remaining work for one experienced developer familiar with the Python/Click/SQLite/pytest codebase, without AI acceleration. Confidence is high because requirements, source seams, and close tests are directly inspectable; uncertainty is bounded to review repairs and interaction with the not-yet-merged baseline. The estimate is uncalibrated because no comparable historical timing series was supplied. Tests were not run for estimation.

## E2E decision

`e2e_required=false`.

The changed behavior is one deterministic catalog fallback inside the existing MR command. The catalog accepts one additional exact key, and the resolver performs at most one additional read before reusing the already tested exact-user, membership, mutation, and response-verification path. Focused catalog/Admin component tests, a catalog-spy client matrix, and the existing fake GitLab HTTP tests can prove every affected WHEN/THEN branch, including lookup order and absence of provider mutation after invalid input. A live GitLab MR would add private credentials and irreversible provider state without closing a remaining contract gap.

The project description names `TEST_REPOSITORY_URL` for Odoo scenarios; it is not used because this change starts no Odoo process and requires no GitLab repository mutation. No numbered `odcli-N` slot, PostgreSQL database, browser session, test-repository branch/MR, or local E2E script is required. Infrastructure unavailability therefore does not hide a necessary live check.

No before/after images are required: the change does not alter a CLI table/read result, logs, progress, final summary, or browser UI. The static `--help` wording and dry-run plan fields are captured and asserted as text; a terminal PNG would be decorative rather than evidence of the selection mechanism.

## Verification matrix

| Case ID | OpenSpec requirement/scenario coverage | Verification gap / local E2E decision | Public boundary | Test path / command | Independent Tester action | CI disposition | Assertions / artifacts |
|---|---|---|---|---|---|---|---|
| `CAT-GLOBAL` | `catalog-settings`: valid global/scoped values; invalid value; independent deletion | Lower-level catalog and authenticated Admin component tests fully observe validation, type preservation, transaction, and row isolation; no live gap | `BackupCatalog` setting APIs and `/admin/settings/*` | Extend `tests/unit/storage/test_catalog_settings.py` and `tests/unit/test_http_admin_settings.py`; run focused pytest nodes | On the exact candidate, inspect the global-key create/edit/delete requests and persisted rows in an isolated temporary catalog; confirm no other scoped row changes | Existing `offline-tests` and `dashboard-tests`; no new job | Positive integer/string round-trip; invalid scalar rejected field-locally; global/scoped rows remain independent; focused text output in Multica only |
| `MR-PRECEDENCE` | `gitlab-merge-request`: absent, project-over-global, global fallback, explicit-over-both, numeric selected default | Catalog-spy client tests make every read and selected argument observable; fake GitLab HTTP tests already prove the downstream exact-user/member/payload path | `odcli-multica gitlab mr publish` command/client | Extend `packages/odcli-multica/tests/test_gitlab.py`; run focused pytest nodes | Review the captured catalog-key sequence and provider arguments for each parametrized case, then inspect one `--dry-run` plan without provider access | Existing `offline-tests` | Exact order `override → project → global → none`; no catalog reads for explicit input; no global read after present project row; existing `default` result compatibility |
| `MR-FAIL-CLOSED` | invalid selected value fails without fallback | Deterministic invalid-value/client and fake-transport tests prove the negative path more precisely than a live provider | Same command/client and existing bounded GitLab transport | Add invalid project/global/explicit cases beside existing preflight tests | Independently inspect that lower-priority catalog reads and GitLab mutation calls are absent after the selected source fails | Existing `offline-tests` | Stable sanitized error; no lower-priority user; no POST/PUT; no token/value leakage |
| `PLAN-HELP-DOC` | Inspectable candidate keys and documented precedence/non-goals | Text assertions and documentation review cover the only visible change; no visual E2E gap | CLI `--help`, immutable dry-run plan, package README | Extend `packages/odcli-multica/tests/test_output_contract.py` and dry-run assertions; run `odcli-multica gitlab mr publish --help` in tests | Compare baseline and candidate help/plan text at fixed width, verify both keys and ordering, and scan docs for private hosts or personal data | Existing `offline-tests`, `lint`, and `types` | Both candidate keys and deferred resolution are safe; help states explicit/project/global order and assignee-not-reviewer; no secret/provider payload |
| `REPO-GATES` | All tasks and unaffected regression contracts | Existing repository checks are sufficient; no new E2E framework | Repository quality gates | Focused tests above, `make lint`, `make types`, strict OpenSpec validation, and applicable offline tests | Confirm exact candidate SHA, clean tree, commands/exit codes, and affected-file diff; Tester does not edit implementation tests | Existing `lint`, `types`, `offline-tests`, `dashboard-tests`, and `compatibility` as applicable | Exact revision and checks linked in Multica; generated logs/diagnostics are not committed |

## Automated verification ownership

The Implementer owns all changes to catalog/Admin tests, `packages/odcli-multica` client/output tests, fixtures, help, and documentation in the implementation branch. The Tester SHALL not edit them. Planned focused commands are:

```text
uv run pytest -q -o addopts='' tests/unit/storage/test_catalog_settings.py
uv run pytest -q -o addopts='' -m dashboard tests/unit/test_http_admin_settings.py
uv run pytest -q -o addopts='' packages/odcli-multica/tests/test_gitlab.py packages/odcli-multica/tests/test_output_contract.py
make lint
make types
openspec validate add-global-gitlab-mr-default-assignee --strict
```

If a full local automatic suite is predicted to exceed ten minutes, or reaches ten minutes, stop only that owned test process and rely on the existing GitHub Actions jobs at the published exact candidate revision. Do not split a long suite to bypass the limit. Planning runs no product tests.

## Independent verification and evidence

The Tester works from a clean checkout of the exact candidate SHA and first confirms it descends from the approved baseline or its merged equivalent. The Tester then runs the focused commands, inspects the parameter matrix and catalog-spy read sequence, invokes the help/dry-run surfaces through the existing test harness, and reviews the affected diff for the explicit/project/global/none branch and fail-closed behavior. Repeating pytest alone is not the manual check: independent inspection of captured catalog/provider calls and immutable plan fields is required.

The final Tester comment in Multica SHALL link the implementation PR, exact candidate commit, and relevant files; record commands/exit codes, CI/focused results, and manual observations; and read back the posted comment/attachments. It SHALL state `GitLab test MR: not applicable — deterministic offline coverage closes the change` rather than creating a provider artifact. Real outputs, diagnostic JSON, hashes, provenance, and assertion results remain in Multica, not Git. No image attachment is expected for this change.

Cleanup removes only isolated temporary catalogs/directories and preserves the implementation PR, branch, commits, and Multica evidence. It SHALL NOT close, merge, or delete a branch, and SHALL NOT change the existing PR unless separately instructed.

## Delivery topology

`delivery_mode=single_wp_no_dag`.

The authoritative `Estimate, hours` property is within the single-package threshold. Exactly one work package covers every `tasks.md` item once. It has no dependency field, stage, parallel sibling, or orchestration graph.

### `WP-MYL-565-FU-01` — Global MR assignee fallback

- **Task coverage:** `1.1`, `1.2`, `2.1`, `2.2`, `2.3`, `3.1`, `3.2`.
- **Deliverable:** the exact global key is valid Admin/catalog configuration, and `odcli-multica gitlab mr publish` applies the fixed explicit → exact-project → global → none precedence with fail-closed validation, compatible output, complete focused coverage, documentation, and repository checks.
- **Owned responsibility scope:** `src/odoo_instance_sdk/storage/catalog/settings.py`; focused catalog/Admin setting tests; `packages/odcli-multica/src/odcli_multica/client.py` and applicable CLI help; companion MR/output tests; `packages/odcli-multica/README.md`; and directly related fixtures, snapshots, generated documentation, or service files required by repository checks. Existing GitLab transport/result code is touched only if a focused regression proves the fixed contract cannot be met at the resolver boundary.
- **Contract surface:** exact keys `gitlab.mr.default_assignee` and `gitlab.mr.default_assignee:https://<normalized-host>/<normalized-project-path>`; positive integer or whitespace-free exact username values; explicit/project/global/none precedence; no fallback after a present invalid source; existing `assignee_id`, member verification, response consistency, `default` result provenance, reviewer separation, and core-CLI exclusion.
- **Definition of done / evidence:** every delta scenario maps to the matrix above; focused catalog/Admin/client/output tests pass; help and README match the contract; dry-run lists both keys and deferred precedence without runtime reads; invalid sources produce no lower-priority read or GitLab mutation; strict OpenSpec, lint, types, and applicable offline gates pass on the exact candidate revision; implementation PR/SHA, commands, exit codes, CI state, file links, and independent observations are posted to Multica without committing evidence.
- **Responsibility boundary:** this package SHALL not alter the completed MYL-565 ledger, introduce a catalog migration or configuration hierarchy, create a GitLab test MR, change existing PR status, or modify production behavior outside this follow-up.
