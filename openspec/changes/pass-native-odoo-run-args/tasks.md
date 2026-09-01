## 1. Characterize the Existing Foreground Boundary

- [ ] 1.1 Extend `tests/unit/test_cli_characterization.py` and `tests/unit/test_cli_security_contract.py` with failing pre-change cases for `run` delimiter parsing, exact `run_foreground_command(args=...)` delegation, port-conflict short-circuiting, use-event ordering, dry-run format/alias behavior, and normal native exit/interrupt behavior; update only the additive run-help snapshot text.
- [ ] 1.2 Extend `tests/unit/resources/test_instance_runtime.py` and `tests/unit/test_run_foreground_runtime_identity.py` with failing pre-change cases that pin generated-config-before-native-argv order, repeated/space/metacharacter element boundaries, input-list mutation after capture, recording-executor parity, inherited stdio, PID persistence/clearing, artifact-lock ordering, process-group cleanup, and real exit-code propagation.

## 2. Share and Harden Native Runtime Argument Validation

- [ ] 2.1 Replace `_FORBIDDEN_SHELL_FLAGS`/`_check_shell_overrides` in `src/odoo_instance_sdk/resources/instance.py` with one private pure runtime-argument validator that returns an unchanged tuple and is called by both foreground and interactive-shell command construction before any snapshot, prepared step, preflight, lock, secret write, identity write, or launch.
- [ ] 2.2 Define the single protected-name table for config/database selectors, database host/port/user/password/SSL mode, db filter, addons/upgrade/data paths, HTTP/gevent/longpolling bind ports, and logfile; match long names only exactly or before `=`, and short `-c`/`-d`/`-r`/`-w` aliases exactly or with attached values.
- [ ] 2.3 Add table-driven SDK tests covering every protected option in spaced and `--name=value` forms plus every short attached form, proving identical shell/foreground rejection, the offending option in a sanitized `InstanceConfigurationError`, and zero execution-side effects; add allowed/repeated `--dev`, `--log-level`, `--workers`, and `--stop-after-init` cases plus near-prefix long options that must not be falsely rejected.

## 3. Capture Native Argv in the Existing SDK Command

- [ ] 3.1 Add keyword-only `args: Sequence[str] = ()` to `OdooInstance.run_foreground()` and `run_foreground_command()`; make the convenience method delegate exactly once with `args`, and append the validated frozen tuple after `_snapshot_start_inputs(config)` generated arguments in the existing `instance.foreground` `PreparedStep`.
- [ ] 3.2 Prove with `RecordingExecutor` that one command instance exposes the same redacted ordered argv in `.plan`/`.commands` that execution consumes, never rebuilds against mutated caller input or ambient config, retains `shell=False`, inherited stdio, foreground/session flags, dependency accounting, and all planned Git identity steps.
- [ ] 3.3 Run the focused instance lifecycle, preflight, runtime-identity, foreground signal/exception cleanup, and shell regression suites; fix only regressions caused by the additive argv path and retain the existing command ledger and lifecycle implementation.

## 4. Pass Click Delimiter Arguments Through the Thin Run Adapter

- [ ] 4.1 Add variadic `click.UNPROCESSED` `odoo_args` to the existing `run` command, document the `--` delimiter in its help, and pass the exact tuple to `instance.run_foreground_command(args=...)` without CLI validation, normalization, subprocess construction, or a new `commands/run.py` module.
- [ ] 4.2 Add CLI tests proving `odcli run -- --dev=reload --log-level debug --dev=xml` preserves values/repetition/order, while `odcli run --dev=reload` exits `2` before SDK resolution; prove protected arguments fail before spawn through the SDK boundary and produce no partial machine document.
- [ ] 4.3 Add dry-run/normal parity cases for default Rich, `--format rich|json|toon`, and `--json`: every preview exposes the same captured native argv without executor activity, JSON alias parity remains exact, and normal execution remains unwrapped native stdin/stdout/stderr with Odoo exit code and interrupt `130`.

## 5. Close the GitHub #35 Architecture Gate and Documentation

- [ ] 5.1 Measure the native-argument planning functions at exact pre/post revisions using the ADR's `ast.If`/`ast.IfExp`/`ast.Match` and Expression adapter/unwrap metric; append the reproducible post-#35 row and stop-condition conclusion to `docs/adr/0002-bounded-expression-checkout-assessment.md`.
- [ ] 5.2 Extend `tests/unit/test_architecture_inventory.py` so the post-#35 assessment cannot remain pending and its revisions, counted functions, counts, inequality, and retain/remove conclusion are internally consistent; if adapters/unwraps exceed branches removed, remove Expression and its lock entry while retaining concrete typed stage signatures and startup exclusions.
- [ ] 5.3 Update README/CLI and Python SDK examples with `odcli run -- --dev=reload --log-level=debug`, `odcli run --dry-run -- --stop-after-init -u sale`, and `run_foreground(args=...)`/`run_foreground_command(args=...)`; document the protected override families and native-stream behavior without copying a full Odoo option catalog.
- [ ] 5.4 Add or extend one real-Odoo integration case in `tests/integration/test_real_odoo_lifecycle.py` that runs an allowed terminating native option such as `--stop-after-init` and asserts the actual exit result, leaving conditional external prerequisites and existing cleanup policy unchanged.

## 6. Verification and Handoff

- [ ] 6.1 Run the focused SDK/CLI/security/architecture/integration tests and the MYL-67 fresh-interpreter startup tests; record any externally unavailable real-Odoo prerequisite as an explicit skip rather than replacing it with a mock-only claim.
- [ ] 6.2 Run `uv run openspec validate pass-native-odoo-run-args --type change --strict --json`, `git diff --check origin/main...HEAD`, Ruff check/format check, strict mypy, and the repository's `make pr` gate; resolve every failure within scope.
- [ ] 6.3 Confirm the final diff contains no second runner, output adapter, full Odoo parser, direct subprocess launch, production `Any`/bare `object`, or unrelated feature scope; push `feat/MYL-69-native-run-args` through the configured SSH remote and report its commit SHA, verification results, and explicit native-argv plus TTY plan/execution parity evidence.
