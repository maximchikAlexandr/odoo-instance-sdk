## Context

The update coordinator already owns an immutable `PreparedStep` snapshot and a per-run consumption ledger. Its plan currently captures install and maintenance, but `_UpdateSession.verify()` calls `_verify_installed_revision()`, whose `run_captured(..., step_id="update.verify.version")` reaches the active ledger without a matching captured step. The child has already installed and migrated the target at that point, so the fail-closed rejection correctly prevents commit and leaves the journal/snapshot at the incomplete boundary.

The read-only path is independent: `_build_check_command()` and `_build_already_current_command()` synthesize `snapshot_state="absent"` and `journal_state="absent"` without reading the paths used by `_UpdateSession`. Installed SHA equality therefore hides exactly the evidence retained by the failed coordinator.

The existing `UpdateResult`, `PreparedStep`, `RunContext`, journal fields, snapshot layout, and update resume behavior are sufficient. This change does not need a new recovery subsystem or a weaker execution boundary.

## Goals / Non-Goals

**Goals:**

- Freeze `update.verify.version` in the same public/private plan as install and maintenance.
- Execute the version subprocess exactly once through `RunContext.process_prepared()` after a valid maintenance result and before commit.
- Give mutating no-op selection and `--check` one canonical, side-effect-free view of journal/snapshot presence.
- Make incomplete state outrank SHA equality and expose one exact supported resume argv.
- Preserve journal/snapshot evidence until verification has succeeded.
- Cover the real old-revision-to-target packaging flow and an interrupted pre-verify state through the public CLI.

**Non-Goals:**

- Relaxing plan parity, ledger consumption, duplicate-step, or omitted-step enforcement.
- Adding a generic workflow engine, recovery registry, new journal format, new result type, dependency, background supervisor, or manual cleanup command.
- Automatically resuming, rolling back, deleting, or rewriting recovery state from `--check`.
- Changing source-repository policy, downgrade policy, migration ordering, lock ownership, or unrelated CLI output behavior.

## Decisions

### D1: Capture verification as a first-class parent process step

`_build_mutating_command()` will construct `PreparedStep(step_id="update.verify.version", argv=(absolute_target_odcli, "--version"), read_only=True)` beside the existing install and maintenance steps and place it immediately after the `update.verify` action begins and before that action completes. `_UpdateSession` will receive the exact object and consume it with `context.process_prepared()`.

The parent will parse the maintenance JSON, execute the captured version step, validate its exit/result against the target revision, then complete `update.verify`. Maintenance will apply migrations and produce the structured result but will not launch a second version process. Failure paths will skip the unconsumed verify step when maintenance cannot reach verification, so ledger completeness remains explicit.

Alternative considered: remove the parent verification call and trust the maintenance child's unplanned direct check. Rejected because dry-run would still omit a subprocess that can run, and execution would not be auditable through the coordinator ledger.

Alternative considered: allow nested `run_captured()` calls while an action is active. Rejected because it weakens the repository-wide immutable-plan contract to mask one missing step.

### D2: Reuse one canonical read-only recovery-state projection

Add a small internal inspector next to the existing journal/snapshot path helpers. It will read, without creating directories or locks, the journal payload, path presence, snapshot directory/metadata presence, immutable target ref, recorded PID, and whether that PID is alive. Both `update_command()` no-op selection and `_build_check_command()` will consume this projection instead of independently hardcoding absent state.

Presence is derived from the canonical paths even when the installed SHA already equals the target. A dead recorded PID is evidence of a stale interrupted update, not evidence that the journal disappeared. The inspector performs bounded local reads only and never owns cleanup.

Alternative considered: call `unfinished_update_journal()` only. Rejected because it cannot report snapshot presence and currently collapses unreadable/non-file state in ways unsuitable for the user-facing evidence fields.

### D3: Incomplete recovery state has priority over target comparison

`_build_check_command()` will still resolve mutable refs through its existing optional `git ls-remote` step. After resolution, it will evaluate the captured recovery projection before choosing `already_current` or the ordinary update-available result. A valid unfinished journal plus snapshot returns `update_incomplete` regardless of installed/target SHA equality.

The response will preserve the installed and journal target SHA fields, report both states as `present`, and expose exactly one structured resume command:

`(<absolute-odcli>, "update", "--ref", <journal-target-ref>, "--yes")`

`next_step` will instruct the caller to run `recovery_argv`. The read-only command will not execute that argv. This reuses the already-supported `odcli update` resume path and the existing `UpdateResult.recovery_argv` field.

Alternative considered: tell the user to delete the journal/snapshot. Rejected because that discards rollback evidence and bypasses the existing resume contract.

Alternative considered: invent a new `odcli update --resume` flag. Rejected because a pinned `--ref` already resumes the journal and a new surface is unnecessary.

### D4: Test ledger integrity and both public outcomes

Unit tests will pin public/private plan parity, argv, ordering, exactly-once consumption, skip behavior on early failure, successful commit cleanup, check precedence, dead-PID classification, and zero mutation during inspection. Existing generic ledger tests remain authoritative for arbitrary unplanned, duplicate, substituted, and omitted steps; the self-update suite will add regression assertions that the fix uses rather than bypasses them.

Packaging E2E will stop skipping `Unplanned execution step` as an environmental compatibility case. Its success scenario will install an older fixture revision, update to the current exact SHA, assert the planned/executed verification evidence, final SHA, and cleanup. A second isolated-home scenario will preserve a valid journal/snapshot at the pre-verify boundary with a dead maintenance PID, invoke public `odcli update --check --format json`, and assert `update_incomplete` plus the exact resume argv.

## Risks / Trade-offs

- **The target executable path can be replaced by install after planning** → Capture the stable uv-tool launcher path, validate target provenance after maintenance, and execute the captured path without rebuilding argv.
- **Early failure can leave a new planned verify step unconsumed** → Every pre-verify return explicitly skips it; successful runs consume it once, preserving completion-ledger guarantees.
- **PID reuse can make liveness ambiguous** → PID liveness is diagnostic only; journal/snapshot presence remains authoritative and `--check` never declares completion or removes evidence because a PID appears alive.
- **Old fixture revisions may lack the repaired contract** → Keep fixture selection explicit and assert the update transition; do not convert the target regression into a skip.
- **Read-only inspection can encounter malformed metadata** → Preserve on-disk evidence, return incomplete with a sanitized diagnostic, and never fabricate a resume argv from untrusted/non-immutable values.

## Migration Plan

1. Add the canonical read-only recovery projection and focused state/PID tests.
2. Capture `update.verify.version`, route it through the existing ledger, remove the duplicate direct version launch from maintenance, and update skip paths.
3. Make no-op and check outcome selection use the recovery projection before SHA equality.
4. Extend unit and packaging regressions, including public CLI interruption evidence.
5. Run strict OpenSpec validation, focused self-update tests, execution-ledger tests, packaging coverage where prerequisites are available, and repository quality gates.

Rollback is a normal code revert. No persisted schema or journal-version migration is introduced; retained version-1 journals remain inputs to the same resume path.

## Open Questions

None. Verification ownership, resume argv, state precedence, PID treatment, and test boundaries are fixed by this design.
