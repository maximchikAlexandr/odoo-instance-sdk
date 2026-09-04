## 1. Help and packaging boundary

- [ ] 1.1 Add a bounded compatible `rich-click` core dependency, refresh the lockfile, and update packaging assertions to require exactly the revised core dependency set.
- [ ] 1.2 Replace `_RichHelpGroup` with `rich-click` command/group integration at the existing Click entry point without routing command results through it.
- [ ] 1.3 Add missing one-line help text and at most four stable task-oriented panels across the root and nested command tree.
- [ ] 1.4 Add characterization tests for root help, a nested group, a typed leaf, required/default/type metadata, validation errors, narrow width, redirected/color-disabled output, shell completion, and lightweight startup imports.

## 2. Owner-neutral project command context

- [ ] 2.1 Extend `ResolvedContext` with one typed owner-neutral runtime view exposing owner identity, root, start config, command prefix, database, HTTP binding, and base provenance while retaining `require_environment()` for lifecycle-only commands.
- [ ] 2.2 Refactor VS Code profile construction to consume the owner-neutral runtime view and preserve environment behavior, default print, atomic `--write`, `--dry-run`, output formats, and existing-JSONC refusal.
- [ ] 2.3 Refactor `module update` to use the resolved project database/runtime without its unused environment ID and preserve `--env`, `--yes`, dry-run, and output contracts.
- [ ] 2.4 Refactor top-level and compatibility Odoo test paths to use the owner-neutral runtime view and one result/runner implementation without environment fabrication.
- [ ] 2.5 Implement project-context changed-test base precedence as explicit `--base`, then configured non-`HEAD` effective checkout base, otherwise actionable failure; add local-only Git provenance tests.
- [ ] 2.6 Add CLI regressions for VS Code print/write/dry-run, module update, direct test, module-test alias, Rich/JSON/TOON parity, and explicit-environment precedence in both owner contexts.

## 3. Project-local environment variables

- [ ] 3.1 Implement a UTF-8 project-bound `.odcli/.env` parser supporting quoted/unquoted assignments while rejecting interpolation, command substitution, malformed input, NULs, and paths outside the resolved project.
- [ ] 3.2 Enforce process-environment precedence, owner-only POSIX permissions, immutable scoped mappings, and sanitized missing/unreadable/malformed-file behavior before project runtime mutation.
- [ ] 3.3 Connect the scoped mapping to restore secret lookup so `ODCLI_TEST_MASTER_PASSWORD` works without shell sourcing while the subprocess boundary removes it from unrelated children.
- [ ] 3.4 Ensure init ignore rules and documentation cover `.odcli/.env`, owner-only permissions, supported grammar, missing-file behavior, and non-exported lifetime.
- [ ] 3.5 Add security tests covering project-boundary discovery, process overrides, permissions, malformed secret-bearing lines, child allowlisting, plans/fingerprints/errors/logs, and Rich/JSON/TOON redaction.

## 4. Shared plan presentation and execution observation

- [ ] 4.1 Add typed semantic plan observations for goal, targets, mutations, precondition status, and warnings without changing the private executable snapshot or public machine plan fields.
- [ ] 4.2 Replace the implementation-heavy default Rich plan rendering with one shared semantic projection and add JSON/TOON equality tests proving full snapshots remain unchanged.
- [ ] 4.3 Move HTTP-port inspection into foreground run planning and execution revalidation so dry-run emits a failed precondition while normal execution still performs no spawn or use update on conflict.
- [ ] 4.4 Extend the existing `Command`/`RunContext` and process executor with an optional typed step observer for start/completion/failure and opt-in sanitized stdout/stderr chunks, preserving final capture and exit behavior.
- [ ] 4.5 Wire interactive restore to Rich logical-step progress and `--show-command-output`; suppress observer rendering/streams in JSON and TOON and associate every visible chunk with its plan step.
- [ ] 4.6 Add tests for ordered restore events, success/failure state, TTY and non-TTY behavior, split-chunk secret redaction, machine stdout isolation, unchanged subprocess results, and existing exit codes.

## 5. Safe project-cluster database drop

- [ ] 5.1 Add exact database-name and PostgreSQL system/template guards plus cluster-bound existence, configured-default, and active-session precondition queries through the existing PostgreSQL transport.
- [ ] 5.2 Add one inspectable database-drop command whose execution revalidates safety state, optionally terminates only target sessions, quotes the exact identifier, drops through a maintenance database, and verifies absence.
- [ ] 5.3 Reconcile current database mappings and record exactly one sanitized `dropped` catalogue event only after the absence postcondition succeeds.
- [ ] 5.4 Register `odcli db drop DATABASE` with `--force-default`, `--force-connections`, `--yes`, and `--dry-run`, using shared context, confirmation ordering, Rich/JSON/TOON output, and credential-free projections.
- [ ] 5.5 Add unit/contract tests for system databases, missing targets, default protection, active sessions, stale precondition revalidation, quoting, dry-run, noninteractive confirmation, catalogue rollback, redaction, and no caller-controlled connection selector.
- [ ] 5.6 Run end-to-end drop success and forced-session cases only on a newly created disposable PostgreSQL cluster/database, verify audit reconciliation, and destroy that disposable environment without touching any user instance.

## 6. Project runtime persistence and monitoring

- [ ] 6.1 Add a transactional catalogue migration that registers canonical initialized projects, backfills them from environment rows, migrates environment runtime records to an exclusive `environment|project` owner table, and rejects invalid dual/no-owner records.
- [ ] 6.2 Upsert project registration during init/resolution without environment lifecycle events, and add pre-migration/backfill/idempotency/rollback fixture tests.
- [ ] 6.3 Attach one private runtime binding from both `InstanceFactory.from_environment()` and `from_project()` and make foreground spawn/finally persistence owner-neutral while leaving manual/shell/background operations unpersisted.
- [ ] 6.4 Refactor monitor planning to start from registered projects, join optional environments and both runtime owners, and reuse existing process/readiness/Git/storage/PostgreSQL collectors and stale-PID checks.
- [ ] 6.5 Extend typed snapshot models additively with nullable project runtime data, preserving environment fields, filtering, ordering, cache semantics, redaction, and JSON/TOON serialization.
- [ ] 6.6 Add unit and integration coverage for project-only catalogue state, live/stale project runtimes, mixed ownership, worker/process/CPU/RAM/readiness/URL/database/cluster metrics, and no synthetic environments.

## 7. HTTP and dashboard project-runtime support

- [ ] 7.1 Publish the additive project-runtime snapshot fields through the existing msgspec-to-OpenAPI bridge and verify representative runtime JSON against the generated schema.
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
