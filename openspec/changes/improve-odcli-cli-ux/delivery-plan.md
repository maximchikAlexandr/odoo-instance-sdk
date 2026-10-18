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
- Test-code destination: the private GitLab test repository supplied by the Multica project description, passed at execution as `TEST_REPOSITORY_URL`; the literal private URL, hostnames, member identity, and credentials SHALL NOT be copied into this public Git repository.
- Reusable test asset: `scripts/myl431_cli_ux_e2e.py` plus minimal fixtures under `fixtures/myl431_cli_ux/` in the test-repository MR. The driver SHALL accept baseline and candidate repository revisions, build/install each into separate disposable tool roots, execute outside either checkout, record command/exit/stdout/stderr provenance, and clean only its owned temporary roots.
- Product-repository responsibility: `WP-05` owns all pytest/CI coverage and any product-side fixture needed by the change. The Tester SHALL NOT edit those GitHub tests; findings are reported back against the candidate SHA.
- Evidence destination: real stdout/stderr text, timing samples, logs, diagnostic JSON, hashes, commands/exit codes, and PNG files SHALL be attached to the final Multica Tester comment with attachment read-back. They SHALL NOT be committed to either repository. The test-repository MR retains only normal test scripts/fixtures and any genuine commits produced by the verified Git behavior.

| Case | Requirement/scenario | Verification gap / decision | Local script and independent Tester steps | CI disposition and assertions | Artifacts / images |
|---|---|---|---|---|---|
| `E2E-UX-01` | Expected invalid UTF-8 and update access failures | Real installed stdout/stderr/exit and traceback hygiene remain after unit fault injection | Driver creates an invalid-byte script and an isolated denied/update-state fixture, invokes candidate Rich/JSON/TOON commands, then Tester independently reruns one Rich and one JSON case and inspects that no child/mutation occurred | Focused pytest error matrix remains authoritative for exact exception families, redaction, and no-effect assertions | Sanitized command transcript and machine document; before/after error text and PNG pair |
| `E2E-UX-02` | Supported machine usage errors and typed field suggestions | Installed Click/lazy-command interception and JSON/TOON transport composition | Driver invokes invalid number/range/field/missing-argument cases on representative bounded leaves; Tester decodes documents independently and compares semantic equality | Parametrized `PUBLIC_LEAF_CASES` tests cover all eligible classifications, exit `2`, empty competing stderr, and no operation effects | Sanitized JSON/TOON documents and assertion summary; no decorative image beyond the changed human-error pair in `E2E-UX-01` |
| `E2E-UX-03` | Focused `env show` selection | Expensive-probe absence is fully observable in lower-level call-count tests; no live stand adds proof | No separate local script. Tester reviews the candidate tests and runs the public command only against disposable catalogue records to confirm result compatibility | Focused tests SHALL assert name/UUID/cwd resolution, ambiguity/absence before probes, exact project id, and one snapshot | Command/exit transcript only; no image because visible output is unchanged |
| `E2E-UX-04` | Lightweight metadata startup | Installed executable outside checkout and first/repeated process timings cannot be represented by import-only unit tests | Driver installs baseline/candidate separately and records cold plus repeated `--help`/`--version` runs from a neutral cwd; Tester independently inspects prohibited modules with the provided fresh-process probe | Deterministic fresh-process tests gate absence of Alembic, SQLAlchemy, db-preparation, and monitor collection modules; timings are evidence, never a shared-CI threshold | Revision-labelled timing/text report and module assertion output; no image |
| `E2E-UX-05` | Readable human doctor | Real terminal wrapping/sanitization is a visual contract | Driver renders identical deterministic WARN/ERROR/unavailable fixtures at normal and narrow widths for baseline and candidate. Tester reads the raw text, verifies target/reason/quoted remediation and no mutation, then generates PNGs from that actual text | Rich presentation tests cover widths, redirected output, unsafe text, all problem states, machine parity, and no remediation execution | Mandatory baseline/candidate doctor text and PNG pairs at both widths |
| `E2E-UX-06` | Help examples, `-h`, and native `-- -h` | Installed command tree and visible help changed; examples must remain parseable with new `init --venv` and existing `env create --create-venv` | Driver captures the six named help pages plus root/group/leaf `-h`, safely parses or dry-runs every documented example, and verifies child `-h` passthrough; Tester independently follows one init and one native example without mutation | Help/parse/dry-run tests gate option names/defaults, no runtime resolution, and the `prepare-venv` recovery wording | Mandatory baseline/candidate help text and PNG pair for representative `init` and `env create` pages |
| `E2E-UX-07` | Local selector and `--fields` completion | Shell protocol quoting and installed completion wiring benefit from a real shell boundary | Driver invokes supported Click completion environment variables against disposable catalogue/config fixtures containing spaces and dotted/comma prefixes; Tester independently inspects candidates and unchanged built-in option/path completion | Completion tests fail network/runtime/snapshot/migration/chmod/mutation seams and assert project scope, prefixes, unreadable sources, leaf schema isolation, and value redaction | Sanitized completion transcript and assertions; no image because completion output is not a changed end-user table |

### Capture, commits, and cleanup

- Baseline is the approved pre-change product revision selected by the Tester; candidate is the exact implementation SHA. The driver SHALL record both and use equivalent fixtures, locale, terminal width, `NO_COLOR`, `TERM=dumb`, and TTY/non-TTY mode as applicable.
- On macOS, raw text is authoritative. Required PNGs SHALL be renders of that captured text produced with bundled `openspec-e2e-testing/scripts/render_terminal.swift`, labelled as renders rather than terminal screenshots. ANSI/TTY/progress limitations SHALL be stated; text and images preserve the tested output language, while the plan and Tester-authored explanations remain English.
- Test script/fixture work and any genuine functional result SHALL use separate commits when both exist. If the verified function produces no separate commit, no artificial second commit is required. Preserve any real generated commit unchanged and link it from the Multica comment.
- Cleanup SHALL remove disposable installations, catalogues, databases, ports, and temporary roots while preserving the evidence MR, branch, scripts, fixtures, and genuine commits. It SHALL NOT merge/close the MR or delete its branch without separate authorization.

## Estimate evidence and invalidation

The estimate considered the complete proposal/design/spec/task/E2E package, the rebased current snapshot, the existing output and typed-field helpers, expanded `PUBLIC_LEAF_CASES`, integrated `init --venv`/`prepare-venv`, lazy registration, split monitor collection implementation and project scoping, doctor renderer, remote configuration commands, startup tests, completion characterization, packaging checks, and recent ownership history. The main uncertainty drivers are Click's pre-callback failure timing across the enlarged lazy command tree, cross-platform installed-executable evidence, and interaction among completion callbacks and project resolution. No implementation tests or project scripts were run for the estimate.

The estimate is invalidated by a change to the machine envelope version, `PUBLIC_LEAF_CASES` classification contract, catalogue/public snapshot schemas, runtime dependencies, `init`/venv integration, command names/defaults, a requirement for network-backed completion, or a requirement to implement atop a materially different MYL-430 boundary. Approval queues, human review time, CI queue time, external provider waits, and unavailable real-service environments are outside active developer-hours.
