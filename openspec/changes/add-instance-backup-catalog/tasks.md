# Implementation tasks

## 1. Public contracts and package layout

- [x] 1.1 Add standard `StrEnum` values and frozen msgspec data models for backups, events, validation and deletion in `src/odoo_instance_sdk/models.py` exactly as defined in `design.md`; do not use msgspec for resources or dependency containers.
- [x] 1.2 Add the new typed exceptions and safe secret-free formatting in `src/odoo_instance_sdk/exceptions.py`; do not add `BackupValidationError`. Rename `RemoteInstanceError` to `NonLocalInstanceError` without compatibility alias.
- [x] 1.3 Remove `BackupArtifact` from `src/odoo_instance_sdk/models.py` and public exports; replace it with `Backup`. Change `RestoreResult.source` field type from `BackupArtifact` to `Backup`.
- [x] 1.4 Define the target resources package exports in `src/odoo_instance_sdk/resources/__init__.py`.
- [x] 1.5 Update `src/odoo_instance_sdk/__init__.py` so every public model, enum, exception and client class in the specs is exported and no removed symbol (`BackupArtifact`, `RemoteInstanceError`) remains.
- [x] 1.6 Remove the `_StructMeta` metaclass and `_matches` helper from `src/odoo_instance_sdk/models.py`; drop `OdooClientConfig` from `models.py` (it moves to `config.py` as a frozen dataclass in task 4.3). Verify `StartConfig` and all remaining `msgspec.Struct` subclasses construct and validate without the metaclass — none contain union types with custom classes after `OdooClientConfig` is gone. Confirm zero `Any` imports and zero `type: ignore` comments in `src/`.

## 2. URL and Odoo config handling

- [x] 2.1 Implement normalized HTTP/HTTPS base URL parsing and local-loopback detection in `src/odoo_instance_sdk/internal/urls.py`.
- [x] 2.2 Add exhaustive URL normalization and rejection tests in `tests/unit/internal/test_urls.py`, including default ports, IPv6, credentials, path, query, fragment, private networks and malformed values.
- [x] 2.3 Implement `[options]` parsing with `RawConfigParser(interpolation=None)` and Odoo 19.0 defaults in `src/odoo_instance_sdk/internal/odoo_config.py`.
- [x] 2.4 Add config fixtures for loopback, IPv6, wildcard, missing section, invalid port, explicit override and comma-separated `db_name` in `tests/fixtures/odoo_config/`.
- [x] 2.5 Test config parsing and base URL inference rules in `tests/unit/internal/test_odoo_config.py`.

## 3. SQLite backup catalog

- [x] 3.1 Implement cache-root and default backup-directory resolution with `platformdirs` in `src/odoo_instance_sdk/internal/paths.py`; delete the old `src/odoo_instance_sdk/_platform_cache.py` module.
- [x] 3.2 Add `platformdirs` as a runtime dependency and keep the lockfile current in `pyproject.toml` and `uv.lock`.
- [x] 3.3 Implement `BackupCatalog` as `@dataclass(slots=True, kw_only=True)` and add schema version 1, tables, indexes and connection pragmas from `design.md` in `src/odoo_instance_sdk/storage/backup_catalog.py`.
- [x] 3.4 Implement transactional methods for start/success/failure download, validation event, deletion, available-list query, latest query, history query and identity verification in `src/odoo_instance_sdk/storage/backup_catalog.py`.
- [x] 3.5 Map every sqlite error to `BackupCatalogError` without deleting or silently recreating the database in `src/odoo_instance_sdk/storage/backup_catalog.py`.
- [x] 3.6 Test schema creation, `PRAGMA user_version`, all state transitions, full audit retention and ordering in `tests/unit/storage/test_backup_catalog.py`.
- [x] 3.7 Test two catalog instances writing sequentially to the same WAL database and respecting busy timeout in `tests/unit/storage/test_backup_catalog_concurrency.py`.

## 4. Instance resource and breaking client wiring

- [x] 4.1 Implement callable `InstanceFactory` as `@dataclass(slots=True, kw_only=True)` with `__call__()` and `from_config()` in `src/odoo_instance_sdk/resources/instance.py`.
- [x] 4.2 Implement `OdooInstance` as `@dataclass(slots=True, kw_only=True)` with normalized `base_url`, private secret field using `repr=False`, informational configured database names, one bound `DatabaseResource` and server lifecycle methods (`run`, `start`, `stop`, `status`, `wait_ready`) that delegate to an internal `ServerResource` in `src/odoo_instance_sdk/resources/instance.py`. Do not expose `instance.server` publicly.
- [x] 4.3 Refactor `OdooClientConfig` as `@dataclass(frozen=True, slots=True, kw_only=True)` in `src/odoo_instance_sdk/config.py`; add `InstanceConfig` with the same dataclass options and `master_password=field(..., repr=False)`.
- [x] 4.4 Keep `OdooClient` as dataclass; wire `client.instance` and `client.backups` in `src/odoo_instance_sdk/client.py`. Move process registry (`_processes` dict and subprocess handles) to private fields on `OdooClient` shared across instances. Update `OdooClient.__repr__` to not reference `base_url` (it no longer lives on `OdooClientConfig`).
- [x] 4.5 Remove `client.database` and `client.server` and any compatibility alias from `src/odoo_instance_sdk/client.py`.
- [x] 4.6 Add public API tests for multiple isolated instances, optional master password, `from_config()`, shared process registry across instances and absence of the old `client.database`/`client.server` API in `tests/unit/test_client.py`.

## 4a. Server lifecycle and readiness migration

- [x] 4a.1 Move `ServerResource` into `src/odoo_instance_sdk/resources/instance.py` (or a dedicated internal module consumed by `OdooInstance`); keep `run()`, `start()`, `stop()`, `status()` behavior identical to the current implementation, reading executable and timeout from `OdooClientConfig` and resolving handles via the shared registry on `OdooClient`.
- [x] 4a.2 Implement `OdooInstance.wait_ready(proc, *, timeout=60.0)` delegating to `poll_health` with `base_url=OdooInstance.base_url` and `alive_check` resolving the handle through the shared registry; no Basic Auth, no master password.
- [x] 4a.3 Refactor `_health.py` into `src/odoo_instance_sdk/internal/health.py`: `poll_health(base_url: str, *, timeout, poll_interval, alive_check)` without `OdooClientConfig` and without `auth=`; GET `/web/health?db_server_status=true` with `httpx.Client(timeout=...)` only.
- [x] 4a.4 Rename `warn_if_cleartext_auth` to `warn_if_cleartext_secret` in `src/odoo_instance_sdk/internal/urls.py` (or a dedicated internal module); keep the once-per-process semantics and the HTTP-to-non-local-host trigger (master password in form POST is cleartext risk).
- [x] 4a.5 Add tests for readiness success, process-exit-before-ready, readiness timeout and shared-registry access across two instances in `tests/unit/resources/test_instance_lifecycle.py`.

## 5. Instance-bound database resource

- [x] 5.1 Refactor `DatabaseResource` as `@dataclass(slots=True, kw_only=True)` with explicit instance-bound URL/password, shared transport, catalog and paths in `src/odoo_instance_sdk/resources/database.py`; secret fields use `repr=False`.
- [x] 5.2 Preserve Odoo response order in `list()` and implement exact membership in `exists()` in `src/odoo_instance_sdk/resources/database.py`.
- [x] 5.3 Add `MasterPasswordRequiredError` guards for backup, restore and drop before HTTP invocation in `src/odoo_instance_sdk/resources/database.py`.
- [x] 5.4 Apply nonlocal restore/drop guard before transport invocation in `src/odoo_instance_sdk/resources/database.py`.
- [x] 5.5 Inline HTTP request decoding directly in `src/odoo_instance_sdk/resources/database.py` (http_models.py was not created; decoding is handled inline).
- [x] 5.6 Remove HTTP Basic Auth from `DatabaseResource._http()`: create `httpx.Client(timeout=...)` without `auth=`; pass `master_pwd` only as a form field in POST bodies. Call `warn_if_cleartext_secret(base_url, stacklevel=3)` instead of `warn_if_cleartext_auth`.
- [x] 5.7 Move the `_redact()` secret-sanitization helper from the current `database.py` into `src/odoo_instance_sdk/internal/redact.py` for reuse by catalog events, restore error messages and any other path that handles typed exceptions with secret context.
- [x] 5.8 Test list/exists, missing password, instance URL isolation, remote destructive-operation rejection and absence of Basic Auth in `tests/unit/resources/test_database_resource.py`.

## 6. Audited backup download

- [x] 6.1 Implement safe server filename extraction, UUID prefixing, fallback filename and destination containment in `src/odoo_instance_sdk/internal/files.py`.
- [x] 6.2 Refactor `DatabaseResource.backup()` to create the audit row before network I/O, stream to `.part`, atomically replace final file and finalize success/failure audit in `src/odoo_instance_sdk/resources/database.py`.
- [x] 6.3 Ensure all error paths remove only the current `.part` file and preserve existing final files in `src/odoo_instance_sdk/resources/database.py`.
- [x] 6.4 Return the exact frozen `Backup` model from successful downloads and verify it round-trips through the SQLite catalog in `src/odoo_instance_sdk/resources/database.py`.
- [x] 6.5 Add HTTP fixture behavior for successful streaming, missing Content-Disposition, unsafe filename, interrupted stream and server error in `tests/fixtures/odoo_database_server.py`.
- [x] 6.6 Add end-to-end download/audit tests in `tests/integration/test_backup_download.py`.

## 7. Backup resource queries and deletion

- [x] 7.1 Implement `BackupResource` as `@dataclass(slots=True, kw_only=True)` and add `list()`, `latest()` and `history()` with exact filters, ordering and missing-file behavior in `src/odoo_instance_sdk/resources/backup.py`.
- [x] 7.2 Implement idempotent `BackupResource.delete()` with catalog identity checks and `BackupDeletionResult` in `src/odoo_instance_sdk/resources/backup.py`.
- [x] 7.3 Test cross-process rehydration, URL normalization in filters, latest selection, missing-file skipping, full audit visibility and idempotent deletion in `tests/unit/resources/test_backup_resource.py`.

## 8. Backup validation

- [x] 8.1 Implement ZIP validation with `is_zipfile`, required root members, `testzip()` and JSON-object parsing in `src/odoo_instance_sdk/internal/backup_validation.py`.
- [x] 8.2 Implement dump validation with `shutil.which`, `pg_restore --list`, timeout and raise/non-raise unavailable behavior in `src/odoo_instance_sdk/internal/backup_validation.py`.
- [x] 8.3 Implement `BackupResource.validate()` catalog checks, typed results and audit events in `src/odoo_instance_sdk/resources/backup.py`.
- [x] 8.4 Add valid, missing-member, invalid-manifest and CRC-corrupted ZIP fixtures in `tests/fixtures/backups/`.
- [x] 8.5 Add fake `pg_restore` executables for exit 0, nonzero and timeout cases in `tests/fixtures/pg_restore/`.
- [x] 8.6 Test every ZIP and dump validation branch, including audit recording and `raise_if_unavailable`, in `tests/unit/resources/test_backup_validation.py`.

## 9. Restore and drop migration

- [x] 9.1 Refactor `DatabaseResource.restore()` to accept only `Backup`, verify catalog identity/state/file and preserve Odoo 19.0 `copy` and `neutralize_database` fields in `src/odoo_instance_sdk/resources/database.py`.
- [x] 9.2 Preserve target-exists precondition and post-restore `exists()` confirmation in `src/odoo_instance_sdk/resources/database.py`.
- [x] 9.3 Preserve post-drop `exists()` confirmation and safe failure handling in `src/odoo_instance_sdk/resources/database.py`.
- [x] 9.4 Add integration tests proving remote backup to local restore, forged backup rejection, existing target rejection and remote restore/drop rejection in `tests/integration/test_database_lifecycle.py`.

## 10. Documentation, typing and release gates

- [x] 10.1 Rewrite README examples to use `client.instance(...).databases`, `client.instance.from_config(...)`, `instance.start(...)`, `instance.wait_ready(...)` and `client.backups` in `README.md`.
- [x] 10.2 Document the default cache layout, persistent audit, validation semantics, optional `pg_restore`, `/web/health` readiness (no auth), removal of Basic Auth and breaking migration (including `client.server` removal) in `README.md`.
- [x] 10.3 Add a changelog entry that explicitly removes `client.database`, `client.server`, `BackupArtifact` and `RemoteInstanceError` in `CHANGELOG.md`.
- [x] 10.4 Update package metadata and version for a breaking release in `pyproject.toml`.
- [x] 10.5 Run `uv run ruff check .`, `uv run ruff format --check .`, strict `uv run mypy`, and the full pytest suite; fix every failure without adding `Any` or converting logic containers to msgspec models.
- [x] 10.6 Build wheel and sdist with `uv build`, install the wheel in a clean environment and execute a smoke import covering all new public symbols.
- [x] 10.7 Run OpenSpec validation for `add-instance-backup-catalog` and fix every structural or scenario-format error before implementation begins.
