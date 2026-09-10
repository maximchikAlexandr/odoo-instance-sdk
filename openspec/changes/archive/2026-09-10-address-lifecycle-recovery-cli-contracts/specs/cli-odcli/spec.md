## ADDED Requirements

### Requirement: Canonical resource command aliases

The CLI SHALL expose the following canonical names while retaining each existing name as a compatible alias of the same Click command object: `env create`/`env checkout`, `env ls`/`env list`, `env rm`/`env remove`, `backup ls`/`backup list`, `backup inspect`/`backup show`, `backup rm`/`backup delete`, `db ls`/`db list`, `db rm`/`db drop`, `postgres ps`/`postgres status`, `resource ls`/`resource list`, and `module ls`/`module list`. Each pair SHALL share callback, parameters, validation, confirmation, immutable plan, stdout, stderr, exit code, dry-run behavior and the existing stable machine `command` identifier. Help SHALL present one operation with its alternate spelling rather than two independent operations. `postgres up`, `postgres stop`, domain-specific verbs, and existing top-level concise verbs SHALL remain unchanged.

#### Scenario: Alias pair has identical observable behavior

- **WHEN** either spelling in a canonical/compatible pair receives the same arguments and input
- **THEN** both invocations produce semantically identical Rich, JSON and TOON results, errors, plans, prompts and exit codes

#### Scenario: Existing script keeps working

- **WHEN** a script invokes an existing compatible spelling after the aliases are added
- **THEN** validation and the stable machine `command` identifier remain unchanged

#### Scenario: Destructive aliases preserve safety

- **WHEN** `env rm`, `backup rm`, or `db rm` is invoked without the confirmation required by its existing command
- **THEN** the alias enforces the same `--yes`, prompt, dry-run and machine-mode refusal contract as the compatible spelling

### Requirement: Replace a selected isolated environment backup

`odcli db restore BACKUP_UUID --replace` SHALL resolve exactly one registered environment from cwd or the existing root `--env` selector and SHALL accept only a non-removed `db_mode=copy` environment with an exact recorded target database. It SHALL reject project context, shared environments, ambiguous selectors, mismatched generated configuration/catalogue data, unavailable or invalid backup artifacts, a live owned runtime, active target-database sessions, and `--target` before mutation. Because no explicit force contract exists, replacement SHALL NOT terminate active sessions. The existing `--reset-admin-password` option SHALL retain its restore compatibility and SHALL complete before final replacement provenance is committed. Rich confirmation, machine-mode `--yes`, dry-run, redaction, progress, output and exit codes SHALL use the existing restore contracts; no command-local environment selector SHALL be added.

#### Scenario: Cwd environment replacement

- **WHEN** the cwd belongs to a stopped registered COPY environment and an available exact backup UUID passes preflight
- **THEN** `db restore BACKUP_UUID --replace` restores that environment through the replacement contract without changing its identity or target database name

#### Scenario: Explicit environment replacement

- **WHEN** `odcli --env ENVIRONMENT db restore BACKUP_UUID --replace` is invoked outside the worktree
- **THEN** root context resolution selects the same environment and produces the same behavior as cwd resolution

#### Scenario: Unsafe context is rejected

- **WHEN** replacement resolves project context, a shared/removed environment, mismatched records, or a running owned process
- **THEN** it fails before database, filestore, catalogue, config, or process mutation

#### Scenario: Active database sessions fail closed

- **WHEN** replacement preflight or execution revalidation observes any active session on the exact target database
- **THEN** it fails before mutation and does not terminate any session

#### Scenario: Replacement target cannot be overridden

- **WHEN** `db restore BACKUP_UUID --replace --target OTHER_DATABASE` is invoked
- **THEN** option validation rejects the invocation before mutation because replacement must use the environment's exact recorded target

#### Scenario: Admin-password reset remains compatible

- **WHEN** replacement is invoked with `--reset-admin-password` and restore reaches that existing post-restore operation
- **THEN** the reset completes before final provenance is committed, and a reset failure follows replacement compensation without advertising the selected backup as active

### Requirement: Stop a selected environment runtime

Top-level `odcli stop` SHALL resolve exactly one registered environment from cwd or the existing root `--env` selector. Without adding a runtime migration, it SHALL re-read the runtime row's environment owner and PID/create time plus the environment row's `runtime_json` (`odoo_bin`, `runtime_cwd`) and generated-config path. At execution it SHALL terminate only when the live PID create time, executable, argv, cwd and config argument match those records and, on POSIX, the live process satisfies `pgid == pid`; Windows SHALL require the same available identity checks before existing process-tree termination. It SHALL never infer ownership from a listening port. No runtime row, or an absent PID still associated with the selected runtime row, SHALL return idempotent success and clear only that stale row; reused, partial, inaccessible, or mismatched identity SHALL fail with actionable sanitized evidence and SHALL NOT signal any process.

#### Scenario: Stop cwd-owned runtime

- **WHEN** cwd resolves a running environment and the re-read runtime/environment records plus every required live-process identity check match
- **THEN** `odcli stop` terminates that exact owned process group through the existing process boundary, verifies exit, and clears its runtime row

#### Scenario: Stop explicitly selected runtime

- **WHEN** `odcli --env ENVIRONMENT stop` is invoked outside the worktree with matching live identity
- **THEN** it has the same plan, safety, output and exit behavior as cwd resolution

#### Scenario: Already stopped is idempotent

- **WHEN** the selected environment has no runtime row or its previously owned process is confirmed absent without PID reuse
- **THEN** `stop` succeeds without signaling a process

#### Scenario: Port occupancy is not ownership

- **WHEN** an unrelated process listens on the environment's recorded port or a PID has been reused
- **THEN** `stop` does not signal it and reports an ownership validation failure when stale or conflicting identity remains

### Requirement: Jira-key environment creation CLI

`odcli env create JIRA_TICKET --base REF` and compatible `odcli env checkout JIRA_TICKET --base REF` SHALL accept one repository-independent uppercase Jira key, omit the public CLI `--name` option, allocate the deterministic resolved branch defined by `development-environment`, and generate the existing `<project>:<resolved-branch>` environment name. They SHALL always create the resolved new branch from the selected effective base and SHALL never attach to a matching old local or remote ticket branch. `--create-venv` SHALL remain explicit and false by default. Rich, JSON and TOON dry-run/execution output SHALL expose the same captured resolved branch and bounded source evidence without fetching or contacting Jira.

#### Scenario: Help exposes Jira input

- **WHEN** root or environment-group help is rendered
- **THEN** the positional metavariable is `JIRA_TICKET`, `--name` is absent, both command spellings describe generated naming, and `--create-venv` remains optional

#### Scenario: Existing ticket receives next iteration

- **WHEN** `env create PROJ-123 --base dev` resolves prior ticket iterations
- **THEN** all plan, Git argv, environment name, catalogue, database/provenance and final output fields use the single captured next branch created from `dev`

#### Scenario: SDK exact-branch compatibility

- **WHEN** a caller uses the public `EnvironmentResource.checkout` exact-branch API directly
- **THEN** its existing branch and optional name contracts remain unchanged by the CLI-only Jira adapter

### Requirement: Environment configuration drift diagnostics

`odcli doctor` SHALL project one read-only typed drift result for each environment component `python`, `dependencies`, `odoo_config`, `addons`, and `git_provenance`. Each component SHALL contain `status=in_sync|drifted|unknown` and a sanitized concrete reason derived from current normalized project/environment inputs, stored applied evidence, and current artifacts. Rich SHALL give the existing relevant remediation (`env sync` only for Python/dependencies; recreate or a future explicit operation for other components), while JSON and TOON SHALL encode the same component statuses/evidence. The same internal projection SHALL be reusable by a future `env show` without adding that command in this change.

Diagnosis SHALL NOT update applied evidence, repair artifacts, call sync, change lifecycle state, fetch Git, or mutate catalogue/worktree/config/dependency/database/process state. Formatting/comments that preserve normalized semantic values SHALL remain `in_sync`; changed project defaults for a database or allocated port SHALL NOT mark the recorded environment binding drifted; ordinary worktree code changes SHALL remain Git context.

#### Scenario: Applied inputs drift independently

- **WHEN** Python selection, dependency inputs, managed Odoo values, or add-on paths differ from their last successful applied evidence
- **THEN** doctor marks only the corresponding components `drifted` and reports their applicable reason/remediation

#### Scenario: Semantically equal config remains synchronized

- **WHEN** comments, whitespace or formatting change without changing normalized managed Odoo/add-on values
- **THEN** doctor reports those components `in_sync`

#### Scenario: Git work is context, not drift

- **WHEN** the resolved branch/base provenance still matches but the worktree is dirty or ahead/behind
- **THEN** `git_provenance` remains `in_sync` and the ordinary Git activity is reported separately

#### Scenario: Diagnosis is inert and format-equivalent

- **WHEN** doctor runs for current, drifted, and legacy-unknown environments in Rich, JSON and TOON
- **THEN** all formats represent the same statuses and reasons and no applied snapshot or runtime resource changes
