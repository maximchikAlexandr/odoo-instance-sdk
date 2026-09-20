from __future__ import annotations  # noqa: I001 -- keep database query lifecycle aliases grouped; remove when Ruff supports grouped aliases.

import contextlib
import os
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

import httpx

from odoo_instance_sdk.exceptions import (
    BackupCatalogError,
    BackupDownloadError,
    ConfigError,
    DatabaseError,
    DatabaseManagerUnavailableError,
    MasterPasswordRequiredError,
    PostgresClusterNotOwnedError,
)
from odoo_instance_sdk.internal.files import (
    ensure_destination,
    make_download_filename,
)
from odoo_instance_sdk.internal.paths import get_backups_dir
from odoo_instance_sdk.internal.redact import format_error
from odoo_instance_sdk.internal.sanitize import sanitize_last_error
from odoo_instance_sdk.internal.urls import assert_local, warn_if_cleartext_secret
from odoo_instance_sdk.models import (
    Backup,
    BackupFormat,
    Database,
    LocksResult,
    MonitoringInitializationResult,
    NoBackup,
    PostgresBloatResult,
    PostgresStatsResult,
    SqlExecutionResult,
)
from odoo_instance_sdk.resources.database.lifecycle import (
    _RESET_ADMIN_PASSWORD_SCRIPT as _RESET_ADMIN_PASSWORD_SCRIPT,
    _annotate_backup_failure as _annotate_backup_failure,
    _normalize_source_git_branch as _normalize_source_git_branch,
    _trustworthy_content_length as _trustworthy_content_length,
    _verify_database_via_psql as _verify_database_via_psql,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        ProcessExecutor,
        ProcessResult,
        RunContext,
    )
    from odoo_instance_sdk.resources.instance import OdooInstance

T = TypeVar("T")


class _QueriesMixin:
    if TYPE_CHECKING:
        base_url: str
        master_password: str | None
        _instance: OdooInstance

        def _action_command(
            self,
            step_id: str,
            description: str,
            callback: Callable[[], T],
            *,
            executor: ProcessExecutor | None,
            read_only: bool = False,
            mutating: bool = False,
            action_steps: Sequence[PreparedAction] = (),
            steps: Sequence[PreparedStep] = (),
            optional_steps: Sequence[str] = (),
        ) -> Command[T]: ...
        def _download_backup_part(
            self,
            database_name: str,
            password: str,
            part_path: Path,
            *,
            backup_id: str,
            timeout: float | None,
            format: BackupFormat,
            filestore: bool,
        ) -> tuple[str | None, int, str]: ...

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/web/database/{path}"

    def _require_password(self) -> str:
        if self.master_password is None:
            raise MasterPasswordRequiredError(
                f"Operation requires master password for {self.base_url}"
            )
        return self.master_password

    def _assert_local(self) -> None:
        assert_local(self.base_url)

    @property
    def _cluster(self) -> tuple[str | None, int] | None:
        if self._instance.config.db_host is None:
            return None
        return (self._instance.config.db_host, self._instance.config.db_port or 5432)

    def psql(self, args: tuple[str, ...] = ()) -> int:
        """Run native psql with the instance-bound identity and terminal."""
        return self.psql_command(args).run()

    def psql_command(
        self,
        args: tuple[str, ...] = (),
        *,
        executor: ProcessExecutor | None = None,
    ) -> Command[int]:
        """Prepare one inherited-TTY psql command and its readiness dependency."""
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.pg.builder import build_psql_specification
        from odoo_instance_sdk.internal.pg.context import resolve_database_context
        from odoo_instance_sdk.internal.proc import (
            SubprocessExecutor,
            wait_foreground,
        )

        binding = resolve_database_context(self._instance)
        specification = build_psql_specification(
            host=binding.host,
            port=binding.port,
            user=binding.user,
            password=binding.password,
            database=binding.database,
            args=args,
            mode="foreground",
            step_id="database.psql",
            _inject_timeout=False,
        )
        dependency_steps, dependency_temporary_path = self._instance._dependency_manifest()
        psql_step = specification.prepared_step
        prepared_steps: tuple[PreparedAction | PreparedStep, ...] = (
            *dependency_steps,
            psql_step,
        )

        def execute(context: RunContext[int]) -> int:
            self._instance._ensure_dependencies_ready(
                context,
                dependency_steps=dependency_steps,
                temporary_path=dependency_temporary_path,
            )
            for dependency_step in dependency_steps:
                if context.planned(dependency_step.step_id) and not context.consumed(
                    dependency_step.step_id
                ):
                    context.skip(dependency_step.step_id)
            handle = context.spawn(psql_step.step_id)
            return wait_foreground(handle)

        plan = ExecutionPlan(steps=tuple(step.public_projection() for step in prepared_steps))
        from odoo_instance_sdk.internal.proc import prepared_command

        return Command.from_prepared(
            plan,
            prepared_command(
                execute,
                prepared_steps,
                executor=executor or SubprocessExecutor(),
            ),
        )

    def execute_sql(self, sql: str, *, timeout: float = 30.0) -> SqlExecutionResult:
        """Execute caller SQL once through the captured shared process boundary."""
        return self.execute_sql_command(sql, timeout=timeout).run()

    def execute_sql_command(
        self,
        sql: str,
        *,
        timeout: float = 30.0,
        executor: ProcessExecutor | None = None,
    ) -> Command[SqlExecutionResult]:
        """Prepare one captured SQL stdin operation against the bound database."""
        if not isinstance(sql, str):
            raise TypeError("sql must be a string")
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.pg.builder import build_psql_specification
        from odoo_instance_sdk.internal.pg.context import resolve_database_context
        from odoo_instance_sdk.internal.proc import SubprocessExecutor
        from odoo_instance_sdk.internal.proc.redaction import redacted_projection

        binding = resolve_database_context(self._instance)
        specification = build_psql_specification(
            host=binding.host,
            port=binding.port,
            user=binding.user,
            password=binding.password,
            database=binding.database,
            stdin=sql.encode(),
            timeout=timeout,
            mode="captured",
            step_id="database.execute_sql",
            _trusted_args=("-q", "-t", "-A", "-v", "ON_ERROR_STOP=1"),
        )
        step = specification.prepared_step

        def execute(context: RunContext[SqlExecutionResult]) -> SqlExecutionResult:
            result = cast("ProcessResult", context.process(step.step_id))
            stdout = (
                result.stdout.decode(errors="replace")
                if isinstance(result.stdout, bytes)
                else (result.stdout or "")
            )
            stderr = (
                result.stderr.decode(errors="replace")
                if isinstance(result.stderr, bytes)
                else (result.stderr or "")
            )
            sanitized_stderr = sanitize_last_error(stderr) or ""
            safe_stderr = cast(
                "str",
                redacted_projection(
                    sanitized_stderr,
                    secrets=(binding.password,) if binding.password else (),
                    field="stderr",
                ),
            )
            safe_stdout = cast(
                "str",
                redacted_projection(
                    stdout,
                    secrets=(binding.password,) if binding.password else (),
                    field="stdout",
                ),
            )
            return SqlExecutionResult(
                returncode=result.returncode,
                stdout=safe_stdout,
                stderr=safe_stderr,
            )

        plan = ExecutionPlan(steps=(step.public_projection(),))
        from odoo_instance_sdk.internal.proc import prepared_command

        return Command.from_prepared(
            plan,
            prepared_command(
                execute,
                (step,),
                executor=executor or SubprocessExecutor(),
            ),
        )

    def locks(self, database: str, *, top: int = 20, timeout: float = 30.0) -> LocksResult:
        """Return a bounded, typed snapshot of active PostgreSQL blockers."""
        return self.locks_command(database, top=top, timeout=timeout).run()

    def locks_command(
        self,
        database: str,
        *,
        top: int = 20,
        timeout: float = 30.0,
        executor: ProcessExecutor | None = None,
    ) -> Command[LocksResult]:
        from odoo_instance_sdk.internal.pg.locks import build_locks_sql, decode_locks

        return self._captured_diagnostic_command(
            database=database,
            sql=build_locks_sql(top=top, timeout=timeout),
            timeout=timeout,
            step_id="database.locks.psql",
            decoder=decode_locks,
            executor=executor,
        )

    def stats(self, database: str, *, top: int = 20, timeout: float = 30.0) -> PostgresStatsResult:
        """Return a bounded, typed snapshot of PostgreSQL table statistics."""
        return self.stats_command(database, top=top, timeout=timeout).run()

    def stats_command(
        self,
        database: str,
        *,
        top: int = 20,
        timeout: float = 30.0,
        executor: ProcessExecutor | None = None,
    ) -> Command[PostgresStatsResult]:
        from odoo_instance_sdk.internal.pg.stats import build_stats_sql, decode_stats

        return self._captured_diagnostic_command(
            database=database,
            sql=build_stats_sql(top=top, timeout=timeout),
            timeout=timeout,
            step_id="database.stats.psql",
            decoder=decode_stats,
            executor=executor,
        )

    def bloat(
        self,
        database: str,
        *,
        top: int = 20,
        exact_max_scan_mb: int = 64,
        timeout: float = 30.0,
    ) -> PostgresBloatResult:
        """Return bounded estimated and optional exact bloat diagnostics."""
        return self.bloat_command(
            database,
            top=top,
            exact_max_scan_mb=exact_max_scan_mb,
            timeout=timeout,
        ).run()

    def bloat_command(
        self,
        database: str,
        *,
        top: int = 20,
        exact_max_scan_mb: int = 64,
        timeout: float = 30.0,
        executor: ProcessExecutor | None = None,
    ) -> Command[PostgresBloatResult]:
        from odoo_instance_sdk.internal.pg.bloat import build_bloat_sql, decode_bloat

        return self._captured_diagnostic_command(
            database=database,
            sql=build_bloat_sql(top=top, exact_max_scan_mb=exact_max_scan_mb, timeout=timeout),
            timeout=timeout,
            step_id="database.bloat.psql",
            decoder=decode_bloat,
            executor=executor,
        )

    def init_monitoring(
        self, database: str, *, timeout: float = 30.0
    ) -> MonitoringInitializationResult:
        """Install only the explicitly supported monitoring extensions."""
        return self.init_monitoring_command(database, timeout=timeout).run()

    def init_monitoring_command(
        self,
        database: str,
        *,
        timeout: float = 30.0,
        executor: ProcessExecutor | None = None,
    ) -> Command[MonitoringInitializationResult]:
        from odoo_instance_sdk.internal.pg.monitoring import (
            build_monitoring_sql,
            decode_monitoring,
        )

        if self._instance._postgres_cluster is None or not self._instance._postgres_cluster.owned:
            raise PostgresClusterNotOwnedError(
                "monitoring initialization requires an SDK-owned PostgreSQL cluster"
            )
        return self._captured_diagnostic_command(
            database=database,
            sql=build_monitoring_sql(timeout=timeout),
            timeout=timeout,
            step_id="database.init-monitoring.psql",
            decoder=decode_monitoring,
            executor=executor,
            mutating=True,
        )

    def _captured_diagnostic_command(
        self,
        *,
        database: str,
        sql: str,
        timeout: float,
        step_id: str,
        decoder: Callable[[str], T],
        executor: ProcessExecutor | None,
        mutating: bool = False,
    ) -> Command[T]:
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.pg.builder import build_psql_specification
        from odoo_instance_sdk.internal.pg.context import resolve_database_context
        from odoo_instance_sdk.internal.proc import SubprocessExecutor

        binding = resolve_database_context(self._instance, explicit=database)
        specification = build_psql_specification(
            host=binding.host,
            port=binding.port,
            user=binding.user,
            password=binding.password,
            database=binding.database,
            stdin=sql.encode(),
            timeout=timeout,
            mode="captured",
            step_id=step_id,
            _trusted_args=("-q", "-t", "-A", "-v", "ON_ERROR_STOP=1"),
            _read_only=not mutating,
            _mutating=mutating,
        )
        dependency_steps, dependency_temporary_path = self._instance._dependency_manifest()
        psql_step = specification.prepared_step
        prepared_steps: tuple[PreparedAction | PreparedStep, ...] = (
            *dependency_steps,
            psql_step,
        )

        def execute(context: RunContext[T]) -> T:
            self._instance._ensure_dependencies_ready(
                context,
                dependency_steps=dependency_steps,
                temporary_path=dependency_temporary_path,
            )
            for dependency_step in dependency_steps:
                if context.planned(dependency_step.step_id) and not context.consumed(
                    dependency_step.step_id
                ):
                    context.skip(dependency_step.step_id)
            result = cast("ProcessResult", context.process(psql_step.step_id))
            if result.returncode != 0:
                raise ConfigError(
                    f"PostgreSQL diagnostic command failed with exit code {result.returncode}"
                ) from None
            stdout = (
                result.stdout.decode(errors="replace")
                if isinstance(result.stdout, bytes)
                else (result.stdout or "")
            )
            return decoder(stdout)

        plan = ExecutionPlan(steps=tuple(step.public_projection() for step in prepared_steps))
        from odoo_instance_sdk.internal.proc import prepared_command

        return Command.from_prepared(
            plan,
            prepared_command(
                execute,
                prepared_steps,
                executor=executor or SubprocessExecutor(),
            ),
        )

    def _latest_backup_for(self, db_host: str | None, db_port: int, name: str) -> Backup | NoBackup:
        b = self._instance._client.get_catalog().latest_restore(db_host, db_port, name)
        return b if b is not None else NoBackup()

    @contextlib.contextmanager
    def _http(self, timeout: float | None = None) -> Iterator[httpx.Client]:
        warn_if_cleartext_secret(self.base_url)
        effective = (
            timeout if timeout is not None else self._instance._client.config.http_timeout_seconds
        )
        with httpx.Client(timeout=httpx.Timeout(effective)) as http:
            yield http

    def names(self) -> tuple[str, ...]:
        """Return database names without touching the local audit catalog."""
        from odoo_instance_sdk.internal.proc import active_context
        from odoo_instance_sdk.resources.instance import active_auxiliary_restore_session

        session = active_auxiliary_restore_session()
        context = active_context()
        if session is not None:
            if context is None:
                raise DatabaseManagerUnavailableError(
                    "auxiliary database manager has no active execution context"
                )
            session.ensure_started(context)
        try:
            with self._http() as http:
                resp = http.post(
                    self._url("list"),
                    json={"jsonrpc": "2.0", "method": "call", "params": {}},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise DatabaseError(
                status_code=exc.response.status_code,
                message=format_error(exc.response.text),
                body=exc.response.content,
            ) from exc
        except httpx.HTTPError as exc:
            raise DatabaseManagerUnavailableError(
                f"Database manager unavailable on {self.base_url}: {format_error(exc)}"
            ) from exc
        if not isinstance(data, dict):
            raise DatabaseManagerUnavailableError(
                f"Unexpected response from {self.base_url}: not a JSON object"
            )
        result = data.get("result", [])
        if not isinstance(result, list):
            raise DatabaseManagerUnavailableError(
                f"Database listing disabled or unavailable on {self.base_url}"
            )
        return tuple(str(name) for name in result)

    def list(self) -> tuple[Database, ...]:
        db_names = self.names()

        ck = self._cluster
        catalog = self._instance._client.get_catalog()

        databases = []
        for name in db_names:
            if ck is not None:
                db_host, db_port = ck
                backup = self._latest_backup_for(db_host, db_port, name)
            else:
                backup = NoBackup()
            databases.append(Database(name=name, backup=backup))

        if ck is not None:
            db_host, db_port = ck
            restored_names = catalog.distinct_restored_database_names(db_host, db_port)
            current_set = set(db_names)
            for rname in restored_names:
                if rname not in current_set:
                    catalog.record_database_dropped(db_host, db_port, rname)

        return tuple(databases)

    def exists(self, name: str) -> bool:
        from odoo_instance_sdk.internal.proc import active_context

        context = active_context()
        if context is not None:
            # The refresh coordinator reserves a separate probe for its
            # construction-time target check.  Selecting only a planned,
            # unconsumed identifier keeps this nested domain query on the
            # coordinator's exact immutable ledger.
            for step_id in (
                "database.restore.exists-reservation",
                "database.restore.exists-before",
                "database.restore.exists-after",
                "database.drop.exists-after",
                "environment.remove.database.exists-before",
                "environment.remove.database.exists-after",
                "environment.remove.database.exists-postcondition",
                "pgadmin.database.exists.psql",
            ):
                if context.planned(step_id) and not context.consumed(step_id):
                    return self._exists_impl(name, psql_step_id=step_id)
            return self._exists_impl(name)
        return self.exists_command(name).run()

    def exists_command(
        self, name: str, *, executor: ProcessExecutor | None = None
    ) -> Command[bool]:
        probe = self._psql_probe_for(name, "database.exists.psql")
        return self._action_command(
            "database.exists",
            "Check whether a database exists",
            lambda: self._exists_impl(name, psql_step_id=probe.step_id if probe else None),
            executor=executor,
            read_only=True,
            steps=(probe,) if probe is not None else (),
            optional_steps=(probe.step_id,) if probe is not None else (),
        )

    def _exists_impl(self, name: str, *, psql_step_id: str | None = None) -> bool:
        from odoo_instance_sdk.internal.proc import active_context

        ck = self._cluster
        direct_result = self._planned_exists_result(name, psql_step_id)
        if direct_result is not None:
            return direct_result

        try:
            databases = self.list()
        except DatabaseManagerUnavailableError:
            if ck is not None and self._instance.config.db_user is not None:
                db_host, db_port = ck
                result = _verify_database_via_psql(
                    db_host,
                    db_port,
                    self._instance.config.db_user,
                    self._instance.config.db_password,
                    name,
                    step_id=psql_step_id,
                )
                if result is True:
                    return True
                if result is False:
                    catalog = self._instance._client.get_catalog()
                    catalog.record_database_dropped(db_host, db_port, name)
                    return False
            raise

        found = any(db.name == name for db in databases)
        if psql_step_id is not None:
            context = active_context()
            if context is not None and context.planned(psql_step_id):
                context.skip(psql_step_id)
        if not found and ck is not None:
            db_host, db_port = ck
            catalog = self._instance._client.get_catalog()
            if catalog.has_tracked_database(db_host, db_port, name):
                catalog.record_database_dropped(db_host, db_port, name)
        return found

    def _planned_exists_result(self, name: str, step_id: str | None) -> bool | None:
        ck = self._cluster
        user = self._instance.config.db_user
        if step_id is None or ck is None or user is None:
            return None
        db_host, db_port = ck
        result = _verify_database_via_psql(
            db_host,
            db_port,
            user,
            self._instance.config.db_password,
            name,
            step_id=step_id,
        )
        if result is None:
            raise DatabaseManagerUnavailableError(
                f"PostgreSQL database existence probe failed for {name!r}"
            )
        if not result:
            catalog = self._instance._client.get_catalog()
            if catalog.has_tracked_database(db_host, db_port, name):
                catalog.record_database_dropped(db_host, db_port, name)
        return result

    def __getitem__(self, index: int) -> Database:
        if not isinstance(index, int):
            raise TypeError(
                f"DatabaseResource indices must be integers, not {type(index).__name__}"
            )
        return self.list()[index]

    def current(self) -> Database:
        return self.current_command().run()

    def current_command(self, *, executor: ProcessExecutor | None = None) -> Command[Database]:
        configured = self._instance.config.configured_database_names
        probe = self._psql_probe_for(configured[0], "database.current.psql") if configured else None
        return self._action_command(
            "database.current",
            "Resolve the configured current database",
            lambda: self._current_impl(psql_step_id=probe.step_id if probe else None),
            executor=executor,
            read_only=True,
            steps=(probe,) if probe is not None else (),
            optional_steps=(probe.step_id,) if probe is not None else (),
        )

    def _psql_probe_for(self, name: str, step_id: str) -> PreparedStep | None:
        cluster = self._cluster
        user = self._instance.config.db_user
        if cluster is None or user is None:
            return None
        host, port = cluster
        from odoo_instance_sdk.internal.pg.builder import build_psql_specification

        query_name = name.replace("'", "''")
        try:
            return build_psql_specification(
                step_id=step_id,
                host=host,
                port=port,
                user=user,
                password=self._instance.config.db_password,
                database="postgres",
                args=(
                    "-c",
                    f"SELECT 1 FROM pg_database WHERE datname='{query_name}'",
                ),
                _trusted_args=("-t", "-A"),
                timeout=30.0,
            ).prepared_step
        except FileNotFoundError:
            return None

    def _current_impl(self, *, psql_step_id: str | None = None) -> Database:
        configured = self._instance.config.configured_database_names
        if not configured:
            return Database(name="", backup=NoBackup())

        name = configured[0]

        try:
            databases = self.list()
        except DatabaseManagerUnavailableError:
            ck = self._cluster
            if ck is not None and self._instance.config.db_user is not None:
                db_host, db_port = ck
                exists_result = _verify_database_via_psql(
                    db_host,
                    db_port,
                    self._instance.config.db_user,
                    self._instance.config.db_password,
                    name,
                    step_id=psql_step_id,
                )
                catalog = self._instance._client.get_catalog()
                if exists_result is True:
                    backup = self._latest_backup_for(db_host, db_port, name)
                    return Database(name=name, backup=backup)
                if exists_result is False:
                    catalog.record_database_dropped(db_host, db_port, name)
                    return Database(name=name, backup=NoBackup())
                return Database(name=name, backup=NoBackup())
            raise

        ck = self._cluster
        catalog = self._instance._client.get_catalog()

        found = any(db.name == name for db in databases)

        if not found:
            if ck is not None:
                db_host, db_port = ck
                catalog.record_database_dropped(db_host, db_port, name)
            return Database(name=name, backup=NoBackup())

        if ck is not None:
            db_host, db_port = ck
            backup = self._latest_backup_for(db_host, db_port, name)
        else:
            backup = NoBackup()

        return Database(name=name, backup=backup)

    def backup(
        self,
        database_name: str,
        *,
        format: BackupFormat = BackupFormat.ZIP,
        filestore: bool = True,
        destination: str | Path | None = None,
        timeout: float | None = None,
        source_git_branch: str | None = None,
        project_id: str | None = None,
    ) -> Backup:
        return self.backup_command(
            database_name,
            format=format,
            filestore=filestore,
            destination=destination,
            timeout=timeout,
            source_git_branch=source_git_branch,
            project_id=project_id,
        ).run()

    def backup_command(
        self,
        database_name: str,
        *,
        format: BackupFormat = BackupFormat.ZIP,
        filestore: bool = True,
        destination: str | Path | None = None,
        timeout: float | None = None,
        source_git_branch: str | None = None,
        project_id: str | None = None,
        executor: ProcessExecutor | None = None,
    ) -> Command[Backup]:
        from odoo_instance_sdk.internal.proc import PreparedAction

        return self._action_command(
            "database.backup",
            "Create a database backup",
            lambda: self._backup_impl(
                database_name,
                format=format,
                filestore=filestore,
                destination=destination,
                timeout=timeout,
                source_git_branch=source_git_branch,
                project_id=project_id,
            ),
            executor=executor,
            mutating=True,
            action_steps=(
                PreparedAction(
                    step_id="database.backup.wait",
                    action="database.backup.wait",
                    description="Wait for backup response headers",
                    mutating=True,
                ),
                PreparedAction(
                    step_id="database.backup.transfer",
                    action="database.backup.transfer",
                    description="Transfer backup bytes",
                    mutating=True,
                ),
            ),
        )

    def _backup_impl(  # noqa: C901
        self,
        database_name: str,
        *,
        format: BackupFormat,
        filestore: bool,
        destination: str | Path | None,
        timeout: float | None,
        source_git_branch: str | None,
        project_id: str | None = None,
    ) -> Backup:
        source_git_branch = _normalize_source_git_branch(source_git_branch)
        pwd = self._require_password()

        if destination is None:
            destination = self._instance._client.config.backups_directory
        else:
            destination = Path(destination)
        if destination is None:
            destination = get_backups_dir()
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(destination, 0o700)

        backup_id = str(uuid.uuid4())
        part_path = ensure_destination(destination, f"{backup_id}.{format.value}.part")
        part_preexisted = part_path.exists()
        published = False
        catalog = self._instance._client.get_catalog()
        resolved_project_id = (
            project_id
            if project_id is not None
            else (
                self._instance._runtime_binding.project_id
                if self._instance._runtime_binding is not None
                else None
            )
        )
        catalog.start_download(
            backup_id=backup_id,
            source_base_url=self.base_url,
            database_name=database_name,
            format=format.value,
            filestore_requested=filestore,
            path=part_path,
            source_git_branch=source_git_branch,
            project_id=resolved_project_id,
        )

        try:
            server_filename, size_bytes, sha256_hex = self._download_backup_part(
                database_name,
                pwd,
                part_path,
                backup_id=backup_id,
                timeout=(
                    timeout
                    if timeout is not None
                    else self._instance._client.config.backup_timeout_seconds
                ),
                format=format,
                filestore=filestore,
            )

            actual_filename = make_download_filename(backup_id, server_filename)
            final_path = ensure_destination(destination, actual_filename)
            if final_path != part_path:
                if final_path.exists():
                    raise BackupDownloadError("Backup destination already exists")  # noqa: TRY301
                part_path.rename(final_path)
            os.chmod(final_path, 0o600)

            if not final_path.resolve().is_relative_to(destination.resolve()):
                with contextlib.suppress(OSError):
                    final_path.unlink()
                raise BackupDownloadError("Path traversal detected after rename")  # noqa: TRY301

            catalog.update_path(backup_id, final_path)
            downloaded_at = datetime.now(UTC)
            catalog.success_download(
                backup_id, final_path.name, size_bytes, sha256_hex, downloaded_at=downloaded_at
            )
            published = True

            return Backup(
                id=uuid.UUID(backup_id),
                source_base_url=self.base_url,
                database_name=database_name,
                format=format,
                filestore_requested=filestore,
                path=str(final_path),
                filename=final_path.name,
                size_bytes=size_bytes,
                sha256=sha256_hex,
                downloaded_at=downloaded_at,
                source_git_branch=source_git_branch,
            )
        except (OSError, BackupCatalogError, BackupDownloadError) as e:
            with contextlib.suppress(BackupCatalogError):
                catalog.fail_download(backup_id, type(e).__name__, format_error(e))
            if not part_preexisted and part_path.exists():
                with contextlib.suppress(OSError):
                    part_path.unlink()
            _annotate_backup_failure(e, backup_id, published=published)
            raise
        except BaseException as e:
            if not published:
                with contextlib.suppress(BackupCatalogError):
                    catalog.fail_download(backup_id, type(e).__name__, format_error(e))
                if not part_preexisted and part_path.exists():
                    with contextlib.suppress(OSError):
                        part_path.unlink()
            _annotate_backup_failure(e, backup_id, published=published)
            raise
