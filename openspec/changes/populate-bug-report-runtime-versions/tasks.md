## 1. Capture managed-project version probes

- [ ] 1.1 Replace the constant version placeholders with one current-project snapshot and strict helpers that normalize only safe Odoo/PostgreSQL version strings, returning `unknown` on unavailable or invalid input.
- [ ] 1.2 Build the optional 5-second read-only Odoo `--version` `PreparedStep` from the existing project Python/Odoo runtime resolution, including deferred uv selectors, and reject output above 16 KiB before parsing.
- [ ] 1.3 Reuse the existing PostgreSQL server-summary plan and collector with a shared deadline of at most 10 seconds, preserving its private credential/redaction boundary and never starting a service.

## 2. Integrate discovery with bug-report initialization

- [ ] 2.1 Compose the optional probe steps and existing draft action into one immutable `bug_report_init_command()` plan while preserving its public signature, result model, fingerprint, and dry-run behavior.
- [ ] 2.2 Execute the two providers independently, account for every skipped optional step, pass only normalized values into `_report_template()`, and keep draft-write failures distinct from best-effort discovery failures.

## 3. Prove behavior and safety

- [ ] 3.1 Add a parametrized unit matrix for valid, unavailable, non-zero, timed-out, oversized, multiline, and unsafe version responses, including independent fallback for each provider.
- [ ] 3.2 Add command-plan/executor tests proving all child processes are captured, bounded, read-only, `shell=False`, fully accounted, and free of credentials or raw failure output in public projections and draft content.
- [ ] 3.3 Add a public `CliRunner` regression from a configured managed-project fixture that writes both concrete versions, plus outside-project and dry-run regressions proving `unknown` fallback and zero execution/mutation.

## 4. Repository verification

- [ ] 4.1 Run the focused bug-report tests, CLI output-boundary tests, Ruff, mypy, and the repository's reproducible core gate; resolve any regressions without changing the specified public contract.
