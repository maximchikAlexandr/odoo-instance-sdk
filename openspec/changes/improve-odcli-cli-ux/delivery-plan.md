## Delivery contract

- Task key: `MYL-431`
- Change: `improve-odcli-cli-ux`
- Approved base: `origin/main` at the SHA recorded in the planning handoff
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
| `WP-04` | 3 | `WP-01`, `WP-02`, `WP-03` |
| `WP-05` | 4 | `WP-02`, `WP-03`, `WP-04` |

Stage 1 is the verified parallel frontier: `WP-01` owns shared output/import infrastructure while `WP-02` owns focused environment selection and collection. Later work converges on shared Click registration and completion surfaces and is therefore ordered to avoid conflicting writes.

## WP-01 — Machine usage boundary and metadata startup

- Stage: `1`
- Covers tasks: `1.1`, `1.2`, `2.3`, `2.4`
- Deliverable: the existing Click/output boundary emits supported JSON/TOON usage failures without a second parser, and metadata paths no longer import operation-only database preparation dependencies while retaining typed failure context.
- Owned responsibility scope: shared command/group error interception, bounded output and failure-context import boundary, typed usage-error contract tests, fresh-process module assertions, installed executable measurement harness/evidence, and directly related fixtures/docs. Critical shared files include `commands/output.py`, the common CLI registration/group implementation, `internal/output_fields.py` only where machine field errors enter the common boundary, `tests/unit/test_cli_output_modes.py`, and `tests/unit/test_cli_startup.py`.
- Contract surface: v1 failure envelope, `error.code="usage_error"`, exit `2`, JSON/TOON parity, native Click fallback boundary, no operation effects, public export identity, and named prohibited metadata imports.
- Definition of done/evidence: parameterized invalid number/range/field/missing-argument cases pass in both machine formats; unknown command/format and unsupported native/live cases retain Click stderr; fresh-process and installed executable checks prove heavy module absence and record before/after measurements without a timing threshold; database failure context still projects correctly.
- Parallel-safety rationale: writes only shared output/import infrastructure and its tests; it does not edit environment selection/monitor code owned by `WP-02`.

## WP-02 — Focused environment resolution and scoped sampling

- Stage: `1`
- Covers tasks: `2.1`, `2.2`
- Deliverable: `env show` resolves UUID/name/cwd from existing catalogue data before probes and produces its unchanged typed result from one snapshot limited to the owning project.
- Owned responsibility scope: catalogue-backed environment identity planning, cwd worktree lookup, focused monitor invocation/selection, and directly related env/monitor tests and fixtures. Critical shared files include `commands/env/checkout.py`, catalogue readers used by that command, `resources/monitor/planning.py` only if the existing pure selector needs a compatible narrow extension, and focused environment tests.
- Contract surface: selector ambiguity/not-found diagnostics, project ownership, `snapshot(project_id=...)`, exactly one sample, stopped/unavailable compatibility, provenance, and absence of unrelated probes or alternate collectors.
- Definition of done/evidence: UUID, name, cwd, missing, ambiguous, stopped, and unavailable cases pass; call assertions prove failure before probes and one selected-project snapshot on success; output schemas and renderings remain compatible.
- Parallel-safety rationale: the write-zone is environment resolution/monitor planning, disjoint from `WP-01` shared output/import files.

## WP-03 — Expected failures, doctor presentation, and help policy

- Stage: `2`
- Direct dependency: `WP-01`
- Covers tasks: `1.3`, `3.1`, `3.2`, `3.3`, `3.4`
- Deliverable: named decode/filesystem failures are controlled, doctor human output is actionable, and accurate `-h` plus examples/constraints are available across the existing command tree without changing native passthrough.
- Owned responsibility scope: exec/update error classification at owning handlers, doctor Rich tables, common help-option policy, help text for the six named commands, and directly related rendering/help/parse/dry-run tests. Critical shared files include `commands/cli_parts/callbacks.py`, `commands/cli_parts/registration.py`, `commands/update.py`, the named command registration modules, and doctor/help tests.
- Contract surface: stable expected-failure codes, sanitized reason/next action, doctor machine-schema preservation, shell-safe remediation display, `-h` inheritance, literal `-- -h` passthrough, unchanged option names/defaults, and no runtime resolution for help.
- Definition of done/evidence: isolated invalid UTF-8 and permission-denial cases have no traceback/secret/effect; doctor passes normal/narrow/non-TTY and machine parity cases; root/group/leaf `-h` and child passthrough tests pass; every added example parses or safely dry-runs under fixtures.
- Parallel-safety rationale: begins only after `WP-01` fixes the common output/import contract; it owns callback/help files before completion callbacks later modify shared registration.

## WP-04 — Local selector completion and schema-derived fields

- Stage: `3`
- Direct dependencies: `WP-01`, `WP-02`, `WP-03`
- Covers tasks: `4.1`, `4.2`, `4.3`
- Deliverable: environment/remote selectors and typed fields complete locally and safely; invalid fields offer bounded leaf-specific suggestions from the same schema source.
- Owned responsibility scope: direct Click `shell_complete` callbacks on existing root/arguments/options, exposure and reuse of schema paths, close-match diagnostics, comma/dotted completion, and all completion/field regression tests. Critical shared files include the finalized common registration from `WP-03`, environment registration from `WP-02`, `commands/remote.py`, `internal/output_fields.py`, and CLI characterization/field tests.
- Contract surface: prefix filtering, spaces, project scope, expected unreadable-source degradation, no network/snapshot/runtime/migration/chmod/mutation, leaf-specific schema paths, bounded value-free suggestions, exit `2`, JSON/TOON usage envelope inherited from `WP-01`, and standard command/option/path completion preservation.
- Definition of done/evidence: shell completion matrices cover environment and remote values, dotted/comma fields, unreadable sources, and spaces; fail-fast mocks prove forbidden seams are untouched; valid projections are byte/semantic compatible; existing nested command, option, and path completion tests remain green.
- Parallel-safety rationale: intentionally follows all owners of registration, environment selection, and machine field errors because it edits those converged seams; no active sibling shares its write-zone.

## WP-05 — Compatibility, documentation, and final verification

- Stage: `4`
- Direct dependencies: `WP-02`, `WP-03`, `WP-04`
- Covers tasks: `5.1`, `5.2`
- Deliverable: the intentional contract change is documented and the integrated implementation is verified at repository and installed-artifact boundaries with limitations recorded honestly.
- Owned responsibility scope: `CHANGELOG.md`, maintained CLI documentation, integration/compatibility adjustments, full repository gates, wheel/executable verification, and evidence records. Directly related tests, fixtures, snapshots, and service files are included when required to close an integration failure and are not owned by an active sibling.
- Contract surface: all AC-1 through AC-9, canonical `PUBLIC_LEAF_CASES`, successful envelope compatibility, repository architecture/security rules, installed executable outside checkout, and no new dependency/infrastructure.
- Definition of done/evidence: focused suites, formatting, lint, type checking, full repository tests, wheel build/install, metadata measurement, machine stdout/stderr/exit, non-TTY/NO_COLOR/TERM=dumb, narrow PTY, completion side-effect, and relevant dry-run gates pass on the final SHA; real Odoo/PostgreSQL/Windows or other unavailable checks are explicitly separated from passed evidence.
- Parallel-safety rationale: final convergence and repair package; it starts only after production work is integrated and has no sibling.

## Task coverage audit

Every OpenSpec task is assigned exactly once:

- `WP-01`: `1.1`, `1.2`, `2.3`, `2.4`
- `WP-02`: `2.1`, `2.2`
- `WP-03`: `1.3`, `3.1`, `3.2`, `3.3`, `3.4`
- `WP-04`: `4.1`, `4.2`, `4.3`
- `WP-05`: `5.1`, `5.2`

The DAG has one genuine concurrent frontier (`WP-01` and `WP-02` at stage 1). Shared contracts are predecessors, later siblings have no conflicting write-zones, and operational WIP limits are intentionally not encoded in topology.

## Estimate evidence and invalidation

The estimate considered the complete proposal/design/spec/task package, current base snapshot, the existing output and typed-field helpers, lazy registration, environment monitor/project scoping, doctor renderer, remote configuration commands, startup tests, completion characterization, packaging checks, and recent ownership history. The main uncertainty drivers are Click's pre-callback failure timing across lazy commands, cross-platform executable measurement, and interaction among completion callbacks and project resolution. No implementation tests or project scripts were run for the estimate.

The estimate is invalidated by a change to the machine envelope version, supported leaf inventory, catalogue/public snapshot schemas, runtime dependencies, command names/defaults, a requirement for network-backed completion, or a requirement to implement atop a materially different MYL-430 boundary. Approval queues, human review time, CI queue time, external provider waits, and unavailable real-service environments are outside active developer-hours.
