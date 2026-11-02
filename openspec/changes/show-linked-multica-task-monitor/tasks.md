## 1. Persist verified binding evidence

- [ ] 1.1 Add frozen public external-task binding/summary/activity/freshness models, exports, validation, and JSON-safe redaction tests without exposing private binding fields in monitor models.
- [ ] 1.2 Add nullable environment-catalogue binding columns and forward migration; update schema/migration parity, row conversion, insert/reuse, legacy-row, and round-trip tests.
- [ ] 1.3 Extend caller-owned adoption to accept the optional binding in its immutable plan, write it only on successful create/reuse, fill an absent legacy binding only from complete proof, and reject conflicting replacement atomically.

## 2. Enrich one canonical monitor sample

- [ ] 2.1 Promote the existing environment-facts protocol to the supported public boundary while retaining the entry-point group, provider process isolation, hard deadline, large-response channel handling, deterministic IDs, and no core dependency on optional packages.
- [ ] 2.2 Move provider invocation into `EnvironmentMonitor.snapshot_command()`, pass immutable rows/private bindings, attach summaries to schema-v5 project/environment facts, and prove missing/failed/timed-out/duplicate providers preserve core owners.
- [ ] 2.3 Make `CheckoutInventory` copy facts from the captured snapshot without a second provider call; update one-shot/watch SDK and CLI fixtures for schema version 5, empty facts, project filters, removed rows, and per-sample call counts.

## 3. Implement the optional Multica provider

- [ ] 3.1 Extend `odcli-multica` prepare planning to build a safe binding from verified context, public Issue/permalink, and daemon evidence and pass it to core adoption; cover successful create/reuse, rollback, unsafe URL, missing permalink context, and identity conflict.
- [ ] 3.2 Implement and register the `multica` facts provider with exact path/server/workspace/runtime/daemon revalidation, grouping by unique issue, concurrency ceiling four, bounded public `multica-py` operations, and one observation shared across matching environments.
- [ ] 3.3 Implement the closed run-status mapping and completeness rules: exact `running` is active; complete known non-running/no-run pages are inactive; incomplete, unknown, unauthorized, timed-out, or malformed negatives are unknown; preserve only the verified identifier/link on dynamic failure.
- [ ] 3.4 Add extension unit/integration tests for issue status, public permalink refresh, all known run states, future status, pagination, timeout/error sanitization, deduplication, wrong host/path, restart reconstruction, absent binding, and no raw CLI/private core import.

## 4. Project the same result through CLI and process inventory

- [ ] 4.1 Update `env list` Rich provider cells and JSON/TOON contracts to show identifier/link/status/activity/freshness from snapshot facts with no second collection; cover compact/medium/wide and watch refresh transitions.
- [ ] 4.2 Add the optional external-task summary to existing checkout process blocks and render one compact task annotation in `ps` Details without a new process row/group; prove Rich/JSON/TOON parity and shared-issue non-attribution.
- [ ] 4.3 Update public field-schema, machine-output characterization, serialization/redaction, and CLI snapshot-count tests for the additive nested models.

## 5. Carry facts through HTTP and the existing card

- [ ] 5.1 Update `Snapshot` OpenAPI components and the generated TypeScript client using the existing generation path; extend Python schema/response contract tests for schema v5, enums, nullable status, and absence of private binding fields.
- [ ] 5.2 Add the task block to the existing `RuntimeCard` with verified new-tab link, status text, green/red/gray plus textual activity, hover/focus help, accessible names, safe unavailable reason, stopped-Odoo visibility, and no block when facts are absent.
- [ ] 5.3 Extend Vitest/Testing Library and CSS/package artifact tests for active/inactive/unknown transitions, API failure, keyboard focus, `noopener,noreferrer`, core-only/unbound cards, and unchanged runtime controls.

## 6. Packaging, repeatable verification, and documentation

- [ ] 6.1 Declare the single extension entry point and document same-interpreter co-installation, compatibility pins, core-only behavior, and the PATH-only non-discovery case without auto-install or companion RPC.
- [ ] 6.2 Add focused Python CI coverage for catalogue migration/adoption, provider isolation/activity/deduplication, monitor→checkout/process projections, HTTP/OpenAPI contracts, and the existing `offline-tests` job; add frontend coverage to the existing web test/build steps without a new CI framework.
- [ ] 6.3 Add `scripts/e2e_linked_task_monitor.py` as the repeatable local harness that accepts explicit baseline/candidate SHAs, isolated catalogue/profile/root/ports, deterministic provider fixtures, exact product/test revisions, failure-safe cleanup, and emits only sanitized assertions/provenance for Multica publication.
- [ ] 6.4 Add the focused E2E fixture/actions needed to capture actual baseline/candidate `env list` and `ps` text plus the existing monitor card in a browser, while keeping raw logs, screenshots, diagnostic JSON, and private hosts outside Git.
- [ ] 6.5 Update public SDK/CLI/monitor documentation and examples for the three task values, activity semantics, unavailable behavior, optional installation, and explicit non-goals.

## 7. Acceptance gates

- [ ] 7.1 Run focused migration/model/provider/monitor/CLI/HTTP/frontend tests and strict lint/type/build/OpenSpec checks within the local-test time limit; use CI on the exact published revision for any automated suite expected to exceed ten minutes.
- [ ] 7.2 Independently execute the planned isolated E2E walkthrough against the exact candidate revision, verify extension→snapshot→HTTP→card and CLI paths, capture required before/after evidence, test link/focus/tooltip/activity changes, and preserve cleanup plus revision assertions in the Multica Tester comment.
- [ ] 7.3 Confirm no credentials, private URL/host, local path, raw diagnostic output, evidence JSON, screenshots, or status history are committed or exposed by SDK/CLI/HTTP/UI contracts.
