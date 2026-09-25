from __future__ import annotations

import contextlib
import sys
from collections.abc import Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.exceptions import InstanceConfigurationError
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    ProcessHandle,
    SubprocessExecutor,
    terminate,
    terminate_pid,
)
from odoo_instance_sdk.internal.process_env import captured_child_environment
from odoo_instance_sdk.internal.server import (
    _write_secret_config,
    cleanup_secret_config,
    get_process_status,
)
from odoo_instance_sdk.models import (
    DetachedLaunchResult,
    OdooProcess,
    ProcessStatus,
    ReadinessResult,
    StartConfig,
    StopEnvironmentResult,
)
from odoo_instance_sdk.resources.instance.auxiliary_restore import (
    _assert_http_port_free,
    _child_secret_values,
    _command_plan,
    _http_port_observation,
    _runtime_owner,
    _snapshot_start_inputs,
    resolve_runtime_argv_extra,
)
from odoo_instance_sdk.resources.instance.runtime import (
    T,
    _ensure_logfile_writable,
    _RuntimeBinding,
    _RuntimeCatalog,
    _RuntimeIdentity,
    _verify_process_exit,
    resolve_effective_logfile,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.config import InstanceConfig
    from odoo_instance_sdk.execution import (
        Command,
    )
    from odoo_instance_sdk.internal.proc import RunContext


def _raise_if_exited(exited: bool, handle: ProcessHandle | None = None) -> None:
    if exited:
        tails = handle.drain_tails() if handle is not None else {"stdout": "", "stderr": ""}
        raise InstanceConfigurationError(
            "detached Odoo process exited immediately after spawn; "
            "check the bound logfile for the failure\n"
            f"stdout_tail={tails['stdout']!r}\n"
            f"stderr_tail={tails['stderr']!r}"
        )


class _PlanningMixin:
    if TYPE_CHECKING:
        config: InstanceConfig
        _client: OdooClient
        _environment_id: str | None
        _runtime_binding: _RuntimeBinding | None

        def _artifact_operation(self, *, exclusive: bool) -> AbstractContextManager[None]: ...
        def _artifact_lock(self) -> AbstractContextManager[None]: ...
        def _read_runtime_identity(self) -> _RuntimeIdentity | None: ...
        @staticmethod
        def _validate_runtime_identity(identity: _RuntimeIdentity) -> None: ...
        def _clear_runtime_identity(self) -> None: ...
        def _dependency_manifest(
            self,
        ) -> tuple[tuple[PreparedStep | PreparedAction, ...], Path | None]: ...
        def _ensure_dependencies_ready(
            self,
            context: RunContext[T] | None = None,
            *,
            dependency_steps: Sequence[PreparedStep | PreparedAction] = (),
            temporary_path: Path | None = None,
        ) -> None: ...
        def _executable_prefix(self) -> tuple[str, ...]: ...
        def _persist_runtime_identity(
            self,
            root_pid: int,
            config: StartConfig,
            cwd: str | Path | None,
            context: RunContext[T] | None = None,
        ) -> None: ...

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
            "owner_kind",
            "owner_id",
            "project_id",
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

    def stop_runtime(self, *, timeout: float = 10.0) -> dict[str, str | None]:
        return self.stop_runtime_command(timeout=timeout).run()

    def stop_environment(self, *, timeout: float = 10.0) -> StopEnvironmentResult:
        payload = self.stop_environment_command(timeout=timeout).run()
        return StopEnvironmentResult(
            status=str(payload["status"]),
            environment_id=str(payload["environment_id"]),
        )

    def stop_environment_command(self, *, timeout: float = 10.0) -> Command[dict[str, str]]:
        if self._runtime_binding is not None and self._runtime_binding.owner_kind != "environment":
            raise InstanceConfigurationError(
                "stop_environment requires an environment-owned runtime"
            )
        return cast(
            "Command[dict[str, str]]",
            self._stop_runtime_command(timeout=timeout, legacy_environment=True),
        )

    def stop_runtime_command(self, *, timeout: float = 10.0) -> Command[dict[str, str | None]]:
        return self._stop_runtime_command(timeout=timeout, legacy_environment=False)

    def _stop_runtime_command(  # noqa: C901
        self, *, timeout: float, legacy_environment: bool
    ) -> Command[dict[str, str | None]]:
        from odoo_instance_sdk.execution import Command, ExecutionPlan

        def owner_fields(identity: _RuntimeIdentity | None) -> dict[str, str | None]:
            if identity is not None:
                return {
                    "owner_kind": identity.owner_kind,
                    "owner_id": identity.owner_id,
                    "project_id": identity.project_id,
                    "environment_id": identity.environment_id,
                }
            binding = self._runtime_binding
            if binding is not None:
                return {
                    "owner_kind": binding.owner_kind,
                    "owner_id": binding.owner_id,
                    "project_id": binding.project_id,
                    "environment_id": (
                        binding.owner_id if binding.owner_kind == "environment" else None
                    ),
                }
            environment_id = self._environment_id
            return {
                "owner_kind": "environment",
                "owner_id": environment_id,
                "project_id": "",
                "environment_id": environment_id,
            }

        def result(status: str, identity: _RuntimeIdentity | None) -> dict[str, str | None]:
            fields = owner_fields(identity)
            if legacy_environment:
                return {
                    "status": status,
                    "environment_id": fields["environment_id"],
                }
            return {"status": status, **fields}

        with self._artifact_operation(exclusive=False):
            planned_identity = self._read_runtime_identity()

        action_ids = (
            "instance.stop.runtime",
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

        def execute(context: RunContext[dict[str, str | None]]) -> dict[str, str | None]:
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
                        return result("already_stopped", None)
                    if identity.vanished:
                        context.skip(action_ids[2])
                        context.action(action_ids[3])
                        context.complete_action(action_ids[3])
                        context.action(action_ids[4])
                        self._clear_runtime_identity_if_matches(identity)
                        context.complete_action(action_ids[4])
                        return result("already_stopped", identity)
                    self._validate_runtime_identity(identity)
                    context.action(action_ids[2])
                    terminate_pid(
                        identity.root_pid,
                        process_group_id=identity.process_group_id,
                        expected_create_time=identity.create_time,
                        timeout=timeout,
                    )
                    context.complete_action(action_ids[2])
                    context.action(action_ids[3])
                    _verify_process_exit(
                        identity.root_pid, expected_create_time=identity.create_time
                    )
                    context.complete_action(action_ids[3])
                    context.action(action_ids[4])
                    self._clear_runtime_identity_if_matches(identity)
                    context.complete_action(action_ids[4])
                    return result("stopped", identity)
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
        validated_args = resolve_runtime_argv_extra(self.config.default_run_args, args)
        resolved_cwd = cwd if cwd is not None else self.config.default_cwd
        # Resolve the effective logfile and inject ``--logfile {path}`` after
        # the protected-option check above.  An explicit non-empty ``logfile``
        # in the bound ``odoo.conf`` wins; otherwise ``odoo.log`` next to the
        # effective config is chosen.  The user's ``odoo.conf`` is never edited.
        effective_logfile = resolve_effective_logfile(
            config, Path(resolved_cwd) if resolved_cwd is not None else None
        )
        snapshot, cli_args, secret_path, secrets = _snapshot_start_inputs(config)
        environment_snapshot, environment_overrides = captured_child_environment(
            env, project_environment=self.config.project_environment
        )
        secrets = (*secrets, *_child_secret_values(self.config.project_environment, env))
        step = PreparedStep(
            step_id="instance.detached",
            argv=(
                *self._executable_prefix(),
                *cli_args,
                "--logfile",
                str(effective_logfile),
                *validated_args,
            ),
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
        command_steps = (*prepared_steps, *actions)
        process_executor = SubprocessExecutor()
        logfile_path = str(effective_logfile)

        def execute(context: RunContext[DetachedLaunchResult]) -> DetachedLaunchResult:
            if type(process_executor) is SubprocessExecutor:
                _assert_http_port_free(config)
            _ensure_logfile_writable(effective_logfile)
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
                    _write_secret_config(snapshot, secret_path)
                    secret_created = True
                handle: ProcessHandle | None = None
                try:
                    context.action(action_ids[1])
                    handle = context.spawn(step.step_id)
                    context.complete_action(action_ids[1])
                    context.action(action_ids[2])
                    exited = handle.poll() is not None
                    context.complete_action(action_ids[2])
                    _raise_if_exited(exited, handle)
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
                            terminate(
                                handle,
                                process_group_id=handle.process_group_id,
                                timeout=5.0,
                            )
                    self._clear_runtime_identity()
                    raise
                finally:
                    if secret_created:
                        cleanup_secret_config(secret_path)

        from odoo_instance_sdk.execution import Command

        return Command.create(
            _command_plan(
                command_steps,
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
            command_steps,
            executor=process_executor,
        )

    def _clear_runtime_identity_if_matches(self, identity: _RuntimeIdentity) -> None:
        catalog = cast("_RuntimeCatalog", self._client.get_catalog())
        cleared = catalog._clear_runtime_if_matches(
            *identity.owner,
            root_pid=identity.root_pid,
            create_time=identity.create_time,
        )
        if not cleared:
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
                    terminate(
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
            executor=SubprocessExecutor(),
        )

    def status(self, proc: OdooProcess) -> ProcessStatus:
        self._client.get_process(proc.id)
        return get_process_status(self._client.get_handle(proc.id))

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
