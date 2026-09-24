## Delivery Basis

- Change: `stop-project-owned-runtime`
- Planning issue: `MYL-244`
- Base revision: `9a862fbe8ff83d57c452144badbd178327d5e598` (`origin/main`)
- Estimate source: authoritative `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` properties on the planning issue.
- Confidence: medium-high. The affected CLI callback, runtime identity path, catalogue schema, cleanup behavior, and regression suite were inspected at the base revision; the main uncertainty is preserving identical project/environment argv and config normalization through two revalidation reads.
- Calibration: uncalibrated for a named implementation agent; the estimate uses one experienced developer familiar with the Python/Click/SQLite codebase, without AI acceleration, and excludes unattended CI plus human review queues.
- Topology decision: `single_wp_no_dag`. The authoritative weighted estimate falls within the mandatory single-work-package threshold, so all OpenSpec tasks remain one execution and verification unit.

## WP-MYL-244-01 — Owner-neutral safe runtime stop

**Task coverage:** `1.1`, `1.2`, `1.3`, `2.1`, `2.2`, `3.1`, `3.2`, `4.1`, `4.2`, `4.3`. This work package covers every task in `tasks.md` exactly once.

**Deliverable:** one backward-compatible implementation in which the existing `odcli stop` leaf safely stops the exact persisted project- or environment-owned runtime, preserves all fail-closed identity checks, conditionally clears only the matching runtime row, reports owner-neutral identity, and passes the required regression and repository checks.

**Owned responsibility scope:**

- Runtime owner/identity and stop planning/execution under `src/odoo_instance_sdk/resources/instance/`.
- Owner-neutral conditional runtime cleanup in `src/odoo_instance_sdk/storage/catalog/`.
- Top-level stop callback, help, context, and bounded output in `src/odoo_instance_sdk/commands/`.
- Directly related unit tests, fixtures, public-leaf inventory assertions, documentation, and generated/check artifacts required by the implementation.
- Critical shared files: `resources/instance/runtime.py`, `resources/instance/identity.py`, `resources/instance/planning.py`, `storage/catalog/environment.py`, `commands/cli_parts/callbacks.py`, `tests/unit/test_runtime_stop.py`, and `tests/unit/test_cli_output_modes.py`.

**Contract surface:**

- Exact runtime owner is the resolved `_RuntimeBinding` pair `owner_kind + owner_id`; no runtime-row scan or port inference.
- Environment expectations continue to use recorded environment artifacts; project expectations reuse the initialized project's canonical command prefix, cwd, and effective config.
- Process signaling remains behind the existing process boundary with unchanged PID/create-time, executable, argv, cwd, config, and process-group checks.
- Runtime cleanup is conditional on owner, PID, and create time and does not remove project registration.
- The CLI remains one `stop` leaf with existing dry-run/output/error behavior and owner-neutral identity fields.

**Definition of done and evidence:**

- Every acceptance scenario in both delta specs maps to an automated assertion or an existing unchanged assertion explicitly exercised for both owners.
- The parameterized safety matrix proves matching termination, mismatch/PID-reuse refusal, inaccessible evidence refusal, planning/execution race refusal, vanished-process cleanup, no-row idempotency, and replacement-row preservation.
- CLI tests prove help wording, project and environment resolution, Rich/JSON/TOON parity, nullable project environment fields, and the unchanged explicit `--env` path.
- Focused runtime-stop, catalogue-runtime, CLI output/help, lint, type, and strict OpenSpec checks pass, or any unrelated base failure is captured with reproducible evidence.
- No migration, dependency, new command, second process registry, supervisor, or production path outside the declared contract is introduced.

**Parallel-safety rationale:** this is intentionally one work package. The runtime identity model, catalogue cleanup predicate, CLI projection, and shared parameterized tests form one security-sensitive vertical slice with overlapping critical files and one fail-closed invariant; splitting them would create intermediate states where signaling and cleanup disagree.

## Estimation Evidence and Assumptions

The estimate accounts for owner-neutral identity/catalogue work, shared stop revalidation and compatibility, CLI/help/output changes, the full regression matrix, and likely review-fix iteration. It is grounded in the existing `_RuntimeBinding`, shared `runtime` table, current environment stop implementation, `terminate_pid` boundary, and nearby tests, so no new subsystem is assumed.

The estimate changes materially if implementation reveals that project detached launch does not expose its effective config path through the current instance state, if public SDK compatibility requires a broader API migration, or if acceptance expands to adoption/discovery of unrecorded processes, end-to-end Odoo startup, or a new supervisor. Tests were not run while estimating; existing source, tests, configuration, and local history were read only.
