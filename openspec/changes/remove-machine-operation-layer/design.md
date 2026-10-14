## Context

The graph-2 planning baseline is PR #137 head `67b30692bd21a81c5e0759937fb08b86c0d30540` over base `9e388b8f1d7ac1d1907922e07493a267007f8cfb`. The branch contains eight consolidated commits: three machine-layer commits, three independently useful monitor/database/recovery commits, one integrated-boundary test commit, and one documentation commit. The owner decided in comment `01a13c0c-8e4a-71a5-a766-195d6553a321` that the existing SDK and JSON CLI are sufficient and that Supervisor, not a new machine-operation layer, will own Odoo processes.

The archived `2026-10-10-add-typed-machine-operation-contracts` package describes graph revision 1 and remains historical. It cannot be edited into a new intent or used as graph-2 acceptance evidence. This active change is based on the current implemented tree so that removal tasks name both machine-only paths and shared paths that must be preserved.

The current tree has a concrete separation:

- Machine-only: `operations/`, `commands/contract.py`, `commands/operation.py`, operation-specific context/public exports, Click registration, provider fixtures, operation packaging/unit tests, stable-ID/schema/session documentation, and machine E2E rows.
- Retained: snapshot-v3 selection/observation/raw CPU behavior; database observation and explicit reconciliation; inert backup reads; nullable structured replacement recovery and its Alembic lineage; shared CLI output/SDK delegation needed by ordinary commands.
- Compatibility gap: the embedded dashboard consumes existing snapshot arrays and passed graph-1 tests, but a separately deployed Moritora build may reject the additive `observation` field. No graph-1 run proved that external consumer.

## Goals / Non-Goals

**Goals:**

- Remove every production, test, packaging, documentation, and specification surface whose only purpose is operation discovery/export/invoke/session/provider consumption.
- Restore the pre-machine-layer test-owned CLI inventory without weakening SDK-first delegation, bounded output, dry-run, native transport, or E2E disposition checks.
- Preserve and independently re-verify monitor, database reconciliation, backup-read, and replacement-recovery behavior.
- Prove the candidate snapshot is accepted by the current external Moritora consumer before graph-2 acceptance.
- Make graph-2 review, CI, and E2E evidence revision-scoped and independent from graph 1.

**Non-Goals:**

- Supervisor installation or integration, daemon/RPC design, remote dispatch, dynamic operation discovery, plugin lifecycle, schema export, or language-specific/Go client generation.
- Reverting retained snapshot, reconciliation, or recovery behavior merely because it was delivered in the same PR.
- Removing the existing `Command` abstraction, ordinary JSON/TOON output, SDK-first CLI boundary, or test-owned CLI classification.
- Replaying old WPs or treating their completion, reviews, CI, or E2E as proof of graph 2.

## Decisions

### D1. Use a new active change and keep the archived package immutable

`remove-machine-operation-layer` is the sole graph-2 package. The archived graph-1 package is read-only provenance and may be cited only to identify superseded scope.

Alternative: edit or unarchive the completed package. Rejected because its checked tasks and archived main-spec application would falsely mix two requirement sets and two evidence graphs.

### D2. Delete the machine layer as one contract surface

Implementation SHALL remove the production operation registry/inventory, descriptor/schema models, adapters, local invoke/session runtime, provider loader/protocol, operation-specific context, CLI commands, public exports, provider fixtures, external-consumer proof, and machine-only docs/tests together. Main specs SHALL remove the complete `machine-operation-contracts` capability and the machine additions to `cli-odcli` and `command-execution`.

The original test-owned `PublicLeafCase` matrix remains the single CLI characterization source. It continues to carry SDK primitive or concrete CLI-only reason, classification, dry-run expectations, and E2E disposition/rationale, but production SHALL NOT import it or use it for runtime discovery.

Alternative: keep descriptors or export for possible future consumers. Rejected by the owner's YAGNI decision and because partial retention keeps the highest-drift part without invoke/provider consumers.

### D3. Preserve shared behavior by contract, not by commit shape

The implementation SHALL classify the full `base..graph-1-head` diff by behavior. A file touched by a machine commit is not automatically removed: shared bounded output, ordinary Click registration, SDK delegation, redaction, and direct-output inventory changes remain when existing non-machine specs/tests require them. Conversely, operation-only branches, imports, fixtures, and docs SHALL be removed even when mixed into shared files.

Snapshot v3, explicit reconciliation, inert reads, and structured recovery remain governed by current main specs. Their code, migrations, and focused tests are retained unless a minimal decoupling edit is necessary after machine imports disappear.

Alternative: revert the first three commits mechanically. Rejected because later consolidated commits and shared files cross those boundaries; a blind revert could remove retained fixes or resurrect obsolete inventory wording.

### D4. Treat external Moritora acceptance as a required compatibility gate

The candidate keeps top-level snapshot schema version 4 and adds optional snapshot-v3 observation metadata. Embedded generated TypeScript/dashboard tests SHALL pass, and an independently supplied current Moritora build SHALL consume a genuine candidate `/api/v1/snapshot` without parse failure, blank/crashed view, or loss of existing project/environment rows. Unknown-field strictness is tested at the actual consumer boundary rather than inferred from the embedded app.

The plan introduces no Moritora adapter. If the current external build or a reproducible launch contract is unavailable, the E2E gate is blocked rather than waived. Any required consumer code change is a separate scope decision unless it is already part of the supplied test repository.

Alternative: rely on OpenAPI compatibility or the embedded dashboard. Rejected because neither executes the separately deployed decoder that prompted the owner's concern.

### D5. Reset acceptance evidence at graph 2 and route acceptance gates on the parent

The reduced candidate SHALL receive new focused tests and full required GitHub CI inside the materializable WP DAG. After all WP branches fan into one authoritative implementation-parent candidate, Manager SHALL route a four-profile review on that parent SHA. Only a clean consolidated review permits Manager to dispatch Tester-owned live E2E as the next parent gate. Neither review nor E2E is a WP child or a WP definition-of-done item. A review finding returns through the parent's integrated-fix loop and invalidates the reviewed SHA; an E2E finding returns through the same parent routing before review and E2E are repeated as required.

Graph-1 evidence may guide test selection but SHALL be labelled historical and SHALL NOT satisfy a graph-2 gate. Local `make compat` remains forbidden; compatibility runs only in GitHub CI. Implementer WPs prepare the reusable scripts, fixtures, CI coverage, and exact-SHA handoff manifest; reviewers and Tester do not edit those deliverables while performing their independent gates.

### D6. Ponytail full gate

No new subsystem is introduced. The change deletes the operation subsystem and reuses the existing CLI test matrix, monitor API, dashboard/OpenAPI checks, database resources, catalogue migration, and replacement compensation flow.

| Candidate mechanism | Necessity | Existing alternative | Decision |
|---|---|---|---|
| Replacement machine registry/session/provider abstraction | No named consumer remains | Python SDK and JSON CLI | Remove |
| Supervisor integration | Separate product decision | Existing process lifecycle remains until separately changed | Exclude |
| New monitor compatibility adapter | Not justified before observing a real incompatibility | Exercise the current Moritora build against candidate JSON | Do not add |
| New E2E framework | Existing test repository and numbered environment tooling are sufficient | Extend focused scripts only | Do not add |
| New recovery/reconciliation abstraction | Current retained implementations already satisfy their specs | Existing typed models and commands | Do not add |

## Risks / Trade-offs

- **Mixed shared files cause accidental regression** → Classify every machine reference and preserve non-machine branches with focused CLI/SDK/output tests plus a final forbidden-symbol scan.
- **Removing the production inventory weakens CLI drift detection** → Restore the existing test-owned exhaustive Click-path matrix and architecture gates; do not create another runtime table.
- **Retained snapshot breaks external Moritora** → Require an exact-build compatibility walkthrough and fail closed when the build or launch contract is unavailable.
- **Catalogue migration is removed with machine cleanup** → Keep migration `0008_replacement_recovery`, schema head, recovery DTO, and historical/current migration tests explicitly owned by the retained recovery work.
- **Old green evidence is mistaken for new acceptance** → Record graph revision and exact candidate SHA in every review, CI, and E2E result; prohibit graph-1 reuse in delivery criteria.
- **A future consumer again needs discovery/invoke** → Start a new change from a named consumer and minimal use cases; do not leave dormant infrastructure in this PR.

## Migration Plan

1. Characterize the graph-1 head and mark every production/test/doc/spec reference as remove, retain, or shared-decouple before editing.
2. Remove operation registry/export/invoke/session/provider surfaces and restore test-owned CLI traceability while preserving ordinary CLI/SDK behavior.
3. Reconcile main and delta specs, docs, packaging, architecture inventories, E2E matrices, and generated artifacts so no machine surface remains.
4. Re-run focused retained-scope tests and regenerate OpenAPI/TypeScript only from the retained snapshot model.
5. Fan all WPs into one exact implementation-parent candidate and run required GitHub CI.
6. On the implementation parent, route four-profile review and only after a clean result route Tester-owned live Moritora/database E2E, using fresh revision-scoped evidence for both gates.

Rollback is a normal revert of graph-2 implementation commits before merge. No retained catalogue column or recovery bytes are removed, so graph-2 introduces no destructive data downgrade. The PR remains draft until all graph-2 gates pass.

## Open Questions

None for planning. The Tester must receive a reproducible reference to the current external Moritora build at execution time; absence is a verification blocker, not an implementation design choice.
