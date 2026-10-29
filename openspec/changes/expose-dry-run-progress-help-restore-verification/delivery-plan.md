## Delivery Contract

- Task key: `MYL-615`.
- OpenSpec change: `expose-dry-run-progress-help-restore-verification`.
- Planning revision: `R3`; this revision replaces the R2 implementation topology after a fresh `ponytail full` audit against the current code without changing product scope.
- Planning base: `origin/main` at the base revision recorded by the publication commit.
- Estimate authority: the root planning issue custom properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`. Numeric totals are intentionally not duplicated here.
- Estimate basis: full remaining work for one experienced developer familiar with this Python/Click/Odoo stack, without AI acceleration. It includes implementation, required tests/fixtures/docs, normal review repair, and attended verification; unattended CI/provider waits and approval queues are excluded.
- Confidence: medium. The existing plan, observer, output, command graph, restore, and real-Odoo seams are inspectable and reusable; uncertainty is concentrated in broad command-family adoption, stderr framing compatibility, Click help edge cases, and real attachment/filestore behavior.
- Calibration: evidence-based but uncalibrated; no comparable historical implementation-time sample is asserted. Tests were not run to manufacture estimate evidence.
- Delivery mode: `dag`. The authoritative estimate property is above the single-WP threshold and the stage-2 packages have independent deliverables and non-conflicting primary write zones after the shared output contract lands.
- Stage mapping: `WP-01 -> 1`; `WP-05 -> 1`; `WP-02 -> 2`; `WP-03 -> 2`; `WP-04 -> 2`; `WP-06 -> 3`.
- Topological frontiers: stage 1 contains independent `WP-01` and `WP-05`; stage 2 contains independent `WP-02`, `WP-03`, and `WP-04`; stage 3 contains `WP-06`. WIP limits are operational and are not encoded as dependencies.
- Scope changes to effect vocabulary, progress framing, help source, restore outcome semantics, or WP dependency surfaces require a planning revision. Assignee/status movement does not.

## Ponytail Result

`ponytail full` found four candidate subsystems and removed two R2 abstractions. The implementation reuses 109 existing `PreparedAction` construction sites across 36 files, the existing `StepEvent` elapsed/completed/total facts and `RunContext.progress` validation, the live Click graph, and the current restore stages/result path. New shared code is limited to a compact `file_effects` validator/projection, one machine stderr frame projection, one live-Click help projection, and restore outcome fields/query checks. There is no new progress field, restore adapter, planner, runner, command registry, scheduler, filesystem tracer, renderer hierarchy, restore service, dependency, or persistent schema. The evidence-based minimal complete diff is approximately 1,800–3,100 changed lines including tests/docs; the breadth comes from the required command/state matrix, not framework code.

## E2E Decision

`e2e_required=true`.

Most requirements are deterministic lower-level contracts and SHALL be proved with parametrized unit/integration tests. Three gaps require real component interaction: a frozen dry-run target must match later filesystem/database effects across a public lifecycle; machine progress must remain frameable while real Git/HTTP/restore work is active; and attachment rows must be reconciled with a real restored filestore. Machine help itself does not require a live stand, but its visible output requires an independent public CLI walkthrough and before/after captures.

The Tester repository is the project-description URL `https://gitlab.icode.by/a.maksimchyk/binta-test-repo`. Planning SHALL NOT run the scenarios. The Tester SHALL use a fresh dedicated numbered `odcli-N`, its own candidate checkout, database, port, catalogue/user root, and test-repository checkout. Slot creation SHALL use `scripts/agent_checkout.py create` with both `--project ~/e2e/odcli/numbered-project-e2e` and `--reference-checkout ~/e2e/odcli/binta-e2e-reference`, a free HTTP port, a unique target database, and the exact implementation SHA; deletion SHALL pass the same `--project`. The reference checkout, legacy stand, persistent source database, and other agents' slots are read-only/out of scope.

## Verification Matrix

| Case ID | OpenSpec requirement/scenario | Verification gap / local decision | OpenAPI operation/schema | Local test script | Independent Tester steps | CI disposition/test/job | Assertions | Artifacts/images |
|---|---|---|---|---|---|---|---|---|
| `E01-effects-parity` | `command-execution`: structured effects, command inventory, stale target, dry-run inertness; `translation-export`: all scenarios; `cli-odcli`: consistent consequences | Real filesystem ownership, Odoo ISO resolution, atomic publication, and restore/removal state make plan/execution parity material; selected focused lifecycle | None | `tests/e2e/myl615_transparent_operations_e2e.py --case effects-parity` | On an isolated slot, capture base/candidate translation replacement and representative init/VS Code/env/database dry-runs; execute only disposable candidate actions; inspect exact affected roots and preserved controls; introduce a deterministic external state change before one execution and require safe refusal/revalidation | Implementer extends existing unit/integration matrices and the real-Odoo full tier; `offline-tests` plus focused full real-Odoo job, not smoke-only | Decoded formats agree; Rich exposes same effects; dry-run hashes/state unchanged; executed targets equal frozen targets; unknown/inapplicable effects are not guarantees; secrets absent | Sanitized commands/exits, before/after filesystem/DB projections and hashes in Multica; actual base/candidate text and PNG renders for translation replace, restore move/retain, and one conditional removal |
| `E02-progress-stream` | `command-execution`: machine progress; `cli-odcli`: separate streams | Real concurrent pipe/HTTP activity is needed to prove progress arrives before final completion and does not corrupt stdout; selected | None | Same script, `--case progress-stream` | Use disposable Git clone, candidate remote-backup download, and restore; drain stdout/stderr concurrently; parse every JSON/TOON frame; independently inspect monotonic bytes/stages and final document; interrupt one disposable operation and inspect cleanup | Unit tests own known/unknown totals, rates, framing and canaries; integration tests own interrupt/failure; full real-Odoo CI covers one restore progress path | Progress is observable before completion; IDs/order attributable; known totals bounded; unknown total has no percentage; JSONL/RS-TOON fully parse; stdout exactly one envelope; no secret/content leak; exit 130 cleanup truthful | Raw sanitized baseline/candidate stderr/stdout captures and parser assertion summaries in Multica only; actual baseline/candidate Rich progress/intermediate/final-summary text and PNG renders. For machine framing, baseline SHALL capture the real command's stdout/stderr/exit and prove that no structured progress frame is present; its labelled text/PNG render SHALL visualize that captured absence rather than inventing a frame. Candidate machine-frame text is rendered and labelled as a render. |
| `E03-restore-verification` | `database-restore`: independent outcomes and all scenarios | Only a real Odoo database plus filestore proves unique `ir_attachment` reconciliation and incomplete-store reporting; selected | None; no API schema change | Same script, `--case restore-verification` | Create disposable fixture attachments with duplicate rows to one stored file and another distinct file; restore a complete fixture, then restore a fixture copy with one owned stored file removed; inspect DB identities and candidate result in every format; never alter reference/source data | Parametrized unit/integration tests own unsafe names/query failure/not-checked; focused full real-Odoo CI owns complete and incomplete fixtures | Complete case passes with unique counts; incomplete case reports exact mismatch and no verified aggregate success; duplicate row counted once; password reset does not affect check state; no content exposed | Sanitized baseline/candidate SQL count summaries, result documents, exit codes, fixture provenance and cleanup ledger in Multica; actual baseline/candidate Rich restore result text and PNG renders. Baseline SHALL show the real pre-change summary and its absence of explicit independent verification state/counts; candidate SHALL show complete/incomplete outcomes. |
| `E04-machine-help` | `cli-odcli`: machine-readable help scenarios | No live Odoo gap remains; local stand excluded. Visible public CLI output still needs independent inspection and images | None | No local E2E script; repository Click integration tests are sufficient | From clean base and candidate wheels/checkouts, invoke root and `git clone` Rich/JSON/TOON help with both option orders; decode machine output; compare live Click tree and verify no context/database/network access | `offline-tests` Click/help characterization; package wheel smoke for installed entry point | One versioned tree/subtree; JSON/TOON semantic parity; safe types/defaults; callable defaults unevaluated; Rich unchanged; no second registry | Actual base/candidate help text; PNG renders for root and leaf machine-help difference; command/exit/decode assertions in Multica |

## Scenario Coverage and Lower-Level Exclusions

| Contract area | Live coverage | Lower-level coverage / reason no extra local E2E |
|---|---|---|
| File-effect schema, normalization, redaction, fingerprint | `E01` samples public projections | Exhaustive create/modify/replace/delete/move, secret canaries, malformed/escape paths, and fingerprint stability are deterministic unit matrices. |
| Init, VS Code, bug-report modes | Representative init/VS Code observations in `E01` | Mode cartesian product, no-op, refusal, and metadata-only changes are filesystem integration tests; replaying every leaf live would add no boundary evidence. |
| Environment ownership and rollback | Representative owned/reused/retained resources in `E01` | Failure after each action, drift, borrowed backup, venv reuse, and retry require controlled executor/catalogue fixtures and SHALL remain integration tests. |
| Database/backup conditional deletion | Representative restore/move/retain in `E01`; restore integrity in `E03` | Provenance permutations, active sessions, unsafe filestore, missing archive, and backup relationship matrices are deterministic integration tests. |
| Git/runtime/module/update uncertainty | None | These contracts assert bounded honesty rather than a particular mutation. Unit/integration plans prove exact known roots and uncertainty; arbitrary-code/Git-conflict live effects cannot prove completeness. |
| Progress known and unknown totals | Real known/available totals in `E02` | Unknown content length, rate clock, frame splitting, failure, and secret injection use controlled fake clocks/streams; a live network proxy would be less reliable. |
| Help graph/default safety | Manual `E04` | Click object graphs and callable defaults are fully controllable in integration tests; no real Odoo stand is relevant. |
| Restore duplicate/missing files | `E03` | Absolute/traversal identities and query faults stay in deterministic tests because unsafe live fixtures are unnecessary. |

## Test Script and Fixture Contract

The Tester-owned GitLab MR SHALL add `tests/e2e/myl615_transparent_operations_e2e.py` and focused offline self-tests in `tests/test_myl615_transparent_operations_e2e.py`. The runner is an assertion harness, not an alternate planner. It SHALL accept explicit baseline/candidate SDK checkouts and SHAs, baseline/candidate executables, test-repository checkout and SHA, numbered slot identity, isolated user/catalogue roots, target/control database and filestore identities, HTTP/PostgreSQL ports, locale, terminal width, artifact directory, and bundled `openspec-e2e-testing/scripts/render_terminal.swift` path. It SHALL not contain private hostnames or credentials.

The script SHALL refuse dirty or revision-mismatched inputs, the reference checkout, persistent database/volume identities, occupied ports, or paths outside declared disposable roots. Setup SHALL create fixture data only inside the dedicated slot. The complete and incomplete restore fixtures SHALL be separate disposable copies; removal of a stored file is allowed only after its captured path is proven inside that fixture's filestore. Progress capture SHALL concurrently drain both pipes to avoid deadlock and retain raw bytes long enough to validate UTF-8/framing before sanitization/publication.

GitLab stores only the reusable scripts, self-tests, fixtures, and genuine outputs created by the tested Git functionality when applicable. Test code and a genuine tested-function commit, if any, remain separate commits; no artificial result commit is required. Runtime JSON/TOON, logs, SQL output, diagnostic JSON, hashes, SHA evidence, commands, exit codes, provenance, and images are published only to Multica.

## Independent Walkthrough and Image Contract

The independent Tester SHALL perform fresh public CLI actions distinct from invoking the automated script, with a separate disposable checkout/database/port/user root. A second script invocation is not the manual walkthrough. The Tester SHALL inspect outputs plus filesystem, Git, PostgreSQL, and filestore state through read-only commands.

For changed macOS terminal output, capture actual UTF-8 text at a fixed locale and 120-column width for baseline and candidate. Render that captured text to PNG with bundled `scripts/render_terminal.swift`; label renders as renders, not terminal screenshots. Preserve the tested language. Capture at minimum:

- baseline/candidate translation replacement dry-run and candidate final publication summary;
- baseline/candidate restore prior-filestore move/retention plan and restore summaries; the baseline text/PNG SHALL visibly preserve the pre-change absence of independent verification state/counts, and candidate captures SHALL show passed/failed verification;
- baseline/candidate Rich progress intermediate state and final summary, with honest notes where redirection cannot preserve TTY animation/ANSI; machine-mode baseline SHALL pair raw stdout/stderr/exit evidence with a labelled text/PNG rendering of the observed absence of structured progress frames;
- baseline root/leaf help behavior and candidate JSON/TOON root/leaf help text.

Missing a mandatory base/candidate text or PNG pair prevents complete verification. Images, actual logs, parser results, hashes, commands, exit codes, and assertions SHALL be attached to the final Multica Tester comment and verified by comment read-back; they SHALL NOT be committed to GitLab.

## Readiness, Attempt, Evidence, and Cleanup Contract

Before a live attempt, the Tester SHALL prove: exact candidate SDK SHA; exact test-script MR/commit SHA; clean test checkout; healthy dedicated PostgreSQL cluster on the project-defined endpoint; free unique port/database/slot; isolated user root; correct Binta wrapper arguments; available renderer; and passing focused deterministic product/tests at that revision. A failing readiness gate consumes no live attempt.

Each case result records candidate/base/test revisions, public commands, material fixture identities, assertions, and cleanup state. A PASS remains valid only for the same product and material fixture revisions. On a new candidate revision, the Tester SHALL perform an explicit impact assessment and rerun only affected cases; unresolved impact leaves the case unverified. Diagnosis uses captured evidence and cheaper deterministic tests before another live attempt.

Cleanup SHALL use the same Binta wrapper and `--project` value, remove only identities captured as disposable, and verify absence of the slot database/process/port/catalogue entries. It SHALL preserve the GitLab MR, branch, test commits, and any genuine commit created by tested Git functionality. It SHALL not close/merge the MR or delete evidence branches without a separate instruction.

## Work Packages

### WP-01 — Shared effect and machine-progress projection

- Stage: `1`.
- `depends_on`: none.
- OpenSpec task coverage: `1.1`, `1.2`, `1.3`, `5.1` exactly once.
- Deliverable: validated `ActionStep.details.file_effects`, shared Rich projection, and machine stderr framing derived from existing `StepEvent` facts, with no domain adoption or new progress model.
- Owned responsibility scope: `execution.py`, prepared-action public projection, `internal/output_rich.py`, progress/output-only regions of `commands/output.py`, narrowly related helpers and unit tests. Related fixtures/docs are included unless owned by an active sibling.
- Critical shared files: effect helper, Rich renderer, and machine frame projection become stable predecessor contracts; later WPs consume but do not redesign them.
- Contract surface: effect vocabulary/order/normalization/conditional representation/redaction plus JSONL/RS-TOON framing, derived byte names/rate, stderr/stdout separation, interruption/failure sanitation, and compatibility.
- DoD/evidence: focused tests prove every action kind, malformed input, secrets, Rich/JSON/TOON parity, fingerprints, existing-event framing, known/unknown totals, derived non-negative rate, one final stdout document, and no dry-run events; no new event field, adapter, registry, or second plan appears.
- Parallel-safety rationale: this predecessor owns the only shared effect and output-projection surfaces before domain siblings begin; `WP-05` owns help-only registration/projection and does not edit these files.

### WP-02 — Project and environment file-effect adoption

- Stage: `2`.
- `depends_on`: `WP-01`.
- OpenSpec task coverage: `2.1`, `2.2`, `2.3`, `2.4`, `3.1`, `3.4` exactly once.
- Deliverable: translations, init, VS Code, bug-report, and environment lifecycle expose truthful effects with plan/execution parity.
- Owned responsibility scope: translation/project-init/VS Code/bug-report/environment planners and their related tests/fixtures/docs; excludes database restore internals owned by `WP-03`.
- Critical shared files: may consume effect helper and existing output functions; changes to shared schema require coordination through `WP-01`, not local divergence.
- Contract surface: PO/POT publication, generated project files, draft metadata, worktree/config/lock/venv/backup/filestore ownership and rollback.
- DoD/evidence: project/environment parametrized modes cover new/existing/no-op/uncertain/owned/reused/preserved/error-cleanup states, environment drift/retry and parity, and exact dry-run non-mutation; no database/backup/restore test is mutable here.
- Parallel-safety rationale: primary writes are project/environment domains and do not overlap Git/runtime or database/restore domains after `WP-01`.

### WP-03 — Database, backup, and restore verification

- Stage: `2`.
- `depends_on`: `WP-01`.
- OpenSpec task coverage: `3.2`, `3.3`, `3.5`, `5.3`, `7.1`, `7.2`, `7.3`, `7.4` exactly once.
- Deliverable: database/backup effects, existing-event progress for download/restore, independent restore verification states/counts, and real-Odoo fixtures.
- Owned responsibility scope: all database restore/refresh/drop implementation, backup deletion/download, restore result models, attachment/filestore validation, restore progress publication, and all related unit/integration/real-Odoo database-domain tests.
- Critical shared files: database/backup/restore modules and their domain tests are exclusively mutable here. The package consumes `WP-01`'s effect and frame projections and SHALL not redesign them.
- Contract surface: prior-filestore move/compensation, provenance-bound deletion/retention, backup facts, download bytes, existing restore stages/heartbeats, neutralization state, and safe unique attachment counts.
- DoD/evidence: database/backup conditional-effect, drift, retry, dry-run-inertness, and plan/execution parity tests pass alongside complete/incomplete/duplicate/unsafe/not-checked/query-failure restore matrices; publication/compensation stays truthful; focused real-Odoo coverage exists.
- Parallel-safety rationale: after both predecessor contracts land, database/restore files and tests are exclusively owned here and are disjoint from the stage-2 project/environment and Git/runtime write zones.

### WP-04 — Git, runtime, module, and self-update effects

- Stage: `2`.
- `depends_on`: `WP-01`.
- OpenSpec task coverage: `4.1`, `4.2`, `4.3`, `4.4`, `5.2` exactly once.
- Deliverable: bounded known consequences and explicit uncertainty across Git, arbitrary-code, and self-update flows, plus existing-observer progress for Git clone.
- Owned responsibility scope: Git planners/resources including clone progress production, run/shell/exec/eval/test/module planners, self-update planning, and their tests/docs.
- Critical shared files: consumes shared effect helper; does not edit help/progress framing or restore result models.
- Contract surface: Git index/history/rebase/push scope, clone stage/available byte facts, managed config/log/data roots, arbitrary-code/conflict limits, OdCLI recovery snapshot/migration wording.
- DoD/evidence: parametrized plan/redaction/raw-stream/dry-run tests prove no fabricated file list and no mislabelled Odoo backup.
- Parallel-safety rationale: domain modules do not overlap sibling primary write zones after `WP-01`.

### WP-05 — Machine-readable help

- Stage: `1`.
- `depends_on`: none.
- OpenSpec task coverage: `6.1`, `6.2`, `6.3` exactly once.
- Deliverable: versioned JSON/TOON root/subtree help projected directly from live Click objects with Rich compatibility.
- Owned responsibility scope: shared Rich Click command/group classes or focused help module, help serialization, and Click/package tests.
- Critical shared files: if CLI composition changes are needed, restrict them to help registration/projection; do not edit execution, Rich plan, or machine progress projection files owned by `WP-01`.
- Contract surface: help schema, option ordering, safe defaults, aliases/hidden/deprecated metadata, no context/domain work, no second registry.
- DoD/evidence: live graph equals machine help; JSON/TOON decode equally; unsafe defaults are not evaluated; root/leaf Rich help remains characterized.
- Parallel-safety rationale: help metadata and tests consume neither filesystem-effect nor progress contracts and are disjoint from `WP-01`'s stage-1 write zone.

### WP-06 — Integrated characterization, CI, docs, and E2E handoff

- Stage: `3`.
- `depends_on`: `WP-02`, `WP-03`, `WP-04`, `WP-05`.
- OpenSpec task coverage: `8.1`, `8.2`, `8.3`, `8.4` exactly once.
- Deliverable: consolidated command inventory, CI/real-Odoo fixtures, user documentation, generated artifacts, and executable independent Tester handoff/evidence contract.
- Owned responsibility scope: `PUBLIC_LEAF_CASES` characterization, generated command matrix, cross-domain integration fixtures, CI workflow adjustments, docs, and evidence coordination. It may make focused repair edits only after sibling integration findings and with ownership coordination.
- Critical shared files: generated inventory and CI become writable only after all predecessors land; product-domain files remain predecessor-owned unless a documented integration repair is required.
- Contract surface: exhaustive leaf coverage, docs for effects/help/progress/verification, exact-revision CI, Mac captures, GitLab MR/test commits, Multica attachments/read-back, cleanup ledger.
- DoD/evidence: strict tests/validation pass at one consolidated revision; every delta scenario maps to CI/lower-level/live evidence; required Tester artifacts and images are published outside Git with exact revision provenance.
- Parallel-safety rationale: integration necessarily follows all independent feature packages and therefore forms the final topological stage.
