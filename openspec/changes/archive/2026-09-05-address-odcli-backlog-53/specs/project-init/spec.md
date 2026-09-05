## ADDED Requirements

### Requirement: Project-local environment loading

After resolving an initialized project and before constructing project runtime operations, `odcli` SHALL look only for `<project-root>/.odcli/.env`; it SHALL NOT walk above the resolved project or load cwd-global dotenv files. A missing file SHALL be ignored. An unreadable or malformed file SHALL fail with a path-and-line-number-only sanitized error before mutation.

The UTF-8 grammar SHALL be deterministic: after stripping a UTF-8 BOM on the first line, a physical line is blank, a comment whose first non-whitespace character is `#`, or an assignment `[ \t]*KEY[ \t]*=[ \t]*VALUE[ \t]*`; `KEY` SHALL match `[A-Za-z_][A-Za-z0-9_]*`. Unquoted `VALUE` SHALL preserve internal whitespace, trim surrounding horizontal whitespace, and treat `#` as data. Single-quoted values SHALL contain any character except single quote, backslash, CR, LF, or NUL and SHALL perform no escaping. Double-quoted values SHALL support only `\\`, `\"`, `\n`, `\r`, and `\t` escapes. Empty values and `KEY=` SHALL be valid. Multiline values, `export`, duplicate keys, trailing tokens after a quoted value, unknown escapes, interpolation (`$NAME`/`${NAME}`), command substitution, backticks, NULs, and invalid UTF-8 SHALL be rejected; no shell evaluation SHALL occur.

The loader SHALL create an immutable effective mapping in which the invoking process value wins over the file value for every key. File-derived ordinary variables SHALL be propagated only to Odoo runtime children (foreground run, Odoo shell/eval/exec, module operations, tests, and Odoo-backed restore steps). They SHALL NOT be propagated to Git, PostgreSQL/psql/pg tools, Docker/Compose, editors, browsers, package/build tools, or any other child class; those children SHALL retain their existing purpose-built sanitized environments. `ODCLI_TEST_MASTER_PASSWORD` SHALL be classified as secret, consumed only by restore coordination, removed before every child spawn including Odoo, and never exported globally. Keys matching the existing credential/secret classifier SHALL be redacted from all public surfaces; classification SHALL not by itself authorize propagation to a denied child class. Loaded values SHALL never mutate `os.environ`.

#### Scenario: Process environment wins
- **WHEN** `.odcli/.env` and the invoking process both define `ODCLI_TEST_MASTER_PASSWORD`
- **THEN** the existing process value is used and neither value is printed

#### Scenario: Search stops at project boundary
- **WHEN** an initialized project has no `.odcli/.env` but a parent directory does
- **THEN** the parent file is not loaded

#### Scenario: Malformed file fails before work
- **WHEN** the resolved project file contains an invalid assignment
- **THEN** the command fails before child creation or mutation without echoing the line's value

#### Scenario: Grammar and escaping are exact
- **WHEN** the file contains blank/comments, valid identifiers, empty/unquoted/single-quoted/double-quoted values and supported double-quote escapes
- **THEN** values decode exactly once according to the grammar, while duplicate keys, interpolation, unsupported escapes, multiline values, or trailing quoted tokens fail before work

#### Scenario: Odoo child receives ordinary values
- **WHEN** a file-defined ordinary variable is not overridden by the process and an Odoo runtime child is spawned
- **THEN** that child receives the value while Git, PostgreSQL, Docker/Compose, and other child classes do not

#### Scenario: Master password is consumed, not propagated
- **WHEN** restore coordination resolves `ODCLI_TEST_MASTER_PASSWORD` from the effective mapping
- **THEN** it uses the value for the privileged restore decision and no spawned child or public projection receives the key or value

### Requirement: Project-local secret-file hygiene

Project initialization SHALL ensure `.odcli/.env` is covered by the repository ignore rules and documentation SHALL require owner-only readability. Loading an existing file with group/other permission bits SHALL fail closed with a path-only remediation message. Secret keys and values SHALL be excluded from Rich, JSON, TOON, dry-run plans, errors, diagnostics, logs, and fingerprints.

#### Scenario: Insecure permissions are refused
- **WHEN** `.odcli/.env` is readable or writable by group or others on a platform supporting POSIX mode bits
- **THEN** loading fails before use and advises owner-only permissions without revealing contents
