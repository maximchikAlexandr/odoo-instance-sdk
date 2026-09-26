## 1. Canonical target and report contracts

- [ ] 1.1 Add one stdlib-only mutation report helper that reads and validates the unique `[tool.mutmut].only_mutate` paths, derives stable shard identifiers and exact mutmut module filters, and emits the GitHub matrix without a duplicated target list.
- [ ] 1.2 Implement strict result parsing and arithmetic validation for positive `total` plus numeric `killed`, `survived`, `timeout`, `suspicious`, and `not checked`, failing on malformed, inconsistent, or incomplete classifications.
- [ ] 1.3 Add focused parametrized tests for exact-once matrix generation, stable ids/filters, duplicate and invalid configuration, supported result formats, zero-count categories, malformed arithmetic, and non-zero `not checked`.

## 2. Target-aware honest runner

- [ ] 2.1 Extend `scripts/run_mutation.py` and the existing `make mutation` target to accept an optional `MUTATION_TARGET`, validate it against the canonical configuration before child execution, and retain the existing unfiltered local full-scope behavior.
- [ ] 2.2 Apply the derived exact target filter symmetrically to `mutmut run` and `mutmut results`, then make successful child execution fail non-zero when the selected report is empty, unparsable, arithmetically incomplete, or has non-zero `not checked`.
- [ ] 2.3 Extend runner tests to prove validation occurs before the bounded smoke, valid target commands use matching filters, unfiltered commands retain full scope, exact child failures remain unchanged, and completeness failures retain non-empty stage-labelled diagnostics.

## 3. Aggregation and regression baseline

- [ ] 3.1 Implement aggregate mode in the helper to require exactly one report per canonical target, reject missing/duplicate/unknown/empty/malformed shards, and write a deterministic per-target and total summary only for complete coverage.
- [ ] 3.2 Add the version-controlled aggregate survived/timeout baseline bound to the complete `f7c3f7c9093529d6744c30745e220efb9aea8f80` evidence, and make aggregation fail closed on increases without automatically rewriting or relaxing the baseline.
- [ ] 3.3 Add focused aggregation tests for successful totals, deterministic ordering, missing/duplicate/unknown reports, incomplete classifications, survivor regression, timeout regression, accepted equality/improvement, and summary retention on failure.

## 4. Bounded GitHub Actions topology

- [ ] 4.1 Replace the single mutation job with a prepare job that emits matrix JSON from canonical configuration and a `fail-fast: false` matrix that gives each exact target one 45-minute shard and invokes `make mutation MUTATION_TARGET=<target>`.
- [ ] 4.2 Upload one uniquely named shard report under `if: always()` with fail-on-missing semantics, then add an `if: always()` aggregate job that downloads the complete artifact pattern, validates it against the prepare matrix/baseline, uploads the required summary, and fails when any shard or aggregate contract fails.
- [ ] 4.3 Extend semantic CI contract tests to prove scheduled/manual parity, canonical dynamic partitioning, exact prepare → matrix → aggregate dependencies, bounded shard timeout, disabled fail-fast, target-aware invocation, unique always-run artifacts, aggregate execution after failure, and no literal second target list.

## 5. Documentation and acceptance evidence

- [ ] 5.1 Update `CONTRIBUTING.md` with frozen dependency setup, unchanged five-file scope, full and single-target invocation, shard/summary artifact locations, terminal-completeness failures, diagnostic/non-required policy, and the reviewed baseline-lowering rule.
- [ ] 5.2 Run focused helper/runner/CI/documentation tests, formatting, lint and strict typing for changed Python, strict OpenSpec validation, and confirm no production/public API file changed.
- [ ] 5.3 Dispatch the implemented workflow manually, retain its URL and artifacts, and verify every canonical target has one terminal shard within 45 minutes, aggregate `not checked` is zero, totals reconcile, baseline comparison passes, and infrastructure/bootstrap/collection failures remain non-zero.
