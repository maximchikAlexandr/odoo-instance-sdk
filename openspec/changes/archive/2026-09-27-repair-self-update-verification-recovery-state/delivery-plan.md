## Delivery contract

- Planning issue: `MYL-304`
- OpenSpec change: `repair-self-update-verification-recovery-state`
- Approved base: `origin/main` at `f7c3f7c9093529d6744c30745e220efb9aea8f80`
- Delivery mode: `single_wp_no_dag`
- Estimate source: the current planning issue custom properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` are the sole persisted numeric totals.
- Estimate basis: remaining active developer effort for one experienced developer familiar with Python, Click, pytest, and this repository, without AI acceleration. It includes investigation, implementation, required tests, review fixes, and attended verification; unattended CI/provider queues and human approval time are excluded.
- Confidence: medium. The affected coordinator, ledger boundary, canonical paths, result model, and close unit-test analogues were inspected at the approved base. The main uncertainty is the real uv-tool old-revision packaging transition and platform-specific PID/process behavior.
- Calibration: uncalibrated; no comparable completed-task timing record was supplied. Tests were not run while estimating.

## WP-MYL-304-01 — Repair planned update verification and honest recovery inspection

**Task coverage:** `1.1`, `1.2`, `1.3`, `2.1`, `2.2`, `2.3`, `3.1`, `3.2`, `4.1`, `4.2`, `4.3`. This WP owns every OpenSpec task exactly once.

**Deliverable:** A reviewed implementation in which the target `odcli --version` verification is frozen and consumed once through the existing ledger, `update --check` reports preserved incomplete state before considering SHA equality, and unit/packaging regressions prove successful cleanup and interrupted-state recovery guidance.

**Owned responsibility scope:**

- Self-update planning, execution, recovery inspection, and result construction in `src/odoo_instance_sdk/internal/self_update.py` and `src/odoo_instance_sdk/internal/self_update_commands.py`.
- Directly related result/CLI integration only when required to preserve the existing typed contract.
- Focused unit and packaging tests, fixtures, snapshots, documentation, and test-support files directly required by the covered tasks.
- Critical shared boundary: `src/odoo_instance_sdk/internal/proc/` and `src/odoo_instance_sdk/execution.py` are contract dependencies, not redesign targets; any necessary edit there requires evidence that the fix cannot remain self-update-local and must preserve all existing ledger gates.

**Contract surface:**

- Public SDK: `update_command()` continues to return `Command[UpdateResult]`.
- CLI: existing `odcli update`, `--check`, `--dry-run`, `--ref`, `--yes`, and structured output surfaces remain stable.
- Plan: adds read-only process step `update.verify.version` with captured absolute target executable and `--version` after maintenance and before commit.
- Result: reuses `UpdateResult.outcome`, `journal_state`, `snapshot_state`, `next_step`, and `recovery_argv`; no new public result type or outcome is introduced.
- Persistence: journal version and snapshot layout remain compatible; inspection is read-only and cleanup occurs only after successful verification.
- Security/integrity: `shell=False`, redacted public projections, immutable argv, exact-SHA resume, and unplanned/duplicate/substituted/omitted-step rejection remain mandatory.

**Definition of done and evidence:**

- All covered task checkboxes are completed with implementation and test evidence.
- Strict OpenSpec validation passes for this change.
- Unit evidence proves plan order/argv, public/private parity, exactly-once verify consumption, early-failure skips, recovery-state precedence, dead-PID classification, no inspection mutation, final SHA, and cleanup.
- Existing generic execution-ledger tests remain green and demonstrate that no enforcement was relaxed.
- Packaging evidence proves an older fixture revision updates to the target without treating `UnplannedStepError` as a skip, and the public interrupted-state check returns the frozen resume argv while preserving journal/snapshot files.
- Repository formatter, lint, strict type checks, focused tests, and applicable packaging gate pass; external-prerequisite skips are reported separately and are not represented as passing regression evidence.
- Review confirms no dependency, persisted schema, journal-version, generic recovery framework, or unrelated production change was added.

**Single-owner safety rationale:** The recovery inspector, outcome precedence, captured verify step, skip paths, cleanup, and both regression layers modify one tightly coupled lifecycle and overlapping test fixtures. One WP keeps public/private plan parity and interruption semantics atomic, avoids conflicting writes in the same two implementation modules, and matches the estimate-property delivery threshold.

## Estimate evidence and assumptions

The estimate was grounded in the complete proposal/design/spec/tasks package, `openspec/specs/self-update/spec.md`, the current `self_update.py` and `self_update_commands.py` flow, `RunContext` ledger enforcement, `UpdateResult`, existing unit matrices in `tests/unit/test_self_update.py`, and packaging coverage in `tests/packaging/test_self_update.py` at the approved base.

Material risks are bounded to: preserving ledger completeness across every early return; deciding PID liveness without treating it as completion authority; producing a safe exact resume argv only from validated immutable journal data; and making the uv-tool packaging regression deterministic across supported environments. The estimate becomes invalid if scope expands to a new journal schema, a new resume command, cross-platform process supervision, a generic execution-layer redesign, or broader update rollback semantics.
