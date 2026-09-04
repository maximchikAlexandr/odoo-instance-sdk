## 1. Help and packaging boundary

- [x] 1.1 Add exactly `rich-click>=1.9,<2` alongside all nine existing dependencies including `expression>=5,<6`, refresh the lockfile, and update wheel assertions to require exactly ten core dependencies with those bounds and the sole existing `dashboard` extra.
- [x] 1.2 Replace `_RichHelpGroup` with `rich-click` command/group integration at the existing Click entry point without routing command results through it.
- [x] 1.3 Add missing one-line help text and at most four stable task-oriented panels across the root and nested command tree.
- [x] 1.4 Add characterization tests for root help, a nested group, a typed leaf, required/default/type metadata, validation errors, narrow width, redirected/color-disabled output, shell completion, and lightweight startup imports.

## 2. Owner-neutral project command context

- [x] 2.1 Extend `ResolvedContext` with one typed owner-neutral runtime view exposing owner identity, root, start config, command prefix, database, HTTP binding, and base provenance while retaining `require_environment()` for lifecycle-only commands.
- [x] 2.2 Refactor VS Code profile construction to consume the owner-neutral runtime view and preserve environment behavior, default print, atomic `--write`, `--dry-run`, output formats, and existing-JSONC refusal.
- [x] 2.3 Refactor `module update` to use the resolved project database/runtime without its unused environment ID and preserve `--env`, `--yes`, dry-run, and output contracts.
- [x] 2.4 Refactor top-level and compatibility Odoo test paths to use the owner-neutral runtime view and one result/runner implementation without environment fabrication.
- [ ] 2.5 Implement project-context changed-test base precedence as explicit `--base`, then configured non-`HEAD` effective checkout base, otherwise actionable failure; add local-only Git provenance tests.
- [ ] 2.6 Add CLI regressions for VS Code print/write/dry-run, module update, direct test, module-test alias, explicit-environment precedence, and exact owner/common-context result fields for executed, changed no-op, and dry-run states with Rich/JSON/TOON parity in both owner contexts.

## 3. Project-local environment variables

- [ ] 3.1 Implement the exact UTF-8/BOM line grammar, key regex, whitespace/comment rules, unquoted/literal-single-quote/bounded-double-quote escape rules, and empty values; reject duplicates, multiline/trailing syntax, interpolation/substitution, unknown escapes, NULs, invalid UTF-8, and paths outside the resolved project.
- [ ] 3.2 Enforce per-key process precedence, owner-only POSIX permissions, immutable scoped mappings, no `os.environ` mutation, and path/line-only missing/unreadable/malformed diagnostics before mutation.
- [ ] 3.3 Propagate ordinary file values only to Odoo runtime children; preserve purpose-built Git/PostgreSQL/Docker/editor/browser/package/other child environments; consume and strip `ODCLI_TEST_MASTER_PASSWORD` before every spawn.
- [ ] 3.4 Ensure init ignore rules and documentation cover `.odcli/.env`, owner-only permissions, exact grammar, process precedence, child propagation/deny matrix, secret classification, missing-file behavior, and non-exported lifetime.
- [ ] 3.5 Add acceptance/security tests for every grammar form/rejection, project-boundary discovery, per-key process overrides, permissions, each Odoo/denied child class, master-password consumption, and plans/fingerprints/errors/logs/Rich/JSON/TOON redaction.

## 4. Shared plan presentation and execution observation

- [ ] 4.1 Add typed semantic plan observations for goal, targets, mutations, precondition status, and warnings without changing the private executable snapshot or public machine plan fields.
- [ ] 4.2 Replace the implementation-heavy default Rich plan rendering with one shared semantic projection and add JSON/TOON equality tests proving full snapshots remain unchanged.
- [ ] 4.3 Move HTTP-port inspection into foreground run planning and execution revalidation so dry-run emits a failed precondition while normal execution still performs no spawn or use update on conflict.
- [ ] 4.4 Extend the existing `Command`/`RunContext` and process executor with an optional typed step observer for start/completion/failure and opt-in sanitized stdout/stderr chunks, preserving final capture and exit behavior.
- [ ] 4.5 Wire restore to TTY Rich `Live` progress and non-TTY Rich step-prefixed sanitized lines; make `--show-command-output` Rich-only, reject it with JSON/TOON/`--json` as exit-2 usage before SDK work, and associate every visible chunk with its plan step.
- [ ] 4.6 Add tests for ordered restore events, TTY `Live`, non-TTY line output without cursor control, Rich-only stream chunks, machine-mode stream-flag usage errors and single-document output without the flag, split-chunk redaction, unchanged subprocess results, and existing execution exit codes.

## 5. Safe project-cluster database drop

- [ ] 5.1 Add a CLI-private/internal cluster-bound operation through the existing PostgreSQL transport with exact-name validation, denylist `postgres|template0|template1`, and read-only existence/`pg_database.datistemplate`/configured-default/active-session preconditions; do not alter public `DatabaseResource.drop/drop_command`.
- [ ] 5.2 Add its inspectable command whose execution revalidates denylist, existence, `datistemplate`, default, and sessions immediately before mutation, fails closed on unreadable/changed state, optionally terminates only target sessions, quotes the exact identifier, drops through maintenance database `postgres`, and verifies absence.
- [ ] 5.3 After the absence postcondition, call existing `record_database_dropped` exactly once and preserve its latest-event idempotency/no-op rule; reconcile current mappings without writing on failure/refusal/dry-run.
- [ ] 5.4 Register `odcli db drop DATABASE` with `--force-default`, `--force-connections`, `--yes`, and `--dry-run`; keep Rich prompting, make machine modes never prompt and return one exit-1 `confirmation_required` envelope without `--yes` before SDK work, execute machine mode with `--yes`, and exempt every dry-run from consent.
- [ ] 5.5 Add unit/contract tests for the exact denylist, custom `datistemplate=true` targets, maintenance DB, missing/default/session preconditions, unreadable and stale execution revalidation, quoting, Rich and machine confirmation parity, machine `--yes`, consent-free dry-run, canonical dropped-event insert/no-op idempotency, catalogue rollback, redaction, no caller-controlled selector, unchanged HTTP/master-password SDK drop semantics, and unchanged public-method set.
- [ ] 5.6 Add `db drop` exactly once to canonical `PUBLIC_LEAF_CASES` and verify the single-source leaf-inventory consumer, all bounded formats/classification, and side-effect-free dry-run.
- [ ] 5.7 Run end-to-end drop success and forced-session cases only on a newly created disposable PostgreSQL cluster/database, verify audit reconciliation, and destroy that disposable environment without touching any user instance.

## 6. Project runtime persistence and monitoring

- [ ] 6.1 Add a transactional catalogue migration that registers canonical initialized projects, backfills them from environment rows, migrates environment runtime records to an exclusive `environment|project` owner table, and rejects invalid dual/no-owner records.
- [ ] 6.2 Upsert registration only after successful non-preview init and before permitted normal foreground runtime persistence; keep resolution/read-only/monitor/dry-run inert and test failed init, legacy project-only normal-run discovery, preview non-discovery, idempotency, backfill, and rollback.
- [ ] 6.3 Attach one private runtime binding from both `InstanceFactory.from_environment()` and `from_project()` and make foreground spawn/finally persistence owner-neutral while leaving manual/shell/background operations unpersisted.
- [ ] 6.4 Refactor monitor planning to start from registered projects, join optional environments and both runtime owners, and reuse existing process/readiness/Git/storage/PostgreSQL collectors and stale-PID checks.
- [ ] 6.5 Bump snapshot schema to 4 and add exactly `ProjectSummary.runtime: RuntimeMetrics | None`, implementing distinct absent-null and present-stopped/null semantics while preserving v3 environment fields and the existing `PostgresServerInfo`, `ClusterSnapshot.server`, `ClusterSnapshot.server_unavailability_reason`, `ClusterUnavailabilityReason`, and `ServerUnavailabilityReason` diagnostics contracts; preserve filtering, ordering, cache/invalidation, partial results, redaction, and JSON/TOON behavior and add v3-to-v4 migration assertions.
- [ ] 6.6 Add unit and integration coverage for project-only catalogue state, live/stale project runtimes, mixed ownership, worker/process/CPU/RAM/readiness/URL/database/cluster metrics, preserved PostgreSQL server diagnostics and every cluster/server unavailability reason, and no synthetic environments.

## 7. HTTP and dashboard project-runtime support

- [ ] 7.1 Publish schema-version-4 `ProjectSummary.runtime` through the existing msgspec-to-OpenAPI bridge and verify null/stopped/live/mixed/filtered/redacted JSON, preserved `PostgresServerInfo` and both typed unavailability fields/reason sets, plus v3 migration fixtures against the generated schema.
- [ ] 7.2 Regenerate the TypeScript client from the canonical OpenAPI schema and update the project card/empty-state logic to render live project-owned runtime metrics when no environments exist.
- [ ] 7.3 Add FastAPI serialization and React tests for project-only, mixed, stopped/stale, filtered, and redacted snapshots without adding an endpoint or handwritten DTO.

## 8. Eval diagnostics and captured output

- [ ] 8.1 Extend the existing nonce-framed eval wrapper payload with separate typed result, captured user stdout, structured user-code error, and truncation state while leaving startup output outside the frame.
- [ ] 8.2 Implement bounded exception/source-context retention that distinguishes startup failure from user-code failure and preferentially retains exception type/message after long startup logs.
- [ ] 8.3 Update Rich and machine projections to display/store user output separately, keep print-only results null, preserve non-zero failure exits, and redact every result/output/diagnostic field.
- [ ] 8.4 Add regressions for scalar success, print-only exec, print-then-exception, multiline Unicode, startup failure, exception after a long startup log, truncation signaling, JSON/TOON parsing, and secret redaction.

## 9. Acceptance and delivery

- [ ] 9.1 Run strict OpenSpec validation, Ruff, strict mypy, the full Python test suite, frontend tests/build, architecture inventory gates, and package build/install smoke tests.
- [ ] 9.2 Exercise restore progress, project-context commands, monitoring, and eval against disposable fixtures or one-time test environments only; record commands and outcomes without exposing secrets.
- [ ] 9.3 Update user documentation and release notes for new flags, project-context behavior, dotenv policy, runtime monitoring, eval output, and safe database deletion.
- [ ] 9.4 Open a feature-branch PR referencing GitHub #53, map each backlog acceptance criterion to tests/evidence, and report local verification while GitHub CI runs; do not modify the user's working instance or database.
