## MODIFIED Requirements

### Requirement: `odcli bug-report init` creates a local draft

`odcli bug-report init --title TEXT --kind bug|enhancement|tech-debt [--format rich|json|toon]` SHALL create a UUID `REPORT_ID`, a directory `get_user_root()/bug-reports/<REPORT_ID>/`, and the files `report.md`, `metadata.json`, and an empty `reviews/` directory. The SDK SHALL expose `bug_report_init_command()` returning `Command[BugReportInitResult]`. `--dry-run` SHALL emit the complete immutable plan without creating files or running processes. `PUBLIC_LEAF_CASES` SHALL set `sdk_primitive=bug_report_init_command`. The directory SHALL be `0700` and the files `0600` on POSIX.

`init` SHALL work offline from any cwd, including outside an initialized Odoo project and without GitHub connectivity or authorization. When the current cwd resolves to a managed project, `init` SHALL perform independent, bounded, read-only discovery of the applicable Odoo and PostgreSQL runtime versions through the existing project/runtime and captured-process boundaries. Discovery SHALL NOT start, stop, restart, install, migrate, or otherwise mutate either runtime. The Odoo probe SHALL execute the configured project runtime and `odoo_bin` with `--version` under a timeout of at most 5 seconds and SHALL reject combined output larger than 16 KiB before parsing. The PostgreSQL probe SHALL reuse the existing server-summary plan and SHALL use a shared deadline of at most 10 seconds without starting a stopped service. Each successful value SHALL be a single normalized, secret-free string of at most 64 characters. A missing project, missing provider prerequisite, unavailable runtime, non-zero exit, timeout, oversized output, malformed response, or provider exception SHALL record `unknown` only for the affected field and SHALL NOT prevent draft creation or suppress a successfully resolved value from the other provider.

Every child process used by discovery SHALL be captured in the immutable `bug_report_init_command()` plan, SHALL run through `internal.proc` with `shell=False`, and SHALL be marked read-only. PostgreSQL credentials and all other secrets SHALL remain in the existing private secret-aware execution boundary and SHALL NOT appear in public argv/environment projections, plan fingerprints, returned results, diagnostics, or `report.md`. Raw probe stdout, stderr, and exception text SHALL NOT be copied into the draft. On `--dry-run`, the discovery steps SHALL be visible in the plan when applicable but SHALL NOT execute.

The command SHALL return the `REPORT_ID` and absolute paths to the directory and `report.md`.

`report.md` SHALL be a template with a title and the sections: context (installed OdCLI version and VCS SHA if available, OS/Python, applicable Odoo/PostgreSQL versions, unknown values explicitly marked), reproduction or scenario, actual vs expected behaviour with the expectation's basis, evidence (separated facts from hypotheses), minimal sufficient solution direction with bounds, verifiable acceptance criteria and a regression scenario, and applicable project constraints and change consequences. Unfilled required sections SHALL block submission. `metadata.json` SHALL hold the ID, creation date, kind, target repository/labels, and submission/URL fields managed by the SDK.

`init` SHALL accept `--dry-run` and SHALL NOT create files or run processes in that mode. The command SHALL follow the shared output contract, exit codes, and redaction rules. `cli_only_reason` SHALL NOT be used.

#### Scenario: init creates a draft offline

- **WHEN** `odcli bug-report init --title "stop does not stop foreground run" --kind bug --format json` runs outside an Odoo project and without network
- **THEN** a UUID draft directory is created under the bug-reports root with `report.md`, `metadata.json`, and `reviews/`, the result returns the `REPORT_ID` and absolute paths, and both runtime version fields are `unknown`

#### Scenario: managed project versions populate the public CLI draft

- **WHEN** the public `odcli bug-report init` command runs from a managed-project fixture whose deterministic read-only Odoo and PostgreSQL providers return valid versions
- **THEN** the generated `report.md` contains both concrete normalized versions and neither field is `unknown`

#### Scenario: provider failures fall back independently

- **WHEN** either the Odoo provider or the PostgreSQL provider is unavailable, times out, fails, or returns malformed or oversized output while the other provider returns a valid version
- **THEN** draft creation succeeds, only the failed provider's field is `unknown`, and the other field contains its concrete normalized version

#### Scenario: discovery is read-only and secret-free

- **WHEN** version discovery uses a managed project whose PostgreSQL runtime requires credentials
- **THEN** every probe is bounded and read-only, no service lifecycle or configuration mutation occurs, and credentials, raw probe output, and secret markers are absent from the public plan, result, diagnostics, and `report.md`

#### Scenario: init dry-run creates nothing

- **WHEN** `odcli bug-report init --title "..." --kind bug --dry-run` runs in a managed project with resolvable version providers
- **THEN** no file or directory is created, no process is started, and the immutable plan includes the applicable read-only discovery steps followed by the draft action

#### Scenario: repeated init does not overwrite

- **WHEN** `odcli bug-report init` runs twice with the same title
- **THEN** two different drafts with different `REPORT_ID`s are created and the first draft is unchanged

#### Scenario: path traversal is rejected

- **WHEN** a path traversal or symlink substitution targets the bug-reports root
- **THEN** `init` rejects it and no file is created outside the root
