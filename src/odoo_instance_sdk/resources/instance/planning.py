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
    DetachedLaunchResult,
    OdooProcess,
    ProcessStatus,
    ReadinessResult,
    StartConfig,
    StopEnvironmentResult,
)
from odoo_instance_sdk.resources.instance import helpers as _helpers

terminate = _instance_shim.terminate


def _raise_if_exited(exited: bool) -> None:
    if exited:
        raise InstanceConfigurationError(
            "detached Odoo process exited immediately after spawn; "
            "check the bound logfile for the failure"
        )


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

    def run_detached(
        self,
        config: StartConfig | None = None,
        *,
        args: Sequence[str] = (),
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> DetachedLaunchResult:
        return self.run_detached_command(config, args=args, cwd=cwd, env=env).run()

    def run_detached_command(  # noqa: C901
        self,
        config: StartConfig | None = None,
        *,
        args: Sequence[str] = (),
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> Command[DetachedLaunchResult]:
        if config is None:
            config = self.config.start_config
            if config is None:
                raise InstanceConfigurationError(
                    "No StartConfig — pass one explicitly or create instance via from_config()"
                )
        raw_logfile = config.logfile
        if raw_logfile is None or not raw_logfile.strip():
            raise InstanceConfigurationError(
                "detached launch requires a logfile in the bound odoo.conf; "
                "set logfile before running detached"
            )
        validated_args = resolve_runtime_argv_extra(self.config.default_run_args, args)
        resolved_cwd = cwd if cwd is not None else self.config.default_cwd
        snapshot, cli_args, secret_path, secrets = _snapshot_start_inputs(config)
        environment_snapshot, environment_overrides = captured_child_environment(
            env, project_environment=self.config.project_environment
        )
        secrets = (*secrets, *_child_secret_values(self.config.project_environment, env))
        step = PreparedStep(
            step_id="instance.detached",
            argv=(*self._executable_prefix(), *cli_args, *validated_args),
            cwd=None if resolved_cwd is None else str(resolved_cwd),
            environment=environment_overrides,
            environment_snapshot=environment_snapshot,
            environment_overrides=environment_overrides,
            mode="long-running",
            secret_values=secrets,
            long_running=True,
            start_new_session=True,
            # ponytail: inherit_stdio=False routes stdout/stderr to PIPE; Odoo
            # writes to the bound logfile so the pipes stay empty.  Redirect to
            # DEVNULL at the proc boundary if a non-logfile Odoo ever blocks.
            inherit_stdio=False,
        )

        dependency_steps, dependency_temporary_path = self._dependency_manifest()
        prepared_steps: tuple[PreparedStep | PreparedAction, ...] = (*dependency_steps, step)
        if (
            self._runtime_binding is not None or self._environment_id is not None
        ) and resolved_cwd is not None:
            target = str(resolved_cwd)
            prepared_steps += (
                PreparedStep(
                    step_id="instance.foreground.git.branch",
                    argv=("git", "-C", target, "rev-parse", "--abbrev-ref", "HEAD"),
                    cwd=target,
                    timeout=10.0,
                    read_only=True,
                ),
                PreparedStep(
                    step_id="instance.foreground.git.commit",
                    argv=("git", "-C", target, "rev-parse", "HEAD"),
                    cwd=target,
                    timeout=10.0,
                    read_only=True,
                ),
            )
        action_ids = (
            "instance.detached.assert_port",
            "instance.detached.spawn",
            "instance.detached.confirm_alive",
            "instance.detached.persist",
        )
        actions = tuple(
            PreparedAction(
                step_id=step_id,
                action=step_id,
                description=step_id.replace(".", " "),
                read_only=step_id == "instance.detached.assert_port",
                mutating=step_id in {"instance.detached.spawn", "instance.detached.persist"},
            )
            for step_id in action_ids
        )
        process_executor = _instance_shim.SubprocessExecutor()
        logfile_path = str((self.config.default_cwd or Path.cwd()) / raw_logfile.strip())

        def execute(context: RunContext[DetachedLaunchResult]) -> DetachedLaunchResult:
            if type(process_executor) is _instance_shim.SubprocessExecutor:
                _assert_http_port_free(config)
            context.action(action_ids[0])
            context.complete_action(action_ids[0])
            self._ensure_dependencies_ready(
                context,
                dependency_steps=dependency_steps,
                temporary_path=dependency_temporary_path,
            )
            for dependency_step in dependency_steps:
                if context.planned(dependency_step.step_id) and not context.consumed(
                    dependency_step.step_id
                ):
                    context.skip(dependency_step.step_id)
            with self._artifact_lock():
                secret_created = False
                if secret_path is not None:
                    _instance_shim._write_secret_config(snapshot, secret_path)
                    secret_created = True
                handle: ProcessHandle | None = None
                try:
                    context.action(action_ids[1])
                    handle = context.spawn(step.step_id)
                    context.complete_action(action_ids[1])
                    context.action(action_ids[2])
                    exited = handle.poll() is not None
                    context.complete_action(action_ids[2])
                    _raise_if_exited(exited)
                    context.action(action_ids[3])
                    if self._runtime_binding is not None or self._environment_id is not None:
                        self._persist_runtime_identity(
                            handle.pid,
                            snapshot,
                            resolved_cwd,
                            context=context,
                        )
                    context.complete_action(action_ids[3])
                    owner_kind, owner_id = _runtime_owner(
                        self._runtime_binding, self._environment_id
                    )
                    return DetachedLaunchResult(
                        pid=handle.pid,
                        owner_kind=owner_kind,
                        owner_id=owner_id,
                        http_endpoint=f"http://{config.http_interface}:{config.http_port}",
                        log_path=logfile_path,
                    )
                except BaseException:
                    if handle is not None:
                        with contextlib.suppress(BaseException):
                            _instance_shim.terminate(
                                handle,
                                process_group_id=handle.process_group_id,
                                timeout=5.0,
                            )
                    self._clear_runtime_identity()
                    raise
                finally:
                    if secret_created:
                        _instance_shim.cleanup_secret_config(secret_path)

        from odoo_instance_sdk.execution import Command

        return Command.create(
            _command_plan(
                prepared_steps,
                secrets=secrets,
                observations=(
                    _http_port_observation(
                        config,
                        environment_id=self._environment_id,
                        client=self._client,
                    ),
                ),
            ),
            execute,
            (*prepared_steps, *actions),
            executor=process_executor,
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
