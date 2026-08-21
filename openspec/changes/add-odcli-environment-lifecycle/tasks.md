## Slice 1 — MVP: Click, project init/import, durable catalog, two-rule context, locks

- [ ] 1.1 Добавить зависимости `click>=8.2,<9` и `json5>=0.15,<1` в `pyproject.toml`; добавить `[project.scripts] odcli = "odoo_instance_sdk.cli:cli"`
- [ ] 1.2 Создать `ProjectConfig` (`msgspec.Struct`, `frozen=True`): `load(project_path) -> ProjectConfig` читает `.odcli/project.toml`; поля: odoo_bin, python, source_config, default_source_database, preferred_http_port, requirements, default_run_args, runtime_cwd
- [ ] 1.3 `odcli init` wizard mode (TTY): Click prompts только для unresolved required values; fully specified values не prompts
- [ ] 1.4 `odcli init --no-input`: forbids prompts; fails with stable list of missing/ambiguous options
- [ ] 1.5 `odcli init --dry-run --json`: returns resolved manifest + provenance (`option`, `vscode`, `discovery`, `default`) без записи
- [ ] 1.6 Idempotent init: existing non-identical manifest never overwritten silently; identical init — no-op
- [ ] 1.7 VS Code import: `--from-vscode <path>` [--launch-name NAME]; parse JSONC через json5; only `request=launch` Python/debugpy configs with Odoo-like `program`; не selects unrelated first profile
- [ ] 1.8 VS Code import interactive: show matching profiles; `--no-input` requires `--launch-name` when >1 candidate
- [ ] 1.9 VS Code import mapping: `python`→`ProjectConfig.python`, `program`→`odoo_bin`, `-c/--config`→`source_config`, `-d/--database`→`default_source_database`, `--http-port`→`preferred_http_port` seed, `--addons-path`/`--upgrade-path`→config overlays, `--dev`→`default_run_args`, `cwd`→`runtime_cwd`
- [ ] 1.10 VS Code import: support only static `${workspaceFolder}`; named-workspace/env/command/input/unresolved variables — explicit errors
- [ ] 1.11 VS Code import: report and drop `-u/-i/--stop-after-init`; never persist as run defaults; `preLaunchTask`/`tasks.json` reported as ignored; envFile/inline env reported as ignored (values not read/copied/printed)
- [ ] 1.12 VS Code import: repo-local `runtime_cwd` stored relative to manifest; external absolute path remains unchanged with portability warning
- [ ] 1.13 Manifest secrets-free: no secrets/runtime artifacts written to repository; runtime artifacts in platformdirs user dirs, linked via canonical Git common dir
- [ ] 1.14 Durable catalog path migration: legacy `user_cache_dir/backups.sqlite3` → durable `user_data_dir/catalog.sqlite3` под exclusive catalog-migration lock; SQLite backup API → temp sibling → fsync/atomic replace → `0600`; legacy DB not auto-deleted; durable authoritative при конфликте, automatic merge запрещён и диагностируется
- [ ] 1.15 Catalog schema v2 → v3: `CREATE TABLE IF NOT EXISTS environments (...)` + constraints; `CREATE TABLE IF NOT EXISTS environment_events (...)`; `PRAGMA user_version = 3`; существующие backups/restores/database_events не трогаются
- [ ] 1.16 `OdooClient.environments` — `EnvironmentResource` exposed on client; catalog internal, not `client.catalog`
- [ ] 1.17 Two-rule context resolution: Rule 1 (cwd inside exact registered worktree → infer project+env), Rule 2 (otherwise explicit `--project`/`--env`/positional); nearest `.odcli/project.toml` upward = explicit project discovery
- [ ] 1.18 Запрет silent defaults: no last-used selection, no single-ready selection; ambiguous → error with candidates
- [ ] 1.19 `fcntl.flock` internal: catalog-migration lock, project+branch provisioning lock (до env ID), per-environment lock; `LOCK_SH` для run/shell, `LOCK_EX` для sync/remove; conflict fail-fast; auto-release on exit/SIGKILL; lock files in `user_state_dir/locks`; no public LockManager
- [ ] 1.20 Тесты: JSONC mapping (fixture `tests/fixtures/comerta-launch.json` — копия `/odoo/comerta/.vscode/launch.json`, committed in-repo; selects `Odoo comerta`, imports external paths, `CMRT-361_1`, port `8068`, `--dev=qweb,xml`, drops `-u comerta_base`), two-rule context, fcntl conflict/release, catalog cache→data migration/ownership

**AC coverage**: AC1, AC4 (partial), AC5 (partial), AC6

## Slice 2 — MVP: Worktree, generated config, shared/copy DB and cleanup

- [ ] 2.1 Checkout preflight: find repo root + git common dir via Git CLI; verify `git`/`uv`/ref/branch/config/Odoo paths/Python mode; default требует existing venv interpreter, без него → error with `--create-venv` hint; check no active env for repo+branch; resolve source/target DB; for `copy` verify local source instance, master password, absent target DB; dirty main checkout не блокирует
- [ ] 2.2 Worktree placement: `<user_data_dir>/environments/<repo-key>/<env-id>/{worktree,venv,requirements.lock,odoo.conf}`; `repo-key` = safe slug + short hash of canonical git common dir
- [ ] 2.3 Worktree branch rules: existing local → `git worktree add`; single matching remote → tracking branch; absent → create from `--base`; branch checked out in another worktree → понятная error; no `--force`/`-B`/reset/delete; state via `git worktree list --porcelain -z`
- [ ] 2.4 System Git via `subprocess.run([...], shell=False)`; no GitPython/Dulwich/pygit2; Git CLI/porcelain/worktree paths internal to `EnvironmentResource`, not public module
- [ ] 2.5 Generated `odoo.conf`: atomic write `0600`; preserve unknown options; rebase repo-local `addons_path`/`upgrade_path` to worktree; external Odoo core/addons unchanged; `http_interface` default `127.0.0.1`; `http_port` from env registry; `db_name` = source DB (shared) / target DB (copy); `dbfilter` limits to selected DB; DB connection/admin_passwd/data_dir preserved; logfile/stdout semantics preserved; stdlib configparser/pathlib/shutil/tempfile/os.replace; comments may not preserve; unknown keys/values MUST preserve
- [ ] 2.6 DB name validation (copy): UTF-8 length ≤63 bytes; regex `[A-Za-z0-9_][A-Za-z0-9_.-]*`; not `.`/`..`; slash/backslash/NUL/absolute/path syntax forbidden; canonicalize `<data_dir>/filestore/<db-name>`, prove containment under resolved filestore root, no escaping symlinks
- [ ] 2.7 `shared` mode: no backup/restore; generated config → source DB; env не владеет БД; `remove` не может `drop()` source DB; результат предупреждает: код/process изолированы, БД и filestore — нет
- [ ] 2.8 `copy` mode: ZIP backup source DB with filestore via existing `backup()`; save `backup_id` as env-owned; restore target DB via `restore(..., copy=True, neutralize_database=True)`; postcondition `exists(target_db) is True`; only then `ready`; source Odoo HTTP must be local+available, else `failed` env with error; target DB never overwritten/auto-deleted
- [ ] 2.9 `DatabaseResource.restore()` MUST send `"name": target_database_name` in POST body
- [ ] 2.10 `env checkout --dry-run`: shows worktree/config/port/DB plan, Python mode (`reuse|create`), ownership, dependency inputs, helper argv; nothing created; candidates re-checked at execution
- [ ] 2.11 `env list`: default table `ID NAME STATE OBSERVED BRANCH PYTHON_MODE DB_MODE DATABASE PORT LAST_USED WORKTREE`; hide only `removed`; `failed`/`cleanup_failed` visible; quick reconciliation (worktree/config/python/lock/port/backup); `OBSERVED` = `port-free|port-occupied|unknown`; `--all` = include removed; `--older-than` filters by `last_used_at` (no auto-delete); `--json` envelope
- [ ] 2.12 `env remove`: plan + preflight; `--yes` или Click confirmation; `--dry-run`; cleanup matrix (shared vs copy); safety rules (dirty worktree blocks, occupied port blocks, `git worktree remove` not recursive delete, owned venv only if `python_environment_owned=true` + containment, reused venv never touched, no Git force, no branch delete, drop only for copy with matched cluster identity, shared source DB never deleted, `BackupResource.delete()` for env-owned backup only, idempotent missing = success, partial error → `cleanup_failed`, `removed` only after all owned artifacts gone, final empty dir deleted, SQLite rows kept)
- [ ] 2.13 Тесты: path/DB-name containment, config rewrite and single `--config`, temporary-Git E2E (default reused venv + explicit owned venv), local-Odoo copy E2E (checkout→run/shell→remove), fake Git/uv/Odoo executables (failure exit codes, atomic rollback, no-RPC)

**AC coverage**: AC2, AC3, AC8 (partial), AC9

## Slice 3 — MVP: Reused or explicitly isolated Python environment and `env sync`

- [ ] 3.1 Default checkout reuses interpreter from project manifest/`--python`; MUST exist + report virtual-env prefix; location may be external; `owned=false`; never deleted by SDK
- [ ] 3.2 `--create-venv` only: `uv venv <env-root>/venv --python <selector>`; `owned=true`; `create_venv` default `false`, cannot come from manifest/VS Code/cwd inference — only explicit `--create-venv`
- [ ] 3.3 Both modes: Odoo Core + project requirements compiled by one `uv pip compile` into env-owned `requirements.lock`
- [ ] 3.4 Reused venv: `uv pip install --python <project-python> -r <lock>` (preserves unrelated tools); Owned venv: `uv pip sync --python <env-python> <lock>` (isolation)
- [ ] 3.5 uv writes serialized by `flock` по canonical Python-environment path
- [ ] 3.6 Repo-local dependency files rebase to worktree; lock/fingerprint relate to worktree
- [ ] 3.7 `env sync --upgrade` updates pins; regular sync preserves them; failed compile does not replace valid lock
- [ ] 3.8 run/shell auto-sync only on changed inputs; external drift → `doctor` (MVP) / `deps verify` (post-MVP)
- [ ] 3.9 Runtime prefix always `[recorded-python, odoo-bin]`; no separate Python resource; `uv venv`/`pip compile`/`pip sync`/fingerprint — internal `sync_python()`, not public venv module
- [ ] 3.10 Тесты: reuse venv (owned=false, not deleted), create venv (owned=true, isolated), sync upgrade vs preserve, failed compile keeps valid lock, auto-sync on changed inputs

**AC coverage**: AC2 (Python env part)

## Slice 4 — MVP: `from_environment`, `command_prefix`/`cwd`, `run_foreground`, interactive `shell`, `run_shell_script` primitive

- [ ] 4.1 `InstanceConfig.command_prefix: tuple[str, ...] | None = None` and `default_cwd: Path | None = None`
- [ ] 4.2 `InstanceFactory.from_environment(env)`: только `ready` env; читает generated `odoo.conf` через existing config flow; applies recorded Python/Odoo entry point/worktree as defaults; uses recorded resolved runtime paths (not re-reading manifest); no master password; returns ordinary `OdooInstance`; no Git/cleanup/audit methods on instance
- [ ] 4.3 `from_config(path)`: не поднимает `MasterPasswordRequiredError` если `admin_passwd` отсутствует — `master_password=None`; записывает `[OdooClientConfig.executable or odoo-bin from config]` в `command_prefix`
- [ ] 4.4 `instance(base_url=...)`: `command_prefix=None`, fallback на `OdooClientConfig.executable`
- [ ] 4.5 `run()`/`start()`/`run_foreground()`/`shell()`/`run_shell_script()` используют instance prefix, затем client fallback
- [ ] 4.6 `StartConfig.from_odoo_config(path)`: MUST set `config_path` to actual `path`, not wait for option inside file
- [ ] 4.7 `_build_cli_args()`: MUST pass ровно one `--config`; no second temp config from `db_password` for persistent `0600` generated conf
- [ ] 4.8 Mutating DB methods (`backup`/`restore`/`drop`): require password at call time, raise `MasterPasswordRequiredError` there
- [ ] 4.9 `OdooInstance.run_foreground(config=None, *, cwd=None, env=None) -> int`: same resolved command-prefix/config/process-group lifecycle as `start()`/`stop()`; inherit stdout/stderr (raw Odoo logs to terminal); block until Odoo exits → return exit code; Ctrl+C → stop owned process group
- [ ] 4.10 `OdooInstance.shell(...) -> int`: same internal foreground subprocess primitive as `run_foreground`; inherit stdin/stdout/stderr/signals/exit code of `odoo-bin shell`; no own REPL; bound config/DB; passthrough config/database overrides (incl. `-cPATH`/`-dDB`) forbidden
- [ ] 4.11 `OdooInstance.run_shell_script(source, *, argv=(), timeout=None, commit=False) -> CommandResult`: captured; one bound config/DB; non-TTY stdin; script `argv` injected after Odoo parsing; nonce-framed payload record in stdout; returns existing `CommandResult`
- [ ] 4.12 Existing `OdooInstance.run(args) -> CommandResult` — unchanged captured one-shot API, not overloaded
- [ ] 4.13 `odcli run`: resolve ready env + verify worktree/config/python; compare dep fingerprint → sync if changed; `socket.bind((http_interface, http_port))` port check; if busy → `port-conflict`/ownership-unknown, no 2nd process, no config change, no `used` update; update `last_used_at` + `use/succeeded` event after free-port preflight; build instance via `from_environment()`; `run_foreground()` → return exit code; Ctrl+C → exit 130
- [ ] 4.14 `odcli shell`: same preflight as `run` without HTTP port check; bound DB (source for shared, target for copy); `from_environment()` → `instance.shell()`; passthrough args after `--`
- [ ] 4.15 `EnvironmentResource` не получает runtime methods `run()`/`shell()`/`start()`/`stop()`
- [ ] 4.16 Тесты: `from_config` without password, instance prefix vs client fallback, `run_shell_script` framing, `run_foreground`/`shell` raw streams + signals + exit code, port-conflict deterministic error

**AC coverage**: AC7, AC8, AC10 (run_shell_script primitive)

## Slice 5 — MVP: `doctor`

- [ ] 5.1 `odcli doctor` / `odcli doctor --json` / `odcli --project /path doctor`: read-only checks over manifest, worktrees, `uv`, recorded Python/ownership, dependencies, Odoo/config, catalog, DB/backups, ports, orphaned artifacts
- [ ] 5.2 `doctor` — CLI coordinator над `list`/`get`/`history` и filesystem checks; не `client.doctor`, не public resource
- [ ] 5.3 Errors → non-zero; warnings → in output; `doctor --fix` не добавляется
- [ ] 5.4 `doctor` показывает migrated legacy DB artifact (cache→data migration)
- [ ] 5.5 Тесты: doctor detects missing worktree/config/python/lock, port state, owned backup missing, orphaned artifacts, migrated legacy DB

**AC coverage**: AC4 (doctor sees one picture), AC5 (stable output)

## Slice 6 — Post-MVP: captured automation: `eval`, `exec`, `module`, `translations export`, `deps verify`

- [ ] 6.1 `odcli eval EXPRESSION`: single Python expression in Odoo shell context (`env`/`odoo`/`self`); scalar/collection JSON or typed recordset summary `{model, ids, count}`; unknown objects → bounded sanitized `repr`; default best-effort rollback; `--commit` visible in plan/event but not security boundary
- [ ] 6.2 `odcli exec SCRIPT [-- SCRIPT_ARGS...]`: reads explicit file (`-` = caller stdin); script via shell stdin; predictable `sys.argv` from tokens after `--`; default best-effort rollback; `--commit` warning in help
- [ ] 6.3 `odcli module list [MODULE...] [--state STATE]`: reads `ir.module.module`; names filter exact; `--state` filters state
- [ ] 6.4 `odcli module update MODULE... [--dry-run] [--yes]`: requires installed modules; lifecycle lock; dry-run plan; explicit `--yes`; `button_immediate_upgrade()` (self-commits); no transactional rollback promise beyond Odoo
- [ ] 6.5 `odcli module test MODULE... --test-tags TAGS [--reload-tests] [--allow-empty]`: Odoo 19 `odoo.tests.shell.run_tests(env, test_tags, modules, reload_tests=...)`; workers=0; exclusive artifact lock + free bound HTTP port required; port conflict → deterministic precondition error; exit non-zero on failed tests AND on zero tests unless `--allow-empty`
- [ ] 6.6 `odcli translations export --module MODULE... --language LANG... [--json]`: `run_shell_script()` with exporter on non-TTY stdin; `base.language.export` (`__new__` for .pot, active language, `format=po`, `export_type=module`); actual PO name from wizard `name`/`tools.get_iso_codes()` (e.g. `ru_RU`→`ru.po`); validate installed module + active language + non-empty base64; atomic write preserving file mode; containment proof for module root/target paths; no commit from bundled exporter; summary with requested code/actual filename/missing counts; partial failure → non-zero
- [ ] 6.7 `odcli deps verify [--json]`: `uv pip check` for installed distributions + imports from addon `external_dependencies['python']` in managed interpreter; Manifest Python не исполняется; missing import → module/import name
- [ ] 6.8 No RPC fallback for any post-MVP command; no new public resources (`ModuleResource`/`TranslationResource`/`PythonResource`); coordinators private application layer over `run_shell_script()`
- [ ] 6.9 Тесты: eval scalar/recordset/unknown, exec stdin/`-`/argv, module list/update/test, translations export `ru_RU→ru.po` + containment + atomic write, deps verify missing import

**AC coverage**: AC11

## Slice 7 — Post-MVP: `vscode generate`

- [ ] 7.1 `odcli vscode generate` / `odcli vscode generate --write`: reverse transform current project/environment → debugpy launch profile; recorded Python/program, config, DB/port, portable `cwd`, integrated terminal, `justMyCode=false`; secrets/tasks/mutating args excluded
- [ ] 7.2 Requires ready environment; default prints only; `--write` atomically creates absent `.vscode/launch.json`, refuses merge/rewrite existing JSONC
- [ ] 7.3 Тесты: generated profile fields, `--write` on absent vs existing, secrets excluded

**AC coverage**: AC11

## Quality gates (MVP close)

- [ ] Q1 ruff lint clean
- [ ] Q2 strict mypy clean on production package and tests
- [ ] Q3 full pytest pass
- [ ] Q4 one disposable local Odoo lifecycle integration pass: init→checkout→run/shell→remove
- [ ] Q5 `run_shell_script()` covered as SDK primitive без CLI `eval`/`module`

**AC coverage**: AC10