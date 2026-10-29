## Context

The current self-update implementation already discovers a supported companion uv-tool, captures immutable core/companion install and verification steps, records both compositions in the journal and snapshot, and retains recovery evidence on a partial failure. Two incorrect command details prevent that design from completing: `companion_install_argv()` emits the root monorepo requirement for the `odcli-multica` distribution, and `build_update_command()` uses `odcli-multica --version` even though that Click application has no version option. The existing metadata probe already reads the `direct_url.json` VCS revisions for both distributions from the protected companion environment.

## Goals / Non-Goals

**Goals:**

- Install `odcli-multica` from `packages/odcli-multica` at the exact selected revision and install its isolated `odoo-instance-sdk` dependency at that same revision.
- Use a supported, non-mutating companion command for launcher readiness and continue to treat protected distribution metadata as the revision authority.
- Keep install, rollback, resume, dry-run, redaction, and incomplete-update behavior coherent.
- Prove the fix with focused tests and one isolated uv-tool coordinated-update walkthrough.

**Non-Goals:**

- Adding `--version` to `odcli-multica`, changing public update output, or introducing a second revision format.
- Refactoring self-update orchestration, journal/snapshot schemas, process execution, or package provenance discovery beyond the two defects.
- Running Odoo, PostgreSQL, Multica services, or mutating a real user tool installation.

## Decisions

### Emit the companion subdirectory in the existing requirement builder

The companion requirement will append the fixed PEP 508 fragment `#subdirectory=packages/odcli-multica`; the core SDK requirement remains rooted at the repository. The same `companion_install_argv()` path is already used for install, rollback, and recovery, so correcting it once keeps every generated command coherent.

Alternative: add a new monorepo package resolver or infer subdirectories from workspace metadata. That adds flexibility with no current caller and is rejected.

### Probe the launcher with `--help` and verify revisions from metadata

The frozen companion launcher step will use `--help`, a supported read-only Click path that proves the captured executable starts. The existing protected-environment Python metadata step remains the authority for exact `odcli-multica` and embedded `odoo-instance-sdk` commit IDs. Both steps must succeed before commit/cleanup.

Alternative: add a public `--version` option solely for updater verification. That expands the companion CLI and duplicates metadata projection without being required by GitHub #185, so it is rejected. Removing launcher execution entirely is also rejected because the current `self-update` contract requires the actual captured launcher to be invoked.

### Retain current recovery topology

No new recovery state is required. Install construction is shared by forward and recovery paths, and the current verifier already turns a non-zero launcher probe, invalid metadata, or revision mismatch into `update_incomplete` with the journal and snapshot retained. Tests will assert those existing outcomes with the corrected commands.

Alternative: introduce a companion-specific retry coordinator. The existing resume path already replays the frozen companion install and verification stages, so a second coordinator is rejected.

## Ponytail full review

No new subsystem is necessary. Monorepo package selection uses the existing requirement/argv builder instead of a package resolver; companion readiness uses the existing frozen verification step instead of a new CLI version API; exact revisions use the existing protected metadata probe instead of a verifier framework; and failed updates use the existing journal/resume machinery instead of a retry service. Each proposed subsystem alternative is removed from scope.

## Risks / Trade-offs

- **Fixed repository layout could move** → keep the subdirectory as one explicit constant/value with an exact argv regression; a future package move requires an intentional contract revision.
- **`--help` proves startup but not the revision** → retain the separate exact metadata assertion for both distributions and require both steps to pass.
- **PEP 508 fragment handling can regress across forward/recovery commands** → route both through `companion_install_argv()` and assert the exact companion and SDK requirements.
- **A live packaging test can mutate developer tools** → isolate HOME/XDG/UV tool directories under the test fixture and clean them on every outcome.

## Migration Plan

No data migration or compatibility shim is needed. Publish the corrected package, run focused unit and isolated packaging gates on the exact revision, and retain the existing incomplete-update recovery instructions for installations already stopped by the defect. Rollback is the ordinary code rollback; persisted journals and snapshots remain schema-compatible.

## Open Questions

None. Package path, launcher probe, revision authority, failure semantics, and verification ownership are fixed by this design.
