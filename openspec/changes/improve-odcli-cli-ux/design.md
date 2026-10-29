## Context

The current tree already provides lazy Click registration, one typed bounded output pipeline, an expanded test-owned `PUBLIC_LEAF_CASES`, schema-driven `--fields`, a canonical `EnvironmentMonitor` split into collection/planning/snapshot mixins, local catalogue readers, and integrated opt-in `init --venv` plus a separate `prepare-venv` recovery command. The retained gaps sit at the seams between those owners: Click may reject a value before a command callback knows the selected machine mode; `env show` snapshots all projects before selecting one; `commands/output.py` imports a database preparation context at module load; expected decode/access failures can escape their owning handlers; and help policy/examples are incomplete.

The active `refactor-cli-output-boundary` change owns shared envelope, serializer, and canonical inventory architecture. This design assumes that owner remains authoritative and limits MYL-431 to additive policy and narrow extensions of those existing seams.

## Goals / Non-Goals

**Goals:**

- Deliver retained issue items 1, 2, 3, 4, 6, and 7 with stable successful machine schemas, exit codes, redaction, native streams, confirmation, and immutable preview/execution behavior.
- Reuse Click parsing, the shared v1 failure document/emitter, typed result schemas, local catalogue access, and the canonical monitor.
- Prove behavior through isolated parameterized matrices, call-count assertions, fresh-process module guards, installed-executable measurements, and help/parse fixtures.

**Non-Goals:**

- No second argv parser, envelope/serializer, field registry, environment registry, monitor, cache, daemon, TUI, loader framework, or completion framework.
- No SDK schema, catalogue schema, command-name, successful JSON/TOON document, dependency, permission, network, mutation, retry, or native-stream change.
- No doctor presentation work, environment/remote value completion, `--fields` suggestions, or `--fields` value completion. Unknown fields remain ordinary typed validation errors.
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

### 5. Put help and `-h` policy on the common Click tree

The shared root context settings will declare `help_option_names=["-h", "--help"]`, allowing inherited Click behavior on lazy groups and leaves. Passthrough commands retain their literal `--` handling, so later `-h` values remain native argv. Command docstrings/help text will carry verified examples and constraints close to registration. The `init` examples will use the current optional `--venv` integration and identify `prepare-venv` as recovery/refresh; `env create` examples will keep its separate optional `--create-venv` contract.

Alternative considered: adding `-h` to each command. Rejected because it duplicates policy and risks missing lazy commands.

### 6. Keep one evidence matrix and one release-contract note

Repeated errors, formats, and help locations will extend existing parametrized CLI suites and `PUBLIC_LEAF_CASES`-based characterization without freezing a second leaf list in this change. Focused unit tests will assert calls and side effects, not only wall time. `CHANGELOG.md` and executable help will document the intended usage-error boundary; generated reference material remains derived from help.

Alternative considered: new standalone inventories or end-to-end-only checks. Rejected because both would duplicate sources of truth and make failures less local.

## Ponytail full gate

No new subsystem is necessary. The apparent candidates—machine-usage parser, focused monitor, lazy-loading package, venv-help abstraction, and timing framework—each have an existing alternative (Click context/error flow, project-scoped canonical monitor, callback-local imports, the existing `init`/`prepare-venv` registrations, and fresh-process/import assertions). All are removed from the design; the retained work is a narrow extension of existing owners plus tests and docs. Completion registries, field-discovery work, and a doctor view-model are outside the reduced scope rather than latent implementation work.

## Risks / Trade-offs

- [Click error timing differs across eager, group, and leaf parameters] → Characterize missing arguments, type/range/choice failures, aliases, invalid formats, and lazy groups before centralizing translation; fall back to native Click when the bounded mode is unresolved.
- [Catalogue pre-resolution and snapshot data can race] → Treat the catalogue selection as planning input, then require the selected identity in the one scoped snapshot and report a sanitized stale/not-found failure instead of recollecting.
- [Help examples drift] → Exercise examples through parse/help/dry-run tests using the registered command tree.
- [Import assertions become platform-specific] → Gate named module families deterministically; keep timing as recorded evidence across first and repeated installed invocations, never as a universal CI threshold.

## Migration Plan

This is an in-place CLI behavior and documentation change with no data migration. Ship the narrow implementation, expanded contract tests, installed-executable evidence, and changelog together. Rollback is the code commit: no persistent format, catalogue row, dependency, or migration state is created. Machine clients that currently scrape Rich usage stderr for supported bounded leaves must switch to the documented v1 `usage_error` stdout document; unsupported parse boundaries retain their current behavior.

## Open Questions

None. The supported machine boundary, focused selector source, scope rules, help coverage, exclusions, and verification policy are fixed by the issue and these delta specifications.
