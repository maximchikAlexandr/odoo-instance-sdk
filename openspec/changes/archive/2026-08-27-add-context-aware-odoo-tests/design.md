## Context

The current CLI registers `module test` in `cli.py` and implements its shell script in `internal/automation.py`. That path already calls Odoo 19 `odoo.tests.shell.run_tests()`, reserves the environment-bound HTTP port, captures the native report, and derives a zero/failure exit code, but it requires explicit modules/tags and has no safe addon or Git selection boundary. It also starts Odoo before proving installed-module state.

MYL-55 is the prerequisite architecture: `commands/context.py` supplies typed project/environment resolution, `commands/output.py` supplies one CLI envelope and `rich|json|toon`, and `cli.py` remains registration/composition. This change is planned on the accepted foundation SHA `af02cc7`, but implementation must begin only after the MYL-55 implementation is merged or rebased into this feature branch.

The selected environment already carries the registered worktree and recorded `base_ref`; its generated Odoo config is parsed into `StartConfig.addons_path` and exact configured database names. These existing values are authoritative. No project-wide addon scan or guessed default branch is needed.

Odoo 19's native tag selector accepts file paths such as `/base/tests/test_tests_tags.py`; this is exercised by upstream `odoo/addons/base/tests/test_tests_tags.py`. The existing shell runner can therefore handle a safe file target without `--test-file`, AST discovery, or a second process path. Sources: [Odoo 19 shell runner](https://github.com/odoo/odoo/blob/19.0/odoo/tests/shell.py), [Odoo 19 tag-selector tests](https://github.com/odoo/odoo/blob/19.0/odoo/addons/base/tests/test_tests_tags.py), and [Odoo 19 test selection documentation](https://www.odoo.com/documentation/19.0/developer/reference/backend/testing.html#test-selection).

## Goals / Non-Goals

**Goals:**

- Resolve module, cwd, test-file, or directly changed addon selection deterministically inside configured worktree-local addon roots.
- Preserve exactly one typed operation and one existing Odoo shell runner for both CLI entry points.
- Prove one bound database and installed module state with read-only preflight before spawning Odoo.
- Report complete selection/base provenance and renderer-independent results through the MYL-55 context/output seams.
- Make path, symlink, Git filename, zero-test, and diagnostics behavior explicit enough for implementation without further product decisions.

**Non-Goals:**

- Reverse/affected dependency computation, CI correctness gating, coverage selection, caching, sharding, flaky history, or scheduling.
- Database provisioning, restore, module install/update, or any hidden mutation.
- A test resource/service/repository/catalog, selector DSL, AST analysis, new Git abstraction framework, or CI-provider integration.
- Running JavaScript tests/tours through a separate runner or changing Odoo's native selector semantics.

## Decisions

### D1: One thin Click adapter and one private pure selection module

Add `commands/test.py` as the Click inbound adapter and registration unit. It consumes the MYL-55 typed `CliContext`, validates option combinations, calls pure selection/preflight/execution functions, and projects the returned values into the shared output adapter. It owns only command-local Rich presentation.

Add one private `internal/test_selection.py` for addon-root validation, target/cwd/file resolution, NUL-safe Git collection, provenance values, and read-only PostgreSQL preflight. Its functions accept `Path`, `DevelopmentEnvironment`, `StartConfig`, and plain/typed values; it imports no Click, Rich, or CLI envelope. Keeping these closely related safety rules in one file is smaller and easier to audit than services, repositories, per-selector classes, or a generic Git provider.

Keep the actual shell execution in `internal/automation.py`, rename/refactor the current `run_module_tests` implementation into one `run_odoo_tests(instance, spec, ...)` path, and have both CLI commands call it. No second application layer is introduced.

Alternative considered: place every concern in `commands/test.py`. Rejected because reusable selection and trust-boundary tests would then depend on Click. Alternative considered: create selector strategies/services/repositories. Rejected because there is one command and four straightforward input modes.

### D2: Two public typed values, no public resource

Define and export frozen `msgspec.Struct` values `OdooTestSpec` and `OdooTestResult` in the existing public model surface. `OdooTestSpec` owns the sorted unique module tuple and native selector. `OdooTestResult` owns the stable counts mapping, native failure/zero flags, and final exit code. Validate non-empty modules/tags at the operation boundary even if direct construction remains mechanically possible.

Selection provenance is CLI/application planning data rather than the execution result and remains a small private typed struct in `internal/test_selection.py`; the Click adapter converts it with the existing JSON-safe envelope path. Native stderr is not a model field because it remains a sanitized stream.

Alternative considered: expose `TestResource`, `TestSelection`, `TestCounts`, and repository interfaces. Rejected because they expand the public surface without a second caller or implementation. A five-key `dict[str, int]` keeps the requested `counts` field literal and stable without another public type.

### D3: Eligible roots are filtered from the bound generated config

Read `instance.config.start_config.addons_path` only after the MYL-55 environment resolution returns one ready environment. For each configured root, resolve relative entries against the recorded worktree/runtime cwd as Odoo does, canonicalize it, and retain only existing directories contained by the canonical registered worktree. External, missing, or escaping configured roots are recorded as ineligible and can never satisfy a target; they do not make all testing impossible when another safe worktree-local root exists.

Module discovery walks upward from a canonical path to the matched eligible root and stops at the nearest directory with a regular non-symlink `__manifest__.py`. The module directory, manifest, and explicit file must remain both lexically and canonically contained. Bare names are restricted to Python package identifiers and checked across every eligible root; zero or multiple matches fail.

For explicit test files, require a regular non-symlink `test_*.py` beneath the module's literal `tests/` subtree. Reject `__init__.py` and directories. Module/cwd defaults become `/<module>`; file defaults become a forward-slash worktree-relative native selector such as `/sale/tests/test_sale_order.py`. A file plus `--tags` is rejected instead of inventing an intersection grammar; callers can use `odcli test sale --tags '/sale/tests/test_sale_order.py:Class.method'` if they need a fully native selector.

Alternative considered: scan the repository for every manifest. Rejected because it ignores configured addon precedence and can select fixtures/vendor directories. Alternative considered: accept canonical containment alone through internal symlinks. Rejected because explicit symlink rejection makes the trust boundary and tests deterministic.

### D4: Changed selection uses four standard Git queries and bytes

Resolve the base as explicit `--base`, otherwise `environment.base_ref` only when it is non-empty and not literal `HEAD`; otherwise fail. Verify base and `HEAD`, then compute the merge base. Do not inspect a remote, infer `main`, fetch, or fall back to `HEAD`.

Use a tiny `_run_git_bytes(argv, cwd, timeout)` local to `internal/test_selection.py` because existing Git helpers force text output and cannot preserve NUL-delimited filenames. Every invocation is `subprocess.run(["git", "-C", worktree, ...], shell=False, capture_output=True, timeout=...)`. Collect:

```text
git diff --no-renames --name-only -z <merge-base> HEAD
git diff --no-renames --name-only -z --cached HEAD
git diff --no-renames --name-only -z
git ls-files --others --exclude-standard -z
```

Splitting bytes on NUL and decoding with `os.fsdecode` preserves spaces, newlines, quotes, and non-UTF-8 filesystem names without shell interpolation. `--no-renames` makes rename sides visible as delete/add paths. Normalize each entry as a relative `PurePath`-style Git path, reject absolute/`..` forms, then sort/deduplicate by the decoded relative string.

Paths outside eligible roots are `ignored_paths`. A path lexically under an eligible root must map to the nearest safe manifest; missing/unsafe mappings are `unmapped_paths` and make the plan non-executable. This distinction prevents docs-only changes from failing while preventing malformed addon layouts from disappearing silently. Direct modules only are selected.

For an executable changed plan without explicit `--tags`, map the sorted unique module tuple to `/<module>` tokens and join them with `,` and no whitespace (for example, `("sale", "stock")` becomes `/sale,/stock`). When `--tags` is explicit, assign its original string directly to `OdooTestSpec.test_tags`; do not trim, parse, reorder, deduplicate, or otherwise normalize it.

Alternative considered: parse `git status --porcelain`. Rejected because its quoting and combined state grammar are unnecessary and easier to mishandle. Alternative considered: use GitPython. Rejected because four stdlib subprocess calls are sufficient and add no dependency.

### D5: Read-only PostgreSQL preflight precedes the test process

Require exactly one configured database name from the bound `OdooInstance`. Extend the existing `internal/postgres_transport.run_psql()` with an optional database argument (default remains `postgres` for existing callers) and use it for one bounded read-only query against that database. Validate selected module names as Python identifiers, SQL-escape literals defensively, and select installed names from `ir_module_module` where `state='installed'`. A non-zero/missing/timeout transport result, missing database, or incomplete installed set is an actionable failure.

This extends an existing concrete primitive rather than adding psycopg or a test repository. It performs no write and avoids spawning Odoo simply to discover that tests cannot run. Dry-run and `no_addon_changes` return before preflight.

Alternative considered: call the existing `list_modules()` shell helper as preflight. Rejected because that already spawns Odoo and violates the required ordering. Alternative considered: install/upgrade missing modules. Rejected as hidden mutation.

### D6: Adapt the current shell script rather than creating a runner

`run_odoo_tests()` accepts the validated spec, confirms the existing HTTP address is free, and calls `_run_shell_script_exclusive()` once. The injected script sets the process-local Odoo workers setting to `0`, imports `odoo.tests.shell.run_tests`, invokes it once, and serializes only native report counts. The captured process is rollback-only and the existing environment-bound port/config/cwd/lock path remains authoritative.

Build `OdooTestResult` from the process return code and native `testsRun`/success/failure/error/skipped data. Do not inspect log wording. Sanitize captured native stderr with the existing diagnostic helper and return it beside the typed result only to the adapter's stderr path. An Odoo process failure is mapped to a non-zero result/error without a second attempt.

Alternative considered: invoke `odoo-bin --test-file` for files or a startup test mode for changed modules. Rejected because it creates a second runner/process contract and bypasses the explicitly retained shell runner.

### D7: Selection and execution share one envelope projection

`commands/test.py` creates one result dictionary from the resolved environment and selection plan/provenance, adding an effective `OdooTestSpec` and `OdooTestResult` projection only when execution occurs. It sends that same JSON-safe value to the MYL-55 envelope/emitter; JSON and TOON differ only at serialization. Rich is a direct command-local projection of the same values.

`odcli module test` keeps its legacy parser surface and `command="module.test"`, but immediately converts modules and test tags into the same selector validation and operation. The top-level path uses `command="test"`. Exit codes come from selection/preflight typed errors or `OdooTestResult.exit_code`, never a renderer.

The projection has three normative success shapes. `executed` includes common environment/selection/modules/exit fields plus tags/options and native counts/failure/zero fields. `dry_run` includes the common fields, `dry_run=true`, complete base/Git provenance, and `exit_code=0`, but omits tags/options/counts/failure/zero fields. `no_addon_changes` includes the common fields with empty modules, `reason="no_addon_changes"`, complete base/Git provenance, and `exit_code=0`, and omits the same execution-only fields; a dry no-op may also include `dry_run=true`. Non-executed shapes construct neither `OdooTestSpec` nor `OdooTestResult` and Rich shows neither fabricated counts nor execution progress. Unsafe `unmapped_paths` emits the collected provenance with non-zero status. None of these non-executed paths starts PostgreSQL preflight or Odoo.

Alternative considered: separate JSON schemas for plans and runs. Rejected because the shared envelope can carry optional selection/run sections and the issue forbids a test-specific output hierarchy.

## Risks / Trade-offs

- [MYL-55 code is not yet on the planning branch] → Make foundation integration the first implementation gate; do not recreate its typed context/output helpers in this change.
- [Recorded environment `base_ref` may be literal `HEAD`] → Treat it as unavailable and require explicit `--base`; never silently choose `main` or a moving self-baseline.
- [Direct SQL couples preflight to Odoo's stable module table] → Limit it to the long-established `ir_module_module(name,state)` read and fail closed; all actual test behavior still belongs to Odoo's runner.
- [Strict symlink rejection excludes some intentionally linked local addon layouts] → Prefer a deterministic safety boundary now; relax only with a future explicit requirement and containment tests.
- [Four Git commands are not one atomic repository snapshot] → Capture/verify `HEAD` before and after collection and fail/retry once only if it changes; index/worktree are inherently live local state and dry-run reports the captured commit provenance.
- [Native file selectors depend on Odoo semantics] → Pin unit fixtures to the upstream Odoo 19 selector form and cover one disposable Odoo integration path.
- [Very large untracked sets can produce large output] → Keep bounded subprocess timeouts and output-size guards; fail actionably rather than truncate selection silently.

## Migration Plan

1. Merge or rebase the accepted MYL-55 implementation into `feat/MYL-56-changed-odoo-tests`, preserving its context/output contracts and resolving specs against the implemented surface.
2. Add characterization tests for current `module test`, then introduce the typed models and pure selectors/preflight without changing the runner.
3. Refactor the current runner once, wire the compatibility alias, then register the top-level command and shared renderers.
4. Run focused unit/integration tests, the opt-in disposable Odoo selector/run test when prerequisites are present, full `make pr`, and `openspec validate add-context-aware-odoo-tests`.
5. Commit only OpenSpec artifacts during planning. Implementation commits and PR follow separately; the PR references MYL-56 and GitHub #25/#26, and push uses the verified SSH remote.

Rollback is additive: remove the top-level command and restore the characterized legacy callback to the same pre-change runner contract. No persistent schema or database data is introduced or migrated.

## Open Questions

None. The implementation may choose private helper names, but the safety boundary, selection precedence, preflight, output fields, and exit behavior are normative in the delta specs.
