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

from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from odoo_instance_sdk.exceptions import InstanceConfigurationError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.pg.builder import build_psql_specification
from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep, SubprocessExecutor
from odoo_instance_sdk.models import StartConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import ProcessExecutor, RunContext

_ContextT = TypeVar("_ContextT")

BOOTSTRAP_DATABASE = "tmp"
_BOOTSTRAP_DATABASE = BOOTSTRAP_DATABASE
_BOOTSTRAP_INIT_MODULE = "base"
_BOOTSTRAP_READY_SQL = b"SELECT state FROM ir_module_module WHERE name = 'base'"

_BOOTSTRAP_STEP_ID = "init.bootstrap.tmp"
_BOOTSTRAP_PROBE_STEP_ID = "init.bootstrap.tmp.probe"
_BOOTSTRAP_READY_STEP_ID = "init.bootstrap.tmp.ready"
_BOOTSTRAP_VERIFY_ACTION_ID = "init.bootstrap.tmp.verify"


class BootstrapFailedError(InstanceConfigurationError):
    """Raised when the ``tmp`` bootstrap database cannot be created or verified."""

    error_code = "init_bootstrap_failed"

    def __init__(self, message: str) -> None:
        super().__init__(message)


def _bootstrap_sql_step(
    *,
    db_host: str,
    db_port: int,
    db_user: str,
    db_password: str,
    step_id: str,
) -> PreparedStep:
    """Build one captured SQL probe for bootstrap readiness."""
    specification = build_psql_specification(
        host=db_host,
        port=db_port,
        user=db_user,
        password=db_password,
        database=_BOOTSTRAP_DATABASE,
        stdin=_BOOTSTRAP_READY_SQL,
        timeout=30.0,
        mode="captured",
        step_id=step_id,
        _trusted_args=("-q", "-t", "-A", "-v", "ON_ERROR_STOP=1"),
        _read_only=True,
        _mutating=False,
    )
    return specification.prepared_step


def bootstrap_tmp_steps(
    *,
    command_prefix: tuple[str, ...],
    start_config: StartConfig,
    db_host: str,
    db_port: int,
    db_user: str,
    db_password: str,
    default_cwd: Path | None = None,
) -> tuple[PreparedStep, PreparedStep, PreparedStep, PreparedAction]:
    """Capture bootstrap spawn, pre/post SQL probes, and verify action.

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
    return spawn_step, probe_step, ready_step, ready_action


def run_bootstrap_tmp(
    context: RunContext[_ContextT],
    spawn_step: PreparedStep,
    probe_step: PreparedStep,
    ready_step: PreparedStep,
) -> bool:
    """Execute or skip the bootstrap spawn using an existing run context."""
    if _probe_tmp_ready(context, probe_step):
        context.skip(spawn_step.step_id)
        context.skip(ready_step.step_id)
        return True
    try:
        context.process_prepared(spawn_step)
    except BaseException as error:
        raise BootstrapFailedError(f"tmp bootstrap spawn failed: {error}") from error
    if not _probe_tmp_ready(context, ready_step):
        raise BootstrapFailedError(
            "tmp bootstrap failed: base module is not installed on tmp after --stop-after-init"
        )
    return True


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
    spawn_step, probe_step, ready_step, ready_action = bootstrap_tmp_steps(
        command_prefix=command_prefix,
        start_config=start_config,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        default_cwd=default_cwd,
    )

    def run(context: RunContext[bool]) -> bool:
        context.action(ready_action.step_id)
        result = run_bootstrap_tmp(context, spawn_step, probe_step, ready_step)
        context.complete_action(ready_action.step_id)
        return result

    plan = ExecutionPlan(
        steps=(
            spawn_step.public_projection(),
            probe_step.public_projection(),
            ready_step.public_projection(),
            ready_action.public_projection(),
        ),
    )
    return Command.create(
        plan,
        run,
        (spawn_step, probe_step, ready_step, ready_action),
        executor=executor or SubprocessExecutor(),
    )


def ensure_project_bootstrap_tmp(instance: object, context: RunContext[_ContextT]) -> None:
    """Ensure owned Compose projects have a valid bootstrap ``tmp`` database."""
    from odoo_instance_sdk.resources.instance import OdooInstance

    if not isinstance(instance, OdooInstance):
        return
    cluster = instance._postgres_cluster
    if cluster is None or not cluster.owned:
        return
    start_config = instance.config.start_config
    if start_config is None:
        return
    spawn_step, probe_step, ready_step, ready_action = bootstrap_tmp_steps(
        command_prefix=instance._executable_prefix(),
        start_config=start_config,
        db_host=cluster.endpoint_host,
        db_port=cluster.endpoint_port,
        db_user=start_config.db_user or "odoo",
        db_password=start_config.db_password or "",
        default_cwd=instance.config.default_cwd,
    )
    context.action(ready_action.step_id)
    run_bootstrap_tmp(context, spawn_step, probe_step, ready_step)
    context.complete_action(ready_action.step_id)


def _probe_tmp_ready(context: RunContext[_ContextT], probe_step: PreparedStep) -> bool:
    """Run the SQL readiness probe; return True when base is ``installed``."""
    try:
        result = context.process_prepared(probe_step)
    except Exception:
        return False
    stdout = (getattr(result, "stdout", "") or "").strip()
    returncode = getattr(result, "returncode", 1)
    return returncode == 0 and stdout == "installed"
