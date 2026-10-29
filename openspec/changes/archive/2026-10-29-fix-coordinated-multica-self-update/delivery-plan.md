# Delivery Plan

## Contract baseline

- **Task key:** `MYL-594`
- **OpenSpec change:** `fix-coordinated-multica-self-update`
- **Approved base:** `origin/main` at `f0918da2d36b59c35cf0a949efc58744d1efb7f9`.
- **Requirements source:** GitHub #185, the current `self-update` main specification, and this change's proposal, design, delta specification, and tasks.
- **Estimate source of truth:** planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; numerical totals are intentionally not duplicated here.
- **Implementation boundary:** the existing self-update composition/command path, focused tests, and directly affected documentation. No new dependency, command, migration, service, or Odoo behavior is authorized.

## Ponytail review

`ponytail full` found no justified new subsystem. The existing requirement builder owns forward/recovery argv, the existing frozen process step owns launcher readiness, the existing protected metadata probe owns exact revisions, and the existing journal/snapshot owns resume and incomplete state. A workspace-package resolver, public companion version API, generic verifier framework, and companion retry coordinator were considered and removed because each duplicates a sufficient existing seam.

## E2E verification contract

`e2e_required=true`. GitHub #185 explicitly requires an end-to-end coordinated update from an existing supported installation, and only a real isolated uv-tool composition proves that the PEP 508 subdirectory is accepted, the installed companion starts, both distributions land at the selected revision, and successful verification clears recovery state. Unit tests remain sufficient for deterministic malformed/mismatch/failure branches, but they cannot prove the packaging interaction.

The project's Odoo scenario repository is `https://gitlab.icode.by/a.maksimchyk/binta-test-repo`; this change SHALL NOT clone or mutate it because no requirement interacts with an Odoo checkout, database, or server. No numbered `odcli-N` slot, PostgreSQL database, port, browser, or Multica daemon is needed. The live fixture uses only a disposable local Git copy and isolated HOME/XDG/UV directories. Planning does not execute any test.

| Case ID | OpenSpec requirement/scenario | Verification gap / local E2E decision | Public operation/schema | Local test script | Independent Tester steps | CI disposition | Assertions | Artifacts/images |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `CU-ARGV` | Installed companion and embedded SDK advance together; partial update remains recoverable | Lower-level check is sufficient for exact deterministic argv across forward and recovery paths | Frozen `update.install.companion` and `update.recovery.companion` `ProcessStep` values | `tests/unit/test_self_update.py`; `uv run pytest -q -o addopts='' tests/unit/test_self_update.py -k 'companion'` | Inspect the dry-run plan at the candidate revision and verify the companion requirement ends in the fixed subdirectory fragment while the SDK requirement does not; inspect recovery argv from one forced fake failure | Existing `offline-tests` job via `make test` | Exact package names, fragments, target/previous revisions, argv boundaries, read-only/mutating flags, and retained journal/snapshot | Multica comment: command/exit, candidate SHA, test/MR links, concise assertion; no image because output is unchanged |
| `CU-VERIFY` | Supported launcher command; package/SDK old; invalid evidence | Lower-level checks are sufficient for non-zero process results and JSON/revision matrices; no live failure injection is needed | Captured companion launcher and protected `direct_url.json` probe | `tests/unit/test_self_update.py`; same focused command | Inspect planned launcher argv and metadata script; confirm each parametrized mismatch/invalid branch returns `update_incomplete`, retains evidence, and never reaches commit cleanup | Existing `offline-tests` | `--help` succeeds; `--version` is absent; launcher failure, malformed evidence, package mismatch, and SDK mismatch fail closed; success requires both target SHAs | Multica comment with commands/exits and linked assertions; diagnostic JSON/logs only in Multica, never Git |
| `CU-LIVE` | Installed companion and embedded SDK advance together; successful coordinated completion | Local E2E required: only uv's real VCS/subdirectory resolver plus two isolated tool environments proves the install/verification integration | Public `odcli update --ref <target> --yes`, `odcli-multica --help`, and Python distribution metadata | Extend `tests/packaging/test_self_update.py` with one focused test; run `uv run pytest -q -o addopts='' -m packaging tests/packaging/test_self_update.py -k 'coordinated_companion'` | In a fresh disposable repository copied from the candidate, create a baseline commit containing the fix and a descendant no-op target; set isolated HOME/XDG/UV paths; install core and companion tools from the baseline, run the installed baseline `odcli update` to the target, invoke installed `odcli-multica --help`, inspect both installed `direct_url.json` records and absence of the update journal, then remove only the disposable fixture | Add to existing `package` job through the established packaging test command; no new CI framework. If the focused local automatic run reaches 10 minutes, stop only its owned process and run the exact published SHA through GitHub Actions instead | Exit `0`; outcome `updated`; companion receipt resolves `packages/odcli-multica`; both commit IDs equal target; help succeeds; journal/snapshot are absent; user/global tool roots are untouched | Multica: exact product SHA, fixture revisions, commands/exits, sanitized relevant output, MR/commit/test-file links, and cleanup result. No PNG: user-visible output is unchanged and the case is packaging/state verification |
| `CU-REGRESSION` | Core-only installation remains core-only; ambiguous provenance; warning/dry-run compatibility | Existing unit/CLI contract coverage is sufficient; no second live stand | Existing `odcli update` command/result surfaces | `tests/unit/test_self_update.py`, `tests/unit/test_self_update_selection.py`, `tests/unit/test_self_update_recovery.py` | Review plan/result projections for core-only and companion-present cases; confirm ambiguity fails before mutation and no new secret-bearing field appears | Existing `offline-tests` | No companion step for core-only install; warning precedes mutation; ambiguity is preflight failure; redaction/recovery contract unchanged | Multica command/exit and linked test evidence; no image because visible text is not changed |

### Script, fixture, and cleanup details

- The Implementer SHALL extend the existing `tests/packaging/test_self_update.py` runner and its isolated-home/local-origin rewrite helpers rather than add a second E2E framework. Any directly needed fixture helper belongs in that test file or its existing focused support module.
- The automated fixture SHALL record baseline, target, and tested product revisions in assertion context; build both uv tools only under fixture-owned paths; apply failure-safe cleanup; and never read or overwrite a developer's installed tools, credentials, global uv state, or private configuration.
- The Tester walkthrough SHALL use a distinct disposable directory and unique isolated paths. It SHALL not use the reference Odoo checkout, the permanent PostgreSQL cluster, or a developer tool root. Cleanup removes the disposable local tools/repository while preserving the published implementation branch, commits, and MR.
- Test scripts, required fixtures, and genuine generated functional artifacts belong in the implementation commit. Raw logs, diagnostic JSON, SHA/hash ledgers, commands/exits, and evidence-only reports belong only in the Multica comment with read-back.
- Public output is unchanged, so before/after terminal text and PNG are not applicable. If implementation changes help, progress, warning, or summary text, planning must be revised to require actual baseline/candidate text plus Mac PNG renders through bundled `scripts/render_terminal.swift`, with ANSI/TTY limitations recorded honestly.

### Readiness and execution order

1. Confirm the implementation branch/MR points at the exact candidate SHA; focused unit tests and repository lint/type checks pass; the disposable fixture paths do not overlap any real tool root.
2. Run the focused automated packaging case once. A failure SHALL retain sanitized output for diagnosis and SHALL be repaired with cheaper unit checks before another live attempt.
3. On the same candidate SHA, perform the independent Tester walkthrough and inspect installed metadata/state directly. Re-running pytest is not a manual check.
4. Publish command/exit, exact revision, relevant MR/commit/file links, assertions, cleanup, and any findings in the final Multica comment; read back the comment and attachments. A product SHA change requires an impact assessment and rerun of affected cases.

## Repository verification

The Implementer owns all product tests/fixtures and runs the following gates subject to the workspace ten-minute limit for automatic suites:

```text
uv run pytest -q -o addopts='' tests/unit/test_self_update.py tests/unit/test_self_update_selection.py tests/unit/test_self_update_recovery.py
uv run pytest -q -o addopts='' -m packaging tests/packaging/test_self_update.py -k 'coordinated_companion'
make lint
make types
make test
make package
openspec validate fix-coordinated-multica-self-update --strict
openspec validate --all --strict
```

If a local automatic suite is expected to exceed or reaches ten minutes, stop only that run's process and use GitHub Actions on the published exact revision; do not split the suite to bypass the limit. Tester verifies rather than edits Implementer-owned tests. Standard CI jobs are `lint`, `types`, `offline-tests`, and `package`; strict OpenSpec validation remains a local/handoff gate.

## Estimate evidence

- **Executor basis:** one experienced developer familiar with the Python/uv-tool codebase, without AI acceleration; active developer-hours include implementation, required tests, investigation, and review fixes, while unattended CI and approval queues are excluded.
- **Confidence/calibration:** medium confidence and uncalibrated. Acceptance criteria and affected seams are clear and close tests exist, but the repository provides no comparable estimate-to-actual history and the real uv subdirectory packaging path remains the material bounded uncertainty. Numeric O/M/P/E totals live only in planning issue properties.
- **Primary evidence:** the two defects are localized in existing command construction and verification seams; focused unit fixtures and a substantial packaging E2E harness already exist; recovery behavior spans immutable plans, resume, and evidence retention and therefore needs explicit regression coverage.
- **Coverage:** estimate units cover tasks `1.1`, `1.2`, `2.1`, `2.2`, and `3.1` exactly once across command construction, launcher/metadata verification, failure/resume tests, packaging E2E, documentation, repository gates, and likely review repairs. Tests were not run during estimation.
- **Potential invalidators:** a required public companion version API, a uv behavior incompatible with local-origin rewrite plus subdirectory fragments, journal/schema changes, or any Odoo/Multica service dependency requires a planning and estimate revision.

## Delivery topology

`delivery_mode=single_wp_no_dag`. The authoritative `Estimate, hours` property is within the single-WP threshold. Exactly one work package covers every OpenSpec task; it has no `depends_on`, stage, frontier, or DAG metadata.

### WP-MYL-594-01-coordinated-update-fix

- **Task coverage:** `1.1`, `1.2`, `2.1`, `2.2`, and `3.1`, each exactly once.
- **Deliverable:** coordinated self-update installs `odcli-multica` from its monorepo package directory, pins both distributions to the selected revision, verifies a supported launcher path plus exact package metadata, and preserves recoverable failure behavior with focused and live packaging evidence.
- **Owned responsibility scope:** existing self-update composition/command construction and recovery integration; focused unit and packaging tests; directly affected README/help/recovery documentation; and directly related fixtures, snapshots, generated functional artifacts, or repository-maintenance files required by checks. This is responsibility guidance, not an exhaustive file allowlist.
- **Critical shared files:** `src/odoo_instance_sdk/internal/self_update_composition.py`, `src/odoo_instance_sdk/internal/self_update_commands.py`, the directly coupled `internal/self_update*.py` recovery/planning modules if an observed regression requires them, `tests/unit/test_self_update*.py`, `tests/packaging/test_self_update.py`, architecture inventory fixtures, and affected documentation.
- **Contract surface:** modified `self-update` requirement; immutable/redacted `ProcessStep` planning; PEP 508 VCS requirement construction; protected uv-tool receipt and `direct_url.json` provenance; core-only compatibility; journal/snapshot/resume/rollback semantics; and existing `UpdateResult` outcomes.
- **Definition of done / evidence:** all nine delta scenarios map to focused assertions or the `CU-LIVE` packaging case; exact forward/recovery requirements carry only the companion subdirectory fragment; launcher readiness uses the supported command; success requires both target revisions and removes journal/snapshot; every specified failure retains recoverable evidence and never reports `updated`; repository and strict OpenSpec gates pass on the exact candidate; Tester publishes and reads back the required Multica evidence.
- **Parallel-safety rationale:** one WP intentionally owns the tightly coupled command, verification, recovery, and packaging test changes, so there are no sibling write zones or cross-WP contract races.
