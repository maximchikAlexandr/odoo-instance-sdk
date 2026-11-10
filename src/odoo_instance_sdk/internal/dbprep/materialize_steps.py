from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import (
    AdminPasswordRequiredError,
    ConfigError,
    InstanceConfigurationError,
)
from odoo_instance_sdk.internal.dbprep.source import (
    RestorePreflight,
    SelectedBackupRestorePayload,
    _load_project,
    _LocalArchiveRestoreSource,
    _planned_project_identity,
    _RemoteRestoreSource,
    _resolve_source_config,
    _RestoreSourceInput,
    build_selected_backup_restore_steps,
    resolve_runtime_binding,
)
from odoo_instance_sdk.internal.dbprep.source_binding import (
    _coerce_restore_source,
)
from odoo_instance_sdk.internal.odoo_config import parse_odoo_config
from odoo_instance_sdk.internal.project_env import load_project_environment
from odoo_instance_sdk.internal.project_runtime import resolve_project_http_port
from odoo_instance_sdk.models import (
    Backup,
    DatabaseRefreshOptions,
    PostRestoreCheckpoint,
    RestoreState,
    StartConfig,
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.postgres import PostgresCluster

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep
    from odoo_instance_sdk.models import DevelopmentEnvironment
    from odoo_instance_sdk.resources.instance import OdooInstance


def _restore_probe_consumed(step_id: str) -> bool:
    from odoo_instance_sdk.internal.proc import active_context

    context = active_context()
    return context is not None and context.planned(step_id) and context.consumed(step_id)


def _captured_restore_exists(step_id: str) -> bool | None:
    from odoo_instance_sdk.internal.proc import ProcessResult, active_context

    context = active_context()
    if context is None or not context.planned(step_id):
        return None
    try:
        result = (
            context.process(step_id)
            if not context.consumed(step_id)
            else context.results.get(step_id)
        )
    except BaseException:
        return None
    if not isinstance(result, ProcessResult) or result.returncode != 0:
        return None
    output = (
        result.stdout.decode(errors="replace")
        if isinstance(result.stdout, bytes)
        else result.stdout
    )
    if not isinstance(output, str):
        return None
    return {
        "1": True,
        "t": True,
        "true": True,
        "yes": True,
        "0": False,
        "f": False,
        "false": False,
        "no": False,
    }.get(output.strip().lower())


def _restore_failure_probe_evidence() -> tuple[bool | None, bool | None, int | None]:
    if not _restore_probe_consumed("database.restore.exists-before"):
        return None, None, None
    from odoo_instance_sdk.internal.proc import ProcessResult, active_context

    context = active_context()
    try:
        raw = (
            context.process("database.restore.exists-after")
            if context is not None
            and context.planned("database.restore.exists-after")
            and not context.consumed("database.restore.exists-after")
            else context.results.get("database.restore.exists-after")
            if context is not None
            else None
        )
    except BaseException:
        raw = None
    if not isinstance(raw, ProcessResult) or raw.returncode != 0:
        oid = None
    else:
        output = (
            raw.stdout.decode(errors="replace") if isinstance(raw.stdout, bytes) else raw.stdout
        )
        try:
            parsed = int(output.strip()) if isinstance(output, str) else 0
            oid = parsed if parsed > 0 else None
        except ValueError:
            oid = None
    return (
        _captured_restore_exists("database.restore.exists-before"),
        oid is not None,
        oid,
    )


def _record_incomplete_restore(  # noqa: C901
    client: OdooClient,
    preflight: RestorePreflight,
    *,
    exists_before: bool | None,
    exists_after: bool | None,
    backup: Backup | None,
    local_restore: SelectedBackupRestorePayload | None,
    post_restore_checkpoint: PostRestoreCheckpoint | None = None,
    database_oid: int | None = None,
) -> bool:
    """Record exact recovery evidence, returning false for any missing proof."""
    if exists_before is not False or exists_after is not True:
        return False
    provenance = getattr(preflight.postgres_cluster, "_restore_provenance", None)
    if not callable(provenance):
        return False
    try:
        cluster_id, data_directory = provenance()
    except Exception:
        return False
    if not isinstance(cluster_id, str) or not cluster_id:
        return False
    if data_directory is None:
        try:
            start_config = StartConfig.from_odoo_config(preflight.source_config)
            if isinstance(start_config.data_dir, str) and start_config.data_dir:
                from odoo_instance_sdk.internal.odoo_config import _resolve_data_dir

                data_directory = str(
                    _resolve_data_dir(start_config.data_dir, preflight.source_config)
                )
        except (ConfigError, OSError, ValueError, InstanceConfigurationError):
            return False
    source = preflight.restore_source
    if isinstance(source, _LocalArchiveRestoreSource):
        if local_restore is None:
            return False
        source_kind = "local_archive"
        source_sha256 = local_restore.verified_sha256
        backup_id = None
    else:
        if backup is None:
            return False
        source_kind = "catalogue"
        source_sha256 = None
        backup_id = str(backup.id)
    if data_directory is not None and not isinstance(data_directory, str | Path):
        return False
    try:
        client.get_catalog().record_restore(
            preflight.postgres_cluster.endpoint_host,
            preflight.postgres_cluster.endpoint_port,
            preflight.target_database,
            backup_id,
            source_kind=source_kind,
            source_sha256=source_sha256,
            cluster_id=cluster_id,
            data_directory=data_directory,
            database_oid=database_oid,
            state=RestoreState.INCOMPLETE,
            post_restore_checkpoint=post_restore_checkpoint,
        )
    except Exception:
        return False
    return True


def _retain_reset_pending_restore(
    client: OdooClient,
    preflight: RestorePreflight,
    *,
    backup: Backup | None,
    local_restore: SelectedBackupRestorePayload | None,
) -> bool | None:
    """Retain the exact completed restore as reset-pending without another event."""
    try:
        cluster_id, data_directory = preflight.postgres_cluster._restore_provenance()
        if data_directory is None:
            return False
        data_directory = str(data_directory)
        catalog = client.get_catalog()
        latest = getattr(catalog, "_latest_restore_binding", None)
        if not callable(latest):
            return False
        row = latest(
            preflight.postgres_cluster.endpoint_host,
            preflight.postgres_cluster.endpoint_port,
            preflight.target_database,
        )
        if row is None:
            return None
        if isinstance(preflight.restore_source, _LocalArchiveRestoreSource):
            if local_restore is None:
                return False
            source_kind, source_sha256, backup_id = (
                "local_archive",
                local_restore.verified_sha256,
                None,
            )
        else:
            if backup is None:
                return False
            source_kind, source_sha256, backup_id = "catalogue", None, str(backup.id)
        oid = row["database_oid"]
        if (
            row["database_name"] != preflight.target_database
            or row["state"] != RestoreState.COMPLETE.value
            or row["post_restore_checkpoint"] is not None
            or row["cluster_id"] != str(cluster_id)
            or row["data_directory"] != data_directory
            or row["backup_id"] != backup_id
            or row["source_kind"] != source_kind
            or row["source_sha256"] != source_sha256
            or type(oid) is not int
            or oid <= 0
        ):
            return False
        retain = getattr(catalog, "_retain_reset_pending_restore", None)
        if not callable(retain):
            return False
        return bool(
            retain(
                preflight.postgres_cluster.endpoint_host,
                preflight.postgres_cluster.endpoint_port,
                preflight.target_database,
                expected_sequence=row["sequence"],
                backup_id=backup_id,
                source_kind=source_kind,
                source_sha256=source_sha256,
                cluster_id=str(cluster_id),
                data_directory=data_directory,
                database_oid=oid,
            )
        )
    except Exception:
        return False


def _post_restore_failure_checkpoint(
    *, database_confirmed: bool, failed_stage: str | None, reset_completed: bool
) -> PostRestoreCheckpoint | None:
    if database_confirmed and failed_stage == "admin_reset":
        return PostRestoreCheckpoint.RESET_PENDING
    if failed_stage == "default_switch":
        return (
            PostRestoreCheckpoint.RESET_COMPLETE
            if reset_completed
            else PostRestoreCheckpoint.RESET_NOT_REQUESTED
        )
    return None


def _preparation_process_steps(
    project: ProjectConfig | str | Path,
    *,
    options: DatabaseRefreshOptions,
    restore_inputs: tuple[str, Path] | None = None,
    selected_environment: DevelopmentEnvironment | None = None,
    selected_instance: OdooInstance | None = None,
    selected_restore: SelectedBackupRestorePayload | None = None,
    admin_password: str | None = None,
) -> tuple[PreparedStep | PreparedAction, ...]:
    """Build the child-process part of a preparation command before effects.

    The preparation implementation deliberately keeps catalog, filesystem,
    HTTP, and lock work in its domain callback.  Its Git, PostgreSQL, compose,
    and optional Odoo-shell launches, however, must be visible in the same
    private snapshot so the active ledger can reject substitutions.
    """
    from odoo_instance_sdk.internal.proc import PreparedStep

    if selected_environment is not None:
        if selected_instance is None or selected_restore is None or restore_inputs is None:
            raise ConfigError("selected restore inputs were not captured")
        return build_selected_backup_restore_steps(
            selected_instance,
            target_database=restore_inputs[0],
            dump_path=selected_restore.dump_path,
            backup_format=selected_restore.format,
        )

    initial, root = _load_project(project)
    steps: list[PreparedStep] = [
        PreparedStep(
            step_id="database.prepare.git.toplevel",
            argv=("git", "-C", str(root), "rev-parse", "--show-toplevel"),
            timeout=30.0,
            read_only=True,
            text=True,
        ),
        PreparedStep(
            step_id="database.prepare.git.common-dir",
            argv=("git", "-C", str(root), "rev-parse", "--git-common-dir"),
            timeout=30.0,
            read_only=True,
            text=True,
        ),
    ]
    if not options.restore:
        return tuple(steps)

    from odoo_instance_sdk.internal.pg.builder import build_psql_specification
    from odoo_instance_sdk.resources.database import _admin_password_reset_script

    project_environment = load_project_environment(root)

    # The real Git identity is captured by the two process steps above and
    # resolved by the callback under the active ledger.  Planning must not
    # launch Git or otherwise inspect mutable repository state.
    _, _, project_id = _planned_project_identity(root)
    cluster = PostgresCluster._from_config(
        initial,
        repository_root=root,
        compose_runner=None,
        project_id=project_id,
    )
    compose_temporary_path = None
    if cluster.mode == "compose":
        compose_temporary_path = (
            cluster.compose_file.parent / f".compose-{uuid.uuid4().hex}.yaml.tmp"
        )
    steps.extend(
        step
        for step in cluster._ensure_running_steps(60.0, temporary_path=compose_temporary_path)
        if isinstance(step, PreparedStep)
    )

    source_config = _resolve_source_config(initial, root)
    parsed = parse_odoo_config(source_config)
    db_user = parsed.get("db_user")
    target_database = restore_inputs[0] if restore_inputs is not None else None
    if db_user and target_database is not None:
        db_host = parsed.get("db_host")
        raw_port = parsed.get("db_port")
        try:
            db_port = int(raw_port) if raw_port else 5432
        except ValueError:
            db_port = 5432
        password = parsed.get("db_password")
        steps.extend(
            (
                build_psql_specification(
                    step_id="database.restore.exists-reservation",
                    host=db_host,
                    port=db_port,
                    user=db_user,
                    password=password,
                    database="postgres",
                    args=(
                        "-c",
                        f"SELECT 1 FROM pg_database WHERE datname='{target_database.replace(chr(39), chr(39) + chr(39))}'",
                    ),
                    _trusted_args=("-t", "-A"),
                    timeout=30.0,
                ).prepared_step,
                build_psql_specification(
                    step_id="database.restore.exists-before",
                    host=db_host,
                    port=db_port,
                    user=db_user,
                    password=password,
                    database="postgres",
                    args=(
                        "-c",
                        f"SELECT 1 FROM pg_database WHERE datname='{target_database.replace(chr(39), chr(39) + chr(39))}'",
                    ),
                    _trusted_args=("-t", "-A"),
                    timeout=30.0,
                ).prepared_step,
                build_psql_specification(
                    step_id="database.restore.exists-after",
                    host=db_host,
                    port=db_port,
                    user=db_user,
                    password=password,
                    database="postgres",
                    args=(
                        "-c",
                        f"SELECT oid FROM pg_database WHERE datname='{target_database.replace(chr(39), chr(39) + chr(39))}'",
                    ),
                    _trusted_args=("-t", "-A"),
                    timeout=30.0,
                ).prepared_step,
            )
        )

    if options.reset_admin_password:
        from odoo_instance_sdk.resources.instance.auxiliary_restore import _build_shell_script_step

        if not admin_password:
            raise AdminPasswordRequiredError(
                "administrator password is required before admin reset"
            )
        runtime = resolve_runtime_binding(initial, root)
        start_config = StartConfig.from_odoo_config(source_config)
        start_config.http_port = resolve_project_http_port(
            initial.preferred_http_port, start_config.http_port
        )
        if restore_inputs is None:
            raise ConfigError("reset admin preparation inputs were not captured")
        start_config.config_path = str(restore_inputs[1])
        start_config.dbfilter = restore_inputs[0]
        start_config.db_name = restore_inputs[0]
        secret_config_path = str(restore_inputs[1]) + ".secret"
        shell_step, _, _, _ = _build_shell_script_step(
            start_config,
            executable_prefix=runtime.command_prefix,
            default_cwd=runtime.runtime_cwd,
            source=_admin_password_reset_script(admin_password),
            commit=True,
            secret_config_path=secret_config_path,
            project_environment=project_environment,
        )
        steps.append(shell_step)

    steps.extend(_default_switch_git_steps(root, cluster.mode))
    return tuple(steps)


def _default_switch_git_steps(root: Path, mode: str) -> tuple[PreparedStep, ...]:
    from odoo_instance_sdk.internal.generated_config import project_generated_config_path
    from odoo_instance_sdk.internal.proc import PreparedStep

    generated = project_generated_config_path(root)
    if mode != "compose" or not (root / ".git").exists() or not generated.is_file():
        return ()
    return tuple(
        PreparedStep(
            step_id=f"database.prepare.default-switch.git.{name}",
            argv=argv,
            timeout=30.0,
            read_only=True,
            text=True,
        )
        for name, argv in (
            ("toplevel", ("git", "-C", str(generated.parent), "rev-parse", "--show-toplevel")),
            (
                "tracked",
                (
                    "git",
                    "-C",
                    str(root),
                    "ls-files",
                    "--cached",
                    "--error-unmatch",
                    "--",
                    generated.relative_to(root).as_posix(),
                ),
            ),
        )
    )


def _preparation_action_steps(
    *,
    operation: str,
    options: DatabaseRefreshOptions,
    restore_source: _RestoreSourceInput = None,
) -> tuple[PreparedAction, ...]:
    """Return honest in-process boundaries for the preparation coordinator."""
    from odoo_instance_sdk.internal.proc import PreparedAction

    selected_source = _coerce_restore_source(restore_source)
    action_steps = [
        PreparedAction(
            step_id="database.prepare.lock",
            action="acquire-preparation-lock",
            description="Serialize project database preparation",
            mutating=True,
        ),
        PreparedAction(
            step_id="database.restore.incomplete-retry",
            action="reconcile-incomplete-restore",
            description="Reconcile an exact retained incomplete restore target",
            mutating=True,
        ),
    ]
    if isinstance(selected_source, _RemoteRestoreSource):
        action_steps.append(
            PreparedAction(
                step_id="database.prepare.remote-backup",
                action="download-remote-backup",
                description="Request the selected remote database backup",
                mutating=True,
            )
        )
    elif isinstance(selected_source, _LocalArchiveRestoreSource):
        action_steps.extend(
            (
                PreparedAction(
                    step_id="database.prepare.local-archive.validate",
                    action="validate-local-archive",
                    description="Validate the selected caller-owned Odoo archive",
                    read_only=True,
                ),
                PreparedAction(
                    step_id="database.prepare.local-archive.snapshot",
                    action="materialize-local-archive-snapshot",
                    description="Materialize a private verified archive snapshot",
                    mutating=True,
                ),
                PreparedAction(
                    step_id="database.prepare.local-archive.cleanup",
                    action="cleanup-local-archive-staging",
                    description="Remove command-owned local archive staging artifacts",
                    mutating=True,
                ),
            )
        )
    else:
        action_steps.append(
            PreparedAction(
                step_id="database.prepare.catalogue-backup",
                action="inspect-catalogue-backup",
                description="Validate the selected registered backup artifact",
                read_only=True,
            )
        )
    if options.restore:
        action_steps.append(
            PreparedAction(
                step_id="database.prepare.local-restore",
                action="restore-local-database",
                description="Restore the captured backup into the reserved database",
                mutating=True,
            )
        )
        if options.reset_admin_password:
            action_steps.append(
                PreparedAction(
                    step_id="database.prepare.odoo-reset",
                    action="reset-odoo-admin-password",
                    description="Reset the administrator password in the target database",
                    mutating=True,
                )
            )
        action_steps.extend(
            (
                PreparedAction(
                    step_id="database.prepare.default-switch",
                    action="switch-default-database",
                    description="Publish the prepared database as the project default",
                    mutating=True,
                ),
                PreparedAction(
                    step_id="database.prepare.rollback",
                    action="compensate-preparation-failure",
                    description="Retain artifacts and record preparation compensation",
                    read_only=True,
                ),
            )
        )
    return tuple(action_steps)
