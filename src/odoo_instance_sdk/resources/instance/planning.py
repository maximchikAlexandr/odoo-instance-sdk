from __future__ import annotations

# ruff: noqa: F821
import sys
from typing import TYPE_CHECKING, cast

import odoo_instance_sdk.resources.instance as _instance_shim
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    ProcessHandle,
)
from odoo_instance_sdk.internal.server import cleanup_secret_config
from odoo_instance_sdk.models import (
    OdooProcess,
    ProcessStatus,
    ReadinessResult,
    StopEnvironmentResult,
)
from odoo_instance_sdk.resources.instance import helpers as _helpers

terminate = _instance_shim.terminate

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import (
        Command,
    )
    from odoo_instance_sdk.internal.proc import RunContext

globals().update(
    {name: value for name, value in _helpers.__dict__.items() if not name.startswith("__")}
)


class _PlanningMixin:
    def _assert_runtime_identity_unchanged(
        self,
        planned: _RuntimeIdentity | None,
        current: _RuntimeIdentity | None,
    ) -> None:
        if planned is None or current is None:
            if planned is current:
                return
            raise RuntimeError("runtime identity changed after planning")

        persisted_fields = (
            "environment_id",
            "root_pid",
            "create_time",
            "expected_executable",
            "expected_argv",
            "expected_cwd",
            "expected_config_path",
        )
        if any(getattr(planned, field) != getattr(current, field) for field in persisted_fields):
            raise RuntimeError("runtime identity changed after planning")
        if planned.vanished != current.vanished and not (
            planned.vanished is False and current.vanished is True
        ):
            raise RuntimeError("runtime identity changed after planning")
        if planned.vanished or current.vanished:
            return
        live_fields = (
            "live_create_time",
            "live_executable",
            "live_argv",
            "live_cwd",
            "live_config_path",
            "process_group_id",
        )
        if any(getattr(planned, field) != getattr(current, field) for field in live_fields):
            raise RuntimeError("runtime identity changed after planning")

    def stop_environment(self, *, timeout: float = 10.0) -> StopEnvironmentResult:
        payload = self.stop_environment_command(timeout=timeout).run()
        return StopEnvironmentResult(
            status=str(payload["status"]),
            environment_id=str(payload["environment_id"]),
        )

    def stop_environment_command(self, *, timeout: float = 10.0) -> Command[dict[str, str]]:
        from odoo_instance_sdk.execution import Command, ExecutionPlan

        with self._artifact_operation(exclusive=False):
            planned_identity = self._read_runtime_identity()

        action_ids = (
            "instance.stop.environment",
            "instance.stop.revalidate",
            "instance.stop.terminate",
            "instance.stop.verify_exit",
            "instance.stop.clear_runtime",
        )
        actions = tuple(
            PreparedAction(
                step_id=step_id,
                action=step_id,
                description=step_id.replace(".", " "),
                read_only=step_id in {"instance.stop.revalidate", "instance.stop.verify_exit"},
                mutating=step_id in {"instance.stop.terminate", "instance.stop.clear_runtime"},
            )
            for step_id in action_ids
        )

        def execute(context: RunContext[dict[str, str]]) -> dict[str, str]:
            context.action(action_ids[0])
            try:
                with self._artifact_operation(exclusive=True):
                    context.action(action_ids[1])
                    identity = self._read_runtime_identity()
                    self._assert_runtime_identity_unchanged(planned_identity, identity)
                    context.complete_action(action_ids[1])
                    if identity is None:
                        for step_id in action_ids[2:]:
                            context.skip(step_id)
                        return {
                            "status": "already_stopped",
                            "environment_id": str(self._environment_id),
                        }
                    if identity.vanished:
                        context.skip(action_ids[2])
                        context.action(action_ids[3])
                        context.complete_action(action_ids[3])
                        context.action(action_ids[4])
                        self._clear_runtime_identity_if_matches(identity)
                        context.complete_action(action_ids[4])
                        return {
                            "status": "already_stopped",
                            "environment_id": identity.environment_id,
                        }
                    self._validate_runtime_identity(identity)
                    context.action(action_ids[2])
                    _instance_shim.terminate_pid(
                        identity.root_pid,
                        process_group_id=identity.process_group_id,
                        timeout=timeout,
                    )
                    context.complete_action(action_ids[2])
                    context.action(action_ids[3])
                    _verify_process_exit(identity.root_pid)
                    context.complete_action(action_ids[3])
                    context.action(action_ids[4])
                    self._clear_runtime_identity_if_matches(identity)
                    context.complete_action(action_ids[4])
                    return {"status": "stopped", "environment_id": identity.environment_id}
            except BaseException as error:
                context.fail_action(action_ids[0], error)
                raise
            else:
                context.complete_action(action_ids[0])

        return Command.create(
            ExecutionPlan(
                steps=tuple(step.public_projection() for step in actions),
            ),
            execute,
            actions,
        )

    def _clear_runtime_identity_if_matches(self, identity: _RuntimeIdentity) -> None:
        catalog = cast("_RuntimeCatalog", self._client.get_catalog())
        if not catalog._clear_environment_runtime_if_matches(
            identity.environment_id,
            root_pid=identity.root_pid,
            create_time=identity.create_time,
        ):
            raise RuntimeError("runtime identity changed before clearing its row")

    def stop(self, proc: OdooProcess, *, timeout: float = 10.0) -> None:
        self.stop_command(proc, timeout=timeout).run()

    def stop_command(self, proc: OdooProcess, *, timeout: float = 10.0) -> Command[None]:
        handle = self._client.get_handle(proc.id)
        steps: list[PreparedStep | PreparedAction] = []
        if handle is not None and sys.platform == "win32":
            steps.append(
                PreparedStep(
                    step_id="instance.stop.taskkill",
                    argv=("taskkill", "/T", "/PID", str(proc.pid), "/F"),
                    timeout=timeout,
                    mode="captured",
                    mutating=True,
                )
            )
        elif handle is not None:
            steps.append(
                PreparedAction(
                    step_id="instance.stop.signal",
                    action="terminate_process_group",
                    description="Terminate the owned POSIX process group",
                    details={"pid": proc.pid, "timeout": timeout},
                    mutating=True,
                )
            )
        if handle is not None:
            steps.append(
                PreparedAction(
                    step_id="instance.stop.cleanup",
                    action="cleanup_secret_config",
                    description="Remove the private process configuration",
                    mutating=True,
                )
            )
        frozen_steps = tuple(steps)

        def execute(context: RunContext[None]) -> None:
            owned, secret_config = self._client.unregister_process(proc.id)
            if owned is None:
                return
            if sys.platform == "win32":
                context.process("instance.stop.taskkill")
            else:
                context.action("instance.stop.signal")
                try:
                    _instance_shim.terminate(
                        ProcessHandle(
                            process=owned,
                            argv=(),
                            process_group_id=owned.pid,
                            session_id=owned.pid,
                            inherited_stdio=False,
                        ),
                        process_group_id=owned.pid,
                        timeout=timeout,
                    )
                finally:
                    context.action("instance.stop.cleanup")
                    cleanup_secret_config(secret_config)
                return
            context.action("instance.stop.cleanup")
            cleanup_secret_config(secret_config)

        from odoo_instance_sdk.execution import Command

        return Command.create(
            _command_plan(frozen_steps),
            execute,
            frozen_steps,
            executor=_instance_shim.SubprocessExecutor(),
        )

    def status(self, proc: OdooProcess) -> ProcessStatus:
        self._client.get_process(proc.id)
        return _instance_shim.get_process_status(self._client.get_handle(proc.id))

    def wait_ready(
        self,
        proc: OdooProcess,
        *,
        timeout: float = 60.0,
        version_info: bool = False,
        database_manager: bool = False,
    ) -> ReadinessResult:
        self._client.get_process(proc.id)
        from odoo_instance_sdk.internal.health import poll_health

        def alive_check() -> bool:
            handle = self._client.get_handle(proc.id)
            return handle is not None and handle.poll() is None

        return poll_health(
            self.config.base_url,
            timeout=timeout,
            alive_check=alive_check,
            version_info=version_info,
            database_manager=database_manager,
        )
