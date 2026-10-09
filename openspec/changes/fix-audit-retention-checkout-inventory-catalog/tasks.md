## 1. Retention TOML boundary

- [ ] 1.1 Add a regression that updates `[backup]` immediately before `[[profile]]`, reparses the result with `tomllib`, and proves retention keys belong only to `backup` while unrelated content remains.
- [ ] 1.2 Update the existing line-preserving retention patcher so every TOML table or array-of-tables header ends the owned `[backup]` region, without adding a dependency or rewriting other content.

## 2. Checkout provenance projection

- [ ] 2.1 Add a public adoption-plan regression asserting the captured commit appears as `plan.provenance.resolved_base_revision` and the execution projection is built from the same normalized provenance.
- [ ] 2.2 Extract one internal provenance-normalization and public/execution projection path used by ordinary checkout stages and `adopt_command`, preserving adoption repository/ownership validation and existing external APIs.
- [ ] 2.3 Run ordinary checkout and adoption characterization tests to prove the shared path changes only the missing adoption base provenance.

## 3. Bounded provider IPC

- [ ] 3.1 Add a provider regression whose valid frozen summary serializes beyond a typical OS pipe buffer and assert that its facts appear in the inventory within the configured deadline.
- [ ] 3.2 Change provider collection to drain the receive channel while the child is running, wait on the channel/process under one monotonic deadline, and retain bounded kill/join cleanup for timeout or failure.
- [ ] 3.3 Re-run provider ordering, failure isolation, missing-provider, and slow-provider timeout tests together with the large-payload regression.

## 4. Historical catalogue fingerprints

- [ ] 4.1 Introduce one internal version-explicit historical fingerprint definition that shares common column omissions and keeps v16 repair versus pre-source-neutral nullability/index differences explicit.
- [ ] 4.2 Route both known-v16 repair and legacy-provenance recognition through the shared historical fingerprint while preserving exact foreign-key/view checks, fail-closed behavior, backup-before-mutation, Alembic stamp, and upgrade ordering.
- [ ] 4.3 Extend catalogue migration fixtures/tests with one shared historically absent field and prove both recognition paths account for it, v16 rows/index repair remain intact, unknown shapes fail closed, and schema metadata still matches the single Alembic head.

## 5. Integrated verification

- [ ] 5.1 Run the focused unit suites for retention, adoption, checkout inventory, and catalogue migration; record commands and results.
- [ ] 5.2 Run the repository's mandatory formatting, lint, typing, architecture/contract, and applicable full test checks; resolve only regressions caused by this change and record any pre-existing or environment-limited failures.
