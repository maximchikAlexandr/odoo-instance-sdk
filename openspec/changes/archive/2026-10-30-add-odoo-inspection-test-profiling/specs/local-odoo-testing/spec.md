## ADDED Requirements

### Requirement: Opt-in native Odoo test profile

`OdooTestSpec` SHALL add `profile: bool = False`. The existing command and runner-source path SHALL invoke Odoo 19 `odoo.tests.shell.run_tests()` exactly once. When enabled, the same result's `_r.stats` SHALL be the sole source of per-test duration and SQL query counts. The implementation SHALL NOT parse logs, add another runner, or invoke tests twice.

#### Scenario: Profile uses the existing native result

- **WHEN** a profiled test run completes with native stats for two tests
- **THEN** one existing runner invocation supplies both the existing count result and the two profile entries
- **AND** no log regex, second test invocation, or custom timing/query counter is used

#### Scenario: Ordinary run does not collect profile output

- **WHEN** the same test spec executes with `profile=false`
- **THEN** the runner preserves its ordinary count/failure/zero/exit behavior and returns no profile value

### Requirement: Frozen validated profile result

The SDK SHALL expose frozen, unknown-field-forbidden profile entry and aggregate values, and `OdooTestResult` SHALL carry a nullable profile. IDs SHALL be non-empty; durations SHALL be finite and non-negative; query counts SHALL be non-negative integers. Entries SHALL sort by descending duration then test ID, with totals derived from entries. Invalid requested stats SHALL fail without fallback or fabricated values.

#### Scenario: Entries and totals are deterministic

- **WHEN** native stats contain equal-duration tests `test_b` and `test_a` plus a slower `test_c`
- **THEN** entries are ordered `test_c`, `test_a`, `test_b` and aggregate duration/query totals equal the sums of those validated entries

#### Scenario: Invalid native stat fails closed

- **WHEN** a requested profile contains a blank test ID, negative/non-finite duration, non-integer query count, or an invalid stats container
- **THEN** result conversion fails with a sanitized actionable error and emits no fabricated profile

### Requirement: Profile preserves test behavior

Profiling SHALL NOT change target, cwd, native tags, `--changed`, explicit base, module selection, `reload_tests`, `allow_empty`, installed-module preflight, port reservation, process locking, native diagnostics, failure detection, zero-test behavior, or exit-code rules. The existing immutable test command SHALL capture the exact profile-aware fixed runner source; preview and execution SHALL use that one command.

#### Scenario: Changed selection profiles the selected tests

- **WHEN** `odcli test --changed --profile` selects installed modules and executes
- **THEN** it uses the same deterministic changed selection and one existing test command, adding profile data from the selected native run only

#### Scenario: Profile does not mask failures

- **WHEN** a profiled native run reports a failed/error test or a process failure
- **THEN** existing failure diagnostics and non-zero exit semantics remain unchanged while valid profile data is included only when conversion completed safely

#### Scenario: Profile dry-run reuses the plan

- **WHEN** `odcli test --changed --profile --dry-run` resolves an executable selection
- **THEN** the existing test command plan shows the exact profile-aware Odoo invocation and fixed runner script, starts no test process, and creates no second planner
