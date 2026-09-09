## 1. Root-Cause Contract Tests

- [x] 1.1 Add owned-runtime checkout regressions for successful `<python> <odoo-bin> --help`, missing import, non-zero exit, timeout termination, redacted diagnostics, rollback and retryable retained cleanup; prove shared-runtime checkout adds no probe.
- [x] 1.2 Add COPY removal regressions for stopped direct drop, unrelated HTTP-port occupancy, a proven live environment runtime, unsafe database sessions, ownership mismatch, idempotent absent artifacts, and retry from `cleanup_failed`.
- [x] 1.3 Add one parameterized alias suite covering identical Click object/callback, help de-duplication, existing machine command IDs, Rich/JSON/TOON parity, destructive confirmation and dry-run for all eleven pairs through existing `PUBLIC_LEAF_CASES`.
- [x] 1.4 Add shared failure-envelope tests for successful dry-run, precondition-failed dry-run and normal failure across representative sibling `fail()` callers, asserting unchanged errors/exits and no child execution.
- [x] 1.5 Add process-memory tests for Darwin root/child `ri_phys_footprint`, non-Darwin RSS, root failure/PID reuse, omitted unreadable children, schema-v4 naming and exact Rich/machine equality.
- [x] 1.6 Add replacement tests for cwd/root-selector parity, active-session refusal without termination, `--replace`/`--target` rejection, compatible `--reset-admin-password` success and compensated failure, exact dry-run actions, unchanged identity, matching database/filestore provenance, success cleanup, reverse compensation, incomplete `cleanup_failed`, retry and concurrent evidence changes with recording/fake executors.
- [x] 1.7 Add stop tests for cwd/root-selector parity, runtime-row owner/PID/create-time plus environment `runtime_json`/config sourcing, matching live executable/argv/cwd/config and POSIX `pgid == pid`, Windows pre-termination checks, no runtime row, vanished process, stale/reused PID, inaccessible or mismatched evidence, unrelated port occupant, bounded escalation, exit verification and selective row clearing.
- [x] 1.8 Add Jira allocation tests for grammar, empty/unsuffixed/mixed/sparse local-catalogue-origin evidence, removed-row reservation, malformed-ref exclusion, selected-base creation, immutable dry-run evidence and late collision without live Jira or fetch.
- [x] 1.9 Add drift tests for Python, dependency inputs, managed Odoo values, add-on paths, Git provenance/context, semantic formatting equality, legacy unknown, failed checkout/sync preservation, Rich/JSON/TOON parity and zero diagnosis mutation.

## 2. Applied Settings Storage and Diagnosis

- [x] 2.1 Add the next sequential additive catalogue migration for versioned secret-free `applied_settings_json`, preserving all rows/foreign keys/indexes and representing every legacy component as unknown without inferred backfill.
- [x] 2.2 Define one private frozen applied-settings codec for normalized Python, dependency, managed-config, add-on and Git provenance fields; apply existing secret projection before semantic fingerprints and reject malformed/unknown versions safely.
- [x] 2.3 Record the complete snapshot atomically only after successful checkout readiness; after successful `env sync`, update only Python/dependency fields and preserve other applied values; keep failure/interruption/dry-run write-free.
- [x] 2.4 Build one private frozen doctor drift projection returning `in_sync|drifted|unknown` plus bounded sanitized reason/remediation per component, treating ordinary worktree changes as Git context and recorded database/port as bindings.
- [x] 2.5 Extend `odcli doctor` Rich and machine projections with identical typed drift results and no repairs, state changes, fetches or catalogue writes; keep the projection reusable by a future `env show` without adding that command.

## 3. Shared Output and Memory Boundaries

- [x] 3.1 Make resolved `dry_run` mandatory in the post-MYL-121 shared failure boundary, update every caller found by the repository audit, and preserve envelope schemas, classifications and exit codes.
- [x] 3.2 Add the private Darwin `proc_pid_rusage(RUSAGE_INFO_V4)` adapter with validated structure/return handling and select physical footprint or RSS once inside `internal/process_metrics` without a dependency or fallback metric.
- [x] 3.3 Advance runtime monitor output to schema v4 with nullable `memory_bytes`, route Rich `Memory` through the same field, update OpenAPI/dashboard generated types and compatibility tests, and eliminate any physical-footprint-as-RSS or duplicate-memory path.

## 4. Compatible Aliases and Jira Checkout

- [x] 4.1 Register `env create|ls|rm`, `backup ls|inspect|rm`, `db ls|rm`, `postgres ps`, `resource ls`, and `module ls` on existing Click command objects while preserving old spellings and semantic command IDs.
- [x] 4.2 Add a CLI-private Jira-key parser/resolution value and recorded Git probes for all local heads and matching current `origin` heads; combine them with repository-filtered catalogue history including removed rows and compute exact/maximum-plus-one only.
- [x] 4.3 Feed the captured new branch into the existing exact-branch checkout command, create it only from effective `--base`, revalidate absence in all sources before mutation, and fail stale without retry/reallocation on collision.
- [x] 4.4 Change both CLI spellings to `JIRA_TICKET`, remove CLI `--name`, retain generated `<project>:<resolved-branch>` naming and explicit `--create-venv`, and preserve the public SDK exact-branch/name API.
- [x] 4.5 Update root/group help, README and shell-completion characterization so aliases are one operation and Jira branch/naming rules are explicit without adding a Jira dependency/configuration.

## 5. Owned Checkout and COPY Cleanup

- [x] 5.1 Insert one immutable 30-second owned-runtime entry-point preflight after runtime/config preparation and before `ready`, execute it only through `internal/proc`, and route failure/timeout through existing rollback and sanitized retained-artifact reporting.
- [x] 5.2 Replace COPY removal's HTTP/port dependency with the existing guarded direct PostgreSQL drop plus exact environment ownership evidence and execution-time revalidation; preserve the shared project cluster and never infer or terminate a port occupant.
- [x] 5.3 Permit `cleanup_failed` removal retries, make each owned cleanup step absence-aware/idempotent, and keep active-runtime/session/ownership failures closed and actionable.

## 6. COPY Environment Replacement

- [x] 6.1 Extend existing preparation inputs with a selected-environment replacement variant that reuses merged root context, backup validation, lock ordering, cluster ownership, generated-config/catalogue equality, stopped-runtime and exact COPY database/filestore checks; fail before mutation on active target sessions without terminating them, and add no public SDK or force method.
- [x] 6.2 Build one inspectable plan that moves the proven prior database/filestore to collision-free rollback names, restores through the existing pipeline to the unchanged target, and records exact sanitized process/action steps.
- [x] 6.3 Execute with mutation-boundary revalidation, database/filestore/postcondition checks, the existing requested admin-password reset and one final catalogue provenance update while preserving every non-backup environment identity field.
- [x] 6.4 Extend existing failure-context/compensation machinery to cover restore and admin-password-reset failure before final provenance, remove only proven partial new artifacts, restore the prior pair in reverse order, retain prior provenance after successful compensation, and record retryable `cleanup_failed` evidence otherwise without advertising the new backup.
- [x] 6.5 Wire `db restore BACKUP_UUID --replace` through cwd/root `--env`, confirmation, machine `--yes`, dry-run, progress, redaction, output and exit paths; reject `--target` and project/shared/removed/ambiguous contexts before mutation while preserving existing `--reset-admin-password` compatibility.

## 7. Selected Environment Stop

- [x] 7.1 Add a private immutable live-process identity projection that re-reads runtime owner/PID/create time, environment `runtime_json` (`odoo_bin`, `runtime_cwd`) and generated-config path, then captures live create time, executable, argv, cwd, config argument and POSIX process group without changing runtime storage.
- [x] 7.2 Extend the existing process boundary to adopt and terminate only after execution-time equality plus POSIX `pgid == pid`, apply the same available identity checks before existing Windows tree termination, use existing bounded escalation, verify exit, and clear only the matching runtime row.
- [x] 7.3 Wire top-level `stop` through cwd/root `--env` and bounded output; return idempotent success for confirmed absence and fail without signaling on stale, reused, inaccessible or mismatched identity.

## 8. Cross-Cutting Verification and Delivery

- [ ] 8.1 Update README/help/release notes for all nine items; keep `.localhost` browser-session isolation and future `env show` explicitly outside this delivery.
- [ ] 8.2 Run strict OpenSpec validation, Ruff format/check, strict mypy, focused/full pytest, architecture inventory, output parity/redaction/security, catalogue migration, documentation, OpenAPI/dashboard codegen/frontend and package build/install gates; record every command and exit code.
- [ ] 8.3 Verify the implementation diff adds no Jira/network client, branch counter, generic diff engine, watcher, parallel runner/renderer/coordinator/inventory, dependency, production launch outside `internal/proc`, shared/source overwrite path, secret-bearing fingerprint, or port-authorized termination.
- [ ] 8.4 Commit completed implementation blocks with Conventional Commits, publish each ordinary commit immediately, and return exact local/remote SHA equality and requirement/scenario-to-test evidence without creating or merging a pull request.
