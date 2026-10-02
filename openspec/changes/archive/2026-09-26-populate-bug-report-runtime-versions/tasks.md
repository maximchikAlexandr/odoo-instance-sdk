## 1. Capture managed-project version probes

- [x] 1.1 Add one process-free current-project snapshot that uses a single strict local Git-marker parser, treats the first `.git` entry as the inclusive manifest boundary even when invalid, requires an existing valid marker/target and exact catalog worktree/common-dir identity, preserves existing `git_common_dir()` caller compatibility, rejects stale or ambiguous identity, avoids command-backed project resolution, and replaces the constant version placeholders with strict normalization that returns `unknown` on unavailable or invalid input.
- [x] 1.2 Build the optional 5-second read-only Odoo `--version` `PreparedStep` from the existing project Python/Odoo runtime resolution, including deferred uv selectors, and reject output above 16 KiB before parsing.
- [x] 1.3 Reuse the existing PostgreSQL server-summary plan and collector with a shared deadline of at most 10 seconds, preserving its private credential/redaction boundary and never starting a service.

## 2. Integrate discovery with bug-report initialization

- [x] 2.1 Compose the optional probe steps and existing draft action into one immutable `bug_report_init_command()` plan while preserving its public signature, result model, fingerprint, and dry-run behavior.
- [x] 2.2 Execute the two providers independently, account for every skipped optional step, pass only normalized values into `_report_template()`, and keep draft-write failures distinct from best-effort discovery failures.

## 3. Prove behavior and safety

- [x] 3.1 Add a parametrized unit matrix for valid, unavailable, non-zero, timed-out, oversized, multiline, and unsafe version responses, including independent fallback for each provider.
- [x] 3.2 Add command-plan/executor tests proving all child processes are captured, bounded, read-only, `shell=False`, fully accounted, and free of credentials or raw failure output in public projections and draft content; include a spawn trap that fails on any `execute()` or `spawn()` during `bug_report_init_command()` construction while asserting the complete applicable plan, plus regressions rejecting catalog rows with missing/stale markers and parent manifests above a nested unrelated repository boundary.
- [x] 3.3 Add a public `CliRunner` regression from a configured managed-project fixture that writes both concrete versions, plus outside-project and dry-run regressions; the dry-run test SHALL use the same spawn trap and prove zero process execution, zero mutation, and complete applicable preview.

## 4. Repository verification

- [x] 4.1 Run the focused bug-report tests, CLI output-boundary tests, Ruff, mypy, and the repository's reproducible core gate; resolve any regressions without changing the specified public contract.
