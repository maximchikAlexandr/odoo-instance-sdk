## Context

The current tree already provides lazy Click registration, one typed bounded output pipeline, `PUBLIC_LEAF_CASES`, schema-driven `--fields`, a canonical `EnvironmentMonitor`, local catalogue/configuration readers, and Rich table helpers. The observed gaps sit at the seams between those owners: Click may reject a value before a command callback knows the selected machine mode; `env show` snapshots all projects before selecting one; `commands/output.py` imports a database preparation context at module load; doctor renders internal representations; and selector/field completion is not attached to existing parameters.

The active `refactor-cli-output-boundary` change owns shared envelope, serializer, and canonical inventory architecture. This design assumes that owner remains authoritative and limits MYL-431 to additive policy and narrow extensions of those existing seams.

## Goals / Non-Goals

**Goals:**

- Deliver AC-1 through AC-9 with stable successful machine schemas, exit codes, redaction, native streams, confirmation, and immutable preview/execution behavior.
- Reuse Click parsing, the shared v1 failure document/emitter, typed result schemas, local catalogue/configuration access, and the canonical monitor.
- Prove behavior through isolated parameterized matrices, module-import assertions, installed-executable measurements, narrow terminal rendering, and completion side-effect guards.

**Non-Goals:**

- No second argv parser, envelope/serializer, field registry, environment registry, monitor, cache, daemon, TUI, renderer hierarchy, or completion framework.
- No SDK schema, catalogue schema, command-name, successful JSON/TOON document, dependency, permission, network, mutation, retry, or native-stream change.
- No arbitrary shared-CI millisecond gate and no real permission, database, Odoo, or remote-service mutation in regression tests.

## Decisions

### 1. Capture supported parse failures at the existing Click/output boundary

The common rich-click command/group boundary will recognize a valid explicit machine format only for a resolved canonical bounded leaf or supported dry-run, allow Click to remain the parser, and translate the resulting `UsageError`/parameter failure through the existing v1 `failure_document` and emitter. Format discovery must use Click's resolved context/parameter state, not a second scan-and-parse implementation. If command or format resolution has not reached the supported boundary, ordinary Click stderr remains authoritative.

Alternative considered: wrap every callback or introduce a standalone argv pre-parser. Rejected because callback decorators cannot catch all missing-argument/conversion failures, while a second parser would drift from lazy registration and Click semantics.

### 2. Classify only named expected I/O failures

`exec` source loading and self-update local state access will catch their concrete decode and filesystem exception families adjacent to the operation, sanitize them, and call the shared failure path. Unknown defects continue to surface for debugging; access guards and file modes are unchanged.

Alternative considered: a root `except Exception`. Rejected because it would hide programming errors and make redaction/error-code ownership ambiguous.

### 3. Resolve focused environment identity from the catalogue, then sample once

`env show` will use the existing catalogue/project provenance to resolve UUID/name/cwd and owning project before constructing the expensive plan. Only after successful resolution will it call `snapshot(project_id=...)` exactly once and reuse the existing pure snapshot selector for the final typed payload. This preserves stopped/unavailable semantics while excluding unrelated projects.

Alternative considered: cache or add a focused collector. Rejected because the catalogue already holds identity and project ownership, and the canonical monitor already accepts project scope.

### 4. Keep metadata imports lazy at their narrow consumers

`DatabasePreparationFailureContext` and any remaining operation-only types will move to `_failure_context` or the exact handler that performs the `isinstance` check. Fresh-process tests will assert the heavy module families are absent for both metadata options. Installed-executable timing will be recorded before and after outside the checkout as evidence, not used as a flaky pass/fail threshold.

Alternative considered: a lazy-import library or broad module split. Rejected because callback-local imports and current lazy command loaders already solve the dependency edge.

### 5. Render doctor from semantic rows, not internal representations

The existing grouped tables remain. Human rows will separate reason, target, and remediation; remediation argv will use shell-safe display quoting and visible mutation/dry-run language rather than Python repr flags. Full facts stay only in the existing machine payload. Rich's measurement/wrapping and current terminal sanitization remain responsible for narrow and redirected output.

Alternative considered: a new doctor view-model/renderer framework. Rejected because the typed `CheckResult` and `DoctorRemediation` already contain all required data.

### 6. Put help and `-h` policy on the common Click tree

The shared root context settings will declare `help_option_names=["-h", "--help"]`, allowing inherited Click behavior on lazy groups and leaves. Passthrough commands retain their literal `--` handling, so later `-h` values remain native argv. Command docstrings/help text will carry verified examples and constraints close to registration.

Alternative considered: adding `-h` to each command. Rejected because it duplicates policy and risks missing lazy commands.

### 7. Complete selectors with existing local readers

Small `shell_complete` callbacks will resolve project scope without starting runtime work, list environment identities from the catalogue or remote names from project configuration, prefix-filter, and return Click `CompletionItem`s. Completion catches only expected absent/unreadable local-source failures and returns no candidates. The callbacks are attached directly to root/argument/option declarations.

Alternative considered: a completion service or cached index. Rejected because Click callbacks and existing local data sources are sufficient.

### 8. Expose the existing schema-path function to validation and completion

The typed `msgspec` schema remains the sole field source. Validation will use deterministic close-match selection over its paths; completion will split only the final comma item, prefix-match dotted paths, and reattach earlier items. Candidate values never require command execution or contain result data.

Alternative considered: per-command allowed-field lists. Rejected because they would duplicate `_field_paths` and inevitably diverge.

### 9. Keep one evidence matrix and one release-contract note

Repeated errors, formats, help locations, and completion cases will extend existing parametrized CLI suites and `PUBLIC_LEAF_CASES`-based characterization. Focused unit tests will assert calls and side effects, not only wall time. `CHANGELOG.md` and executable help will document the intended usage-error boundary; generated reference material remains derived from help.

Alternative considered: new standalone inventories or end-to-end-only checks. Rejected because both would duplicate sources of truth and make failures less local.

## Ponytail full gate

No new subsystem is necessary. The apparent candidates—machine-usage parser, focused monitor, completion registry, field registry, doctor view-model, lazy-loading package, and timing framework—each have an existing alternative (Click context/error flow, project-scoped canonical monitor, direct `shell_complete`, `_field_paths`, semantic table rows, local imports, and fresh-process/import assertions). All are removed from the design; the retained work is narrow extension of existing owners plus tests and docs.

## Risks / Trade-offs

- [Click error timing differs across eager, group, and leaf parameters] → Characterize missing arguments, type/range/choice failures, aliases, invalid formats, and lazy groups before centralizing translation; fall back to native Click when the bounded mode is unresolved.
- [Catalogue pre-resolution and snapshot data can race] → Treat the catalogue selection as planning input, then require the selected identity in the one scoped snapshot and report a sanitized stale/not-found failure instead of recollecting.
- [Completion accidentally performs expensive resolution] → Test call counts and monkeypatch network, monitor, runtime, migration, chmod, and mutation seams to fail if invoked.
- [Help examples drift] → Exercise examples through parse/help/dry-run tests using the registered command tree.
- [Import assertions become platform-specific] → Gate named module families deterministically; keep timing as recorded evidence across first and repeated installed invocations, never as a universal CI threshold.
- [Field suggestions become noisy] → Bound and deterministically order close matches from the leaf schema only; invalid input still exits `2`.

## Migration Plan

This is an in-place CLI behavior and documentation change with no data migration. Ship the narrow implementation, expanded contract tests, installed-executable evidence, and changelog together. Rollback is the code commit: no persistent format, catalogue row, dependency, or migration state is created. Machine clients that currently scrape Rich usage stderr for supported bounded leaves must switch to the documented v1 `usage_error` stdout document; unsupported parse boundaries retain their current behavior.

## Open Questions

None. The supported machine boundary, selector sources, scope rules, help coverage, completion safety, field source, and verification policy are fixed by the issue and these delta specifications.
