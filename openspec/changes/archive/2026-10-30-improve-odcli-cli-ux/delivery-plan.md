## Delivery contract

- Task key: `MYL-431`
- Change: `improve-odcli-cli-ux`
- Approved base: `origin/main` at `f0918da2d36b59c35cf0a949efc58744d1efb7f9`
- Delivery mode: `dag`
- Estimate source: the verified numeric `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` properties on the MYL-431 planning issue; numeric totals are authoritative there and intentionally not duplicated in this artifact
- Estimate basis: remaining active work for one experienced developer familiar with Python, Click/Rich, and this repository, without AI acceleration; implementation tests were not run for estimation
- Confidence: medium; current owners and exclusions are inspectable, while Click pre-callback failure interception and installed-executable evidence retain bounded uncertainty
- Calibration: uncalibrated; no comparable historical developer-hour records were available, and Git history was used only to locate current integration surfaces
- Topology rule: stages are consecutive topological layers and only direct dependencies are listed

### Stage map

| Work package | Stage | Direct dependencies |
|---|---:|---|
| `WP-01` | 1 | none |
| `WP-02` | 1 | none |
| `WP-03` | 2 | `WP-01` |
| `WP-04` | 3 | `WP-02`, `WP-03` |

Stage 1 is the genuine parallel frontier: `WP-01` owns shared output/import infrastructure while `WP-02` owns focused environment resolution and monitor planning. `WP-03` consumes the common CLI boundary before changing shared help/callback surfaces. `WP-04` joins the two production fronts for repository-wide compatibility and evidence.

## WP-01 — Machine usage boundary and metadata startup

- Stage: `1`
- Covers tasks: `1.1`, `1.2`, `2.3`, `2.4`
- Deliverable: the existing Click/output boundary emits supported JSON/TOON usage failures without a second parser, and metadata paths defer operation-only imports while retaining typed failure context.
- Owned responsibility scope: common command/group error interception, bounded output and failure-context imports, current `PUBLIC_LEAF_CASES`-driven usage-error tests, fresh-process module guards, installed executable measurement harness, and directly related fixtures/docs. Critical shared files include `commands/output.py`, common registration/group code, `internal/output_fields.py` only where its ordinary validation error crosses the shared boundary, `tests/unit/test_cli_output_modes.py`, and startup/import tests.
- Contract surface: v1 failure envelope, `error.code="usage_error"`, exit `2`, JSON/TOON parity, native Click fallback, no operation effects, public export identity, and prohibited metadata imports. Unknown `--fields` is covered only as a generic validation error; suggestions and completion are excluded.
- Definition of done/evidence: invalid number/range/field/missing-argument cases pass in both machine formats; unknown command/format and unsupported native/live paths retain Click stderr; fresh-process and installed checks prove named heavy-module absence and record before/after measurements without a timing threshold; database failure context remains compatible.
- Parallel-safety rationale: writes shared output/import infrastructure and its tests, not environment resolution/monitor files owned by `WP-02`.

## WP-02 — Focused environment resolution and scoped sampling

- Stage: `1`
- Covers tasks: `2.1`, `2.2`
- Deliverable: `env show` resolves UUID/name/cwd from existing catalogue data before probes and produces its unchanged typed result from one snapshot limited to the owning project.
- Owned responsibility scope: catalogue-backed identity planning, cwd worktree lookup, focused monitor invocation/selection, and related env/monitor tests and fixtures. Critical shared files include `commands/env/checkout.py`, catalogue readers, `resources/monitor/planning.py`, and the current monitor collection split only where project-bounded sampling needs a narrow extension.
- Contract surface: ambiguity/not-found diagnostics, project ownership, `snapshot(project_id=...)`, exactly one sample, stopped/unavailable compatibility, provenance, and absence of unrelated probes or alternate collectors.
- Definition of done/evidence: UUID, name, cwd, missing, ambiguous, stopped, and unavailable cases pass; assertions prove failure before probes and one selected-project snapshot on success; output schemas and renderings remain compatible.
- Parallel-safety rationale: its environment/monitor write-zone is disjoint from `WP-01` shared output/import files.

## WP-03 — Expected failures and help policy

- Stage: `2`
- Direct dependency: `WP-01`
- Covers tasks: `1.3`, `3.1`, `3.2`
- Deliverable: named decode/filesystem failures are controlled and accurate `-h` plus examples/constraints are available across the existing command tree without changing native passthrough.
- Owned responsibility scope: exec/update error classification at owning handlers, common help-option policy, help text for the six named commands, and related error/help/parse/dry-run tests. Critical shared files include common callbacks/registration, `commands/cli_parts/init_registration.py`, update/exec handlers, named command modules, and help characterization tests.
- Contract surface: stable expected-failure codes, sanitized reason/next action, `-h` inheritance, literal `-- -h` passthrough, accurate separation of optional `init --venv`/`prepare-venv` from optional owned-checkout `env create --create-venv`, unchanged names/defaults, and no runtime resolution for help.
- Definition of done/evidence: invalid UTF-8 and permission-denial fixtures have no traceback/secret/effect; root/group/leaf `-h` and child passthrough tests pass; every example parses or safely dry-runs under fixtures.
- Parallel-safety rationale: starts after `WP-01` fixes the shared CLI boundary and has no stage sibling.

## WP-04 — Compatibility, documentation, and final verification

- Stage: `3`
- Direct dependencies: `WP-02`, `WP-03`
- Covers tasks: `4.1`, `4.2`, `4.3`
- Deliverable: the reduced intentional contract change is documented and the integrated implementation is verified at repository and installed-artifact boundaries.
- Owned responsibility scope: `CHANGELOG.md`, maintained CLI docs, product-repository tests/fixtures, case-linked extensions consumed by the existing `offline-tests`, `compatibility`, `package`, and `windows-prepare-venv` jobs, full repository gates, wheel/executable verification, and product-side evidence contracts. Directly related tests, fixtures, snapshots, and service files may be repaired when no active sibling owns them. Tester-owned test-repository driver/fixtures, execution, captures, renders, and Multica attachments are excluded.
- Contract surface: retained issue items 1, 2, 3, 4, 6, and 7; canonical `PUBLIC_LEAF_CASES`; successful-envelope compatibility; architecture/security rules; installed executable outside checkout; no doctor presentation, selector completion, field suggestions/completion, dependency, or infrastructure expansion.
- Definition of done/evidence: focused suites, formatting, lint, type checks, full repository tests, wheel build/install, metadata imports/measurements, machine stdout/stderr/exit, non-TTY and help/dry-run gates pass on the final SHA; unavailable platform/service checks are separated honestly.
- Parallel-safety rationale: joins both production fronts after their shared contracts settle and has no sibling.

## Task coverage audit

Every task is assigned exactly once:

- `WP-01`: `1.1`, `1.2`, `2.3`, `2.4`
- `WP-02`: `2.1`, `2.2`
- `WP-03`: `1.3`, `3.1`, `3.2`
- `WP-04`: `4.1`, `4.2`, `4.3`

The DAG has one real concurrent frontier (`WP-01` and `WP-02` at stage 1), no redundant edge, and no conflicting sibling write-zone.

## E2E and independent verification contract

- `e2e_required=true`: unit/contract coverage proves parsing, call counts, schemas, and guards, but not installed-executable startup or final human help/error presentation.
- OpenAPI disposition: `N/A`; this is CLI-only and changes no HTTP operation or schema.
- Environment disposition: the selected checks are synthetic and need no live Odoo, Binta stand, numbered `odcli-N`, PostgreSQL database, Docker resource, or port. Adding live Odoo requires a planning revision assigning Tester a dedicated numbered slot, checkout, database, and port through the Binta wrapper.
- Test-code destination: the private GitLab test repository from the Multica project description, passed as `TEST_REPOSITORY_URL`; its literal private URL, hostnames, identity, and credentials SHALL NOT enter this public repository.
- Reusable asset: `scripts/myl431_cli_ux_e2e.py` with minimal `fixtures/myl431_cli_ux/`. Entry command:

```sh
export BASELINE_SHA=f0918da2d36b59c35cf0a949efc58744d1efb7f9
export CANDIDATE_SHA=<exact-40-hex-implementation-sha>
export PRODUCT_REPOSITORY_URL=https://github.com/maximchikAlexandr/odoo-instance-sdk
export TEST_WORK_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/myl431-e2e.XXXXXX")"
export MULTICA_EVIDENCE_DIR=<absolute-non-git-evidence-directory>
python3 scripts/myl431_cli_ux_e2e.py \
  --product-repository "$PRODUCT_REPOSITORY_URL" \
  --baseline-sha "$BASELINE_SHA" \
  --candidate-sha "$CANDIDATE_SHA" \
  --work-root "$TEST_WORK_ROOT" \
  --evidence-root "$MULTICA_EVIDENCE_DIR" \
  --cases E2E-UX-01,E2E-UX-02,E2E-UX-03,E2E-UX-04,E2E-UX-06
```

The driver SHALL reject a missing/non-40-hex candidate, verify both checkout `HEAD` values exactly, and record those SHAs plus its own test-repository SHA. It SHALL create `$TEST_WORK_ROOT/{baseline,candidate}/{checkout,tool,bin,user,home,xdg-config,xdg-data,tmp,cwd,cases}`, install each exact checkout with separate `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR`, and run from a neutral revision-specific cwd with separate `ODCLI_USER_ROOT`, `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, and `TMPDIR`.

Before every case the driver SHALL recreate only the owned revision/case directory and seed equivalent fixtures. `finally` SHALL restore changed permissions, stop only an owned child if one unexpectedly remains, and remove the work root only after verifying its ownership sentinel. The evidence directory remains outside that root. `WP-04` owns GitHub tests/fixtures; Tester SHALL NOT edit them and SHALL report findings against the candidate SHA.

### Automatic-run time policy

- Before starting any local automatic suite, including the complete selected-case driver invocation above, Tester SHALL estimate its uninterrupted duration from the setup and available prior timing evidence. A suite estimated to exceed 10 minutes SHALL NOT start locally; its automated assertions SHALL run in the mapped GitHub Actions jobs on a published branch/PR whose checkout is the exact candidate SHA.
- If a locally started automatic suite reaches 10 minutes, Tester SHALL stop only the process tree owned by that invocation, preserve its partial commands, exits, logs, assertions, and candidate SHA, mark unfinished automatic cases as not passed, and continue the automated verification in the mapped GitHub jobs on that same published exact candidate. Tester SHALL NOT split or selectively rerun pieces locally to evade the limit.
- Independent agent walkthroughs, manual inspection, and capture/render preparation are manual checks and have no 10-minute limit. They remain separate from both the automatic driver result and GitHub job result.
- Existing GitHub jobs check out `${{ github.event.pull_request.head.sha || github.sha }}`. Evidence SHALL record the job URL and prove that checked-out SHA equals the candidate before treating the job as applicable. No private test-repository credential or URL is introduced into GitHub CI.

Real stdout/stderr, timings, logs, diagnostic JSON, hashes, commands/exit codes, and PNGs SHALL be attached to the final Multica Tester comment with attachment read-back, never committed. The test-repository MR retains only normal driver/fixtures and genuine commits produced by verified Git behavior.

| Case | Trace / public action | Deterministic setup, reset, assertions, cleanup | Tester / CI / evidence |
|---|---|---|---|
| `E2E-UX-01` | `Expected CLI failures`: both scenarios. Run invalid UTF-8 `exec` dry-runs and denied `update --check` in Rich/JSON/TOON. | Write byte `0xff`. For denial, create `$CASE/denied-parent` mode `000` and set `ODCLI_USER_ROOT` to its absent `user` child; a child-process precheck SHALL prove child creation fails before invoking OdCLI. Reset per revision; assert controlled non-zero exit, no traceback/child/mutation, one machine document, absent child, and redaction. Restore parent mode in `finally`. | Manual: rerun Rich invalid-source and JSON denied-update, inspect exits/documents/hashes, and capture error text/PNGs. CI: existing `offline-tests` (`make test`) and Linux/macOS `compatibility` (`make compat`) run planned cases in `tests/unit/test_cli_characterization.py` and `tests/unit/test_self_update.py`; `tmp_path` invalid-byte and monkeypatched access-denial fixtures assert stable code/exit, one document, redaction, and zero operation calls. Driver remains local-only for installed presentation/capture because its scripts live in the assigned private test repository; time-policy overflow continues through these exact-SHA CI assertions, not a private-repository checkout in GitHub. |
| `E2E-UX-02` | `Stable machine output`: supported usage error and JSON/TOON parity. Run invalid `db stats --top`, invalid `run --readiness-timeout`, `env show ... --fields invalid.path`, and missing `exec` argument. | Seed only the revision-local catalogue needed to reach ordinary typed-field validation. Reset from a fresh fixture; assert exit `2`, one generic `usage_error` document, paired JSON/TOON equality, no suggestion/completion requirement, no effects, and unchanged hashes; remove case root. | Manual: independently decode a JSON/TOON pair and inspect stderr/hashes; no image. CI: existing `offline-tests` command `make test` runs the planned parametrized matrix in `tests/unit/test_cli_output_modes.py` plus native-boundary cases in `tests/unit/test_cli_characterization.py`; current command fixtures and a revision-local catalogue fixture assert exit `2`, one equivalent v1 document, native fallback, and no callback/effect. No separate live case is needed for the full leaf inventory. |
| `E2E-UX-03` | `Focused environment inspection`: all scenarios. Run missing, known name, UUID, cwd, and duplicate-name `env show --format json`. | `seed_catalog.py` SHALL use the installed catalogue API to create two projects, a stopped/unavailable known environment, duplicate display names, stable UUID, and worktree. Recreate/reseed per revision; hash before/after; assert missing/ambiguous failures, correct project/id/state for successful selectors, and unchanged catalogue. | Manual: independently seed and run all five commands, compare ids/states to the manifest, and attach transcript/manifest/hashes; no image. CI: existing `offline-tests` (`make test`) and Linux/macOS `compatibility` (`make compat`) run planned extensions to `tests/unit/test_cli_env_show.py`; its temporary catalogue and fake monitor fixtures assert zero snapshots for missing/ambiguous selectors, exactly one `snapshot(project_id=selected_project)` for name/UUID/cwd, no unrelated project probe, and compatible stopped/unavailable result. |
| `E2E-UX-04` | `CLI metadata paths`: fresh metadata and public exports. From neutral cwd run `odcli --help`, `odcli --version`, and the fresh-process import probe. | No project/catalogue fixture. Use a fresh process per sample; reset captures; assert exit `0`, compatible exports, and absence of named operation-only modules. Timings are evidence, not thresholds; remove case root. | Manual: repeat installed help/version outside checkout and inspect the fresh-process module/timing report; no image. CI: existing `offline-tests` (`make test`) runs planned guards in `tests/unit/test_cli_startup.py` using isolated subprocesses and module manifests; existing `package` (`make package`) runs `tests/packaging/test_artifacts.py` isolated wheel/sdist fixtures. Assertions are exit `0`, absent Alembic/SQLAlchemy/dbprep/monitor collector modules, and preserved export identity/help/version. Comparative timing remains manual evidence, never a CI threshold. |
| `E2E-UX-06` | `Command help`: all scenarios. Run root/group/six named leaf `-h`, parse-safe or dry-run examples, and native `-- -h` passthrough. | Copy a minimal disposable project/backup fixture and snapshot it. Recreate per revision; capture every page; assert registered options/defaults, accurate `init --venv` versus `env create --create-venv` wording, no resolution/mutation, native passthrough, and unchanged hash; remove case root. | Manual: follow one init example and one passthrough example, attach baseline/candidate help text and representative rendered PNG pairs. CI: existing `offline-tests` and Linux/macOS `compatibility` run planned help/parse/dry-run assertions in `tests/unit/test_cli_characterization.py` and `tests/unit/test_integrated_init_venv.py`; existing `windows-prepare-venv` runs `test_integrated_init_venv.py -k 'real_platform_backend or integrated or init_help'`; existing `package` verifies installed help. Minimal project/backup fixtures assert root/group/leaf `-h` exit `0`, accurate venv wording/defaults, no resolution/mutation, and literal child `-h` passthrough. |

### Capture, commits, and cleanup

- Baseline is exactly `f0918da2d36b59c35cf0a949efc58744d1efb7f9`; candidate is the exact implementation SHA supplied to the entry command. Both use equivalent fixtures, locale, terminal width, `NO_COLOR`, `TERM=dumb`, and TTY/non-TTY mode as applicable.
- On macOS raw text is authoritative. PNGs SHALL be renders of captured text via bundled `openspec-e2e-testing/scripts/render_terminal.swift`, labelled as renders; ANSI/TTY/progress limitations are stated honestly. Tested output keeps its language; plan and Tester explanations are English.
- Test driver/fixture work and any genuine functional result use separate commits only when both exist. A real generated commit is preserved unchanged and linked; no artificial result commit is required.
- Cleanup removes only owned disposable installs, catalogues, and roots while preserving the evidence MR/branch/scripts/fixtures/commits. It SHALL NOT merge/close the MR or delete its branch without separate authorization.

## Estimate evidence and invalidation

The estimate considered the complete reduced package and current base, existing output/field helpers, `PUBLIC_LEAF_CASES`, integrated venv paths, lazy registration, split monitor collection/project scoping, startup tests, packaging checks, and the Tester-owned driver/execution/evidence boundary. Product implementation/tests, repository documentation/package verification, Tester assets/execution/evidence, and uncertainty were counted once. Product pytest work in `WP-04` excludes Tester driver/fixtures/execution; shared fixtures are charged to their owner; equivalent baseline/candidate actions are one paired E2E activity; image preparation is included once; range uncertainty is not duplicated as contingency. No doctor presentation, selector completion, field suggestions/completion, new abstraction, registry, runner, framework, dependency, or infrastructure is estimated. No implementation tests or project scripts were run for estimation.

The estimate is invalidated by a change to the envelope version, `PUBLIC_LEAF_CASES` classification, catalogue/public snapshot schemas, runtime dependencies, venv integration, command names/defaults, the explicit exclusions, or the MYL-430 boundary. Approval/review/CI queue/external waits are outside active developer-hours.
