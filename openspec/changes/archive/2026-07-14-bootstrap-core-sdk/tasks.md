## 1. Project bootstrap & tooling

- [x] 1.1 Run `uv init --package odoo-instance-sdk` (or equivalent); set `src/odoo_instance_sdk/` layout, `requires-python >= "3.12"`, `version = "0.1.0"`.
- [x] 1.2 Add runtime deps to `pyproject.toml`: `httpx>=0.27,<1.0`, `msgspec`.
- [x] 1.3 Add dev deps: `mypy`, `ruff`. Configure `mypy --strict` (strict = true).
- [x] 1.4 Create `ruff.toml` by copying `multica-py/ruff.toml` and changing `known-first-party = ["odoo_instance_sdk"]`.
- [x] 1.5 Add README.md, copy LICENSE already exists; populate `pyproject.toml` metadata (`description`, `readme`, `license`, `authors`, classifiers).
- [x] 1.6 Verify `uv build` produces both `dist/*.whl` and `dist/*.tar.gz`.
- [x] 1.7 Verify `ruff check .` and `mypy --strict src/odoo_instance_sdk` both pass on an empty package (skeleton import only).

## 2. Type foundation: models & exceptions

- [x] 2.1 Create `src/odoo_instance_sdk/exceptions.py` with hierarchy: `OdooInstanceSdkError`, `ConfigError`, `CommandTimeoutError`, `ProcessNotFoundError`, `ProcessExitedBeforeReady`, `ReadinessTimeoutError`, `RemoteInstanceError`, `DatabaseError` (with `status_code`, `message`, `body` fields).
- [x] 2.2 Create `src/odoo_instance_sdk/models.py` with `msgspec.Struct`: `OdooClientConfig` (with custom `__repr__` masking `master_pwd`), `StartConfig` (verify field set against `odoo/tools/config.py` 19.0 — minimum: `http_port`, `http_interface`, `config_path`, `addons_path`, `data_dir`, `dbfilter`, `workers`, `max_cron_threads`, `log_level` as `Literal[...]`, `dev_mode`), `CommandResult`, `OdooProcess`, `ProcessStatus`, `ReadinessResult`, `BackupArtifact`, `RestoreResult`, `DropResult`.
- [x] 2.3 Create `src/odoo_instance_sdk/__init__.py` re-exporting all public models, exceptions, and `OdooClient`.
- [x] 2.4 Verify `ruff check` and `mypy --strict` pass.

## 3. OdooClient & resources skeleton

- [x] 3.1 Create `src/odoo_instance_sdk/client.py` with `OdooClient(config: OdooClientConfig)` that exposes `server` and `database` resources, holds a per-client process registry (`dict[str, OdooProcess]`). Ensure `OdooClient.__repr__` does not leak `master_pwd`.
- [x] 3.2 Create `src/odoo_instance_sdk/_registry.py` for the per-client registry (add/remove/lookup by id, iteration).
- [x] 3.3 Create `src/odoo_instance_sdk/server.py` with `ServerResource.__init__(client)` storing backref to client (for config + registry).
- [x] 3.4 Create `src/odoo_instance_sdk/database.py` with `DatabaseResource.__init__(client)` similarly.
- [x] 3.5 Verify `ruff` and `mypy --strict` pass.

## 4. `server.run()` — one-shot CLI

- [x] 4.1 Implement `ServerResource.run(args, *, cwd=None, env=None, timeout=None)` using `subprocess.run(..., start_new_session=True)`; build command `[executable, *args]` without shell.
- [x] 4.2 On timeout: kill process group (POSIX `os.killpg`) or `taskkill /T` on Windows, then raise `CommandTimeoutError` (typed, no `master_pwd` in message).
- [x] 4.3 Return `CommandResult` populated with `args`, `returncode`, `stdout`, `stderr`, `duration` (timed via `perf_counter`).
- [x] 4.4 Self-test via `python -m` style or `__main__` snippet (run `--version` style command) — add to `tests/` as a runnable self-check; no test framework.

## 5. `server.start()/stop()/status()` — long-lived process lifecycle

- [x] 5.1 Implement `ServerResource.start(config: StartConfig, *, cwd=None, env=None) -> OdooProcess`: build CLI args from `StartConfig` fields (verified mapping to Odoo 19.0 options), spawn with `start_new_session=True`, register in client's registry, return `OdooProcess` (id=uuid4().hex).
- [x] 5.2 Implement `ServerResource.status(proc) -> ProcessStatus`: verify proc is in registry (else `ProcessNotFoundError`), poll `proc.poll()` and return `state="running"` (`returncode=None`) or `"exited"` with code.
- [x] 5.3 Implement `ServerResource.stop(proc, *, timeout=10.0)`: graceful `SIGTERM` (POSIX killpg) or `taskkill /T` (Windows), wait, then `SIGKILL` if needed. Idempotent: `ESRCH`/missing=policy-treat-as-stopped; no error.
- [x] 5.4 Add `__main__` self-check: start an odoo-equivalent stub (or a real Odoo if available in dev), stop, double-stop, status checks.

## 6. `server.wait_ready()` — readiness polling

- [x] 6.1 Create `src/odoo_instance_sdk/_health.py` with `wait_ready(client, proc, *, timeout, poll_interval=1.0)` using `httpx.Client` with `GET <base_url>/web/health?db_server_status=true`.
- [x] 6.2 In each iteration: check linked proc not exited (else `ProcessExitedBeforeReady`); on HTTP 200 + body `status == "pass"` return `ReadinessResult`; otherwise sleep poll_interval.
- [x] 6.3 On overall timeout: raise `ReadinessTimeoutError` with last observed status if any.
- [x] 6.4 Expose `ServerResource.wait_ready(proc, *, timeout=60.0)` as thin shim to `_health.wait_ready`.
- [x] 6.5 Self-check against a stub HTTP server returning `{"status": "pass"}`.

## 7. `database.backup()` & `BackupArtifact`

- [x] 7.1 Create `src/odoo_instance_sdk/_platform_cache.py` with `default_backup_dir()` using stdlib only (POSIX `XDG_CACHE_HOME`/`~/.cache`, Windows `LOCALAPPDATA`/`~\AppData\Local`).
- [x] 7.2 Implement `DatabaseResource.backup(db, *, format="zip", include_filestore=True, dest=None, timeout=None)`: build request to `<base_url>/web/database/backup` with basic auth (`admin`, `master_pwd`); stream response to file via `httpx.Client.stream()` writing chunks; create dest dir if needed; filename `<db>.<format>`; overwrite existing.
- [x] 7.3 Verify against `addons/web/controllers/database.py` 19.0 the exact request fields (likely `name`, `format`, `master_pwd` via basic auth, `copy` flag) — adjust as needed.
- [x] 7.4 Return `BackupArtifact(path=<resolved abs path>, source_db=db, format=format, has_filestore=include_filestore, source_base_url=base_url)`.
- [x] 7.5 Self-check against a stub HTTP server returning a fixed zip stream.

## 8. Local-only guard

- [x] 8.1 Create `src/odoo_instance_sdk/_local_guard.py` with `assert_local(base_url: str) -> None` using `urllib.parse.urlsplit` + `ipaddress` stdlib; allow `localhost` (case-insensitive), IPv4 `127.0.0.0/8`, IPv6 `::1`. Raise `RemoteInstanceError` otherwise.
- [x] 8.2 Unit self-check covering: `localhost`, `127.0.0.1`, `127.1.2.3`, `::1`, `example.com`, `192.168.0.1`.

## 9. `database.restore()`

- [x] 9.1 Implement `DatabaseResource.restore(artifact, new_db, *, timeout=None)`: call `assert_local(client.config.base_url)` before any HTTP. Raise `RemoteInstanceError` on violation.
- [x] 9.2 Verify against `odoo/service/db.py` 19.0 the exact multipart shape for restore (fields: `master_pwd`, `name`, `backup_tmp` file part, `copy=True` flag if applicable).
- [x] 9.3 Implement multipart `POST <base_url>/web/database/restore` with `httpx` (`files=...` and `data=...`), basic auth.
- [x] 9.4 On HTTP error: parse body, raise `DatabaseError(status_code, message, body)`. Message must not contain `master_pwd`.
- [x] 9.5 Return `RestoreResult(new_db=new_db, source=artifact)`.
- [x] 9.6 Self-check against a stub server: successful restore, restore when guard fires (remote URL), restore when server returns 400.

## 10. `database.list()/exists()/drop()`

- [x] 10.1 Implement `DatabaseResource.list()`: `POST <base_url>/web/database/list` (verify shape in `addons/web/controllers/database.py` 19.0); return `list[str]` (parse JSON `databases` field).
- [x] 10.2 Implement `DatabaseResource.exists(db)` as `return db in self.list()`. No additional HTTP method. Raises `DatabaseError` if list() errors.
- [x] 10.3 Implement `DatabaseResource.drop(db, *, timeout=None)`: call `assert_local(base_url)` first (raise `RemoteInstanceError`). Then `POST <base_url>/web/database/drop` with shape verified from `odoo/service/db.py` 19.0.
- [x] 10.4 On HTTP error during `drop`: raise `DatabaseError(status_code, message, body)`.
- [x] 10.5 Return `DropResult(db=db)`.
- [x] 10.6 Self-check: list + exists + drop on stub http server; drop on remote URL → `RemoteInstanceError`.

## 11. CLI verification with real Odoo 19.0

- [x] 11.1 Download/inspect Odoo 19.0 `odoo/tools/config.py` and confirm `StartConfig` field set covers everything needed to launch HTTP server; add missing essential fields only (no speculation).
- [x] 11.2 Inspect `addons/web/controllers/database.py` 19.0 to confirm exact request shapes for `backup`, `restore`, `list`, `drop` (parameter names, basic auth usage, multipart layout).
- [x] 11.3 Inspect `addons/web/controllers/home.py` 19.0 for `/web/health` exact route, query param `db_server_status`, and response JSON shape.
- [x] 11.4 Adjust any of steps 7–10 if discrepancies found; record findings in `design.md` (Open Questions update is fine).

## 12. Final checks & packaging

- [x] 12.1 Run `ruff check .` — must exit 0.
- [x] 12.2 Run `mypy --strict src/odoo_instance_sdk` — must exit 0.
- [x] 12.3 Run `uv build`; confirm both `dist/*.whl` and `dist/*.tar.gz` produced.
- [x] 12.4 Verify wheel and sdist both contain `pyproject.toml`, README, LICENSE, and `odoo_instance_sdk/` package files.
- [x] 12.5 Test install via `uv tool install ./dist/*.whl` and verify CLI works.
- [x] 12.6 Final pass: confirm `master_pwd` does not leak via `repr(OdooClientConfig(...))`, `repr(OdooClient(...))`, or any thrown exception message.