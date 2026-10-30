## Delivery Basis

- Change: `add-git-rename-branch`, planning revision at this commit.
- Approved base: `origin/main` at `f0918da2d36b59c35cf0a949efc58744d1efb7f9`.
- Estimate source of truth: the root planning issue custom properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; this document intentionally does not duplicate numeric totals.
- Estimate basis: remaining implementation from the approved base for one experienced developer familiar with the project stack, without AI acceleration. Confidence, assumptions, and evidence are recorded below after the mandatory estimate read-back.

## E2E Decision

`e2e_required=true`.

Repository-owned integration tests with disposable local/bare Git repositories are sufficient for allocation arithmetic, local/catalogue/origin collisions, stale branch/HEAD/target detection, detached HEAD, dirty/index preservation, standard branch-configuration behavior, and proof that no remote or catalogue mutation occurs. A focused independent Mac walkthrough remains required because the change introduces visible Rich terminal output and a public selector-to-SDK-to-real-Git path; lower-level assertions cannot produce the mandatory genuine before/after output evidence.

Baseline and candidate live execution SHALL each use a fresh Binta numbered binding created by `scripts/agent_checkout.py` with the numbered project profile, approved reference checkout, a unique HTTP port and target database, and an explicit complete `--sdk-sha` for the revision under test. Odoo and database behavior are not rename assertions, but the owned slot, checkout, database, port, environment, `odcli-N` launcher, and exact full SDK SHA are mandatory isolation and provenance. The walkthrough SHALL prove that rename changes none of those bindings or resources.

## Verification Matrix

| Case ID | OpenSpec requirement/scenario | Verification gap / local E2E decision | OpenAPI operation/schema | Local test script | Independent agent steps | CI disposition / test / job | Assertions | Artifacts / images |
|---|---|---|---|---|---|---|---|---|
| GRB-01 | Shared allocation: first/max/gaps and unavailable sources | Lower-level deterministic coverage is sufficient; no live stand | N/A | `uv run pytest -q -o addopts='' tests/unit/test_cli_ticket_checkout.py tests/unit/test_ticket_branch_allocation.py` | Inspect test fixtures and compare shared allocator use from checkout and rename; do not rerun as the manual check | Extend `offline-tests` through the named unit nodes | Exact unsuffixed/`_N` selection, maximum non-reuse, source failure is non-empty error, checkout parity | Pytest command/exit and candidate SHA in Multica only; no image |
| GRB-02 | Safe rename: free/local/catalogue/origin/multiple/repeat/detached/stale/state preservation | Disposable repository integration closes mutation/stale-plan gap without Odoo | N/A | `uv run pytest -q -o addopts='' tests/integration/test_git_rename_branch.py` | Independently inspect before/after refs, HEAD, index tree, porcelain status, untracked file, catalogue rows, and bare-origin refs for one passing fixture | Extend `offline-tests`; add focused integration node, no new framework | Exactly one non-force rename; no switch/overwrite; repeated call no-op; changed branch/HEAD/target fails stale; developer state unchanged except local branch and Git's standard moved branch configuration | Command/exit, fixture hashes, and assertions in Multica only; no decorative image |
| GRB-03 | Local-only rename with standard branch configuration | The existing integration fixture can prove the absence of extra mutation; no fault-injection/recovery subsystem is in scope | N/A | Same integration node as GRB-02 plus focused branch-config assertion | Inspect the immutable plan and before/after local branch config, upstream and bare-origin refs | `offline-tests` | Plan contains read-only revalidation plus one `git branch -m`; configured upstream relation is retained by standard Git behavior; no fetch/push/remote ref/worktree/catalogue write | Structured assertion summary in Multica only; no image |
| GRB-04 | CLI leaf: selectors, dry-run, Rich/JSON/TOON, bounded failure, inventory/docs | Automated CLI contracts cover envelopes; live case required for exact-SHA numbered binding, real selector/origin, resource non-mutation, and visible output | N/A | `uv run pytest -q -o addopts='' tests/unit/test_cli_git.py tests/unit/test_cli_output_modes.py tests/unit/test_cli_characterization.py tests/unit/test_git_architecture.py tests/unit/test_verification_assets.py` | On Mac, create separate fresh baseline and candidate numbered installations through Binta `scripts/agent_checkout.py create`, always passing `--project ~/e2e/odcli/numbered-project-e2e`, `--reference-checkout ~/e2e/odcli/binta-e2e-reference`, unique `--http-port`/`--target-database`, and the explicit complete baseline or candidate `--sdk-sha`; inspect each allocated number before the attempt, invoke only its returned `odcli-N` executable against its owned checkout, capture baseline command-not-present text at the approved base, then candidate dry-run and successful rename; compare Git plus slot/environment/database/port bindings before and after | Extend existing `offline-tests` and `compatibility` jobs; `PUBLIC_LEAF_CASES` disposition `not-applicable` for Real Odoo with rationale that the live proof is an isolated numbered Git workflow rather than an Odoo lifecycle scenario | Baseline/candidate full SHA, numbered identity, launcher, checkout, environment, database, and port recorded; dry-run leaves all state unchanged; success returns old/selected names and preserves HEAD/index/porcelain, origin, catalogue count, and every non-Git resource binding; help/inventory/docs agree | `git-rename-branch-before.txt/.png`, `git-rename-branch-dry-run.txt/.png`, `git-rename-branch-after.txt/.png`, sanitized create/inspect/cleanup provenance; Multica attachments only |

## Implementer-Owned Verification Assets

- Add `tests/unit/test_ticket_branch_allocation.py` only if extraction from `test_cli_ticket_checkout.py` makes ownership clearer; do not retain duplicate matrices.
- Add `tests/integration/test_git_rename_branch.py` using temporary working and bare repositories plus an isolated temporary catalogue. The test SHALL require no private URL, credentials, Odoo runtime, database, or network.
- Extend existing CLI/output/architecture/documentation fixtures and `.github/workflows/ci.yml` only as needed to run the tests through existing jobs. Do not add a new runner or workflow.
- The Implementer owns all test code, fixtures, README changes, and CI wiring. The independent Tester SHALL not edit them; findings return to the Implementer.

## Independent Tester Contract

### Readiness gate

Before consuming a live attempt, the Tester SHALL record the candidate product SHA, approved baseline SHA, test-code SHA, locale, terminal width, output mode, and passing focused deterministic tests. The test repository URL SHALL be resolved from the Multica project description but SHALL NOT be copied into Git or public evidence.

The Tester SHALL then create two fresh numbered bindings from the Binta reference wrapper, using distinct free ports and target databases:

```sh
cd ~/e2e/odcli/binta-e2e-reference
python3 scripts/agent_checkout.py create \
  --project ~/e2e/odcli/numbered-project-e2e \
  --reference-checkout ~/e2e/odcli/binta-e2e-reference \
  --http-port "$BASELINE_HTTP_PORT" \
  --target-database "$BASELINE_DATABASE" \
  --sdk-sha "$BASELINE_SHA"
python3 scripts/agent_checkout.py inspect "$BASELINE_N"

python3 scripts/agent_checkout.py create \
  --project ~/e2e/odcli/numbered-project-e2e \
  --reference-checkout ~/e2e/odcli/binta-e2e-reference \
  --http-port "$CANDIDATE_HTTP_PORT" \
  --target-database "$CANDIDATE_DATABASE" \
  --sdk-sha "$CANDIDATE_SHA"
python3 scripts/agent_checkout.py inspect "$CANDIDATE_N"
```

Create output supplies each allocated number and launcher; the plan variables SHALL be filled from that output rather than guessed. For both slots, `inspect` SHALL prove ownership, checkout linkage, environment identity, database, unique port, launcher/interpreter, and exact complete SDK SHA before any baseline/candidate command runs. The baseline SHA SHALL equal the approved base and the candidate SHA SHALL equal the exact published implementation revision. A missing/mismatched field, non-owned resource, occupied port/database, non-fresh checkout, global/unqualified `odcli`, or failed readiness check blocks the attempt rather than allowing substitution. Private hostnames, credentials, user identity, and local absolute paths SHALL be sanitized from published evidence.

### Walkthrough

1. From the verified baseline `inspect` output, use only the baseline numbered launcher and its owned checkout: `"$BASELINE_ODCLI" --project "$BASELINE_CHECKOUT" git rename-branch ICODE-596`. Capture the genuine command-not-present text at the approved base; do not invent a success baseline or invoke global `odcli`.
2. In the candidate numbered checkout, create an equivalent non-ticket starting branch with one committed file, one staged change, one unstaged change, and one untracked file. Record HEAD, index tree, porcelain status, current branch, upstream, matching origin refs, catalogue row count, and the candidate `inspect` resource projection.
3. Run `"$CANDIDATE_ODCLI" --project "$CANDIDATE_CHECKOUT" git rename-branch ICODE-596 --dry-run` and verify every recorded Git and numbered-resource item remains equivalent.
4. Run the same candidate numbered executable without `--dry-run`; verify returned old/selected names, unchanged HEAD/index/porcelain/untracked content, changed local current branch only, standard Git movement of any local branch configuration with its upstream relation retained, unchanged origin refs and catalogue row count, and no change to owned environment/database/port/SDK/interpreter bindings.
5. Invoke the same ticket again through the candidate numbered executable and verify a no-op result with no later suffix or resource mutation.
6. Run `scripts/agent_checkout.py inspect "$CANDIDATE_N"` again and compare the owned checkout/environment/database/port/launcher/exact-SDK projection with readiness evidence, allowing only the expected checkout branch observation to differ. The slot and database remain isolation/provenance fixtures, not Odoo behavior assertions.
7. Render genuine captured text with the bundled `openspec-e2e-testing/scripts/render_terminal.swift`. Preserve the tested output language; agent-authored captions and the final report remain English. ANSI/TTY/progress omissions SHALL be stated honestly when redirected output cannot preserve them.

The live budget is two attempts: one planned attempt and one repair attempt only after publishing the first failure diagnostics and rerunning cheaper deterministic checks. A PASS on the same product/test revisions and fixtures is retained; a new product SHA requires an impact assessment and rerun of affected cases only.

### Evidence publication and cleanup

The final Multica Tester comment SHALL include the candidate/baseline/test revisions, both numbered identities, exact-SHA `inspect`/ownership evidence, unique database/port bindings, commands and exit codes, sanitized actual text, resource non-mutation assertions, before/dry-run/after PNG attachments with read-back, cleanup outcomes, and links to the implementation PR/MR, commits, and changed test files. Logs, diagnostic JSON, hashes, provenance, screenshots, and assertion results SHALL remain in Multica and SHALL NOT be committed. No function-produced Git commit is expected from branch rename, so no separate test-repository result commit is required.

After all evidence is durably published, the Tester SHALL remove each owned numbered installation with the same Binta wrapper and numbered project profile, never by deleting the checkout/database directly:

```sh
cd ~/e2e/odcli/binta-e2e-reference
python3 scripts/agent_checkout.py remove "$BASELINE_N" \
  --project ~/e2e/odcli/numbered-project-e2e
python3 scripts/agent_checkout.py remove "$CANDIDATE_N" \
  --project ~/e2e/odcli/numbered-project-e2e
```

Post-cleanup `list` evidence plus the expected bounded not-found result from `inspect` for each removed identity SHALL prove both owned identities and their checkout/environment/database/port resources are gone while the reference checkout, numbered project profile, dedicated PostgreSQL cluster, evidence MR/branch/commits, and unrelated slots remain untouched. Cleanup failure is published as incomplete evidence and blocks PASS; the Tester SHALL NOT stop Colima or the shared/dedicated cluster, close/merge the evidence MR, or delete its branch without separate authority.

## Delivery Topology

`delivery_mode: single_wp_no_dag`

The authoritative `Estimate, hours` property was written and read back successfully and is within the mandatory single-package threshold. The implementation SHALL therefore use exactly one work package. No stage field is created. OpenSpec tasks remain the stable scope; operational status and assignee data remain outside this change.

### WP-01 — Deliver ticket-safe current-branch rename

- Task coverage (complete and one-time): `1.1`, `1.2`, `1.3`, `2.1`, `2.2`, `2.3`, `3.1`, `3.2`, `3.3`, `4.1`, `4.2`, `4.3`.
- Deliverable: one reviewed implementation that moves the existing allocation helpers to a shared SDK owner, exposes the minimal public SDK rename command and convenience operation, registers the bounded CLI leaf, preserves local Git state and standard branch configuration, and includes all tests, docs, and CI wiring required by the OpenSpec.
- Responsibility scope: shared ticket-allocation ownership; `commands/env/checkout.py`; `resources/git.py`; public Git models and exceptions; Git/catalogue/process helpers; CLI adapter; test-owned leaf inventory; focused unit/integration/architecture/output/documentation tests; README and directly related fixtures/CI metadata. Critical shared files are `src/odoo_instance_sdk/resources/git.py`, `src/odoo_instance_sdk/commands/env/checkout.py`, `src/odoo_instance_sdk/commands/git.py`, `tests/unit/test_cli_output_modes.py`, and `README.md`.
- Contract surface: the two delta specs, unchanged main `development-environment` allocation semantics, `GitResource.rename_branch_command()` / `rename_branch()`, frozen rename result, existing root project/environment selectors, bounded Rich/JSON/TOON envelope, `internal/proc`, and `PUBLIC_LEAF_CASES` as the sole CLI inventory authority.
- Definition of done and evidence: all OpenSpec tasks checked only after implementation; focused tests prove every scenario; repository lint/type/architecture/test gates satisfy the ten-minute local-run policy with longer required suites on published exact-revision CI; strict OpenSpec validation passes; independent Tester completes GRB-01 through GRB-04 and publishes required Multica evidence/attachments with read-back.
- Parallel-safety rationale: the threshold mandates one package, and the core extraction plus `GitResource`, CLI inventory, and checkout regression work share write-hot files and invariants. Splitting them would create artificial coordination without an independent deliverable.

## Estimate Confidence and Evidence

Confidence is medium; calibration is not applicable because the required basis is one experienced human developer familiar with the stack, without AI acceleration. The estimate covers remaining work from the approved base and was derived from non-overlapping units for moving the existing allocation helpers, the single-step SDK rename with minimal revalidation, focused tests and fixtures, CLI/inventory/docs, and verification/review repair.

Evidence includes the existing ticket implementation in `commands/env/checkout.py`, the mature inspectable command patterns in `resources/git.py`, existing allocation tests in `tests/unit/test_cli_ticket_checkout.py`, Git resource/CLI/integration analogues, and existing offline/macOS compatibility CI jobs. Uncertainty is concentrated in maintaining current checkout plan/provenance while moving its helpers, fitting catalogue revalidation into the immutable command callback, and updating the large test-owned CLI inventory without duplication. No tests were run for the estimate. Material expansion to custom upstream cleanup/recovery, remote rename, generic explicit repositories, alternative ticket formats, or Odoo/database effects invalidates it.
