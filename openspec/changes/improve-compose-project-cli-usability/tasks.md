## 1. Pin Root-Cause Regressions

- [x] 1.1 Add init tests for identical no-op, default headless refusal, interactive confirmation, `--no-input --yes` atomic replacement, and `--yes --dry-run` zero writes; assert the repository-root `.gitignore` is never created or changed.
- [x] 1.2 Add an imported Compose project test that preserves the external source config, creates an owner-only project runtime config bound to the owned cluster, uses `preferred_http_port`, and proves cluster credentials are absent from manifest, output, diagnostics, plans, and fingerprints.
- [x] 1.3 Add behavioral catalogue migration tests for v7-to-current row preservation, both child foreign-key targets, a successful post-migration environment-event insert with foreign keys enabled, fresh-schema references, and unchanged current v14 on this base.
- [x] 1.4 Add generated module-source regressions proving requested names enter the Odoo domain as a list, empty selection is rejected before spawn, and an empty or incomplete returned `updated` set cannot produce success.
- [x] 1.5 Add Git collector regressions with `base_ref=dev`, no `main`, local and upstream baseline variants, committed numstat, excluded binary/uncommitted changes, missing ancestry, and equality between recorded and direct collectors.
- [x] 1.6 Add socket regressions for an immediate bind after a closed accepted loopback connection, a live listener that remains occupied, unknown socket failures, and module-test preflight proceeding without manual retry.

## 2. Make Project Initialization Usable

- [x] 2.1 Add `init --yes` and thread it through existing-manifest handling so only a validated non-identical manifest bypasses the Rich prompt; preserve no-op, stable refusal, machine output, and dry-run semantics.
- [x] 2.2 Replace repository-root ignore mutation in `internal/project_manifest.py` with an atomically written, symlink-safe `.odcli/.gitignore` rule for the local environment file and remove obsolete root-ignore code/tests.
- [x] 2.3 Extend the existing generated-config path to build the project-owned Compose runtime config from `source_config`, overlay owned cluster connection values and effective HTTP/database values, preserve unknown options, and keep source bytes unchanged.
- [x] 2.4 Read the Compose password only from the existing owner-only cluster secret artifact, keep generated files mode `0600`, add the password to existing private redaction inputs, and route project runtime construction to the generated config without adding manifest fields.
- [x] 2.5 Centralize explicit override → `preferred_http_port` → effective Odoo config → default precedence in the project runtime/context resolver and replace every project-bound endpoint inference, including restore postconditions, with that resolved value.

## 3. Scope Catalogue Data and Expose Worktree Paths

- [x] 3.1 Extend backup and local-resource catalogue queries with the existing canonical project identity predicate before pagination; add `--all-projects`, explicit global provenance, and outside-project refusal while leaving `db list` project-bound.
- [x] 3.2 Add a CLI-only environment projection that joins the existing catalogue `DevelopmentEnvironment.worktree_path` to one already-collected monitor snapshot by UUID without changing monitor/FastAPI/dashboard models or collecting metrics twice.
- [x] 3.3 Add `WORKTREE` to Rich `env list` and `worktree_path` to each CLI JSON/TOON environment result, preserving stable ordering, watch selection, machine parity, and absence of local paths from HTTP/OpenAPI/dashboard fixtures.
- [x] 3.4 Implement read-only `env path [ENVIRONMENT]` with existing cwd/name/UUID resolution, active and directory validation, exact plain-path Rich output, normal JSON/TOON envelopes, and actionable unknown/ambiguous/removed/missing failures; do not add root `--env`, `env cd`, or a nested shell.
- [x] 3.5 Add the short help/README example for `cd "$(odcli env path <environment>)"` and tests for cwd resolution, name/UUID, paths with spaces, exact no-ANSI stdout, machine parity, and no misleading `env cd` documentation.

## 4. Fix Truthful Runtime State

- [x] 4.1 Change `_update_modules_source()` to JSON-decode the frozen name list inside Odoo shell, retain pre-spawn empty-selection validation, and verify all requested modules in the returned result before building a success document.
- [x] 4.2 Thread each catalogue row's validated `base_ref` through bounded Git probe construction, `_recorded_git_activity()`, direct collection, compatibility output, and cache identity; resolve upstream then local baseline without fetch/pull and preserve orphan semantics.
- [x] 4.3 Set `SO_REUSEADDR` before the shared probe bind on supported sockets while retaining timeout, multi-address, live-listener, and `UNKNOWN` behavior without sleeps or broad retries.
- [x] 4.4 Retain `_migrate_v14_environment_foreign_keys()` as the single current repair, adjust it only if the behavioral tests reveal a preservation/index/transaction defect, and do not advance schema version solely for this issue.

## 5. Identify Progress and Preserve Dry-Run Commands

- [x] 5.1 Carry stable `step_id`, sanitized operation/target context, elapsed time, reliable units, and known process exit status through existing run-context observer events for process and action steps.
- [x] 5.2 Update the shared Rich observer so every start/progress/completion/failure line is attributable and no bare anonymous `started`/`completed` output can occur; keep TTY lifecycle, non-TTY determinism, machine silence, redaction, and interrupt cleanup.
- [x] 5.3 Update the shared semantic Rich plan projection to append every captured `ProcessStep.display` in execution order, including after failed preconditions, while describing `ActionStep` values without fabricated commands and omitting the remaining low-level fields.
- [x] 5.4 Add one parameterized progress contract covering refresh, restore, environment lifecycle, tests, module update, eval, exec, translations, and PostgreSQL startup, including exit and elapsed context plus machine-output isolation.
- [x] 5.5 Add parameterized Rich dry-run coverage for `run`, `env checkout`, `postgres up/stop`, `module update`, and `translations export`, plus recording/fake cases for conditional `env sync/remove`, `db drop`, `test`, and project/environment run branches; require each planned process display and zero execution.

## 6. Make Rich Output Consistently Human-Oriented

- [x] 6.1 Establish the exact bounded-leaf presentation inventory in the existing shared output-contract tests and assert non-empty, non-raw-primary-JSON, non-concatenated Rich output with unchanged JSON/TOON documents and exit codes except for exactly the additive CLI `env list.worktree_path` field and the new CLI `env path` envelope.
- [x] 6.2 Make `backup list` rendering single-owner and convert `backup`, `db`, `resource`, `module`, and `env` list results to one Rich table per logical result set using the existing human byte formatter while preserving exact machine integers.
- [x] 6.3 Render the bound cluster once for `db list` and use database, size, sessions, default, and origin columns; remove repeated unstructured cluster fields from rows.
- [x] 6.4 Replace affected bounded Rich raw JSON and key-value dumps, including `db restore`, with concise shared summaries, labelled panels/tables, and spaced nested details in current leaf renderers; remove duplicate pre-emission and do not add a renderer hierarchy.
- [x] 6.5 Update CLI help/documentation for project-scoped `--all-projects`, human-size tables, endpoint precedence, identified progress, and exact sanitized commands in Rich dry-run.

## 7. Verify the Integrated Change

- [ ] 7.1 Run the focused init/config, catalogue migration, CLI context/output, monitor Git, automation, address, and local-test suites and record all commands and exit codes.
- [ ] 7.2 Run `openspec validate improve-compose-project-cli-usability --strict`, `make lint`, `make types`, and the repository unit/architecture/documentation checks required by the affected areas; fix failures without weakening inventories or allowlists.
- [ ] 7.3 Run machine parity, redaction/security, dashboard/OpenAPI no-local-path, compatibility, and package checks affected by the change; confirm no new dependency, public model, direct subprocess launch, or production `Any`/bare `object` annotation.
