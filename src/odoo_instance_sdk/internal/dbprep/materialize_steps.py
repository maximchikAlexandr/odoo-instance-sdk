from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import (
    AdminPasswordRequiredError,
    ConfigError,
)
from odoo_instance_sdk.internal.dbprep.source import (
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
from odoo_instance_sdk.models import DatabaseRefreshOptions, StartConfig
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.postgres import PostgresCluster

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep
    from odoo_instance_sdk.models import DevelopmentEnvironment
    from odoo_instance_sdk.resources.instance import OdooInstance


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
                        f"SELECT 1 FROM pg_database WHERE datname='{target_database.replace(chr(39), chr(39) + chr(39))}'",
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
    return tuple(steps)


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
