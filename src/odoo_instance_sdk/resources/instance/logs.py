"""Public structured log operations on an Odoo instance."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

from odoo_instance_sdk.exceptions import InstanceConfigurationError
from odoo_instance_sdk.execution import ActionStep, Command, ExecutionPlan, JsonValue
from odoo_instance_sdk.internal.logs.launch import (
    StructuredLaunch,
    finish_environment_pid,
    project_logging,
    record_structured_process,
    recorded_checkout,
)
from odoo_instance_sdk.internal.logs.store import (
    LogFilters,
    LogScope,
    create_mark,
    delete_mark,
    high_water_cursor,
    iter_follow,
    list_marks,
    resolve_mark_cursor,
    run_logfile,
    search_page,
    show_mark,
)
from odoo_instance_sdk.internal.proc import PreparedAction, PrivateJsonValue, RunContext
from odoo_instance_sdk.models.logs import (
    LogCursor,
    LogEvent,
    LogMark,
    LogMarkDeletion,
    LogMarkList,
    LogPage,
)
from odoo_instance_sdk.resources.instance.auxiliary_restore import (
    _process_create_time,
    _runtime_owner,
)
from odoo_instance_sdk.resources.instance.runtime import _iter_logfile, _RuntimeCatalog

if TYPE_CHECKING:
    from odoo_instance_sdk.resources.instance import OdooInstance


class _LogsMixin:
    """Structured Odoo log queries for one bound instance."""

    def structured_odoo_logs(self) -> bool:
        """Return whether this instance should read per-run Odoo logs."""
        config = project_logging(cast("OdooInstance", self))
        return config is not None and config.structured

    def iter_run_logs(
        self,
        *,
        run: str | None = None,
        tail: int = 100,
        follow: bool = False,
    ) -> Iterator[str]:
        """Yield raw lines from the selected structured run logfile."""
        path = run_logfile(self._log_scope(), run)
        yield from _iter_logfile(path, tail=tail, follow=follow)

    def log_cursor(self, *, run: str | None = None) -> LogCursor:
        """Return the high-water cursor for the selected run."""
        self._require_structured_logs()
        cursor, run_id = high_water_cursor(self._log_scope(), run)
        return LogCursor(cursor=cursor, run_id=run_id)

    def search_log_events(
        self,
        *,
        run: str | None = None,
        text: str | None = None,
        levels: tuple[str, ...] = (),
        logger_prefix: str | None = None,
        database: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        after: str | None = None,
        until_cursor: str | None = None,
        limit: int | None = None,
    ) -> LogPage:
        """Return one bounded page of structured Odoo events."""
        self._require_structured_logs()
        return search_page(
            self._log_scope(),
            run,
            _filters(text, levels, logger_prefix, database, since, until),
            after=after,
            until=until_cursor,
            limit=limit,
        )

    def iter_log_events(
        self,
        *,
        run: str | None = None,
        text: str | None = None,
        levels: tuple[str, ...] = (),
        logger_prefix: str | None = None,
        database: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        after: str | None = None,
        until_cursor: str | None = None,
        limit: int | None = None,
        follow: bool = False,
    ) -> Iterator[LogEvent]:
        """Yield structured events, optionally following new records."""
        self._require_structured_logs()
        filters = _filters(text, levels, logger_prefix, database, since, until)
        if follow:
            yield from iter_follow(self._log_scope(), run, filters, after=after)
            return
        yield from self.search_log_events(
            run=run,
            text=text,
            levels=levels,
            logger_prefix=logger_prefix,
            database=database,
            since=since,
            until=until,
            after=after,
            until_cursor=until_cursor,
            limit=limit,
        ).events

    def show_log_mark(self, name: str) -> LogMark:
        """Return one environment mark."""
        self._require_structured_logs()
        return show_mark(self._log_scope(), name)

    def list_log_marks(self) -> LogMarkList:
        """Return the environment marks in name order."""
        self._require_structured_logs()
        return LogMarkList(marks=list_marks(self._log_scope()))

    def create_log_mark_command(
        self,
        name: str,
        *,
        run: str | None = None,
        replace: bool = False,
    ) -> Command[LogMark]:
        """Capture mark creation so dry-run does not write the mark."""
        self._require_structured_logs()
        scope = self._log_scope()
        return _log_command(
            "logs.mark.create",
            "Create a named Odoo log mark",
            {"name": name, "replace": replace},
            lambda: create_mark(scope, name, run, replace=replace),
        )

    def delete_log_mark_command(self, name: str) -> Command[LogMarkDeletion]:
        """Capture mark deletion so dry-run does not remove the mark."""
        self._require_structured_logs()
        scope = self._log_scope()

        def delete() -> LogMarkDeletion:
            delete_mark(scope, name)
            return LogMarkDeletion(name=name, deleted=True)

        return _log_command(
            "logs.mark.delete",
            "Delete a named Odoo log mark",
            {"name": name},
            delete,
        )

    def resolve_log_mark(self, name: str) -> str:
        """Return the cursor stored for one mark."""
        self._require_structured_logs()
        return resolve_mark_cursor(self._log_scope(), name)

    def _record_structured_process(self, launch: StructuredLaunch, pid: int) -> None:
        """Attach the persisted runtime identity to one prepared run."""
        instance = cast("OdooInstance", self)
        catalog = cast("_RuntimeCatalog", instance._client.get_catalog())
        owner_kind, owner_id = _runtime_owner(instance._runtime_binding, instance._environment_id)
        branch, commit = recorded_checkout(catalog.get_runtime(owner_kind, owner_id))
        record_structured_process(
            launch,
            pid=pid,
            create_time=_process_create_time(pid),
            branch=branch,
            commit=commit,
        )

    def _finish_structured_pid(self, pid: int, *, state: str) -> None:
        """Record a terminal state for the run owned by one process id."""
        start = cast("OdooInstance", self).config.start_config
        if start is None or not start.config_path:
            return
        finish_environment_pid(Path(start.config_path).resolve().parent, pid, state=state)

    def _require_structured_logs(self) -> None:
        if not self.structured_odoo_logs():
            raise InstanceConfigurationError("structured Odoo logging is disabled")

    def _log_scope(self) -> LogScope:
        instance = cast("OdooInstance", self)
        binding = instance._runtime_binding
        start = instance.config.start_config
        if (
            binding is None
            or binding.owner_kind != "environment"
            or start is None
            or not start.config_path
        ):
            raise InstanceConfigurationError("structured Odoo logs require a ready environment")
        return LogScope(
            project_id=binding.project_id,
            environment_id=binding.owner_id,
            environment_root=Path(start.config_path).resolve().parent,
        )


def _filters(
    text: str | None,
    levels: tuple[str, ...],
    logger_prefix: str | None,
    database: str | None,
    since: datetime | None,
    until: datetime | None,
) -> LogFilters:
    return LogFilters(
        text=text,
        levels=levels,
        logger_prefix=logger_prefix,
        database=database,
        since=since,
        until=until,
    )


_MarkResult = TypeVar("_MarkResult", LogMark, LogMarkDeletion)


def _log_command(
    step_id: str,
    description: str,
    details: dict[str, str | bool],
    operation: Callable[[], _MarkResult],
) -> Command[_MarkResult]:
    """Build one mutating mark command around a single action step."""
    action = PreparedAction(
        step_id=step_id,
        action=step_id,
        description=description,
        details=cast("PrivateJsonValue", details),
        mutating=True,
    )

    def execute(context: RunContext[_MarkResult]) -> _MarkResult:
        context.action(step_id)
        result = operation()
        context.complete_action(step_id)
        return result

    return Command.create(
        ExecutionPlan(
            steps=(
                ActionStep(
                    step_id=step_id,
                    action=step_id,
                    description=description,
                    details=cast("JsonValue", details),
                    mutating=True,
                ),
            )
        ),
        execute,
        (action,),
    )
