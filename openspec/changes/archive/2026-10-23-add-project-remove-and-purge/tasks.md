## 1. Registration State and Migration

- [x] 1.1 Extend fresh catalogue metadata and the next single-head Alembic migration with one checked project registration-state column; backfill every existing row as registered, preserve rows/relations/indexes/existing timestamps, require forgotten rows before downgrade refusal, and cover fresh/prior-head metadata equivalence without adding a dedicated forgotten timestamp.
- [x] 1.2 Add narrow catalogue APIs for registered-only listing, explicit full-id lifecycle lookup, locked forget, and canonical reactivation; enforce legal state transitions and idempotent existing-timestamp behavior, and cover unknown, duplicate, repeated, and foreign-key-linked cases.
- [x] 1.3 Make initialization and project removal share the canonical project lifecycle lock; reactivate only after successful init postconditions, keep dry-run/failure inert, and cover concurrent init/remove serialization without resource adoption.

## 2. Typed Project Lifecycle and Resource Projection

- [x] 2.1 Add frozen JSON-safe selector, minimal resource-decision (stable identity/type, action, reason, sanitized target), result, and structured-error models; keep ownership/active-use/dependency/fingerprint evidence internal, and add the lazy-exported `ProjectResource` facade and `OdooClient.projects` wiring while preserving lightweight imports and the characterized public SDK boundary.
- [x] 2.2 Extend the existing local resource inventory with one deterministic full-project projection for runtimes/routes, environments, worktrees/venvs/config/logs, project config/secrets/primary venv/logs/temp, databases/filestores/backups, Compose container/volume, and source root; deduplicate shared identities and assign every item one fail-closed delete/retain reason.
- [x] 2.3 Implement exact path/full-id selection, bounded sanitized projection, expected confirmation token, and a stable resource-decision fingerprint; cover missing roots, unknown/abbreviated ids, shared resources, symlink escapes, caller/linked/other-tool-managed checkouts, unknown files, and secret redaction.
- [x] 2.4 Build one immutable project removal command by flattening captured child command steps in stable dependency order; revalidate registration and projection under the project lock and preserve each child operation's executor, action ledger, narrower locks, stale-plan checks, and postconditions.

## 3. Forget and Monitor Semantics

- [x] 3.1 Implement non-purge active-resource preflight and the terminal forgotten transition with no resource mutation; cover live/stale-unresolved project runtime, publication, non-removed/cleanup-in-progress environment refusals, inert retained resources, repeated forget, and row-for-row/file-for-file preservation.
- [x] 3.2 Filter ordinary catalogue/monitor global and project reads to registered rows, invalidate affected cache entries on state change, and cover forgotten, retained-resource, unknown-filter, reactivated, project-only, mixed-environment, and generated HTTP/OpenAPI/client compatibility paths.

## 4. Ownership-Safe Purge Execution

- [x] 4.1 Compose exact project/environment runtime stop and publication removal before destructive work; verify identity drift, stale PID, absent route, partial failure, and unrelated process/route isolation.
- [x] 4.2 Remove every selected environment in stable UUID order through its existing captured command, preserving caller-owned code, shared databases/Python environments, borrowed backups, cleanup-failed retry evidence, and per-environment postconditions.
- [x] 4.3 Enumerate and delete only latest-proven project databases, contained filestores, and exclusively owned unreferenced backups through existing guarded commands; cover missing/malformed/stale/foreign evidence, active bindings/sessions, shared inputs, already-absent retry, and unrelated project isolation.
- [x] 4.4 Add exact SDK-owned Compose cluster destroy support after dependent cleanup, requiring the active claim plus matching project/container/volume labels and no retained dependency; stop/remove only that container and volume, retain Docker images/external clusters, and cover drift, reuse, and postcondition failures.
- [x] 4.5 Delete only canonical contained SDK-owned global artifacts, primary venv, project-local config/secrets, and ownership-proven primary source; preserve symlink escapes, unknown files, caller/linked/other-tool-managed checkouts, remote repositories, and all evidence needed by later phases.
- [x] 4.6 Enforce exact confirmation before lock/mutation, keep dry-run inert, mark forgotten only after required actions and terminal retains, and return deterministic completed/retained/remaining details on success or failure; cover mismatch, `--yes` insufficiency, bounded truncation, interrupted retry by full id, and repeated successful purge.

## 5. CLI, Documentation, and Architecture Contracts

- [x] 5.1 Register canonical root `odcli remove` with `odcli rm` as an alias of the same Click command object, exclusive `--project`/`--project-id`, `--purge`, `--confirm-project`, `--dry-run`, and shared format options; keep interactive token collection in the CLI while delegating all domain work to the already captured SDK command.
- [x] 5.2 Add only canonical `remove` to `PUBLIC_LEAF_CASES`, bounded Rich/JSON/TOON output, confirmation/error mappings, help/synopsis, SDK-first/import/architecture inventories, and generated command matrix; cover `remove`/`rm` callback-option-output-completion parity with stable machine command id `remove`, selector usage errors, machine non-prompt behavior, exact token execution, plan parity, exit codes, and secret-free partial errors.
- [x] 5.3 Document forget versus purge, full-id recovery for a missing source, exact confirmation, ownership/retention rules, partial failure/retry, re-init, and explicit exclusions for remote/shared/external resources and Docker images.

## 6. Verification

- [x] 6.1 Add focused unit and integration matrices for migration, catalogue transitions, monitor filtering, projection/ownership, lock/drift behavior, action ordering, partial failure, idempotence, redaction, bounded output, and cross-project isolation; in `tests/integration/test_project_removal.py`, inject deterministic fail-after-action cases with `odoo_instance_sdk.internal.proc.RecordingExecutor(result_factory=...)` for captured process steps and pytest `monkeypatch` of the named guarded adapter callback for in-process action steps, assert the public structured result and retry ledger, add no production failure-injection flag or public seam, and use parametrization for repeated cases.
- [x] 6.2 Extend the repository's disposable real-Odoo/Compose CI tier with a public CLI/SDK init → forget → re-init → purge scenario and negative ownership/confirmation/drift cases; assert catalogue/monitor visibility, retained bytes/rows, exact deletion, unrelated project/shared resource preservation, cleanup audit, and canonical leaf/evidence metadata.
- [x] 6.3 Run focused tests plus repository format, lint, typing, offline/compatibility, migration/equivalence, architecture, generated-contract, and real-Odoo gates; record the owner-cancelled independent managed E2E disposition in `delivery-plan.md`, retaining historical results and exact-head CI evidence without claiming waived scenarios PASS or committing runtime evidence to Git.
