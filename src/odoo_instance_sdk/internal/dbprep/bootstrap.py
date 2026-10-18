"""Bootstrap database ``tmp`` with ``base`` installed for self-contained init.

Per design D5a, a self-contained Compose ``init`` creates a valid empty Odoo
database named exactly ``tmp`` with ``base`` installed so the Odoo 13 Database
Manager / ``ir.http`` exist before the first restore. The Odoo process is
spawned once via ``internal/proc`` with ``--database tmp --init=base
--stop-after-init`` (``shell=False``); after it exits, readiness is confirmed
by SQL on the owned cluster. The operation is idempotent: a valid ``tmp`` is
not recreated; an invalid same-named database fails with ``init_bootstrap_failed``.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from odoo_instance_sdk.exceptions import InstanceConfigurationError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.locks import (
    database_preparation_lock_path,
    exclusive_lock_until,
)
from odoo_instance_sdk.internal.pg.builder import build_psql_specification
from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep, SubprocessExecutor
from odoo_instance_sdk.models import StartConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import ProcessExecutor, RunContext
    from odoo_instance_sdk.resources.instance import OdooInstance
    from odoo_instance_sdk.resources.postgres import PostgresCluster

_ContextT = TypeVar("_ContextT")

BOOTSTRAP_DATABASE = "tmp"
_BOOTSTRAP_DATABASE = BOOTSTRAP_DATABASE
_BOOTSTRAP_INIT_MODULE = "base"
_BOOTSTRAP_READY_SQL = b"SELECT state FROM ir_module_module WHERE name = 'base'"
_BOOTSTRAP_CREATE_SQL = (
    b"SELECT 'CREATE DATABASE tmp' "
    b"WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'tmp')\\gexec"
)

_BOOTSTRAP_STEP_ID = "init.bootstrap.tmp"
_BOOTSTRAP_PROBE_STEP_ID = "init.bootstrap.tmp.probe"
_BOOTSTRAP_READY_STEP_ID = "init.bootstrap.tmp.ready"
_BOOTSTRAP_VERIFY_ACTION_ID = "init.bootstrap.tmp.verify"
_BOOTSTRAP_LOCK_ACTION_ID = "init.bootstrap.tmp.lock"
_BOOTSTRAP_RECORD_ACTION_ID = "catalog.bootstrap.tmp.record"


class BootstrapFailedError(InstanceConfigurationError):
    """Raised when the ``tmp`` bootstrap database cannot be created or verified."""

    error_code = "init_bootstrap_failed"

    def __init__(self, message: str) -> None:
        super().__init__(message)


class BootstrapOutcome(StrEnum):
    """Whether this invocation created ``tmp`` or reused a ready database."""

    CREATED = "created"
    ALREADY_READY = "already_ready"


@dataclass(frozen=True, slots=True)
class BootstrapTmpPlan:
    """Named immutable bootstrap inputs shared by init and foreground callers."""

    spawn_step: PreparedStep
    probe_step: PreparedStep
    ready_step: PreparedStep
    verify_action: PreparedAction
    lock_action: PreparedAction | None = None
    create_step: PreparedStep | None = None
    record_action: PreparedAction | None = None
    project_id: str | None = None

    @property
    def execution_steps(self) -> tuple[PreparedStep | PreparedAction, ...]:
        steps: list[PreparedStep | PreparedAction] = []
        if self.lock_action is not None:
            steps.append(self.lock_action)
        steps.extend((self.verify_action, self.probe_step))
        if self.create_step is not None:
            steps.append(self.create_step)
        steps.extend((self.spawn_step, self.ready_step))
        if self.record_action is not None:
            steps.append(self.record_action)
        return tuple(steps)


def _bootstrap_sql_step(
    *,
    db_host: str,
    db_port: int,
    db_user: str,
    db_password: str,
    step_id: str,
    database: str = _BOOTSTRAP_DATABASE,
    stdin: bytes = _BOOTSTRAP_READY_SQL,
    read_only: bool = True,
    mutating: bool = False,
) -> PreparedStep:
    """Build one captured SQL step for bootstrap creation or readiness."""
    specification = build_psql_specification(
        host=db_host,
        port=db_port,
        user=db_user,
        password=db_password,
        database=database,
        stdin=stdin,
        timeout=30.0,
        mode="captured",
        step_id=step_id,
        _trusted_args=("-q", "-t", "-A", "-v", "ON_ERROR_STOP=1"),
        _require_binary=False,
        _read_only=read_only,
        _mutating=mutating,
    )
    return specification.prepared_step


def bootstrap_tmp_plan(
    *,
    command_prefix: tuple[str, ...],
    start_config: StartConfig,
    db_host: str,
    db_port: int,
    db_user: str,
    db_password: str,
    default_cwd: Path | None = None,
    include_create: bool = True,
    project_id: str | None = None,
    record_action: PreparedAction | None = None,
) -> BootstrapTmpPlan:
    """Capture bootstrap database creation, spawn, SQL probes, and verify action.

    The Odoo argv comes from the same runtime builder used by ``run`` plus
    ``--database tmp --init=base --stop-after-init``. Readiness is a SQL probe
    on the owned cluster, not an HTTP ``version_info`` POST.
    """
    from odoo_instance_sdk.resources.instance.auxiliary_restore import _snapshot_start_inputs

    _snapshot, cli_args, secret_path, secrets = _snapshot_start_inputs(start_config)
    bootstrap_args = (
        "--database",
        _BOOTSTRAP_DATABASE,
        "--init",
        _BOOTSTRAP_INIT_MODULE,
        "--stop-after-init",
    )
    create_step = (
        _bootstrap_sql_step(
            db_host=db_host,
            db_port=db_port,
            db_user=db_user,
            db_password=db_password,
            step_id="init.bootstrap.tmp.create",
            database="postgres",
            stdin=_BOOTSTRAP_CREATE_SQL,
            read_only=False,
            mutating=True,
        )
        if include_create
        else None
    )
    spawn_step = PreparedStep(
        step_id=_BOOTSTRAP_STEP_ID,
        argv=(*command_prefix, *cli_args, *bootstrap_args),
        cwd=str(default_cwd) if default_cwd is not None else None,
        secret_config_path=secret_path,
        secret_values=secrets,
        mode="captured",
        mutating=True,
        read_only=False,
        timeout=300.0,
    )
    probe_step = _bootstrap_sql_step(
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        step_id=_BOOTSTRAP_PROBE_STEP_ID,
    )
    ready_step = _bootstrap_sql_step(
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        step_id=_BOOTSTRAP_READY_STEP_ID,
    )
    ready_action = PreparedAction(
        step_id=_BOOTSTRAP_VERIFY_ACTION_ID,
        action="verify-bootstrap-tmp-base",
        description="SQL readiness: base module installed on tmp",
        read_only=True,
    )
    lock_action = (
        PreparedAction(
            step_id=_BOOTSTRAP_LOCK_ACTION_ID,
            action="acquire-database-preparation-lock",
            description="Serialize opted-in tmp database preparation",
            details={"project_id": project_id},
            mutating=True,
        )
        if include_create and project_id is not None
        else None
    )
    return BootstrapTmpPlan(
        spawn_step=spawn_step,
        probe_step=probe_step,
        ready_step=ready_step,
        verify_action=ready_action,
        lock_action=lock_action,
        create_step=create_step,
        record_action=record_action,
        project_id=project_id if lock_action is not None else None,
    )


def bootstrap_record_action() -> PreparedAction:
    """Return the conditional lifecycle publication action for bootstrap."""
    return PreparedAction(
        step_id=_BOOTSTRAP_RECORD_ACTION_ID,
        action="record-bootstrap-tmp",
        description="Record exact lifecycle evidence for newly-created tmp",
        mutating=True,
    )


def run_bootstrap_tmp(
    context: RunContext[_ContextT],
    plan: BootstrapTmpPlan,
) -> BootstrapOutcome:
    """Execute or skip the bootstrap spawn using an existing run context."""
    if _probe_tmp_ready(context, plan.probe_step):
        context.skip(plan.spawn_step.step_id)
        if plan.create_step is not None:
            context.skip(plan.create_step.step_id)
        context.skip(plan.ready_step.step_id)
        return BootstrapOutcome.ALREADY_READY
    if plan.create_step is not None:
        result = context.process_prepared(plan.create_step)
        if getattr(result, "returncode", 1) != 0:
            raise BootstrapFailedError("tmp bootstrap database creation failed")
    try:
        context.process_prepared(plan.spawn_step)
    except BaseException as error:
        raise BootstrapFailedError(f"tmp bootstrap spawn failed: {error}") from error
    if not _probe_tmp_ready(context, plan.ready_step):
        raise BootstrapFailedError(
            "tmp bootstrap failed: base module is not installed on tmp after --stop-after-init"
        )
    return BootstrapOutcome.CREATED


def execute_bootstrap_tmp(
    context: RunContext[_ContextT],
    plan: BootstrapTmpPlan,
    *,
    on_created: Callable[[], None] | None = None,
) -> BootstrapOutcome:
    """Serialize the complete bootstrap boundary, including lifecycle record."""
    lock = (
        exclusive_lock_until(
            database_preparation_lock_path(plan.project_id),
            time.monotonic() + 300.0,
        )
        if plan.lock_action is not None and plan.project_id is not None
        else contextlib.nullcontext()
    )
    with lock:
        if plan.lock_action is not None:
            context.action(plan.lock_action.step_id)
        context.action(plan.verify_action.step_id)
        outcome = run_bootstrap_tmp(context, plan)
        context.complete_action(plan.verify_action.step_id)
        if plan.record_action is not None:
            if outcome is BootstrapOutcome.CREATED:
                context.action(plan.record_action.step_id)
                if on_created is not None:
                    on_created()
                context.complete_action(plan.record_action.step_id)
            else:
                context.skip(plan.record_action.step_id)
        if plan.lock_action is not None:
            context.complete_action(plan.lock_action.step_id)
    return outcome


def tmp_bootstrap_command(
    *,
    command_prefix: tuple[str, ...],
    start_config: StartConfig,
    db_host: str,
    db_port: int,
    db_user: str,
    db_password: str,
    default_cwd: Path | None = None,
    executor: ProcessExecutor | None = None,
) -> Command[bool]:
    """Build the immutable ``tmp`` bootstrap command for preview and execution.

    On execute: the owned cluster must already be running (callers ensure this).
    The command is idempotent — if the SQL readiness probe already succeeds,
    the Odoo spawn step is skipped.
    """
    bootstrap_plan = bootstrap_tmp_plan(
        command_prefix=command_prefix,
        start_config=start_config,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        default_cwd=default_cwd,
        include_create=False,
    )

    def run(context: RunContext[bool]) -> bool:
        execute_bootstrap_tmp(context, bootstrap_plan)
        return True

    execution_plan = ExecutionPlan(
        steps=(*(step.public_projection() for step in bootstrap_plan.execution_steps),),
    )
    return Command.create(
        execution_plan,
        run,
        bootstrap_plan.execution_steps,
        executor=executor or SubprocessExecutor(),
    )


def project_bootstrap_tmp_plan(instance: OdooInstance) -> BootstrapTmpPlan | None:
    """Capture bootstrap steps for an owned Compose project."""
    from odoo_instance_sdk.resources.instance import OdooInstance

    if not isinstance(instance, OdooInstance):
        return None
    cluster = instance._postgres_cluster
    if cluster is None or not cluster.owned:
        return None
    start_config = instance.config.start_config
    if start_config is None:
        return None
    return bootstrap_tmp_plan(
        command_prefix=instance._executable_prefix(),
        start_config=start_config,
        db_host=cluster.endpoint_host,
        db_port=cluster.endpoint_port,
        db_user=start_config.db_user or "odoo",
        db_password=start_config.db_password or "",
        default_cwd=instance.config.default_cwd,
        include_create=False,
        project_id=cluster._project_id,
        record_action=bootstrap_record_action(),
    )


def ensure_project_bootstrap_tmp(
    instance: OdooInstance,
    context: RunContext[_ContextT],
    *,
    plan: BootstrapTmpPlan | None = None,
) -> None:
    """Ensure owned Compose projects have a valid bootstrap ``tmp`` database."""
    captured = project_bootstrap_tmp_plan(instance) if plan is None else plan
    if captured is None:
        return
    execute_bootstrap_tmp(context, captured, on_created=lambda: _record_bootstrap_event(instance))


def _probe_tmp_ready(context: RunContext[_ContextT], probe_step: PreparedStep) -> bool:
    """Run the SQL readiness probe; return True when base is ``installed``."""
    try:
        result = context.process_prepared(probe_step)
    except Exception:
        return False
    stdout = (getattr(result, "stdout", "") or "").strip()
    returncode = getattr(result, "returncode", 1)
    return returncode == 0 and stdout == "installed"


def _record_bootstrap_event(instance: OdooInstance) -> None:
    """Publish bootstrap ownership using the already-resolved project identity."""
    from odoo_instance_sdk.internal.project_init import (
        verify_project_owned_data_dir,
    )

    cluster = instance._postgres_cluster
    if cluster is None or not cluster.owned:
        raise BootstrapFailedError("bootstrap cluster ownership is unavailable")
    project_root = instance.config.default_cwd or Path.cwd()
    configured_data_directory = (
        instance.config.start_config.data_dir if instance.config.start_config else None
    )
    if configured_data_directory is None:
        raise BootstrapFailedError("bootstrap data directory is not configured")
    configured_path = Path(configured_data_directory)
    if not configured_path.is_absolute():
        configured_path = project_root / configured_path
    try:
        data_directory = verify_project_owned_data_dir(project_root, configured_path)
    except InstanceConfigurationError as error:
        raise BootstrapFailedError(
            f"bootstrap data directory is not project-owned: {error}"
        ) from error
    record_bootstrap_event(cluster, data_directory)


def record_bootstrap_event(cluster: PostgresCluster, data_directory: str | Path) -> None:
    """Publish a bootstrap event for an active, exact Compose cluster claim."""
    from odoo_instance_sdk.internal.paths import get_catalog_path
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    catalog = BackupCatalog(db_path=get_catalog_path())
    claim = catalog._get_postgres_cluster(cluster._project_id)
    if claim is None or claim.state != "active":
        catalog.close()
        raise BootstrapFailedError("bootstrap cluster claim is not active")
    try:
        catalog._record_database_bootstrapped(
            cluster.endpoint_host,
            cluster.endpoint_port,
            BOOTSTRAP_DATABASE,
            cluster_id=claim.cluster_id,
            data_directory=data_directory,
        )
    except Exception as error:
        if isinstance(error, BootstrapFailedError):
            raise
        raise BootstrapFailedError(f"bootstrap lifecycle publication failed: {error}") from error
    finally:
        catalog.close()
