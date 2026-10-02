## 1. Canonical recovery inspection

- [ ] 1.1 Add a side-effect-free internal recovery-state projection over the canonical update journal and snapshot paths, including immutable target ref, recorded maintenance PID, PID liveness, and actual `present`/`absent` states without creating directories, locks, or files.
- [ ] 1.2 Make exact-SHA no-op selection and `update --check` consume that projection before SHA comparison, returning `update_incomplete` for a valid unfinished journal plus snapshot with the exact frozen `(<absolute-odcli>, "update", "--ref", <journal-target-ref>, "--yes")` recovery argv and no mutation.
- [ ] 1.3 Add focused unit tests for absent state, matching installed/target SHA with incomplete state, dead maintenance PID, exact recovery argv, malformed immutable recovery metadata, and proof that check inspection leaves files and process execution untouched.

## 2. Planned verification boundary

- [ ] 2.1 Capture read-only `PreparedStep` `update.verify.version` with the target absolute OdCLI path and `--version` in the mutating command's public/private immutable snapshot after maintenance and before commit.
- [ ] 2.2 Route final executable verification through `RunContext.process_prepared()` exactly once, remove the duplicate maintenance-side version launch, validate the expected target result, and explicitly skip the planned verify step on every pre-verify failure path.
- [ ] 2.3 Extend unit tests to pin dry-run argv/order, public/private projection parity, exactly-once execution, no `UnplannedStepError`, fail-closed duplicate/substituted/omitted behavior, failure evidence retention, and successful final SHA plus journal/snapshot cleanup.

## 3. Public CLI packaging regressions

- [ ] 3.1 Strengthen the old-revision-to-current packaging E2E so `Unplanned execution step` is a failure, and assert planned/executed `update.verify.version`, `updated`, the final target SHA, and absent journal/snapshot after commit.
- [ ] 3.2 Add an isolated-home packaging scenario that preserves a valid journal and snapshot before verify/commit with a dead maintenance PID, invokes public `odcli update --check --format json`, and asserts `update_incomplete`, actual states, and the single exact resume argv without cleanup or resume.

## 4. Verification and delivery

- [ ] 4.1 Run the self-update unit slice and generic execution-ledger tests; resolve every project-owned failure while preserving unplanned, duplicate, substituted, and omitted-step enforcement.
- [ ] 4.2 Run the packaging self-update slice when `uv` and local Git fixture prerequisites are available, recording prerequisite skips separately from regressions.
- [ ] 4.3 Run strict OpenSpec validation and the repository's formatter, lint, type, and test gates required for the touched Python and packaging paths; confirm no dependency, persisted schema, journal-version, or unrelated production change was introduced.
