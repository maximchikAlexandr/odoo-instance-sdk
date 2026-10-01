## Delivery Contract

- Task key: `MYL-355`
- OpenSpec change: `restore-missing-data-dir-disk-preflight`
- Approved base: `origin/main` at the base SHA recorded in the planning handoff
- Delivery mode: `single_wp_no_dag`
- Topology rule: the authoritative `Estimate, hours` property on the root planning issue does not exceed the governing threshold, so exactly one all-covering work package is permitted. The package has no dependency or stage metadata.
- Estimate source: the root planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; numeric totals are intentionally not duplicated here.
- Estimate basis: remaining active developer effort for one experienced developer familiar with Python, Click, pytest, and this repository, without AI acceleration. Unattended CI, approval queues, meetings, and external blocking are excluded.
- Confidence: medium-high. The failure and shared call path are directly inspectable, close unit and CLI fixtures already exist, and no migration or live service is required. Uncertainty is concentrated in public CLI fixture setup and consistent typed-error export/serialization.
- Calibration: evidence-based engineering estimate, not calibrated from historical elapsed-time records for this exact task.

## WP-MYL-355-01 — Truthful restore disk preflight

**Task coverage (complete and one-time):** `1.1`, `1.2`, `2.1`, `2.2`, `2.3`, `3.1`, `3.2`, `3.3`, `3.4` from `tasks.md`. No task is assigned outside this work package.

**Deliverable:** restore planning measures capacity on the configured destination or its nearest existing directory ancestor without creating paths; uninspectable or invalid paths produce a dedicated actionable typed failure; measured insufficient capacity keeps the existing reserve policy; focused and public dry-run regressions prove the behavior without live Odoo or PostgreSQL.

**Owned responsibility scope:**

- Shared restore capacity selection and error translation in `src/odoo_instance_sdk/internal/backup_validation.py`.
- The dedicated backup-policy exception and its public export surface in `src/odoo_instance_sdk/exceptions.py` and `src/odoo_instance_sdk/__init__.py`.
- Directly related unit, SDK command-construction, CLI dry-run, fixture, snapshot, and documentation files needed to verify the OpenSpec contract, primarily under `tests/unit/internal/`, `tests/unit/`, and existing restore test support.
- No catalog internals, database schema, dependency declarations, configuration format, or production restore orchestration outside the shared preflight call path unless a directly observed contract break requires the smallest compatible adjustment.

**Contract surface:**

- Preserve `preflight_restore_disk_space(uncompressed_bytes, data_dir)` for measured outcomes and all current callers.
- Preserve the reserve formula, archive-size accounting, operator limit, CRC bound, ZIP safety ordering, immutable command construction, and dry-run non-mutation.
- Add one stable typed backup-policy inspection failure with sanitized requested/inspection path details; never represent an unmeasured filesystem as zero capacity or as `backup_insufficient_disk`.
- Existing destinations, missing nested destinations, fallback backup directory, and genuine insufficient capacity retain the behaviors specified in the delta spec.

**Definition of done / evidence:**

- Every OpenSpec task is completed and checked only after its implementation or verification evidence exists.
- Focused tests demonstrate nearest-existing-directory selection, existing-directory parity, no directory creation, invalid non-directory ancestry, injected resolution/traversal/measurement failures, fallback selection, and measured insufficient capacity.
- Public SDK command construction and machine-readable CLI dry-run cover a valid local ZIP with missing and existing `data_dir`; inspection failure produces one actionable failure envelope; no live service is contacted.
- Repository lint, type checks, focused tests, and the standard offline test suite pass, with commands and exit codes recorded in the implementation handoff.
- Strict OpenSpec validation remains successful and the implementation diff contains no unrelated refactor, dependency, migration, or storage abstraction.

**Execution rationale:** the code and test changes share one central function, one exception surface, and overlapping fixtures. Splitting them would create conflicting write zones and coordination overhead without an independent deliverable, while one package lets the Implementer/WP Verifier pair validate the root-cause fix atomically.

## Estimation Evidence and Assumptions

- Inspected source: `internal/backup_validation.py`, its restore callers in `internal/dbprep/` and environment settings, CLI failure transport in `commands/db.py`, exception definitions/exports, and existing focused tests.
- Local analogues already cover the reserve calculation, explicit `data_dir` selection, restore planning, machine-readable CLI envelopes, and archive validation, reducing fixture invention.
- The estimate includes investigation, implementation, focused and offline verification, and likely review repairs. It excludes deployment, live-Odoo/PostgreSQL testing, schema migration, and feature work outside the accepted delta spec.
- The estimate becomes invalid if implementation reveals a required public error-envelope redesign, platform-specific filesystem semantics beyond `pathlib`/`shutil`, or acceptance expands to live restore execution.
