from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.internal.backup_maintenance import attach_auto_prune
from odoo_instance_sdk.internal.dbprep.materialize import (
    SelectedBackupRestorePayload,
    T,
    _assert_source_plan_current,
    _build_incomplete_retry_command,
    _capture_restore_inputs,
    _capture_selected_restore,
    _coerce_restore_source,
    _load_project,
    _RemoteRestoreSource,
    _RestoreSourceInput,
    _source_identity,
    prepare_download,
    prepare_restore,
    resolve_test_source,
)
from odoo_instance_sdk.internal.dbprep.materialize_steps import (
    _preparation_action_steps,
    _preparation_process_steps,
)
from odoo_instance_sdk.internal.proc import RunContext
from odoo_instance_sdk.models import DatabasePreparationResult, DatabaseRefreshOptions
from odoo_instance_sdk.project import ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        ProcessExecutor,
    )


@dataclass(slots=True)
class DatabasePreparationCoordinator:
    client: OdooClient

    def prepare(
        self,
        project: ProjectConfig | str | Path,
        *,
        options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
        coalesce: bool = False,
        restore_source: _RestoreSourceInput = None,
        target_database: str | None = None,
        admin_password: str | None = None,
        admin_password_provenance: str = "environment",
    ) -> DatabasePreparationResult:
        return self.prepare_command(
            project,
            options=options,
            coalesce=coalesce,
            restore_source=restore_source,
            target_database=target_database,
            admin_password=admin_password,
            admin_password_provenance=admin_password_provenance,
        ).run()

    def prepare_command(
        self,
        project: ProjectConfig | str | Path,
        *,
        options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
        coalesce: bool = False,
        restore_source: _RestoreSourceInput = None,
        target_database: str | None = None,
        admin_password: str | None = None,
        admin_password_provenance: str = "environment",
        executor: ProcessExecutor | None = None,
    ) -> Command[DatabasePreparationResult]:
        planned_source = None
        if isinstance(_coerce_restore_source(restore_source), _RemoteRestoreSource):
            planned_project, _planned_root = _load_project(project)
            planned_source = _source_identity(resolve_test_source(planned_project, options))
        selected_restore = (
            _capture_selected_restore(project, restore_source) if options.restore else None
        )
        restore_inputs = _capture_restore_inputs(
            project,
            options,
            restore_source=restore_source,
            client=self.client,
            target_database=target_database,
            selected_restore=selected_restore,
        )
        from odoo_instance_sdk.internal.proc import SubprocessExecutor

        process_executor = executor or SubprocessExecutor()
        project_root = (
            project.repository_root if isinstance(project, ProjectConfig) else Path(project)
        )
        retry_drop_command = (
            _build_incomplete_retry_command(
                self.client,
                project,
                restore_inputs[0],
                executor=process_executor,
            )
            if (
                options.restore
                and target_database is not None
                and restore_inputs is not None
                and (project_root / ".odcli" / "project.toml").is_file()
            )
            else None
        )
        retry_drop_steps = (
            tuple(retry_drop_command._prepared().steps) if retry_drop_command is not None else ()
        )
        steps: tuple[PreparedStep | PreparedAction, ...] = (
            *_preparation_action_steps(
                operation="prepare", options=options, restore_source=restore_source
            ),
            *_preparation_process_steps(
                project,
                options=options,
                restore_inputs=restore_inputs,
                admin_password=admin_password,
            ),
        )
        return self._action_command(
            "database.prepare",
            "Prepare a project database",
            lambda context: self._prepare_impl(
                project,
                options=options,
                coalesce=coalesce,
                restore_inputs=restore_inputs,
                restore_source=restore_source,
                selected_restore=selected_restore,
                target_database=target_database,
                admin_password=admin_password,
                admin_password_provenance=admin_password_provenance,
                planned_source=planned_source,
                retry_drop_command=retry_drop_command,
                execution_context=cast("RunContext[object]", context),
            ),
            executor=process_executor,
            steps=(*steps, *retry_drop_steps),
            optional_steps=tuple(
                step.step_id
                for step in (*steps, *retry_drop_steps)
                if step.step_id
                in {
                    "database.restore.exists-reservation",
                    "database.restore.exists-before",
                    "database.restore.exists-after",
                    "database.restore.incomplete-retry",
                    "database.prepare.rollback",
                    "database.prepare.local-archive.cleanup",
                }
                or step.step_id.startswith("database.drop")
            ),
        )

    def _prepare_impl(
        self,
        project: ProjectConfig | str | Path,
        *,
        options: DatabaseRefreshOptions,
        coalesce: bool,
        restore_inputs: tuple[str, Path] | None = None,
        restore_source: _RestoreSourceInput = None,
        selected_restore: SelectedBackupRestorePayload | None = None,
        target_database: str | None = None,
        admin_password: str | None = None,
        admin_password_provenance: str = "environment",
        planned_source: tuple[str | None, str, str | None, str | None] | None = None,
        retry_drop_command: Command[object] | None = None,
        execution_context: RunContext[object] | None = None,
    ) -> DatabasePreparationResult:
        _assert_source_plan_current(project, options, planned_source)
        if options.restore:
            return prepare_restore(
                self.client,
                project,
                options=options,
                coalesce=coalesce,
                restore_inputs=restore_inputs,
                restore_source=restore_source,
                selected_restore=selected_restore,
                target_database=target_database,
                admin_password=admin_password,
                admin_password_provenance=admin_password_provenance,
                retry_drop_command=retry_drop_command,
                execution_context=execution_context,
            )
        return prepare_download(self.client, project, options=options, wait_for_lock=True)

    def refresh_database(
        self,
        project: ProjectConfig | str | Path,
        *,
        options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
        restore_source: _RestoreSourceInput = None,
        target_database: str | None = None,
        admin_password: str | None = None,
        admin_password_provenance: str = "environment",
    ) -> DatabasePreparationResult:
        return self.refresh_database_command(
            project,
            options=options,
            restore_source=restore_source,
            target_database=target_database,
            admin_password=admin_password,
            admin_password_provenance=admin_password_provenance,
        ).run()

    def refresh_database_command(
        self,
        project: ProjectConfig | str | Path,
        *,
        options: DatabaseRefreshOptions = DatabaseRefreshOptions(),
        restore_source: _RestoreSourceInput = None,
        target_database: str | None = None,
        admin_password: str | None = None,
        admin_password_provenance: str = "environment",
        executor: ProcessExecutor | None = None,
    ) -> Command[DatabasePreparationResult]:
        planned_source = None
        if isinstance(_coerce_restore_source(restore_source), _RemoteRestoreSource):
            planned_project, _planned_root = _load_project(project)
            planned_source = _source_identity(resolve_test_source(planned_project, options))
        selected_restore = (
            _capture_selected_restore(project, restore_source) if options.restore else None
        )
        restore_inputs = _capture_restore_inputs(
            project,
            options,
            restore_source=restore_source,
            client=self.client,
            target_database=target_database,
            selected_restore=selected_restore,
        )
        from odoo_instance_sdk.internal.proc import SubprocessExecutor

        process_executor = executor or SubprocessExecutor()
        project_root = (
            project.repository_root if isinstance(project, ProjectConfig) else Path(project)
        )
        retry_drop_command = (
            _build_incomplete_retry_command(
                self.client,
                project,
                restore_inputs[0],
                executor=process_executor,
            )
            if (
                options.restore
                and target_database is not None
                and restore_inputs is not None
                and (project_root / ".odcli" / "project.toml").is_file()
            )
            else None
        )
        retry_drop_steps = (
            tuple(retry_drop_command._prepared().steps) if retry_drop_command is not None else ()
        )
        steps: tuple[PreparedStep | PreparedAction, ...] = (
            *_preparation_action_steps(
                operation="refresh", options=options, restore_source=restore_source
            ),
            *_preparation_process_steps(
                project,
                options=options,
                restore_inputs=restore_inputs,
                admin_password=admin_password,
            ),
        )
        command = self._action_command(
            "database.refresh",
            "Refresh a project database",
            lambda context: self._prepare_impl(
                project,
                options=options,
                coalesce=False,
                restore_inputs=restore_inputs,
                restore_source=restore_source,
                selected_restore=selected_restore,
                target_database=target_database,
                admin_password=admin_password,
                admin_password_provenance=admin_password_provenance,
                planned_source=planned_source,
                retry_drop_command=retry_drop_command,
                execution_context=cast("RunContext[object]", context),
            ),
            executor=process_executor,
            steps=(*steps, *retry_drop_steps),
            optional_steps=tuple(
                step.step_id
                for step in (*steps, *retry_drop_steps)
                if step.step_id
                in {
                    "database.restore.exists-reservation",
                    "database.restore.exists-before",
                    "database.restore.exists-after",
                    "database.restore.incomplete-retry",
                    "database.prepare.rollback",
                    "database.prepare.local-archive.cleanup",
                }
                or step.step_id.startswith("database.drop")
            ),
        )
        return attach_auto_prune(
            command,
            backups=self.client.backups,
            project=project,
        )

    def _action_command(
        self,
        step_id: str,
        description: str,
        callback: Callable[[], T] | Callable[[RunContext[T]], T],
        *,
        executor: ProcessExecutor | None,
        steps: Sequence[PreparedStep | PreparedAction] = (),
        optional_steps: Sequence[str] = (),
    ) -> Command[T]:
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.proc import (
            PreparedAction,
            SubprocessExecutor,
            prepared_command,
        )

        step = PreparedAction(
            step_id=step_id, action=step_id, description=description, mutating=True
        )

        def run(context: RunContext[T]) -> T:
            context.action(step_id)
            import inspect

            if inspect.signature(callback).parameters:
                contextual = cast("Callable[[RunContext[T]], T]", callback)
                result = contextual(context)
            else:
                no_context = cast("Callable[[], T]", callback)
                result = no_context()
            for optional_step_id in optional_steps:
                if not context.consumed(optional_step_id):
                    context.skip(optional_step_id)
            return result

        prepared_steps: tuple[PreparedAction | PreparedStep, ...] = (step, *steps)

        return Command.from_prepared(
            ExecutionPlan(
                steps=tuple(item.public_projection() for item in prepared_steps),
            ),
            prepared_command(
                run,
                prepared_steps,
                executor=executor or SubprocessExecutor(),
            ),
        )
