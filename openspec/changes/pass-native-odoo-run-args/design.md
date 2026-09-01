## Context

At `origin/main` commit `ea24ac9`, MYL-68 has merged the shared immutable `Command[T]`, `ExecutionPlan`, `PreparedStep`, `internal/proc`, bounded dry-run, and typed output contracts. `OdooInstance.run_foreground_command()` already captures one foreground step with inherited stdio, dependency steps, artifact locking, generated secret-config cleanup, runtime identity persistence, process-group cleanup, and exact exit handling. It currently accepts no native Odoo argv. `shell_command()` already accepts unprocessed argv but protects only config/database flags through `_check_shell_overrides`.

The Click `run` adapter already builds one command for both `run_or_preview` paths and keeps normal execution native while dry-run uses Rich/JSON/TOON. The smallest safe slice is therefore to extend the existing SDK command and validator, not add a runner, output path, or Odoo option parser.

## Goals / Non-Goals

**Goals:**

- Pass delimiter-separated native Odoo argv through CLI and SDK without changing boundaries or order.
- Fail closed on arguments that can replace the generated environment identity or managed runtime paths.
- Preserve one captured foreground step for preview and execution plus all existing TTY, signal, PID, lock, cleanup, and exit behavior.
- Complete the mandatory post-#35 Expression measurement with reproducible evidence.

**Non-Goals:**

- Model or validate every Odoo option, add an Odoo option registry, or normalize allowed values.
- Permit config/database/credential/path/bind/log overrides.
- Change generated configuration, shell semantics, process supervision, output envelopes, or context resolution.
- Add a generic argv-policy abstraction, new dependency, or second process/output/preview path.

## Decisions

### 1. Generalize the existing shell validator into one small runtime-argument boundary

Replace the shell-specific name with one private pure function that freezes `Sequence[str]` to `tuple[str, ...]`, scans each token once, raises `InstanceConfigurationError` for a protected name, and otherwise returns the tuple unchanged. Both `shell_command()` and `run_foreground_command()` call it before snapshot or `PreparedStep` construction.

The deny set is limited to environment identity and managed resources: config/database selectors, database connection and credentials, addons/upgrade/data paths, HTTP/gevent/longpolling bind ports, and logfile. For a token beginning `--`, validation isolates the option name before the first `=` and rejects it when it is an exact protected long name or a non-empty proper prefix of any protected long name. This deliberately fails closed for unique abbreviations that Odoo's `optparse` may resolve (for example `--datab` to `--database`), even when a short prefix is ambiguous or unknown to a particular Odoo version. A longer near-prefix such as `--database-extra` is not an abbreviation and is not rejected by this rule. Short aliases match exact or attached values. Everything else is passed through rather than being maintained in a second allowlist.

Alternative considered: reproduce Odoo's ambiguity resolution or maintain an allowed-option catalog. Rejected because either will drift across Odoo versions and is explicitly outside GitHub #35; conservatively denying every protected-name prefix is the version-independent fail-closed boundary.

### 2. Capture native argv in the existing foreground command

Add keyword-only `args: Sequence[str] = ()` to `run_foreground()` and `run_foreground_command()`. The convenience method delegates once. Command construction validates and freezes the sequence, then builds the existing argv as:

```text
resolved executable prefix + generated config arguments + native arguments
```

The resulting tuple is stored only in the existing foreground `PreparedStep`. The command callback continues to consume that step by `step_id`; it never sees the caller's sequence and cannot rebuild argv. Existing public redaction projects the captured step for dry-run. Mutating the original list or ambient config after construction cannot change the command.

Alternative considered: append CLI arguments immediately before spawn. Rejected because preview would not prove execution parity and it would violate the shared command ledger.

### 3. Keep Click as delimiter parsing and delegation only

Give `run` a variadic `click.UNPROCESSED` argument plus a small run-specific `click.Command` subclass. Its `parse_args(ctx, args)` override snapshots the raw token list, delegates normal option parsing to Click, and, when the parsed variadic tuple is non-empty, raises `click.UsageError` unless the raw list contained a literal `--` before the first passthrough token. This preserves Click's standard unknown-option handling while rejecting bare positionals such as `odcli run sale`; after the guard, the callback passes Click's ordered tuple to `run_foreground_command(args=...)` without validation or normalization.

The existing `run_or_preview(lambda: command, emit_normal=False)` stays unchanged. After ready-instance resolution, port preflight runs first and a conflict returns before command construction. On a free port the callback captures `run_foreground_command(args=...)` exactly once. Dry-run then previews that command without `record_use`; normal execution calls `record_use` once after command capture and before `run_or_preview` executes it. Normal execution inherits native streams and exits with the command's integer result; dry-run emits the captured plan through the existing Rich/JSON/TOON boundary.

Alternative considered: create `commands/run.py` solely for this feature. Rejected because the current callback is short and a move would add churn without isolating a new responsibility.

### 4. Preserve lifecycle by changing only command input

Dependency preparation, the artifact lock, generated secret-config creation/removal, `context.spawn`, runtime identity persistence/clearing, `wait_foreground_process`, and TERM/KILL/reap paths remain the current code. Tests assert these paths receive the new argv and retain their ordering rather than reimplementing them.

The real integration case uses an allowed terminating native flag such as `--stop-after-init`, so it proves the argument reaches Odoo without leaving a persistent service. TTY and signal behavior remain covered by focused recording/characterization tests and the existing foreground integration suites.

Alternative considered: create a specialized native-run executor. Rejected because `internal/proc` already owns foreground inherited-stdio execution.

### 5. Close the post-#35 Expression gate in the existing ADR

Update `docs/adr/0002-bounded-expression-checkout-assessment.md` with exact before/after revisions, the native-argument planning functions counted, conditional-node counts, Expression adapter/unwrap count, and the stop-condition result. Extend the architecture test so a pending post-#35 assessment fails. This slice should need no Expression adapter; the recorded result, not that expectation, decides whether Expression remains.

Alternative considered: treat the preliminary checkout measurement as sufficient because this validator is small. Rejected because AGENTS.md, the main packaging spec, and GitHub #45 make the post-#35 check mandatory.

## Risks / Trade-offs

- [A protected Odoo synonym is missed] → Keep one explicit security-focused protected-name table with tests for exact, equals, spaced, abbreviated-long, and short-attached spellings; fail closed when new binding flags are identified.
- [A protected-name prefix is ambiguous or unknown in one Odoo version] → Reject it conservatively; only longer near-prefixes that cannot abbreviate a protected name bypass this boundary.
- [CLI preview differs from execution] → Assert public plan argv against redacted recording-executor inputs from the same `Command` instance.
- [Caller mutates an input list after planning] → Freeze before validation and step construction; test mutation after command creation.
- [Native output is accidentally wrapped] → Retain `emit_normal=False` and existing raw-stream characterization while adding argv assertions.
- [The Expression gate becomes subjective] → Record AST node and boundary-operation counts with revisions and enforce completion through the existing architecture test.

## Migration Plan

1. Add characterization tests for CLI delimiter behavior, the current foreground lifecycle, and shared protected options.
2. Generalize the validator and extend the two foreground SDK signatures; keep all lifecycle code in place.
3. Pass Click's unprocessed delimiter tuple into the SDK command and add plan/run/TTY/exit parity tests.
4. Update CLI/SDK documentation and the Expression ADR plus its enforcement test.
5. Run focused unit and real-Odoo integration coverage, startup boundaries, strict OpenSpec validation, Ruff, strict mypy, and the repository PR gate.

Rollback is a normal branch revert: the change is additive and introduces no persisted schema or data migration.

## Open Questions

None. GitHub #35 and the merged #40/#45 contracts fix the public syntax, protected boundary, command parity, native transport, and assessment requirement.
