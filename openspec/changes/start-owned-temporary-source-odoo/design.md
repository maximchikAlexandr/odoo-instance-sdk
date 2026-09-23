## Context

COPY checkout currently performs its source availability and target-name preflight through `EnvironmentResource._preflight_copy_checkout()`, then creates the environment and calls `_do_copy_restore()` for the existing HTTP backup and restore sequence. The first unavailable source request is translated into `InstanceConfigurationError`, so a stopped project Odoo blocks checkout before durable mutation.

The repository already owns the difficult lifecycle pieces in `resources/instance/auxiliary_restore.py`: immutable process capture, secret config creation and removal, same-project runtime ownership checks, Database Manager readiness, registration, bounded process-group termination, and skip semantics for unused optional steps. Project restore attaches those steps at the CLI boundary, but COPY checkout must work identically through the public SDK command, so the resource command itself must own the fallback lifecycle.

Repository execution rules require one immutable command ledger, all process launches through `internal/proc`, redacted public projections, and no process activity during planning or dry-run.

## Goals / Non-Goals

**Goals:**

- Make local COPY checkout succeed when the source Odoo is stopped but its project runtime can safely start an auxiliary Database Manager.
- Preserve one captured command across SDK and CLI use, including conditional lifecycle steps and guaranteed cleanup.
- Reuse the existing source endpoint when responsive and a proven same-project runtime when already running.
- Keep every existing backup, restore, journal, rollback, target-absence, provenance, and postcondition guarantee.

**Non-Goals:**

- Starting remote Odoo, adopting or terminating an unowned listener, or changing the local-only rule.
- Adding a direct PostgreSQL backup/restore path, changing public checkout signatures, or introducing new dependencies.
- Keeping the helper alive after checkout or changing shared-mode behavior.

## Decisions

### 1. Put the fallback inside the captured environment checkout command

`_command_from_snapshot()` will prepare a purpose-specific auxiliary Database Manager session for COPY plans and insert its start, readiness, and cleanup steps into `_checkout_steps()` around the database phase. Execution will own session activation and cleanup in a `try/finally` spanning preflight and `_do_checkout()`. Non-COPY commands will not carry these steps.

This keeps `checkout()`, `checkout_with_plan()`, `plan_checkout()`, direct `checkout_command().run()`, and the CLI on one behavior and one ledger. Attaching only in `commands/env/checkout.py`, as project restore currently does, was rejected because SDK callers would retain the bug and the CLI would become a second command-construction authority.

### 2. Probe first and activate fallback only for the typed unavailable error

COPY preflight will call the existing source `names()` request before durable checkout state. A successful response remains the authority even when the serving process is not registered by the SDK; checkout neither claims nor stops it. Only `DatabaseManagerUnavailableError` enables fallback. The session then verifies a free endpoint or a live recorded same-project owner, starts or reuses the runtime, waits for Database Manager readiness, and retries `names()` once. HTTP status/domain errors and target conflicts retain their existing meanings and do not start a helper.

Starting a helper unconditionally was rejected because it would conflict with a healthy unrecorded Odoo and add needless process churn. Retrying indefinitely was rejected because it obscures a deterministic configuration or readiness failure.

### 3. Generalize the existing auxiliary lifecycle without duplicating it

The restore-only helper will become a purpose-aware internal auxiliary Database Manager session. Its constructor will accept stable step identifiers/descriptions and the captured start inputs needed by the caller; stopped-project restore keeps its current step IDs and behavior, while COPY uses `checkout.source-odoo.auxiliary.start`, `.ready`, and `.cleanup`.

For COPY, the helper combines the resolved project executable prefix, working directory, environment, default run arguments, runtime ownership binding, and cluster dependency with the exact selected source `StartConfig`. This preserves explicit `--config` selection rather than silently reverting to the manifest's default source config. Secrets remain private inputs to `PreparedStep` and the public plan uses the existing redaction projection.

A separate COPY subprocess wrapper was rejected because it would duplicate port ownership, registration, readiness, cleanup, and secret-handling logic that is already covered by lifecycle tests.

### 4. Keep one session alive across preflight, backup, and restore

The helper, when started, remains active from the failed preflight probe through `_do_copy_restore()`. The backup, restore, and database list calls continue through the existing `DatabaseResource` HTTP paths against the same captured base URL. Finalization always invokes session cleanup; unused optional steps are marked skipped, reused runtimes remain untouched, and an owned helper cleanup failure is surfaced rather than reported as successful checkout.

Starting one process for preflight and another for backup was rejected because it creates a race at the endpoint and weakens ownership and cleanup reasoning.

### 5. Preserve failure ordering and durable evidence

Unavailable-source fallback, foreign-listener rejection, and helper readiness failure remain pre-mutation checks: no environment row or checkout artifact is created. Once preflight succeeds, failures use the existing COPY journal and `_cleanup_on_failure()` behavior. The outer helper cleanup is independent of database compensation so both are attempted; if both fail, the primary failure remains visible and the cleanup failure is attached as diagnostic context.

## Risks / Trade-offs

- [The existing helper is named and wired around restore] → Refactor only the internal lifecycle abstraction, retain restore compatibility tests and existing restore step IDs, and add COPY-specific ledger assertions.
- [An explicit checkout source config can differ from the manifest config] → Build the helper from project runtime identity plus the exact captured source `StartConfig`; stale revalidation continues comparing the selected config before any start.
- [Cleanup can fail after database work] → Always unregister/terminate by exact owned process identity, remove secret material in `finally`, retain normal COPY audit evidence, and surface cleanup failure.
- [A listener can appear after the failed probe] → Recheck endpoint ownership immediately before spawn and fail closed; never choose a replacement port because the captured base URL is authoritative.
- [Generalizing the session can regress stopped-project restore] → Preserve its constructor facade and behavior while extending focused lifecycle, restore adapter, and integration tests.

## Migration Plan

No data or configuration migration is required. Implement the internal session generalization and COPY command integration, add focused unit and integration regressions, then run strict OpenSpec validation and the repository's format, lint, type, focused test, and full test gates. Rollback is the single implementation commit(s); persisted catalog schema and public API remain unchanged.

## Open Questions

None. The trigger error, retry count, ownership boundary, step placement, cleanup policy, and source-config/runtime composition are fixed by this design.
