## MODIFIED Requirements

### Requirement: `OdooInstance.run_foreground()`

`OdooInstance.run_foreground(config: StartConfig | None = None, *, args: Sequence[str] = (), cwd=None, env=None) -> int` SHALL delegate exactly once to `run_foreground_command(config, args=args, cwd=cwd, env=env).run()`. `run_foreground_command()` SHALL expose the same keyword-only `args` parameter and SHALL:

- use `self.config.start_config` when `config is None`; when both are absent, raise `InstanceConfigurationError`;
- use the same resolved command prefix, generated config arguments, dependency preflight, artifact lock, process-group lifecycle, and cleanup as `start()`/`stop()`;
- freeze the caller-supplied sequence as an ordered tuple during command construction, validate it once through the same runtime-argument validator used by `shell_command()`, and append it after the generated config arguments in the single captured foreground `ProcessStep`;
- preserve each allowed argument as one argv element without shell interpolation, normalization, deduplication, reordering, or reconstruction during `.run()`;
- reject protected environment-binding overrides in exact spaced, long `--name=value`, Odoo-recognizable abbreviated-long, and attached short forms before creating the foreground step or spawning a child. The protected names SHALL be `-c`/`--config`, `-d`/`--database`, `--db-filter`, `-r`/`--db_user`, `-w`/`--db_password`, `--db_host`, `--db_port`, `--db_sslmode`, `--addons-path`, `--upgrade-path`, `--data-dir`, `--http-interface`, `--http-port`, `--gevent-port`, `--longpolling-port`, and `--logfile`;
- allow other native runtime arguments, including repeated `--dev`, `--log-level`, `--workers`, and `--stop-after-init` values;
- inherit stdin/stdout/stderr so live Odoo output remains native and unbuffered, block until Odoo exits, and return its actual exit code;
- stop the owned process group correctly on Ctrl+C.

For every token beginning `--`, the validator SHALL compare the option-name portion before the first `=` with the protected long names. It SHALL reject an exact match and every non-empty proper prefix of a protected name, regardless of whether that prefix is ambiguous or unknown in the installed Odoo version, so Odoo `optparse` abbreviation cannot bypass the boundary. It SHALL not reject a longer near-prefix that no protected name starts with. It SHALL reject an exact short protected name or its attached value. It SHALL not implement or duplicate the complete Odoo option parser. `shell_command()` SHALL retain its subcommand placement (`... generated-config-args shell <args>`) while using this expanded shared protected-name boundary.

After spawn, only an instance bound through `from_environment()` SHALL persist current runtime identity in `environment_runtime` (`root_pid`, `create_time`, `started_at`, branch/commit, `http_url`/`http_port`, `database_name`). `run_foreground()` SHALL clear that identity best-effort in `finally`. Manual instances SHALL not persist it; `shell()`/`run_shell_script()`/`start()`/`stop()` SHALL not persist it.

#### Scenario: Foreground run with explicit config

- **WHEN** `instance.run_foreground(config=cfg)` runs and Odoo exits with code `0`
- **THEN** the method returns `0` and clears runtime identity in `finally`

#### Scenario: Foreground run uses start config

- **WHEN** `instance.run_foreground()` is called on an instance created through `from_environment()` with a bound start config
- **THEN** the captured command uses `self.config.start_config`

#### Scenario: Foreground run has no start config

- **WHEN** `instance.run_foreground()` is called with `config=None` and `self.config.start_config is None`
- **THEN** it raises `InstanceConfigurationError` before child-process launch

#### Scenario: Allowed native arguments preserve boundaries and order

- **WHEN** `run_foreground_command(args=("--dev=reload", "--log-level", "debug", "--dev=xml", "--stop-after-init"))` is constructed
- **THEN** its foreground `ProcessStep.argv` contains those five exact elements, in that order, after the generated config arguments
- **AND** a recording executor receives the identical captured private argv when `.run()` executes

#### Scenario: Mutable caller input changes after capture

- **WHEN** a list passed as `args` is changed after `run_foreground_command()` returns
- **THEN** `.plan`, `.commands`, and the argv consumed by `.run()` remain unchanged

#### Scenario: Protected overrides fail closed

- **WHEN** native args contain any protected name in spaced form, `--name=value` form, or attached short form such as `-cPATH`, `-dDB`, `-rUSER`, or `-wSECRET`
- **THEN** command construction raises `InstanceConfigurationError` identifying the offending option
- **AND** no dependency preflight, artifact lock, secret-config write, runtime identity write, or child-process launch occurs

#### Scenario: Protected long-option abbreviation cannot bypass validation

- **WHEN** native args contain `--datab other`, `--datab=other`, or any other non-empty proper prefix of a protected long option
- **THEN** command construction raises `InstanceConfigurationError` before a foreground step or side effect exists
- **AND** a longer token such as `--database-extra` is not treated as an abbreviation of `--database`

#### Scenario: Shell and foreground share the protected boundary

- **WHEN** the same protected addons, data, database-connection, HTTP bind/port, or logfile override is passed to `shell_command(args=...)` or `run_foreground_command(args=...)`
- **THEN** both operations reject it through the same validator and neither constructs a process step

#### Scenario: Ctrl+C stops process group

- **WHEN** `instance.run_foreground(args=("--dev=reload",))` receives Ctrl+C
- **THEN** the owned process group is stopped, runtime identity is cleared in `finally`, and the CLI exits `130`

#### Scenario: Manual instance does not persist runtime identity

- **WHEN** `client.instance("http://localhost:8069").run_foreground(config=cfg, args=("--stop-after-init",))` executes
- **THEN** no `environment_runtime` row is written or cleared

#### Scenario: Shell does not persist runtime identity

- **WHEN** `instance.shell()` executes
- **THEN** no runtime identity is written because only foreground run owns that lifecycle
