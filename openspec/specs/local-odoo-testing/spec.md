# local-odoo-testing Specification

## Purpose
TBD - created by archiving change add-context-aware-odoo-tests. Update Purpose after archive.
## Requirements
### Requirement: Minimal typed Odoo test operation

The SDK SHALL expose transport-neutral frozen typed values with this minimum contract:

```python
OdooTestSpec(
    modules: tuple[str, ...],
    test_tags: str,
    reload_tests: bool = False,
    allow_empty: bool = False,
)

OdooTestResult(
    counts: dict[str, int],
    failures: bool,
    zero_tests: bool,
    exit_code: int,
)
```

`counts` SHALL contain integer `tests`, `successful`, `failed`, `errors`, and `skipped` entries. Modules SHALL be non-empty, sorted, and unique; `test_tags` SHALL be a non-empty native Odoo selector string. The operation SHALL call Odoo 19 `odoo.tests.shell.run_tests(env, test_tags=..., modules=..., reload_tests=...)` exactly once and SHALL NOT add a second runner, `TestResource`, test service/repository, scheduler, catalog, or selector DSL.

The runner process SHALL execute in threaded mode (`workers=0`) with the existing environment-bound HTTP interface/port and the existing environment operation lock. It SHALL not commit application data.

#### Scenario: One spec invokes one existing runner

- **WHEN** a valid spec for modules `sale` and `stock` is executed
- **THEN** the existing Odoo shell path invokes `odoo.tests.shell.run_tests()` once with the sorted module tuple and unchanged `test_tags` and `reload_tests` values

#### Scenario: Invalid empty spec is rejected

- **WHEN** a caller constructs or executes a spec with no modules or blank `test_tags`
- **THEN** validation fails before an Odoo process is spawned

### Requirement: Configured worktree-local addon boundary

Eligible addon roots SHALL come only from the selected ready environment's generated Odoo `addons_path`. A root is eligible only when its canonical path is an existing directory contained by the canonical registered worktree. External roots, missing roots, and roots whose symlink resolution escapes the worktree SHALL never supply a selected module.

An addon module SHALL be identified by the nearest ancestor containing a regular non-symlink `__manifest__.py`, and that module directory and manifest SHALL remain canonically inside one eligible addon root and the registered worktree. Bare module selection SHALL use a valid Python/Odoo module directory name and SHALL resolve to exactly one eligible manifest. Duplicate module names across eligible roots, symlinked module directories/manifests, path traversal, and paths outside eligible roots SHALL fail before preflight or Odoo execution.

#### Scenario: Duplicate module name is ambiguous

- **WHEN** two eligible addon roots each contain a regular `sale/__manifest__.py` and the target is `sale`
- **THEN** selection fails with both safe candidate roots and no preflight or Odoo process runs

#### Scenario: External configured root cannot supply a target

- **WHEN** `addons_path` includes a directory outside the registered worktree and a target resolves only there
- **THEN** the target is rejected as external before an Odoo process runs

#### Scenario: Symlink escape is rejected

- **WHEN** a candidate module, manifest, or explicit test file reaches outside the eligible root or worktree through a symlink
- **THEN** selection fails without reading or executing the escaped target

### Requirement: Context, module, and test-file selection

The operation selector SHALL support exactly one optional explicit target. With no target, it SHALL walk from the current directory upward only within eligible addon roots and select the nearest safe addon manifest. A current directory that is the project/worktree root, lies outside every eligible addon, or yields no unambiguous addon SHALL produce an actionable error and run nothing.

A bare target SHALL resolve as one exact addon module name across eligible roots. A path target SHALL resolve relative to the caller's current directory unless absolute, and SHALL be accepted only when it is an existing regular non-symlink Python file named `test_*.py` canonically beneath the selected module's `tests/` directory. Directories, `tests/__init__.py`, non-Python files, missing files, and files outside `<module>/tests/` SHALL be rejected.

Without `--tags`, module/cwd selection SHALL derive the native Odoo selector `/<module>`, file selection SHALL derive the native worktree-relative selector `/<module>/tests/<path>`, and executable changed selection SHALL derive one native selector by mapping each sorted unique selected module to `/<module>` and joining those tokens with a comma and no whitespace. Thus modules `sale`, `sale`, and `stock`, after sorting and deduplication, produce exactly `/sale,/stock`. Explicit `--tags` SHALL replace this default construction and be passed byte-for-byte as native Odoo `[-][tag][/module][:class][.method]` grammar; it SHALL be valid with module/cwd/changed selection and SHALL NOT be split, normalized, sorted, deduplicated, or rewritten. A file target combined with `--tags` SHALL be a usage error; callers needing class/method selection SHALL use the module target with native `--tags` rather than a custom syntax.

#### Scenario: Cwd selects nearest addon

- **WHEN** `odcli test` runs from `addons/sale/tests/` and `addons/sale/__manifest__.py` is inside an eligible root
- **THEN** it selects module `sale` with default native tag `/sale`

#### Scenario: Explicit test file derives native path selector

- **WHEN** `odcli test test_sale_order.py` runs from `addons/sale/tests/`
- **THEN** it selects only module `sale` and derives `/sale/tests/test_sale_order.py` for the existing Odoo tag selector

#### Scenario: Project root is not guessed

- **WHEN** `odcli test` runs at the registered worktree root without a target
- **THEN** it returns an actionable instruction to pass a module/file or change into an addon and starts no preflight or Odoo process

#### Scenario: Native class and method selector is unchanged

- **WHEN** `odcli test sale --tags ':TestSaleOrder.test_confirm'` runs
- **THEN** the exact string `:TestSaleOrder.test_confirm` reaches `OdooTestSpec.test_tags` without parsing or rewriting by a custom DSL

#### Scenario: Changed modules derive one deterministic native selector

- **WHEN** changed paths resolve, in discovery order and with repetition, to modules `stock`, `sale`, and `stock`, and `--tags` is absent
- **THEN** selection builds one `OdooTestSpec` with modules `("sale", "stock")` and `test_tags="/sale,/stock"`

#### Scenario: Explicit changed tags remain byte-for-byte unchanged

- **WHEN** `odcli test --changed --tags ' standard,/stock,-slow '` selects modules `sale` and `stock`
- **THEN** the exact string ` standard,/stock,-slow ` reaches `OdooTestSpec.test_tags` without trimming, splitting, sorting, deduplication, or rewriting

### Requirement: Read-only database and installed-module preflight

After selection and before the Odoo shell process, the operation SHALL confirm that the selected environment binds exactly one database, that the database exists, and that every selected module has `state='installed'`. The preflight SHALL use the existing local PostgreSQL transport extended only as needed for a bounded read-only query against the bound database; it SHALL use argv execution with `shell=False`, a timeout, and sanitized failures.

If PostgreSQL tooling/authentication is unavailable, the database is missing or ambiguous, the query fails, or any selected module is absent/not installed, the operation SHALL fail actionably and SHALL NOT spawn Odoo. The operation SHALL NOT install, update, restore, create, drop, or otherwise mutate a database or module state.

`--dry-run` and a changed selection with no addon changes SHALL stop before this database/module preflight because neither path can execute tests.

#### Scenario: Uninstalled module stops before Odoo

- **WHEN** the selected database exists but module `sale` is absent or not in installed state
- **THEN** preflight identifies `sale`, exits non-zero, performs no mutation, and never starts the Odoo shell

#### Scenario: Ambiguous database stops before Odoo

- **WHEN** the environment configuration binds zero or more than one database name
- **THEN** preflight fails with an actionable database-selection error and no Odoo process runs

#### Scenario: Preflight is read-only

- **WHEN** database and installed-module checks succeed
- **THEN** only bounded read-only PostgreSQL queries occurred before the one test process and no install/update/restore statement or API was called

### Requirement: Changed-addon Git selection

Changed selection SHALL be enabled only by `--changed` and SHALL be incompatible with an explicit target. The baseline SHALL resolve in this order: explicit `--base REF`, then the selected environment's recorded non-empty `base_ref` when it is not the literal `HEAD`; otherwise selection SHALL fail with an actionable `--base` instruction. The implementation SHALL NOT hardcode `main`, substitute `HEAD` as the baseline, fetch, pull, contact a remote, or mutate Git state.

Git SHALL verify the baseline and `HEAD`, compute `merge-base(BASE, HEAD)`, and union paths from all four states:

- committed changes from merge-base through `HEAD`;
- staged changes against `HEAD`;
- unstaged changes against the index;
- untracked non-ignored files.

Every Git call SHALL use an argv list with `shell=False`, a bounded timeout, and NUL-delimited path output. Diff collection SHALL disable rename detection so both sides of a rename are represented as delete/add paths. Paths SHALL be decoded with the platform filesystem encoding, normalized as worktree-relative paths without interpreting shell metacharacters, then sorted and deduplicated deterministically.

Each changed path SHALL map to its nearest safe addon manifest inside an eligible root. Paths outside eligible addon roots SHALL be reported as `ignored_paths`. Paths lexically inside an eligible root that do not map safely to a manifest, escape canonically, or encounter an unsafe symlink SHALL be reported as `unmapped_paths` and make selection fail rather than being silently skipped. The module result SHALL contain only sorted unique directly changed addons; reverse dependants SHALL NOT be computed.

#### Scenario: All local Git states are unioned

- **WHEN** a temporary repository has one committed-since-base addon change, one staged addon change, one unstaged addon change, and one untracked non-ignored addon file
- **THEN** all four files appear once in deterministic `changed_files` and their directly containing addons appear once in sorted `modules`

#### Scenario: Renamed addon paths retain both sides

- **WHEN** a tracked file is renamed from one addon to another
- **THEN** rename detection is disabled for selection, both old and new paths are considered, and both direct addons are selected when each maps safely

#### Scenario: Docs-only diff is a successful no-op

- **WHEN** every changed path is outside eligible addon roots
- **THEN** selection returns success reason `no_addon_changes`, reports those paths as ignored, performs no database preflight, and starts no Odoo process

#### Scenario: Unsafe path under addons root is not skipped

- **WHEN** a changed path is lexically under an eligible addon root but has no safe containing manifest or escapes through a symlink
- **THEN** it appears in `unmapped_paths`, selection exits non-zero, and no Odoo process runs

### Requirement: Dry-run provenance

`--dry-run` SHALL be accepted only with `--changed`. It SHALL run environment/addon-boundary and Git selection, then return without database preflight or Odoo execution. Its typed machine result SHALL contain the selected environment identity/worktree, base source (`explicit` or `environment`), requested base, resolved base commit, merge-base commit, `HEAD` commit, sorted changed files, sorted modules, ignored paths, and unmapped paths.

A safe selection SHALL exit `0`, including `no_addon_changes`. A Git/base failure or any non-empty unsafe `unmapped_paths` SHALL return the available sanitized provenance and exit non-zero. Rich, JSON, and TOON SHALL represent the same values.

#### Scenario: Dry-run has no execution side effects

- **WHEN** `odcli test --changed --base origin/dev --dry-run` resolves two safe addons
- **THEN** it reports base/merge-base/HEAD, paths, modules, and provenance identically across decoded formats without preflight, Odoo, fetch, pull, or Git mutation

#### Scenario: Dry-run reports unsafe mapping and fails

- **WHEN** dry-run encounters an unmapped path inside an eligible addon root
- **THEN** the result includes that path, exits non-zero, and does not silently downgrade it to an ignored path

### Requirement: Result, diagnostics, and exit semantics

The test operation SHALL derive `OdooTestResult` only from the native runner report and process outcome, not by matching words or regular expressions in logs. `failures` SHALL be true when native failed or error counts are non-zero. `zero_tests` SHALL be true when the native test count is zero. `exit_code` SHALL be non-zero for runner/process failure, failed/error tests, and zero tests unless `allow_empty=True`; `allow_empty` SHALL affect only the zero-test case.

Sanitized native Odoo failure diagnostics SHALL remain a stderr stream and SHALL NOT be inserted unsanitized into the typed result or machine stdout document. Renderer choice SHALL not change selection, preflight, runner invocation, result fields, or exit code.

The command result SHALL use exactly these state-dependent shapes:

| State | Required common fields | Required state fields | Execution-only fields |
| --- | --- | --- | --- |
| `executed` | resolved environment identity/worktree, selector kind/value/provenance, modules, `exit_code` | effective `test_tags`, `reload_tests`, `allow_empty` | `counts`, `failures`, and `zero_tests` SHALL be present and SHALL come from `OdooTestResult` |
| `dry_run` | resolved environment identity/worktree, selector kind/value/provenance, modules, `exit_code=0` | `dry_run=true` and complete base/Git provenance | `test_tags`, `reload_tests`, `allow_empty`, `counts`, `failures`, and `zero_tests` SHALL be absent |
| `no_addon_changes` | resolved environment identity/worktree, selector kind/value/provenance, empty `modules`, `exit_code=0` | `reason="no_addon_changes"` and complete base/Git provenance | `test_tags`, `reload_tests`, `allow_empty`, `counts`, `failures`, and `zero_tests` SHALL be absent |

`dry_run` with no addon changes SHALL use the `no_addon_changes` shape and MAY additionally contain `dry_run=true`. These non-executed success states SHALL NOT construct an `OdooTestSpec` or `OdooTestResult`, synthesize zero counts or false flags, emit Odoo diagnostics, or display execution progress. JSON and strict-decoded TOON SHALL be semantically equal for every state.

#### Scenario: Non-executed success does not fabricate a result

- **WHEN** a safe changed dry-run selects addons, or a non-dry changed selection finds no addon changes
- **THEN** its machine result follows the corresponding state shape, exits `0`, omits every execution-only field, and emits no runner progress or Odoo diagnostics

#### Scenario: Zero tests requires explicit allowance

- **WHEN** the native runner reports zero tests
- **THEN** the result has `zero_tests=true` and a non-zero exit code unless `allow_empty=true`, in which case it exits `0`

#### Scenario: Test failures use native counts

- **WHEN** the native report contains one failure or error while the shell process itself returns zero
- **THEN** `failures=true`, the corresponding count is preserved, the command exits non-zero, and sanitized native diagnostics remain on stderr

#### Scenario: Renderer does not infer success from logs

- **WHEN** diagnostics contain success- or failure-like words inconsistent with the native report
- **THEN** counts and exit code follow the native report/process status and no regex log gate changes them
