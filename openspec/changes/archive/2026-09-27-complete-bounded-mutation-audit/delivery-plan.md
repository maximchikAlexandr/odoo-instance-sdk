## Planning Basis

- Planning issue: `MYL-306`
- OpenSpec change: `complete-bounded-mutation-audit`
- Approved source baseline: `f7c3f7c9093529d6744c30745e220efb9aea8f80` (`origin/main` at planning start)
- Estimate source of truth: planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`
- Executor basis: one experienced developer familiar with Python, pytest, mutmut and GitHub Actions; active developer effort includes implementation, required verification and likely review fixes, while unattended workflow runtime and approval queues are excluded
- Confidence: medium; current runner, workflow, TOML configuration, focused tests and the complete prior mutation evidence are directly inspectable, while GitHub-hosted per-target duration and exact result-format edge cases remain bounded implementation risks
- Calibration: uncalibrated; no comparable target-sharded delivery timing was available
- Delivery mode: `single_wp_no_dag`, selected from the authoritative estimate property

## Work Package

### WP-01 — Complete the bounded mutation audit

**OpenSpec task coverage:** 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 4.1, 4.2, 4.3, 5.1, 5.2, 5.3. Every task is owned exactly once by this package.

**Independent deliverable:** A scheduled/manual Mutation Testing workflow that derives one shard per canonical target, completes every target within its configured bound, retains failure diagnostics, publishes one validated aggregate report with zero unchecked mutants, and rejects survived/timeout regressions against a reviewable baseline without changing production SDK/CLI behavior.

**Owned responsibility scope:**

- canonical target discovery and target-to-filter/shard derivation from `[tool.mutmut].only_mutate`;
- strict mutmut result parsing, completeness arithmetic, aggregation and baseline validation in one focused stdlib-only helper;
- optional target selection and fail-closed completeness behavior in `scripts/run_mutation.py` and the existing `make mutation` entrypoint;
- the version-controlled mutation baseline and its evidence binding;
- `.github/workflows/mutation.yml` prepare, matrix, per-shard upload and aggregate topology;
- focused runner/helper/workflow/documentation tests and directly related fixtures;
- `CONTRIBUTING.md` mutation instructions and acceptance-evidence recording;
- directly related snapshots, report fixtures and generated service files needed to verify these responsibilities.

Production modules under `src/odoo_instance_sdk/`, public Python/CLI contracts, the five-file mutation target set, mutation framework/dependency family, required PR gates and unrelated CI workflows are outside this package.

**Critical shared files:** `pyproject.toml`, `Makefile`, `scripts/run_mutation.py`, the new focused mutation report helper, `.github/workflows/mutation.yml`, the mutation baseline, `tests/unit/test_mutation_runner.py`, `tests/unit/test_ci_contract.py`, focused helper tests, and `CONTRIBUTING.md`.

**Contract surface:**

- `pyproject.toml` remains the only canonical target list and produces unique stable shard identities;
- `make mutation` remains the only local/CI entrypoint, accepts only a configured optional target, and preserves unfiltered local full-scope behavior;
- the same exact filter scopes both `mutmut run` and `mutmut results` for a target shard;
- a successful shard has a positive reconciled terminal classification and zero `not checked`; invalid targets and incomplete reports fail before being accepted;
- matrix cancellation is disabled, each shard always attempts a uniquely named fail-on-missing diagnostic upload, and aggregation always examines available evidence;
- aggregation requires exactly one report for every canonical target, rejects malformed coverage/classification, and emits deterministic per-target and total counts;
- aggregate survived and timeout counts cannot exceed the committed evidence-bound baseline, which runtime automation never rewrites;
- scheduled and manual triggers share identical bounded topology and failure semantics.

**Definition of done and evidence:**

- every checkbox in `tasks.md` is completed without product-scope expansion;
- focused tests prove canonical exact-once matrix generation, stable filters, strict parsing/arithmetic, invalid targets, missing/duplicate/unknown reports, baseline equality/improvement/regression, deterministic summary and preserved exact child failures;
- CI contract tests prove prepare-to-matrix-to-aggregate data flow, target-aware invocation, bounded shards, disabled fail-fast, always-run unique uploads, fail-on-missing behavior and aggregate execution after failures;
- formatting, lint, strict typing for changed Python, focused unit/documentation tests and strict OpenSpec validation pass;
- repository diff proves no production/public API file changed and the five canonical targets are unchanged;
- a manual workflow run provides a URL plus retained shard/aggregate artifacts showing one complete terminal report per canonical target, reconciled totals, zero `not checked`, a passing baseline comparison and no job timeout;
- controlled infrastructure, bootstrap, collection, missing-artifact, incomplete-classification and baseline-regression paths remain non-zero.

**Parallel-safety rationale:** The package is intentionally indivisible. Target derivation, runner filtering, report grammar, baseline comparison, workflow topology and contract tests define one fail-closed observable contract and share critical files; splitting them would create overlapping write zones and transient states in which CI could accept incomplete evidence.
