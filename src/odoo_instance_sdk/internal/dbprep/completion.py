"""Complete a proven post-import restore using existing project operations."""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec

from odoo_instance_sdk.exceptions import (
    AdminPasswordRequiredError,
    BackupCatalogError,
    ConfigError,
)
from odoo_instance_sdk.internal.dbprep.source import (
    _load_project,
    _resolve_source_config,
    _target_config_path,
    _wait_for_preparation_lock,
    _write_target_config,
)
from odoo_instance_sdk.internal.odoo_config import _resolve_data_dir
from odoo_instance_sdk.internal.project_init import switch_project_default
from odoo_instance_sdk.internal.repo_key import git_common_dir, repo_key
from odoo_instance_sdk.models import (
    PostRestoreCheckpoint,
    RestoreCompletionAction,
    RestoreCompletionFailureContext,
    RestoreCompletionPlan,
    RestoreCompletionResult,
    StartConfig,
)
from odoo_instance_sdk.project import managed_filestore_path

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import ProcessExecutor, RunContext
    from odoo_instance_sdk.project import ProjectConfig


def complete_restore_command(  # noqa: C901 -- one captured command owns the safety-critical transition.
    client: OdooClient,
    project: ProjectConfig | str | Path,
    target_database: str,
    *,
    update_modules: Sequence[str] = (),
    admin_password: str | None = None,
    admin_password_provenance: str = "environment",
    executor: ProcessExecutor | None = None,
) -> Command[RestoreCompletionResult]:
    """Capture exact restore evidence, then revalidate it under the project lock."""
    from odoo_instance_sdk.execution import Command, ExecutionPlan
    from odoo_instance_sdk.internal.proc import PreparedAction, SubprocessExecutor, prepared_command
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    modules = tuple(dict.fromkeys(update_modules))
    if any(not isinstance(name, str) or not name or name.strip() != name for name in modules):
        raise ConfigError("module names must be non-empty exact strings")
    config, root = _load_project(project)
    target = target_database.strip()
    if not target or target != target_database:
        raise ConfigError("completion target must be an exact database name")
    project_id = f"project_{repo_key(root, git_common_dir(root))}"
    cluster = PostgresCluster.from_project(root)
    cluster_id, data_directory = cluster._restore_provenance()
    if cluster_id is None:
        raise BackupCatalogError("active project cluster identity is unavailable")
    data_directory = _restore_data_directory(config, root, data_directory)
    instance = client.instance.from_project(config)
    catalog = client.get_catalog()
    row = catalog._latest_restore_binding(cluster.endpoint_host, cluster.endpoint_port, target)
    if row is None:
        raise BackupCatalogError("target has no tracked restore binding")
    checkpoint_value = row["post_restore_checkpoint"]
    if row["state"] == "complete":
        if config.default_source_database != target:
            raise BackupCatalogError("completed restore target is not the current project default")
        checkpoint = None
        no_op = True
    else:
        try:
            checkpoint = PostRestoreCheckpoint(checkpoint_value)
        except (TypeError, ValueError) as exc:
            raise BackupCatalogError(
                "target restore has no completion-eligible checkpoint"
            ) from exc
        no_op = False
    if checkpoint is None and not no_op:
        raise BackupCatalogError("target restore has no completion checkpoint")
    if row["cluster_id"] != cluster_id or row["data_directory"] != data_directory:
        raise BackupCatalogError(
            "target restore provenance does not match the active project cluster"
        )
    claim = catalog._get_postgres_cluster_by_id(cluster_id)
    if claim is None or claim.state != "active" or claim.project_id != project_id:
        raise BackupCatalogError("target restore is not owned by the active project cluster")
    inventory = instance.databases.list_inventory(root)
    target_item = next((item for item in inventory.databases if item.name == target), None)
    if (
        target_item is None
        or not row["database_oid"]
        or target_item.database_oid != row["database_oid"]
    ):
        raise BackupCatalogError("target database identity is unavailable")
    filestore = managed_filestore_path(config)
    try:
        filestore.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise BackupCatalogError(
            "target managed filestore is not contained by the project"
        ) from exc
    if not filestore.is_dir():
        raise BackupCatalogError("target managed filestore is unavailable")

    if checkpoint is PostRestoreCheckpoint.RESET_PENDING and admin_password is None:
        raise AdminPasswordRequiredError(
            "administrator password is required to complete the pending reset"
        )
    actions: list[RestoreCompletionAction] = []
    if not no_op and modules:
        actions.append(RestoreCompletionAction.UPDATE_MODULE)
    if checkpoint is PostRestoreCheckpoint.RESET_PENDING:
        actions.extend(
            (RestoreCompletionAction.RESET_ADMIN, RestoreCompletionAction.ADVANCE_CHECKPOINT)
        )
    if not no_op:
        actions.append(RestoreCompletionAction.SWITCH_DEFAULT)
    plan = RestoreCompletionPlan(
        target_database=target,
        checkpoint=checkpoint,
        modules=modules,
        actions=tuple(actions),
        previous_default=config.default_source_database,
        effective_default=target,
        no_op=no_op,
    )

    module_command = None
    reset_command = None
    target_config_path: Path | None = None
    if not no_op and (modules or checkpoint is PostRestoreCheckpoint.RESET_PENDING):
        source_config = _resolve_source_config(config, root)
        target_config_path = _target_config_path(source_config)
        try:
            _write_target_config(source_config, target_config_path, target)
            start_config = (
                msgspec.structs.replace(
                    instance.config.start_config,
                    config_path=str(target_config_path),
                    db_name=target,
                    dbfilter=target,
                    data_dir=str(filestore),
                )
                if instance.config.start_config is not None
                else None
            )
            if start_config is None:
                raise ConfigError("project Odoo config is unavailable")
            target_instance = replace(
                instance,
                config=replace(
                    instance.config,
                    configured_database_names=(target,),
                    start_config=start_config,
                ),
            )
            if modules:
                module_command = target_instance.modules.update_command(modules)
            if checkpoint is PostRestoreCheckpoint.RESET_PENDING:
                reset_command = target_instance.databases.reset_admin_password_command(
                    admin_password=admin_password or "",
                    provenance=admin_password_provenance,
                )
        finally:
            target_config_path.unlink(missing_ok=True)

    step_ids = ["database.restore.complete.lock", "database.restore.complete.revalidate"]
    if not no_op and modules:
        step_ids.append("database.restore.complete.modules")
    if checkpoint is PostRestoreCheckpoint.RESET_PENDING:
        step_ids.extend(
            ("database.restore.complete.admin-reset", "database.restore.complete.checkpoint")
        )
    if not no_op:
        step_ids.append("database.restore.complete.default-switch")
    steps = tuple(
        PreparedAction(
            step_id=step,
            action=step,
            description=step.replace("database.restore.complete.", "").replace("-", " "),
            mutating=step
            not in {"database.restore.complete.lock", "database.restore.complete.revalidate"},
        )
        for step in step_ids
    )

    def run(context: RunContext[RestoreCompletionResult]) -> RestoreCompletionResult:  # noqa: C901
        context.action("database.restore.complete.lock")
        with _wait_for_preparation_lock(project_id, timeout=300.0):
            context.complete_action("database.restore.complete.lock")
            context.action("database.restore.complete.revalidate")
            live_config = type(config).load(root)
            live_cluster = PostgresCluster.from_project(root)
            live_cluster_id, live_data_directory = live_cluster._restore_provenance()
            live_data_directory = _restore_data_directory(live_config, root, live_data_directory)
            live_row = catalog._latest_restore_binding(
                cluster.endpoint_host, cluster.endpoint_port, target
            )
            live_claim = (
                None
                if live_cluster_id is None
                else catalog._get_postgres_cluster_by_id(live_cluster_id)
            )
            if (
                live_row is None
                or live_row["sequence"] != row["sequence"]
                or any(
                    live_row[field] != row[field]
                    for field in (
                        "database_name",
                        "backup_id",
                        "source_kind",
                        "source_sha256",
                        "cluster_id",
                        "data_directory",
                        "database_oid",
                        "state",
                        "post_restore_checkpoint",
                    )
                )
                or live_cluster.endpoint_host != cluster.endpoint_host
                or live_cluster.endpoint_port != cluster.endpoint_port
                or live_cluster_id != cluster_id
                or live_data_directory != data_directory
                or live_claim is None
                or live_claim.state != "active"
                or live_claim.project_id != project_id
                or managed_filestore_path(live_config) != filestore
                or live_config != config
            ):
                raise BackupCatalogError("restore completion plan is stale")
            current_inventory = instance.databases.list_inventory(root)
            current_item = next(
                (item for item in current_inventory.databases if item.name == target), None
            )
            if current_item is None or current_item.database_oid != row["database_oid"]:
                raise BackupCatalogError("target PostgreSQL identity changed")
            context.complete_action("database.restore.complete.revalidate")
            if no_op:
                return RestoreCompletionResult(
                    target_database=target,
                    checkpoint=None,
                    completed_stages=(),
                    pending_stage=None,
                    requested_modules=(),
                    previous_default=target,
                    effective_default=target,
                    published=True,
                    no_op=True,
                )
            if checkpoint is None:
                raise BackupCatalogError("restore completion checkpoint changed")
            completed: list[str] = []
            current_checkpoint = checkpoint
            expected_sequence = row["sequence"]

            def attach_failure(
                error: BaseException, pending: str, *, published: bool = False
            ) -> None:
                setattr(
                    error,
                    "failure_context",
                    RestoreCompletionFailureContext(
                        target_database=target,
                        checkpoint=current_checkpoint,
                        completed_stages=tuple(completed),
                        pending_stage=pending,
                        requested_modules=modules,
                        previous_default=config.default_source_database,
                        effective_default=live_config.default_source_database,
                        published=published,
                    ),
                )

            if target_config_path is not None:
                _write_target_config(
                    _resolve_source_config(config, root), target_config_path, target
                )
            try:
                if modules:
                    context.action("database.restore.complete.modules")
                    if module_command is None:
                        raise ConfigError("module update plan is unavailable")
                    try:
                        module_command.run()
                    except BaseException as error:
                        attach_failure(error, "module_update")
                        raise
                    context.complete_action("database.restore.complete.modules")
                    completed.append("module_update")
                if checkpoint is PostRestoreCheckpoint.RESET_PENDING:
                    context.action("database.restore.complete.admin-reset")
                    if reset_command is None:
                        raise ConfigError("administrator reset plan is unavailable")
                    try:
                        reset_command.run()
                    except BaseException as error:
                        attach_failure(error, "admin_reset")
                        raise
                    context.complete_action("database.restore.complete.admin-reset")
                    completed.append("admin_reset")
                    context.action("database.restore.complete.checkpoint")
                    try:
                        expected_sequence = catalog._advance_restore_checkpoint(
                            cluster.endpoint_host,
                            cluster.endpoint_port,
                            target,
                            expected_sequence=expected_sequence,
                            expected_checkpoint=PostRestoreCheckpoint.RESET_PENDING,
                            checkpoint=PostRestoreCheckpoint.RESET_COMPLETE,
                        )
                    except BaseException as error:
                        attach_failure(error, "checkpoint_advance")
                        raise
                    context.complete_action("database.restore.complete.checkpoint")
                    current_checkpoint = PostRestoreCheckpoint.RESET_COMPLETE
                try:
                    latest_before_publication = catalog._latest_restore_binding(
                        cluster.endpoint_host, cluster.endpoint_port, target
                    )
                    binding_changed = (
                        latest_before_publication is None
                        or latest_before_publication["sequence"] != expected_sequence
                        or latest_before_publication["post_restore_checkpoint"]
                        != current_checkpoint.value
                    )
                except BaseException as error:
                    attach_failure(error, "default_switch")
                    raise
                if binding_changed:
                    binding_error = BackupCatalogError("restore binding changed before publication")
                    attach_failure(binding_error, "default_switch")
                    raise binding_error
                context.action("database.restore.complete.default-switch")
                latest_config = type(config).load(root)
                switched = msgspec.structs.replace(
                    latest_config,
                    default_source_database=target,
                    managed_filestore=latest_config.managed_filestore
                    or filestore.relative_to(root),
                )
                try:
                    switch_project_default(root, switched)
                except BaseException as error:
                    attach_failure(error, "default_switch")
                    raise
                try:
                    catalog._complete_restore_binding(
                        cluster.endpoint_host,
                        cluster.endpoint_port,
                        target,
                        expected_sequence=expected_sequence,
                        expected_checkpoint=current_checkpoint,
                    )
                except BaseException as error:
                    attach_failure(error, "catalogue_completion", published=True)
                    raise
                context.complete_action("database.restore.complete.default-switch")
                completed.append("default_switch")
                return RestoreCompletionResult(
                    target_database=target,
                    checkpoint=current_checkpoint,
                    completed_stages=tuple(completed),
                    pending_stage=None,
                    requested_modules=modules,
                    previous_default=config.default_source_database,
                    effective_default=target,
                    published=True,
                )
            finally:
                if target_config_path is not None:
                    with contextlib.suppress(OSError):
                        target_config_path.unlink(missing_ok=True)

    public_plan = ExecutionPlan(
        steps=tuple(step.public_projection() for step in steps),
        observations=(
            msgspec.to_builtins(plan),
            *(
                ({"module_update_plan": msgspec.to_builtins(module_command.plan)},)
                if module_command is not None
                else ()
            ),
            *(
                ({"admin_reset_plan": msgspec.to_builtins(reset_command.plan)},)
                if reset_command is not None
                else ()
            ),
        ),
    )
    prepared = prepared_command(run, steps, executor=executor or SubprocessExecutor())
    return Command.from_prepared(public_plan, prepared)


def _restore_data_directory(
    config: ProjectConfig, root: Path, claimed_directory: str | None
) -> str | None:
    if claimed_directory is not None:
        return claimed_directory
    source_config = _resolve_source_config(config, root)
    start_config = StartConfig.from_odoo_config(source_config)
    if not start_config.data_dir:
        return None
    return str(_resolve_data_dir(start_config.data_dir, source_config))
