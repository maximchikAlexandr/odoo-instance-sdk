## ADDED Requirements

### Requirement: DatabaseResource exposes native psql through shared command plans

The existing instance-bound `DatabaseResource` SHALL expose:

```python
psql_command(args: tuple[str, ...] = ()) -> Command[int]
psql(args: tuple[str, ...] = ()) -> int
execute_sql_command(sql: str, *, timeout: float = 30.0) -> Command[SqlExecutionResult]
execute_sql(sql: str, *, timeout: float = 30.0) -> SqlExecutionResult
```

Each convenience method SHALL build its sibling command exactly once and delegate to `Command.run()`. The command SHALL capture the bound database, cluster host/port/user, sanitized private environment, exact native argv/stdin, timeout, and captured or inherited-TTY mode. Planning, dry-run, and execution SHALL use the shared immutable process contract; no PostgreSQL-specific executor or preview reconstruction SHALL exist.

For an SDK-owned cluster the plan SHALL include the shared `ensure_running` action before `psql`. For an external cluster it SHALL validate reachability without Docker lifecycle operations.

#### Scenario: Convenience method delegates to one command

- **WHEN** `instance.databases.psql(args=("-c", "SELECT 1"))` is called
- **THEN** it runs the exact process specification captured by `psql_command()` and returns the native exit code

#### Scenario: Interactive command preserves TTY mode

- **WHEN** `psql_command()` is built with no native arguments and run from a TTY
- **THEN** its process step inherits stdin/stdout/stderr and preserves native completion, history, signals, and exit code

#### Scenario: Owned cluster readiness is shared

- **WHEN** `psql_command()` targets a stopped SDK-owned cluster
- **THEN** its plan uses the accepted shared lifecycle/action boundary to ensure readiness before the psql process and does not hide an unplanned Docker launch

### Requirement: Native psql cannot override bound connection identity

The shared PostgreSQL builder SHALL add database connection identity from the bound instance/cluster and implement this closed native-option grammar:

- protected identity options `-d/--dbname`, `-h/--host`, `-p/--port`, and `-U/--username` SHALL be rejected in split, attached-short, and long-`=` forms;
- allowed one-value options SHALL be exactly `-c/--command`, `-f/--file`, `-F/--field-separator`, `-L/--log-file`, `-o/--output`, `-P/--pset`, `-R/--record-separator`, `-T/--table-attr`, and `-v/--set/--variable`; each short form SHALL accept one split or attached value and each long form one split or `=` value, and a missing value SHALL be rejected;
- allowed zero-value options SHALL be exactly `-a/--echo-all`, `-b/--echo-errors`, `-e/--echo-queries`, `-E/--echo-hidden`, `-H/--html`, `-l/--list`, `-n/--no-readline`, `-q/--quiet`, `-s/--single-step`, `-S/--single-line`, `-t/--tuples-only`, `-x/--expanded`, `-X/--no-psqlrc`, `-w/--no-password`, `-W/--password`, `-z/--field-separator-zero`, `-0/--record-separator-zero`, `-1/--single-transaction`, and `--csv`;
- every other short/long option and every unconsumed positional operand, including database/user and URI/keyword connection strings, SHALL be rejected before spawn; `--` SHALL end option recognition but SHALL NOT make following positional operands valid.

Validation SHALL scan left-to-right without reordering or combining tokens. This closed set is the supported native-option passthrough contract; adding a future PostgreSQL option requires an accepted spec update with its arity and identity effect.

All launches SHALL use `shell=False`. The builder SHALL remove ambient libpq identity/service overrides, `PSQLRC`, and ambient `PGOPTIONS` before adding any SDK-owned statement-timeout option; it MAY preserve an explicitly allowed `PGPASSFILE`. An explicit password SHALL exist only in the private child environment and SHALL NOT appear in argv, public plan, fingerprint, repr, stdout, exception text, or logs.

#### Scenario: Connection override is rejected

- **WHEN** native arguments contain `-h other-host`, `-d other-db`, `--username=other`, or any equivalent protected alias
- **THEN** command construction fails before spawn with an actionable usage error

#### Scenario: Query and file options pass through

- **WHEN** native arguments are `("-v", "ON_ERROR_STOP=1", "-f", "query.sql")`
- **THEN** those argument boundaries are preserved in the captured psql argv alongside the SDK-owned connection identity

#### Scenario: Presentation value options preserve arity

- **WHEN** native arguments contain split and attached forms such as `-F "|"`, `-Pborder=2`, `--record-separator=::`, `-T`, and `class=compact`
- **THEN** each declared value is consumed exactly once and the original token boundaries reach the planned argv

#### Scenario: Unknown option is rejected

- **WHEN** native arguments contain an option outside the closed zero-value/one-value sets
- **THEN** command construction fails before spawn even if a later token could look like its value

#### Scenario: Positional connection identity is rejected

- **WHEN** native arguments contain `other_db`, `postgresql://other/db`, `host=other dbname=other`, or any such operand after `--`
- **THEN** command construction fails before spawn rather than allowing native positional identity to override the binding

#### Scenario: Ambient PGOPTIONS is replaced, not inherited

- **WHEN** the parent environment defines `PGOPTIONS` that changes query behavior and a bounded SQL command is planned
- **THEN** that value affects neither execution nor the public plan, and only the SDK-owned statement timeout is present in the private child environment

#### Scenario: Password is private

- **WHEN** the bound configuration contains a password and the command is previewed, executed, fails, and is represented
- **THEN** the password appears only in the private child environment and nowhere in user-visible or fingerprinted data

### Requirement: execute_sql is a narrow captured transport operation

`execute_sql()` SHALL execute exactly one caller-provided SQL string in captured mode against the bound database and return a frozen `SqlExecutionResult` containing only `returncode`, `stdout`, and sanitized `stderr`. The SQL SHALL be captured as exact stdin or command input in the shared plan, and a positive finite timeout SHALL govern both server statement execution and the subprocess.

The method SHALL NOT promise parameter binding, map arbitrary rows into typed models, present itself as an application query layer, or silently modify the caller SQL. Callers remain responsible for using a safe parameterized API for untrusted input.

#### Scenario: Captured result preserves process outcome

- **WHEN** caller SQL writes `value\n` to stdout and psql exits 0
- **THEN** `SqlExecutionResult(returncode=0, stdout="value\n", stderr="")` is returned

#### Scenario: Invalid timeout fails before spawn

- **WHEN** `timeout` is non-finite or not greater than zero
- **THEN** command construction fails and no process is spawned
