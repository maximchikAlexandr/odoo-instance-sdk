## Delivery contract

- Task key: `MYL-431`
- Change: `improve-odcli-cli-ux`
- Approved base: `origin/main` at `d6a4e093377cd6c29fcf564406626ea5f17718aa`
- Delivery mode: `dag`
- Estimate source: the verified numeric `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` properties on the MYL-431 planning issue; numeric totals are authoritative there and intentionally not duplicated in this artifact
- Estimate basis: remaining active developer work for one experienced developer familiar with Python, Click/Rich, and this repository, without AI acceleration; tests were not run for estimation
- Confidence: medium; the code and acceptance boundaries are inspectable, but the central Click parse-error interception and installed-executable evidence carry bounded implementation uncertainty
- Calibration: uncalibrated; no comparable historical developer-hour records were available, and Git history was used only to identify current owners and integration surfaces, not to infer elapsed effort
- Topology rule: stages are topological layers, numbered consecutively; only direct dependencies are listed

### Stage map

| Work package | Stage | Direct dependencies |
|---|---:|---|
| `WP-01` | 1 | none |
| `WP-02` | 1 | none |
| `WP-03` | 2 | `WP-01` |
| `WP-04` | 3 | `WP-02`, `WP-03` |
| `WP-05` | 4 | `WP-04` |

Stage 1 is the verified parallel frontier: `WP-01` owns shared output/import infrastructure while `WP-02` owns focused environment selection and collection. Later work converges on shared Click registration and completion surfaces and is therefore ordered to avoid conflicting writes. `WP-04` receives `WP-01` transitively through `WP-03`; `WP-05` receives every earlier prerequisite transitively through `WP-04`, so neither repeats those edges.

## WP-01 — Machine usage boundary and metadata startup

- Stage: `1`
- Covers tasks: `1.1`, `1.2`, `2.3`, `2.4`
- Deliverable: the existing Click/output boundary emits supported JSON/TOON usage failures without a second parser, and metadata paths no longer import operation-only database preparation dependencies while retaining typed failure context.
- Owned responsibility scope: shared command/group error interception, bounded output and failure-context import boundary, typed usage-error contract tests over the current `PUBLIC_LEAF_CASES` classifications, fresh-process module assertions, installed executable measurement harness/evidence, and directly related fixtures/docs. Critical shared files include `commands/output.py`, the common CLI registration/group implementation, `internal/output_fields.py` only where machine field errors enter the common boundary, `tests/unit/test_cli_output_modes.py`, and `tests/unit/test_cli_startup.py`.
- Contract surface: v1 failure envelope, `error.code="usage_error"`, exit `2`, JSON/TOON parity, native Click fallback boundary, no operation effects, public export identity, and named prohibited metadata imports.
- Definition of done/evidence: parameterized invalid number/range/field/missing-argument cases pass in both machine formats; unknown command/format and unsupported native/live cases retain Click stderr; fresh-process and installed executable checks prove heavy module absence and record before/after measurements without a timing threshold; database failure context still projects correctly.
- Parallel-safety rationale: writes only shared output/import infrastructure and its tests; it does not edit environment selection/monitor code owned by `WP-02`.

## WP-02 — Focused environment resolution and scoped sampling

- Stage: `1`
- Covers tasks: `2.1`, `2.2`
- Deliverable: `env show` resolves UUID/name/cwd from existing catalogue data before probes and produces its unchanged typed result from one snapshot limited to the owning project.
- Owned responsibility scope: catalogue-backed environment identity planning, cwd worktree lookup, focused monitor invocation/selection, and directly related env/monitor tests and fixtures. Critical shared files include `commands/env/checkout.py`, catalogue readers used by that command, `resources/monitor/planning.py`, and the current `resources/monitor/collection_parts/{planning,snapshot}.py` split only where project-bounded sampling needs a compatible narrow extension.
- Contract surface: selector ambiguity/not-found diagnostics, project ownership, `snapshot(project_id=...)`, exactly one sample, stopped/unavailable compatibility, provenance, and absence of unrelated probes or alternate collectors.
- Definition of done/evidence: UUID, name, cwd, missing, ambiguous, stopped, and unavailable cases pass; call assertions prove failure before probes and one selected-project snapshot on success; output schemas and renderings remain compatible.
- Parallel-safety rationale: the write-zone is environment resolution/monitor planning, disjoint from `WP-01` shared output/import files.

## WP-03 — Expected failures, doctor presentation, and help policy

- Stage: `2`
- Direct dependency: `WP-01`
- Covers tasks: `1.3`, `3.1`, `3.2`, `3.3`, `3.4`
- Deliverable: named decode/filesystem failures are controlled, doctor human output is actionable, and accurate `-h` plus examples/constraints are available across the existing command tree without changing native passthrough.
- Owned responsibility scope: exec/update error classification at owning handlers, doctor Rich tables, common help-option policy, help text for the six named commands, and directly related rendering/help/parse/dry-run tests. Critical shared files include `commands/cli_parts/callbacks.py`, `commands/cli_parts/registration.py`, `commands/cli_parts/init_registration.py`, `commands/prepare_venv.py`, `commands/update.py`, the named command registration modules, and doctor/help tests.
- Contract surface: stable expected-failure codes, sanitized reason/next action, doctor machine-schema preservation, shell-safe remediation display, `-h` inheritance, literal `-- -h` passthrough, accurate separation of optional `init --venv`/`prepare-venv` and `env create --create-venv`, unchanged option names/defaults, and no runtime resolution for help.
- Definition of done/evidence: isolated invalid UTF-8 and permission-denial cases have no traceback/secret/effect; doctor passes normal/narrow/non-TTY and machine parity cases; root/group/leaf `-h` and child passthrough tests pass; every added example parses or safely dry-runs under fixtures.
- Parallel-safety rationale: begins only after `WP-01` fixes the common output/import contract; it owns callback/help files before completion callbacks later modify shared registration.

## WP-04 — Local selector completion and schema-derived fields

- Stage: `3`
- Direct dependencies: `WP-02`, `WP-03`
- Covers tasks: `4.1`, `4.2`, `4.3`
- Deliverable: environment/remote selectors and typed fields complete locally and safely; invalid fields offer bounded leaf-specific suggestions from the same schema source.
- Owned responsibility scope: direct Click `shell_complete` callbacks on existing root/arguments/options, exposure and reuse of schema paths, close-match diagnostics, comma/dotted completion, and all completion/field regression tests. Critical shared files include the finalized common registration from `WP-03`, environment registration from `WP-02`, `commands/remote.py`, `internal/output_fields.py`, and CLI characterization/field tests.
- Contract surface: prefix filtering, spaces, project scope, expected unreadable-source degradation, no network/snapshot/runtime/migration/chmod/mutation, leaf-specific schema paths, bounded value-free suggestions, exit `2`, JSON/TOON usage envelope inherited from `WP-01`, and standard command/option/path completion preservation.
- Definition of done/evidence: shell completion matrices cover environment and remote values, dotted/comma fields, unreadable sources, and spaces; fail-fast mocks prove forbidden seams are untouched; valid projections are byte/semantic compatible; existing nested command, option, and path completion tests remain green.
- Parallel-safety rationale: directly follows the environment owner `WP-02` and the registration owner `WP-03`; the machine-output foundation `WP-01` is already inherited through `WP-03`. No active sibling shares its write-zone.

## WP-05 — Compatibility, documentation, and final verification

- Stage: `4`
- Direct dependency: `WP-04`
- Covers tasks: `5.1`, `5.2`
- Deliverable: the intentional contract change is documented and the integrated implementation is verified at repository and installed-artifact boundaries with limitations recorded honestly.
- Owned responsibility scope: `CHANGELOG.md`, maintained CLI documentation, product-repository tests and fixtures, integration/compatibility adjustments, full repository gates, wheel/executable verification, and evidence contracts. Directly related tests, fixtures, snapshots, and service files are included when required to close an integration failure and are not owned by an active sibling; Tester-owned test-repository scripts and captured evidence files are excluded from this WP.
- Contract surface: all AC-1 through AC-9, canonical `PUBLIC_LEAF_CASES`, successful envelope compatibility, repository architecture/security rules, installed executable outside checkout, and no new dependency/infrastructure.
- Definition of done/evidence: focused suites, formatting, lint, type checking, full repository tests, wheel build/install, metadata measurement, machine stdout/stderr/exit, non-TTY/NO_COLOR/TERM=dumb, narrow PTY, completion side-effect, and relevant dry-run gates pass on the final SHA; real Odoo/PostgreSQL/Windows or other unavailable checks are explicitly separated from passed evidence.
- Parallel-safety rationale: final convergence and repair package; its sole direct predecessor `WP-04` already joins `WP-02`, `WP-03`, and transitively `WP-01`, so it starts only after production work is integrated and has no sibling.

## Task coverage audit

Every OpenSpec task is assigned exactly once:

- `WP-01`: `1.1`, `1.2`, `2.3`, `2.4`
- `WP-02`: `2.1`, `2.2`
- `WP-03`: `1.3`, `3.1`, `3.2`, `3.3`, `3.4`
- `WP-04`: `4.1`, `4.2`, `4.3`
- `WP-05`: `5.1`, `5.2`

The DAG has one genuine concurrent frontier (`WP-01` and `WP-02` at stage 1). Shared contracts are predecessors, later siblings have no conflicting write-zones, and operational WIP limits are intentionally not encoded in topology.

## E2E and independent verification contract

- `e2e_required=true`: unit/contract coverage can prove parsing, call counts, schema parity, and side-effect guards, but it cannot prove installed-executable startup behavior or the actual human-facing doctor/help/error presentation. Those remaining cross-component and visual gaps require a focused local walkthrough.
- OpenAPI disposition: `N/A`. This change is CLI-only; no HTTP operation or schema is changed or invoked. Every selected case below traces directly to named OpenSpec requirements and scenarios.
- Environment disposition: these synthetic checks require no live Odoo, Binta stand, numbered `odcli-N`, PostgreSQL database, Docker resource, or reserved port. Adding a live-Odoo case requires a planning revision that assigns Tester a dedicated numbered `odcli-N`, checkout, database, and port through the Binta wrapper.
- Test-code destination: the private GitLab test repository supplied by the Multica project description, passed at execution as `TEST_REPOSITORY_URL`; the literal private URL, hostnames, member identity, and credentials SHALL NOT be copied into this public Git repository.
- Reusable test asset: `scripts/myl431_cli_ux_e2e.py` plus minimal fixtures under `fixtures/myl431_cli_ux/` in the test-repository MR. The entry command SHALL be:

```sh
export BASELINE_SHA=d6a4e093377cd6c29fcf564406626ea5f17718aa
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
  --cases E2E-UX-01,E2E-UX-02,E2E-UX-03,E2E-UX-04,E2E-UX-05,E2E-UX-06,E2E-UX-07
```

  The driver SHALL reject a missing or non-40-hex candidate, resolve and verify both checkout `HEAD` values exactly, and record those product SHAs plus its own test-repository SHA.
- Revision isolation: the driver SHALL create `$TEST_WORK_ROOT/{baseline,candidate}/{checkout,tool,bin,user,home,xdg-config,xdg-data,tmp,cwd,cases}`. It SHALL build and install each exact checkout with separate `UV_TOOL_DIR` and `UV_TOOL_BIN_DIR`, and run its executable from a neutral revision-specific `cwd` with revision-specific `ODCLI_USER_ROOT`, `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, and `TMPDIR`. No catalogue, cache, configuration, install, or working directory is shared across revisions.
- Case lifecycle: before each case, the driver SHALL delete and recreate only the owned `$TEST_WORK_ROOT/<revision>/cases/<case>` directory and seed equivalent fixtures for both revisions. A `finally` path SHALL restore fixture permissions, stop only an owned child process if a failed assertion left one behind, and remove the work root only after checking its ownership sentinel. The evidence directory is outside that root and survives cleanup.
- Product-repository responsibility: `WP-05` owns all pytest/CI coverage and any product-side fixture needed by the change. The Tester SHALL NOT edit those GitHub tests; findings are reported back against the candidate SHA.
- Evidence destination: real stdout/stderr text, timing samples, logs, diagnostic JSON, hashes, commands/exit codes, and PNG files SHALL be attached to the final Multica Tester comment with attachment read-back. They SHALL NOT be committed to either repository. The test-repository MR retains only normal test scripts/fixtures and any genuine commits produced by the verified Git behavior.

| Case | Requirement/scenario trace | Gap / decision | Public action | Initial state, reset, assertions, and failure cleanup | Independent Tester step | CI disposition | Artifacts / images |
|---|---|---|---|---|---|---|---|
| `E2E-UX-01` | `Expected CLI failures are bounded and actionable`: `Invalid exec source is controlled`; `Update access denial is controlled` | Installed stdout/stderr/exit and traceback hygiene remain after unit fault injection. OpenAPI: N/A. | `odcli exec "$CASE/invalid_utf8.py" --dry-run --format rich\|json\|toon`; `odcli update --check --format rich\|json\|toon` | Write byte `0xff`; create an isolated update state; set its `ODCLI_USER_ROOT` to mode `000` and verify in a child process that a sentinel cannot be created. For each revision reset the case directory, then assert controlled non-zero exit, no traceback/child/mutation, one decodable machine document, and redaction. In `finally`, restore mode before owned-root cleanup. | Rerun Rich invalid-source and JSON denied-update commands; inspect exit/text/document and compare before/after hashes. | GitHub `offline-tests`: focused output-mode and self-update tests own exact exception, redaction, and no-effect assertions. | Sanitized transcripts/documents and `E2E-UX-01-{baseline,candidate}-error.txt/.png`. |
| `E2E-UX-02` | `Stable machine output`: `Supported machine usage error`, `TOON usage error is semantically equal to JSON`; `Typed field discovery reuses the result schema`: `Unknown field suggests only leaf-valid paths` | Installed Click/lazy-command interception and transport composition. OpenAPI: N/A. | `odcli db stats demo --top nope --format json`; `odcli run --dry-run --readiness-timeout -1 --format toon`; `odcli env show known-env --format json --fields enviroment.name`; `odcli exec --format json` | Seed only the revision-local `known-env` catalogue used by the field case; snapshot catalogue/config hashes. Reset to a fresh copy per revision; assert exit `2`, single machine document, JSON/TOON semantic parity where paired, bounded suggestion, no operation effects, and unchanged hashes; cleanup owned case directory. | Decode one JSON and its TOON peer independently, compare normalized values, and verify stderr and state hashes. | GitHub `offline-tests`: parametrized `PUBLIC_LEAF_CASES`, output-mode, characterization, and typed-field tests remain authoritative for all eligible leaves. | Sanitized JSON/TOON and assertion summary; no additional image. |
| `E2E-UX-03` | `Focused environment inspection resolves before collection`: all four scenarios | Public result compatibility is E2E-visible; exact probe/snapshot call counts remain a deterministic unit-test responsibility. OpenAPI: N/A; no live stand adds evidence. | `odcli env show missing --format json`; `odcli env show known-env --format json`; `odcli env show <seeded-uuid> --format json`; `(cd "$CASE/worktree" && odcli env show --format json)`; `odcli env show duplicate --format json` | `fixtures/myl431_cli_ux/seed_catalog.py` SHALL use the installed catalogue API to create two projects, a stopped/unavailable `known-env`, the same display name in both projects, a stable UUID, and a worktree path. Reset by recreating and reseeding the revision-local catalogue; hash it before/after. Assert missing/ambiguous selectors fail without traceback, name/UUID/cwd select the expected project/id/state, and catalogue bytes are unchanged. Cleanup the case directory. | Run all five public commands against a separately seeded case and compare selected ids/states with the fixture manifest; inspect candidate call-count tests rather than claiming external probes were observed. | GitHub `offline-tests`: focused `env show` tests SHALL prove absence/ambiguity before probes, exact project scope, and one selected snapshot. | Command/exit transcript, fixture manifest, hashes; no image because visible output is unchanged. |
| `E2E-UX-04` | `CLI metadata paths defer operation-only imports`: `Fresh metadata process stays lightweight`; `Public exports remain compatible` | Installed executable outside checkout and cold/repeated processes are not represented by import-only tests. OpenAPI: N/A. | From the revision-neutral cwd: `odcli --help`, `odcli --version`, plus the fixture's fresh-process public-import probe. | No project/catalogue fixture. Start a fresh process for every sample, reset only captured output between samples, and assert exit `0`, compatible public exports, and absence of named operation-only modules. Timing is comparative evidence, never a CI threshold. Cleanup the case directory. | Repeat one help and one version invocation, then inspect the fresh-process module list. | GitHub `package` and `compatibility`: deterministic import/public-export checks; `offline-tests`: fresh-process module guards. | Revision-labelled timing/text/module report; no image. |
| `E2E-UX-05` | `Human doctor output prioritizes remediation`: both scenarios | Real terminal wrapping and sanitization are visual contracts. OpenAPI: N/A. | `COLUMNS=100 NO_COLOR=1 TERM=dumb odcli --project "$CASE/project" doctor`; repeat with `COLUMNS=50`; `odcli --project "$CASE/project" doctor --format json`; repeat with `--format toon` | Copy a deterministic manifest fixture per revision and rewrite paths into that case root. It SHALL reference a missing Odoo executable and unavailable local targets to yield stable WARN/ERROR/unavailable facts without network or child processes. Capture Rich runs through Python stdlib `pty`; reset via a fresh fixture copy. Assert readable target/reason/shell-safe remediation, no Python repr, machine facts/argv parity, no remediation execution, and unchanged state. Kill only an owned child if an assertion unexpectedly finds one, then cleanup. | Read both raw widths, run one machine command, verify one remediation argv is quoted but not executed, and compare state hashes. | GitHub `offline-tests` and `compatibility`: workflow-table, delivery-compatibility, and Rich-presentation tests own width, redirected-output, unsafe-text, state, and parity assertions. | `E2E-UX-05-{baseline,candidate}-doctor-{100,50}.txt/.png` plus machine documents. |
| `E2E-UX-06` | `Command help is discoverable and accurate`: all three scenarios | Installed command tree and visible help changed. OpenAPI: N/A. | `odcli -h`; `odcli env -h`; each of the six named leaf `-h` calls; documented examples for `init --venv`, `env create --create-venv`, restore/run/exec/test in parse-safe or documented dry-run mode; `odcli run --dry-run -- -h` | Copy a minimal disposable project/backup fixture; snapshot it. Reset per revision, capture every page, parse/dry-run every example, and assert registered option names/defaults, no runtime resolution/mutation, native child `-h` passthrough, and unchanged hash. Cleanup case directory. | Follow one init example and one native passthrough example against an independent fixture without mutation. | GitHub `offline-tests`: help/parse/dry-run tests own registration, defaults, no-resolution seams, and `prepare-venv` recovery wording. | Baseline/candidate help text and representative `init`/`env create` PNG pairs. |
| `E2E-UX-07` | `Selector completion is local and side-effect-free`: all three scenarios; `Typed field discovery reuses the result schema`: `Comma-separated completion preserves prior fields` | Installed shell protocol and quoting benefit from a real process boundary. OpenAPI: N/A. | `_ODCLI_COMPLETE=bash_complete COMP_WORDS='odcli --project <project> --env sp' COMP_CWORD=4 odcli`; similarly `... env show sp` with `COMP_CWORD=5`, `... remote update sta` with `COMP_CWORD=5`, and `... env show known-env --format json --fields environment.na` with `COMP_CWORD=9`; repeat the last value as `project.name,envir` | Seed only revision-local catalogue/config with `space env`, another project's environment, remotes `staging` and `space remote`, and a known result schema. The driver substitutes the isolated executable/project path into the exact strings. Reset readable and unreadable-source variants per revision; snapshot all state. Assert scoped/prefix-filtered encoded candidates, comma-prefix preservation, safe empty degradation when unreadable, unchanged built-in command/option/path completion, no traceback/network/runtime/snapshot/migration/chmod/mutation, and unchanged hashes. Restore permissions in `finally`, then cleanup. | Execute all four completion strings plus unreadable-source variant, decode candidates, and compare catalogue/config hashes. | GitHub `offline-tests`: characterization/output-mode/completion tests own seam failures, project scope, schema isolation, prefixes, and value redaction. | Sanitized completion transcript, decoded candidates, hashes; no image. |

### Capture, commits, and cleanup

- Baseline is exactly `d6a4e093377cd6c29fcf564406626ea5f17718aa`; candidate is the exact 40-hex implementation SHA supplied to the entry command. The driver SHALL record both and use equivalent fixtures, locale, terminal width, `NO_COLOR`, `TERM=dumb`, and TTY/non-TTY mode as applicable.
- On macOS, raw text is authoritative. Required PNGs SHALL be renders of that captured text produced with bundled `openspec-e2e-testing/scripts/render_terminal.swift`, labelled as renders rather than terminal screenshots. ANSI/TTY/progress limitations SHALL be stated; text and images preserve the tested output language, while the plan and Tester-authored explanations remain English.
- Test script/fixture work and any genuine functional result SHALL use separate commits when both exist. If the verified function produces no separate commit, no artificial second commit is required. Preserve any real generated commit unchanged and link it from the Multica comment.
- Cleanup SHALL remove only owned disposable installations, catalogues, and temporary roots while preserving the evidence MR, branch, scripts, fixtures, and genuine commits. There are no databases or ports in this CLI-only plan. Cleanup SHALL NOT merge/close the MR or delete its branch without separate authorization.

## Estimate evidence and invalidation

The estimate considered the complete proposal/design/spec/task/E2E package, the rebased current snapshot, the existing output and typed-field helpers, expanded `PUBLIC_LEAF_CASES`, integrated `init --venv`/`prepare-venv`, lazy registration, split monitor collection implementation and project scoping, doctor renderer, remote configuration commands, startup tests, completion characterization, packaging checks, and recent ownership history. The main uncertainty drivers are Click's pre-callback failure timing across the enlarged lazy command tree, cross-platform installed-executable evidence, and interaction among completion callbacks and project resolution. No implementation tests or project scripts were run for the estimate.

The estimate is invalidated by a change to the machine envelope version, `PUBLIC_LEAF_CASES` classification contract, catalogue/public snapshot schemas, runtime dependencies, `init`/venv integration, command names/defaults, a requirement for network-backed completion, or a requirement to implement atop a materially different MYL-430 boundary. Approval queues, human review time, CI queue time, external provider waits, and unavailable real-service environments are outside active developer-hours.
