from __future__ import annotations  # noqa: I001 -- keep database backup lifecycle aliases grouped; remove when Ruff supports grouped aliases.

import contextlib
import os
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from odoo_instance_sdk.exceptions import (
    BackupDownloadError,
    BackupNotAvailableError,
    DatabaseAlreadyExistsError,
    DatabaseError,
    DatabaseManagerUnavailableError,
    DropFailedError,
    InstanceConfigurationError,
    RestoreFailedError,
)
from odoo_instance_sdk.internal.files import (
    extract_server_filename,
)
from odoo_instance_sdk.internal.locks import backup_lock_path, exclusive_lock
from odoo_instance_sdk.internal.redact import format_error
from odoo_instance_sdk.internal.transport import TransportError, TransportStatusError
from odoo_instance_sdk.models import (
    AdminPasswordResetResult,
    Backup,
    BackupFormat,
    DropResult,
    RestoreResult,
)
from odoo_instance_sdk.resources.database.lifecycle import (
    _admin_password_reset_script as _admin_password_reset_script,
    _stream_response_to_file,
    _trustworthy_content_length as _trustworthy_content_length,
)

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.dbprep.source import SelectedBackupRestorePayload
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        ProcessExecutor,
        RunContext,
    )
    from odoo_instance_sdk.internal.transport import OdooHttpClient
    from odoo_instance_sdk.resources.instance import OdooInstance

T = TypeVar("T")


class _BackupMixin:
    if TYPE_CHECKING:
        base_url: str
        master_password: str | None
        _instance: OdooInstance

        def _url(self, path: str) -> str: ...
        def _require_password(self) -> str: ...
        def _assert_local(self) -> None: ...
        @property
        def _cluster(self) -> tuple[str | None, int] | None: ...
        def _http(self, timeout: float | None = None) -> AbstractContextManager[OdooHttpClient]: ...
        def _exists_impl(self, name: str, *, psql_step_id: str | None = None) -> bool: ...
        def _psql_probe_for(self, name: str, step_id: str) -> PreparedStep | None: ...
        def exists(self, name: str) -> bool: ...

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
    ) -> tuple[str | None, int, str]:
        """Fetch a backup, converting HTTPX failures without retaining them."""
        http_failure: str | None = None
        with exclusive_lock(backup_lock_path(backup_id)):
            try:
                from odoo_instance_sdk.internal.proc import active_context
                from odoo_instance_sdk.resources.instance.auxiliary_restore import (
                    active_auxiliary_restore_session,
                )

                context = active_context()
                if context is not None and context.planned("database.backup.wait"):
                    context.action("database.backup.wait")
                auxiliary_session = active_auxiliary_restore_session()
                if auxiliary_session is not None and context is not None:
                    auxiliary_session.authorize_request(
                        context,
                        instance=self._instance,
                        request_action=auxiliary_session.backup_request_action,
                    )
                with (
                    self._http(timeout=timeout) as http,
                    http.stream(
                        "POST",
                        self._url("backup"),
                        data={
                            "master_pwd": password,
                            "name": database_name,
                            "backup_format": format.value,
                            "filestore": "true" if filestore else "false",
                        },
                    ) as resp,
                ):
                    resp.raise_for_status()
                    content_type = (
                        resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    )
                    if content_type and content_type not in {
                        "application/octet-stream",
                        "application/zip",
                        "application/x-zip-compressed",
                    }:
                        raise BackupDownloadError("Backup response was not an archive")
                    server_filename = extract_server_filename(
                        resp.headers.get("content-disposition")
                    )
                    expected_bytes = _trustworthy_content_length(resp.headers)
                    if context is not None and context.planned("database.backup.wait"):
                        context.complete_action("database.backup.wait")
                    if context is not None and context.planned("database.backup.transfer"):
                        context.action("database.backup.transfer")
                    size_bytes, sha256_hex = _stream_response_to_file(
                        resp,
                        part_path,
                        expected_bytes=expected_bytes,
                        progress=(
                            lambda received: (
                                context.progress(
                                    "database.backup.transfer", received, expected_bytes
                                )
                                if context is not None
                                and context.planned("database.backup.transfer")
                                else None
                            )
                        ),
                    )
                    if context is not None and context.planned("database.backup.transfer"):
                        context.complete_action("database.backup.transfer")
            except TransportStatusError as exc:
                # Keep only a status-derived value; the transport exception
                # carries no request/response/stream graph or master_pwd.
                http_failure = f"Backup request failed with HTTP status {exc.status_code}"
            except TransportError:
                # Do not format the exception: transport failures may carry
                # context that references the remote origin.
                http_failure = "Backup request failed"

        if http_failure is not None:
            raise BackupDownloadError(http_failure) from None
        return server_filename, size_bytes, sha256_hex

    def reset_admin_password(
        self, *, admin_password: str, provenance: str
    ) -> AdminPasswordResetResult:
        from odoo_instance_sdk.internal.proc import active_context

        context = active_context()
        if (
            context is not None
            and context.planned("instance.shell_script")
            and self._instance.config.start_config is not None
        ):
            configured = self._instance.config.configured_database_names
            if len(configured) != 1 or not configured[0].strip():
                raise InstanceConfigurationError(
                    "Administrator password reset requires exactly one configured database"
                )
            script = _admin_password_reset_script(admin_password)
            try:
                result = self._instance._run_shell_script_exclusive(
                    script,
                    commit=True,
                )
            except Exception:
                raise DatabaseManagerUnavailableError(
                    "Administrator password reset failed"
                ) from None
            if result.returncode != 0:
                raise DatabaseManagerUnavailableError("Administrator password reset failed")
            environment_id: uuid.UUID | None = None
            if self._instance._environment_id is not None:
                with contextlib.suppress(ValueError):
                    environment_id = uuid.UUID(self._instance._environment_id)
            return AdminPasswordResetResult(
                database=configured[0],
                completed=True,
                xml_id="base.user_admin",
                environment_id=environment_id,
                provenance=provenance,
            )
        return self.reset_admin_password_command(
            admin_password=admin_password, provenance=provenance
        ).run()

    def reset_admin_password_command(
        self,
        *,
        admin_password: str,
        provenance: str,
        executor: ProcessExecutor | None = None,
    ) -> Command[AdminPasswordResetResult]:
        self._assert_local()
        configured = self._instance.config.configured_database_names
        if len(configured) != 1 or not configured[0].strip():
            raise InstanceConfigurationError(
                "Administrator password reset requires exactly one configured database"
            )
        script = _admin_password_reset_script(admin_password)
        # Keep the legacy diagnostic seam for synthetic instances that cannot
        # construct a shell command.  Real instances use the captured shell
        # command below, so the child argv/stdin/env remain inspectable.
        if self._instance.config.start_config is None:
            return self._action_command(
                "database.reset-admin-password",
                "Reset the Odoo administrator password",
                lambda: self._reset_admin_password_impl(
                    admin_password=admin_password, provenance=provenance
                ),
                executor=executor,
                mutating=True,
            )

        return self._instance._shell_script_command(
            script,
            commit=True,
            exclusive=True,
            callback_override=lambda: self._reset_admin_password_impl(
                admin_password=admin_password, provenance=provenance
            ),
            executor=executor,
        )

    def _reset_admin_password_impl(
        self, *, admin_password: str, provenance: str
    ) -> AdminPasswordResetResult:
        """Reset ``base.user_admin`` on this instance's one bound database."""
        self._assert_local()
        configured = self._instance.config.configured_database_names
        if len(configured) != 1 or not configured[0].strip():
            raise InstanceConfigurationError(
                "Administrator password reset requires exactly one configured database"
            )

        database = configured[0]
        script = _admin_password_reset_script(admin_password)
        try:
            command = self._instance._run_shell_script_exclusive(
                script,
                commit=True,
            )
        except Exception:
            # Shell diagnostics may contain application data. Never expose them through
            # this resource's error surface.
            raise DatabaseManagerUnavailableError("Administrator password reset failed") from None
        if command.returncode != 0:
            raise DatabaseManagerUnavailableError("Administrator password reset failed")

        environment_id: uuid.UUID | None = None
        if self._instance._environment_id is not None:
            with contextlib.suppress(ValueError):
                environment_id = uuid.UUID(self._instance._environment_id)
        return AdminPasswordResetResult(
            database=database,
            completed=True,
            xml_id="base.user_admin",
            environment_id=environment_id,
            provenance=provenance,
        )

    def restore(
        self,
        backup: Backup,
        target_database_name: str,
        *,
        copy: bool = False,
        neutralize_database: bool = False,
        timeout: float | None = None,
    ) -> RestoreResult:
        from odoo_instance_sdk.internal.proc import active_context

        if active_context() is not None:
            return self._restore_impl(
                backup,
                target_database_name,
                copy=copy,
                neutralize_database=neutralize_database,
                timeout=timeout,
            )
        return self.restore_command(
            backup,
            target_database_name,
            copy=copy,
            neutralize_database=neutralize_database,
            timeout=timeout,
        ).run()

    def restore_command(
        self,
        backup: Backup,
        target_database_name: str,
        *,
        copy: bool = False,
        neutralize_database: bool = False,
        timeout: float | None = None,
        executor: ProcessExecutor | None = None,
    ) -> Command[RestoreResult]:
        before_probe = self._psql_probe_for(target_database_name, "database.restore.exists-before")
        after_probe = self._psql_probe_for(target_database_name, "database.restore.exists-after")
        probes = tuple(probe for probe in (before_probe, after_probe) if probe is not None)
        return self._action_command(
            "database.restore",
            "Restore a database backup",
            lambda: self._restore_impl(
                backup,
                target_database_name,
                copy=copy,
                neutralize_database=neutralize_database,
                timeout=timeout,
                before_step_id=before_probe.step_id if before_probe else None,
                after_step_id=after_probe.step_id if after_probe else None,
            ),
            executor=executor,
            mutating=True,
            steps=probes,
            optional_steps=tuple(probe.step_id for probe in probes),
        )

    def _restore_after_verified_absence(
        self,
        backup: Backup,
        target_database_name: str,
        *,
        copy: bool = False,
        neutralize_database: bool = False,
        timeout: float | None = None,
        record_provenance: bool = True,
    ) -> RestoreResult:
        """Restore after a caller consumed an authoritative absence probe."""
        from odoo_instance_sdk.internal.proc import active_context

        if active_context() is None:
            raise RuntimeError("verified restore requires an active command context")
        return self._restore_impl(
            backup,
            target_database_name,
            copy=copy,
            neutralize_database=neutralize_database,
            timeout=timeout,
            skip_existence_checks=True,
            record_provenance=record_provenance,
        )

    def _restore_local_archive(
        self,
        payload: SelectedBackupRestorePayload,
        target_database_name: str,
        *,
        copy: bool = False,
        neutralize_database: bool = False,
        timeout: float | None = None,
    ) -> None:
        """Restore one verified caller-owned archive without catalogue identity."""
        from odoo_instance_sdk.internal.dbprep.source import (
            SelectedBackupRestorePayload,
            _assert_verified_snapshot_unchanged,
        )
        from odoo_instance_sdk.internal.proc import active_context

        if active_context() is None:
            raise RuntimeError("verified local restore requires an active command context")
        if not isinstance(payload, SelectedBackupRestorePayload):
            raise InstanceConfigurationError("local restore evidence is unavailable")
        _assert_verified_snapshot_unchanged(payload)
        self._restore_impl_locked(
            payload,
            target_database_name,
            copy=copy,
            neutralize_database=neutralize_database,
            timeout=timeout,
        )

    def _restore_impl(
        self,
        backup: Backup,
        target_database_name: str,
        *,
        copy: bool,
        neutralize_database: bool,
        timeout: float | None,
        skip_existence_checks: bool = False,
        before_step_id: str | None = None,
        after_step_id: str | None = None,
        record_provenance: bool = True,
    ) -> RestoreResult:
        with exclusive_lock(backup_lock_path(str(backup.id))):
            self._restore_impl_locked(
                backup,
                target_database_name,
                copy=copy,
                neutralize_database=neutralize_database,
                timeout=timeout,
                skip_existence_checks=skip_existence_checks,
                before_step_id=before_step_id,
                after_step_id=after_step_id,
                record_provenance=record_provenance,
            )
        return RestoreResult(new_db=target_database_name, source=backup)

    def _restore_impl_locked(  # noqa: C901
        self,
        source: Backup | SelectedBackupRestorePayload,
        target_database_name: str,
        *,
        copy: bool,
        neutralize_database: bool,
        timeout: float | None,
        skip_existence_checks: bool = False,
        before_step_id: str | None = None,
        after_step_id: str | None = None,
        record_provenance: bool = True,
    ) -> None:
        from odoo_instance_sdk.internal.dbprep.source import SelectedBackupRestorePayload

        self._assert_local()
        pwd = self._require_password()

        if not isinstance(source, (Backup, SelectedBackupRestorePayload)):
            raise InstanceConfigurationError("restore evidence is unavailable")
        catalog = self._instance._client.get_catalog()
        source_kind = "catalogue"
        source_sha256: str | None = None
        if isinstance(source, Backup):
            catalog.verify_identity(source)
            archive_path = Path(source.path)
            archive_filename = source.filename
        else:
            from odoo_instance_sdk.internal.dbprep.source import _assert_verified_snapshot_unchanged

            _assert_verified_snapshot_unchanged(source)
            archive_path = source.verified_snapshot_path
            archive_filename = "odoo-archive.zip"
            source_kind = "local_archive"
            source_sha256 = source.verified_sha256

        # Classify the target before the remote effect.  A pending or malformed
        # managed claim is never downgraded to nullable legacy provenance.
        cluster_identity: str | None = None
        postgres_cluster = self._instance._postgres_cluster
        provenance = (
            None
            if postgres_cluster is None
            else getattr(postgres_cluster, "_restore_provenance", None)
        )
        if callable(provenance):
            cluster_identity, _ = provenance()
        start_config = self._instance.config.start_config
        data_directory: str | None = None
        if start_config is not None and start_config.data_dir:
            from odoo_instance_sdk.internal.odoo_config import _resolve_data_dir

            data_directory = str(_resolve_data_dir(start_config.data_dir, start_config.config_path))

        if data_directory is not None:
            from odoo_instance_sdk.internal.project_init import verify_project_owned_data_dir
            from odoo_instance_sdk.resources.instance.runtime import _RuntimeBinding

            binding = getattr(self._instance, "_runtime_binding", None)
            if isinstance(binding, _RuntimeBinding) and binding.owner_kind == "project":
                verify_project_owned_data_dir(binding.repository_root, data_directory)

        if not archive_path.is_file() or not os.access(archive_path, os.R_OK):
            if isinstance(source, Backup):
                raise BackupNotAvailableError("Backup file not found or unreadable")
            raise DatabaseManagerUnavailableError("local restore snapshot is unavailable")

        def target_exists(step_id: str | None) -> bool:
            if step_id is not None:
                return self._exists_impl(target_database_name, psql_step_id=step_id)
            return self.exists(target_database_name)

        if not skip_existence_checks and target_exists(before_step_id):
            raise DatabaseAlreadyExistsError(
                f"Database {target_database_name!r} already exists on {self.base_url}"
            )

        from odoo_instance_sdk.internal.proc import active_context
        from odoo_instance_sdk.internal.restore_stages import restore_stage_heartbeat
        from odoo_instance_sdk.resources.instance import active_auxiliary_restore_session

        auxiliary_session = active_auxiliary_restore_session()
        context = active_context()
        if auxiliary_session is not None:
            if context is None:
                raise DatabaseManagerUnavailableError(
                    "auxiliary database manager has no active execution context"
                )
            with restore_stage_heartbeat("auxiliary_start"):
                auxiliary_session.ensure_started(context)

        http_failure: tuple[int, str] | tuple[None, str] | None = None
        restore_http_failure: tuple[int, str] | None = None
        try:
            if auxiliary_session is not None and context is not None:
                auxiliary_session.authorize_request(
                    context,
                    instance=self._instance,
                    request_action=auxiliary_session.restore_request_action,
                )
            restore_timeout = (
                timeout
                if timeout is not None
                else self._instance._client.config.backup_timeout_seconds
            )
            with (
                restore_stage_heartbeat("db_restore"),
                open(archive_path, "rb") as fp,
                self._http(timeout=restore_timeout) as http,
            ):
                resp = http.post(
                    self._url("restore"),
                    data={
                        "master_pwd": pwd,
                        "name": target_database_name,
                        "copy": "true" if copy else "false",
                        "neutralize_database": "true" if neutralize_database else "false",
                    },
                    files={
                        "backup_file": (archive_filename, fp, "application/octet-stream"),
                    },
                )
                if resp.is_error:
                    restore_http_failure = (
                        resp.status_code,
                        f"Database restore failed with HTTP status {resp.status_code}",
                    )
        except TransportStatusError as exc:
            # Convert outside the except scope so the SDK error has no
            # transport cause/context/request/response/stream references.
            http_failure = (
                exc.status_code,
                f"Database restore failed with HTTP status {exc.status_code}",
            )
        except TransportError:
            http_failure = (None, "Database restore request failed")

        if restore_http_failure is not None:
            http_failure = restore_http_failure

        if not skip_existence_checks and not target_exists(after_step_id):
            if http_failure is not None:
                status_code, message = http_failure
                raise DatabaseError(
                    status_code=status_code or 0, message=message, body=b""
                ) from None
            raise RestoreFailedError(
                f"Database {target_database_name!r} was not created after restore"
            )

        ck = self._cluster
        if ck is not None and record_provenance:
            db_host, db_port = ck
            if source_kind == "local_archive":
                catalog.record_restore(
                    db_host,
                    db_port,
                    target_database_name,
                    source_kind=source_kind,
                    source_sha256=source_sha256,
                    cluster_id=cluster_identity,
                    data_directory=data_directory,
                )
            elif cluster_identity is None and data_directory is None:
                assert isinstance(source, Backup)
                catalog.record_restore(
                    db_host,
                    db_port,
                    target_database_name,
                    str(source.id),
                )
            else:
                assert isinstance(source, Backup)
                catalog.record_restore(
                    db_host,
                    db_port,
                    target_database_name,
                    str(source.id),
                    cluster_id=cluster_identity,
                    data_directory=data_directory,
                )

    def drop(
        self,
        database_name: str,
        *,
        timeout: float | None = None,
    ) -> DropResult:
        return self.drop_command(database_name, timeout=timeout).run()

    def drop_command(
        self,
        database_name: str,
        *,
        timeout: float | None = None,
        executor: ProcessExecutor | None = None,
    ) -> Command[DropResult]:
        probe = self._psql_probe_for(database_name, "database.drop.exists-after")
        return self._action_command(
            "database.drop",
            "Drop a database",
            lambda: self._drop_impl(
                database_name,
                timeout=timeout,
                psql_step_id=probe.step_id if probe else None,
            ),
            executor=executor,
            mutating=True,
            steps=(probe,) if probe is not None else (),
            optional_steps=(probe.step_id,) if probe is not None else (),
        )

    def _drop_impl(
        self,
        database_name: str,
        *,
        timeout: float | None,
        psql_step_id: str | None = None,
    ) -> DropResult:
        pwd = self._require_password()
        self._assert_local()

        with self._http(timeout=timeout) as http:
            resp = http.post(
                self._url("drop"),
                data={
                    "master_pwd": pwd,
                    "name": database_name,
                },
            )
            if resp.is_error:
                raise DatabaseError(
                    status_code=resp.status_code,
                    message=format_error(resp.text),
                    body=resp.content,
                ) from None

        if self.exists(database_name):
            raise DropFailedError(f"Database {database_name!r} still exists after drop")

        ck = self._cluster
        if ck is not None:
            db_host, db_port = ck
            catalog = self._instance._client.get_catalog()
            catalog.record_database_dropped(
                db_host,
                db_port,
                database_name,
            )

        return DropResult(db=database_name)

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
    ) -> Command[T]:
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.proc import PreparedAction, SubprocessExecutor

        step = PreparedAction(
            step_id=step_id,
            action=step_id,
            description=description,
            read_only=read_only,
            mutating=mutating,
        )

        def run(context: RunContext[T]) -> T:
            context.action(step_id)
            result = callback()
            for optional_step_id in optional_steps:
                if not context.consumed(optional_step_id):
                    context.skip(optional_step_id)
            return result

        prepared_steps: tuple[PreparedAction | PreparedStep, ...] = (step, *action_steps, *steps)
        from odoo_instance_sdk.internal.proc import prepared_command

        return Command.from_prepared(
            ExecutionPlan(steps=tuple(item.public_projection() for item in prepared_steps)),
            prepared_command(
                run,
                prepared_steps,
                executor=executor or SubprocessExecutor(),
            ),
        )
