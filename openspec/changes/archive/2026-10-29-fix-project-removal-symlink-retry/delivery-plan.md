## Delivery Contract

- Task key: `MYL-593`
- OpenSpec change: `fix-project-removal-symlink-retry`
- Approved base: `origin/main` at the planning base recorded in the publication comment
- Estimate source: the planning issue custom properties `Estimate, hours`,
  `Estimate min, hours`, and `Estimate max, hours`; numeric totals SHALL NOT be
  duplicated in this repository artifact.
- Estimate basis: remaining implementation from the approved base for one
  experienced developer familiar with the Python project, without AI
  acceleration. Confidence, calibration, assumptions, and evidence are recorded
  below after the estimate gate.

## E2E Planning Gate

`e2e_required=false` for a separate local managed E2E run. The changed behavior
is an internal filesystem safety boundary and a removal-planning recovery path;
both have deterministic, observable postconditions that focused unit/integration
tests can prove without a live Odoo process. The one material platform-integration
gap—an actual OdCLI-created venv layout on Unix—is covered by extending the
existing source-backed GitHub CI scenario and dispatching
`.github/workflows/real-odoo-full.yml` on the published candidate branch with
`operation=full` and `mode=project-removal`, rather than relying on its restricted
push trigger or duplicating that stand locally.

The project test-repository resource is therefore not consumed by this change.
No private repository URL or hostname is stored in Git. If a later verified
planning revision establishes a local-only gap, the Tester SHALL resolve
`PROJECT_TEST_REPOSITORY_URL` from the Multica project description and use a
fresh numbered `odcli-N` slot with its own checkout, database, port, and cleanup.

### Verification Matrix

| Case | Requirement/scenario | Gap and disposition | Script/test owner | Independent check | CI disposition | Assertions | Artifacts/images |
| --- | --- | --- | --- | --- | --- | --- | --- |
| V01 | `local-resource-lifecycle` / nested symlink in owned non-source directory | Lower-level check is sufficient: standard-library deletion and referent preservation are deterministic filesystem effects | Implementer: parameterized cases in `tests/unit/internal/test_project_filesystem_purge.py` | WP Verifier inspects the temporary tree before/after and confirms both external file and directory referents are byte-stable; this is distinct from rerunning the test | Existing `CI / offline-tests` through `make test` | Owned target absent; nested links absent; external referents present and unchanged; no traversal | Pytest command/exit code and relevant commit/file links in Multica; no image because output is unchanged/internal |
| V02 | `local-resource-lifecycle` / top-level and source-root guards | Existing focused unit boundary is sufficient | Implementer: retain and parameterize guard cases in `tests/unit/internal/test_project_filesystem_purge.py` | WP Verifier manually reviews target classification and inspects that source/top-level targets remain after the attempted operation | Existing `CI / offline-tests` | Top-level symlink refused; nested source link refused; referents unchanged; containment/provenance guards remain | Command/exit code and commit/file links; no image |
| V03 | `project-removal` / absent primary runtime retry and incomplete evidence | Integration seam is sufficient because the required observations, adapters, catalogue rows, and failure ordering are already injectable without a live Odoo process | Implementer: focused cases in `tests/integration/test_project_removal.py` | WP Verifier performs a fresh fixture walkthrough: remove the owned venv, capture by full id, inspect the plan, execute remaining fake-backed point operations, and inspect catalogue/filesystem postconditions without invoking the test function | Existing `CI / offline-tests`; no new job | No venv recreation or process execution; durable evidence produces the same projection path; remaining owned actions complete once; missing evidence mutates nothing and preserves registration | Sanitized commands, exit codes, assertions, and commit/file links in Multica; diagnostic JSON/logs are not committed |
| V04 | `project-removal` / complete isolated purge with OdCLI venv symlinks | Existing source-backed CI E2E closes the real Unix venv-layout gap; no duplicate local stand | Implementer: extend `tests/integration/real_odoo/test_project_removal.py` in the existing targeted selection | WP Verifier reviews the CI fixture construction, exact referent-preservation assertions, and completed run read-back, then reports coverage findings; Tester does not edit the GitHub test | Manager dispatches `.github/workflows/real-odoo-full.yml` on the published candidate branch with `operation=full`, `mode=project-removal`; the reusable job SHALL be `real-odoo-targeted / real-odoo-project-removal`; no new framework/job | Run `headSha` equals the reviewed candidate SHA; an OdCLI-created venv has a nested interpreter link; confirmed purge succeeds; external canary survives unchanged; project reaches forgotten terminal state | GitHub run/job/test and evidence-artifact links, dispatch command/exit code, and exact product SHA in Multica; no image because public output format/content is unchanged |

There are no affected OpenAPI operations or schemas. V01–V04 cover every changed
scenario; unchanged retention, output, and idempotence scenarios remain under the
main-spec suites.

### V04 Focused Dispatch Contract

The Implementer owns task `3.1`, including the product test, fixture assertions,
and any directly required reusable-workflow maintenance. After the candidate is
integrated and published on the feature branch, the Manager owns exactly one
focused dispatch for V04. The WP Verifier owns the independent run/job/SHA and
assertion read-back. This operational split does not create another OpenSpec task
or work package, and the E2E Tester is not invoked while
`e2e_required=false`.

Before dispatch, the Manager SHALL set `CANDIDATE_SHA` to the reviewed forty-hex
product revision and SHALL prove that the remote feature branch resolves to that
revision. From a clean product checkout with `origin` configured, the executable
dispatch contract is:

```bash
CANDIDATE_BRANCH=feat/MYL-593-fix-project-removal-symlink-retry
CANDIDATE_SHA=<reviewed-candidate-sha>
REPOSITORY=maximchikAlexandr/odoo-instance-sdk
test "$(git ls-remote origin "refs/heads/${CANDIDATE_BRANCH}" | cut -f1)" = "${CANDIDATE_SHA}"
gh workflow run real-odoo-full.yml \
  --repo "${REPOSITORY}" \
  --ref "${CANDIDATE_BRANCH}" \
  --raw-field operation=full \
  --raw-field mode=project-removal
```

The Manager SHALL preserve the run URL or run id returned by the dispatch and
publish the dispatch command and exit code. The run is not sufficient merely
because it used the feature branch. Before V04 can pass, the WP Verifier SHALL
read the completed run and assert the tested revision and focused job explicitly:

```bash
gh run view <run-id> \
  --repo maximchikAlexandr/odoo-instance-sdk \
  --json databaseId,url,event,headBranch,headSha,status,conclusion,jobs
test "$(gh run view <run-id> \
  --repo maximchikAlexandr/odoo-instance-sdk \
  --json headSha --jq .headSha)" = "${CANDIDATE_SHA}"
gh api \
  repos/maximchikAlexandr/odoo-instance-sdk/actions/runs/<run-id>/artifacts \
  --jq '.artifacts[] | select(.name == "real-odoo-project-removal-evidence") | {name,expired,size_in_bytes,archive_download_url}'
```

The read-back SHALL show `event=workflow_dispatch`, the expected feature branch,
`headSha=${CANDIDATE_SHA}`, `status=completed`, `conclusion=success`, and a
successful `real-odoo-targeted / real-odoo-project-removal` job. The evidence
artifact SHALL exist and not be expired. A run on another SHA, an absent focused
job, a skipped/failed/cancelled job, or a missing artifact leaves V04 unverified;
it SHALL NOT be reported as PASS or replaced by the scheduled/full-tier result.

## Local Scripts, Images, and Evidence

No new local E2E script, GitLab test MR, fixture repository change, or generated
artifact is required. Product tests and fixtures belong in the GitHub product
repository and are Implementer-owned. The independent verifier uses fresh
temporary roots and read-only inspection; it does not modify product tests.

Visible CLI text, tables, progress, prompts, and summaries do not change, so no
before/after capture or `scripts/render_terminal.swift` PNG is required. If an
implementation unexpectedly changes visible output, verification is incomplete
until a planning revision defines equivalent baseline/candidate UTF-8 captures
on macOS, fixed locale and 120-column width, actual text, and rendered PNGs with
honest ANSI/TTY/progress limitations.

The final Multica verification comment SHALL include the product PR, commit and
file links; tested product SHA; exact commands and exit codes; V01–V04 assertion
results; the V04 dispatch command, run id/URL, run `headSha`, focused job name and
URL/conclusion, evidence-artifact metadata/link; and cleanup disposition. Runtime
logs, diagnostic JSON, hashes, provenance, and any later images are Multica
attachments/evidence only, not Git commits. The WP Verifier SHALL read the posted
comment and attachment/link metadata back before verification is declared
complete.

## Cleanup

Unit/integration fixtures remove their own temporary roots. The existing
source-backed CI owns its disposable resources and preserves the product
branch/PR/commits and CI evidence. Verification SHALL NOT close or merge the PR,
delete its branch, delete shared images, touch a reference checkout, or operate
on persistent Odoo/PostgreSQL infrastructure without a separate instruction.

## Estimate Evidence

The estimate uses the complete OpenSpec package, the current source revision,
`project_filesystem_purge.py`, `resources/project.py`, the existing
project-removal coordinator/inventory primitives, focused unit/integration tests,
and the targeted real-Odoo workflow. Tests and application code were not run for
the estimate. The issue properties are the sole numeric source.

Confidence is medium: the affected code and close local analogues are inspectable,
the filesystem part is narrow, and the public contracts are stable; bounded
uncertainty remains in safely reusing the observation-only instance construction
after runtime resolution fails and in likely review repairs around fail-closed
negative cases. The estimate is not calibrated against a comparable historical
delivery record. Unattended CI time, approval queues, meetings, and external
blocking are excluded from active developer-hours.

## Work Package Topology

`delivery_mode=single_wp_no_dag`, derived from the authoritative `Estimate,
hours` property. The Planner creates no implementation issue; the Manager SHALL
materialize exactly the one work package below.

### WP-01 — Safe symlink purge and missing-runtime recovery

- **Task coverage:** `1.1`, `1.2`, `1.3`, `2.1`, `2.2`, `2.3`, `3.1`, `3.2`,
  and `3.3`, each covered exactly once.
- **Deliverable:** One reviewed product change that safely purges an owned
  symlink-bearing primary venv, recovers a full-id purge when that venv is already
  absent and durable ownership evidence is complete, and preserves every
  fail-closed boundary when evidence is incomplete.
- **Owned responsibility scope:** Filesystem purge policy and focused tests;
  project removal observation construction and focused integration tests; the
  existing source-backed project-removal CI case and its reusable workflow
  contract; directly related fixtures, docs, snapshots, generated contract
  checks, and service files. The Implementer owns product changes, the Manager
  owns the exact candidate dispatch, and the WP Verifier owns independent
  run/job/SHA/evidence read-back. Critical shared
  files include `src/odoo_instance_sdk/internal/project_filesystem_purge.py`,
  `src/odoo_instance_sdk/resources/project.py`,
  `src/odoo_instance_sdk/internal/project_removal_coordinator.py`,
  `tests/unit/internal/test_project_filesystem_purge.py`,
  `tests/integration/test_project_removal.py`,
  `tests/integration/real_odoo/test_project_removal.py`, and the existing
  real-Odoo workflow selection.
- **Contract surface:** No public SDK/CLI, serialized result, OpenAPI, storage,
  dependency, or migration change. The target itself remains non-symlink and
  contained; source roots remain strict; nested links in proven non-source trees
  are unlinked without traversal; recovery consumes durable evidence without
  recreating or executing the missing runtime.
- **Definition of done/evidence:** All OpenSpec tasks complete; V01–V04 assertions
  pass on the candidate revision; the focused workflow dispatch resolves to that
  exact candidate SHA and its named project-removal job and evidence artifact are
  successful; focused and repository checks pass within the workspace test-time
  policy; strict OpenSpec validation remains clean; the PR, exact SHA, dispatch
  command, run/job/artifact links, commands/exit codes, manual inspection, and
  cleanup are published and read back in Multica; no runtime evidence is
  committed.
- **Parallel-safety rationale:** The two code paths converge in one removal
  projection and share the same integration fixture and safety review boundary.
  Splitting them would create conflicting writes and duplicate the fail-closed
  acceptance proof, so one execution unit is the smallest complete delivery.
