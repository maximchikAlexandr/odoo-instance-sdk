## MODIFIED Requirements

### Requirement: `odcli run`

```bash
odcli run
odcli --env <environment-id> run
odcli run -- --dev=reload --log-level=debug
odcli run --dry-run -- --stop-after-init -u sale
```

The command SHALL:

1. Accept zero or more unprocessed Odoo arguments only after the Click `--` delimiter. It SHALL preserve each argument value, repetition, and order and SHALL not interpret or reconstruct Odoo options.
2. Call the existing shared `ready_instance()` contract, which resolves a ready environment, validates its worktree/config plus recorded Python and Odoo entry point without invoking `sync_python`, creates the instance through `client.instance.from_environment(environment)`, and returns client, environment, and instance.
3. Check the bound port through standard-library `socket.bind((http_interface, http_port))`; when occupied, perform only the existing observational HTTP health check for diagnostics.
4. On an occupied port, return deterministic `port-conflict`/ownership-unknown without changing generated config, updating use metadata, validating into a command, or launching a second process.
5. After a free-port preflight, call the instance already returned by `ready_instance()` through `instance.run_foreground_command(args=<exact delimiter args>)` exactly once.
6. For normal execution only, after command capture and before execution, update `last_used_at` and the generic `use/succeeded` event exactly once. Dry-run SHALL NOT update use metadata.
7. For normal execution, run that command with native inherited stdin/stdout/stderr and return the Odoo exit code without a Rich/JSON/TOON document wrapper.
8. For `--dry-run`, emit the same command's bounded plan through the existing Rich/JSON/TOON output boundary without invoking `.run()`; the plan SHALL contain the exact validated native argv in its original order.
9. On Ctrl+C, rely on the foreground command to stop only the process group created by that call and exit `130`.

The CLI SHALL use a run-specific Click command boundary that inspects the raw argument list before Click discards the `--` marker and rejects every non-empty variadic `odoo_args` tuple unless a literal `--` preceded it. The CLI SHALL not change or bypass shared `ready_instance()`, duplicate the SDK protected-override validator, construct subprocess argv, acquire the artifact lock, or rebuild the command between preview and execution. `--format`/`--json` SHALL retain their existing rule that they are accepted for `run` only with `--dry-run` and fail with Click exit `2` before SDK resolution otherwise.

#### Scenario: Port conflict deterministic error

- **WHEN** `odcli run -- --dev=reload` finds the bound port occupied
- **THEN** `ready_instance()` has already completed SDK/environment resolution and instance creation, but the command returns `port-conflict`/ownership-unknown with no foreground command construction, use update, config change, or process launch

#### Scenario: Free port starts Odoo

- **WHEN** `odcli run -- --dev=reload --log-level debug --dev=xml` finds the port free
- **THEN** it uses the instance returned by `ready_instance()`, captures `run_foreground_command(args=("--dev=reload", "--log-level", "debug", "--dev=xml"))` once, and then records use once before execution
- **AND** normal execution returns the foreground Odoo exit code on native streams

#### Scenario: Delimiter is required for native arguments

- **WHEN** a caller invokes `odcli run --dev=reload` without the `--` delimiter
- **THEN** Click reports an unknown-option usage error with exit code `2`
- **AND** no SDK resolution or process launch occurs

#### Scenario: Bare positional input is rejected

- **WHEN** a caller invokes `odcli run sale` without a literal `--`
- **THEN** the run-specific Click boundary reports a usage error with exit code `2`
- **AND** no SDK resolution, use update, command construction, or process launch occurs

#### Scenario: Protected override is rejected before spawn

- **WHEN** `odcli run -- --database other` or another protected runtime-identity override is invoked
- **THEN** the SDK validator returns a sanitized error before command execution and no child process starts

#### Scenario: Dry-run and execution use one captured argv

- **WHEN** the same allowed delimiter args are supplied to dry-run and normal execution under a recording executor
- **THEN** dry-run displays their exact ordered elements in the captured foreground `ProcessStep`
- **AND** normal execution consumes that captured step without reconstructing argv
- **AND** dry-run performs no use-metadata write, while normal execution records use once between command capture and execution

#### Scenario: Native TTY and exit behavior remain unchanged

- **WHEN** `odcli run -- --workers=2` executes normally, writes to inherited streams, exits non-zero, or is interrupted
- **THEN** stdin/stdout/stderr remain native, the real exit code is returned, and interrupt cleanup preserves exit `130`
