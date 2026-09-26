from __future__ import annotations

import contextlib
import copy
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

import psutil

from odoo_instance_sdk.exceptions import (
    InstanceConfigurationError,
)
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    ProcessHandle,
    ProcessResult,
    terminate,
)
from odoo_instance_sdk.internal.process_env import (
    captured_child_environment,
    sanitized_child_environment,
)
from odoo_instance_sdk.internal.project_env import (
    MASTER_PASSWORD_KEY,
    project_environment_secret_values,
)
from odoo_instance_sdk.internal.server import (
    _write_secret_config,
    cleanup_secret_config,
)
from odoo_instance_sdk.models import (
    CommandResult,
    OdooProcess,
    StartConfig,
)
from odoo_instance_sdk.resources.instance import runtime_identity as _restore_identity
from odoo_instance_sdk.resources.instance.runtime import (
    _PROTECTED_RUNTIME_OPTIONS,
    T,
    _build_cli_args,
    _canonical_runtime_path,
    _RuntimeBinding,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.commands.output import _InspectableCommand
    from odoo_instance_sdk.execution import (
        Command,
        ExecutionPlan,
        PlanObservation,
        SemanticPlanObservation,
    )
    from odoo_instance_sdk.internal.proc import RunContext
    from odoo_instance_sdk.internal.project_runtime import DeferredProjectRuntime
    from odoo_instance_sdk.project import ProjectConfig
    from odoo_instance_sdk.resources.instance import OdooInstance


_ContextResult = TypeVar("_ContextResult")
_CommandResult = TypeVar("_CommandResult")

_listener_owner_pids = _restore_identity._listener_owner_pids
_project_runtime_owns_port = _restore_identity._project_runtime_owns_port
_recorded_runtime_pid = _restore_identity._recorded_runtime_pid
_socket_owned_by = _restore_identity._socket_owned_by


def _process_create_time(pid: int) -> float:
    """Return the exact process identity used by runtime reconciliation."""
    try:
        return float(psutil.Process(pid).create_time())
    except (psutil.Error, OSError):
        # Test/injected process seams may expose a synthetic PID.  Real
        # subprocesses take the exact psutil path; the timestamp fallback keeps
        # persistence best-effort without making foreground execution fail.
        return time.time()


def _worktree_ref(
    cwd: str | Path | None,
    *,
    context: RunContext[T] | None = None,
) -> tuple[str, str]:
    """Return ``(branch, commit_sha)`` for the worktree at ``cwd``.

    Best-effort: on any git failure returns ``("unknown", "")``.
    """
    if cwd is None:
        return "unknown", ""
    target = str(cwd)
    try:
        if context is None:
            from odoo_instance_sdk.internal.proc import run_captured

            branch_proc = run_captured(
                ("git", "-C", target, "rev-parse", "--abbrev-ref", "HEAD"),
                env=sanitized_child_environment(),
                timeout=10,
                text=True,
            )
            sha_proc = run_captured(
                ("git", "-C", target, "rev-parse", "HEAD"),
                env=sanitized_child_environment(),
                timeout=10,
                text=True,
            )
        else:
            branch_proc = cast("ProcessResult", context.process("instance.foreground.git.branch"))
            sha_proc = cast("ProcessResult", context.process("instance.foreground.git.commit"))
    except (subprocess.SubprocessError, OSError):
        return "unknown", ""
    branch_output = branch_proc.stdout if isinstance(branch_proc.stdout, str) else ""
    sha_output = sha_proc.stdout if isinstance(sha_proc.stdout, str) else ""
    branch = branch_output.strip() if branch_proc.returncode == 0 else "unknown"
    sha = sha_output.strip() if sha_proc.returncode == 0 else ""
    return branch or "unknown", sha


def _validate_runtime_args(args: Sequence[str]) -> tuple[str, ...]:
    captured = tuple(args)
    for token in captured:
        if token.startswith("--"):
            option = token.split("=", 1)[0]
            long_name = option[2:]
            if long_name:
                for protected in _PROTECTED_RUNTIME_OPTIONS:
                    if protected.startswith("--") and (
                        option == protected or protected.startswith(option)
                    ):
                        raise InstanceConfigurationError(
                            f"runtime argument override {option!r} is forbidden; "
                            "managed environment binding cannot be changed"
                        )
        else:
            for protected in _PROTECTED_RUNTIME_OPTIONS:
                if not protected.startswith("--") and (
                    token == protected or token.startswith(protected)
                ):
                    raise InstanceConfigurationError(
                        f"runtime argument override {protected!r} is forbidden; "
                        "managed environment binding cannot be changed"
                    )
    return captured


def resolve_runtime_argv(
    start_config: StartConfig,
    default_run_args: Sequence[str],
    extra_args: Sequence[str] = (),
) -> tuple[str, ...]:
    """Return the resolved non-executable runtime argv shared by ``run`` and projections.

    Produces the same managed CLI arguments plus validated extra arguments that
    ``OdooInstance.run_foreground_command`` spawns.  ``default_run_args`` from the
    owning project are validated against the disallowed managed-override families
    and appended after the managed arguments; an empty ``default_run_args`` adds
    nothing.  Callers that need secret-config handling for an actual spawn keep
    using ``_snapshot_start_inputs``; this function is the argv source for
    non-spawning projections such as the VS Code launch profile.
    """
    managed_args = tuple(_build_cli_args(start_config))
    validated_extra = resolve_runtime_argv_extra(default_run_args, extra_args)
    return (*managed_args, *validated_extra)


def resolve_runtime_argv_extra(
    default_run_args: Sequence[str],
    extra_args: Sequence[str] = (),
) -> tuple[str, ...]:
    """Validate and return the extra runtime arguments appended after managed args.

    This is the shared validation seam used by both the foreground launch path
    and non-spawning projections so ``default_run_args`` flow through one
    disallowed-override check instead of being duplicated.
    """
    return _validate_runtime_args((*default_run_args, *extra_args))


def _command_plan(
    steps: tuple[PreparedStep | PreparedAction, ...],
    *,
    secrets: Sequence[str] = (),
    observations: Sequence[PlanObservation] = (),
) -> ExecutionPlan:
    from odoo_instance_sdk.execution import ExecutionPlan

    plan = ExecutionPlan(
        steps=tuple(step.public_projection() for step in steps),
        observations=tuple(observations),
    )
    return plan.with_fingerprint(secrets=secrets)


def _http_port_observation(
    config: StartConfig,
    *,
    environment_id: str | None = None,
    client: OdooClient | None = None,
) -> SemanticPlanObservation:
    """Capture the bounded, read-only HTTP binding check for a plan."""
    from odoo_instance_sdk.execution import PlanPrecondition, SemanticPlanObservation
    from odoo_instance_sdk.internal.address import AddressState, probe_address

    endpoint = f"{config.http_interface}:{config.http_port}"
    try:
        state = probe_address(config.http_interface, config.http_port)
    except OSError as error:
        precondition = PlanPrecondition(
            name="http-port-free",
            status="unknown",
            detail=f"unable to inspect {endpoint}: {error}",
        )
    else:
        if state is AddressState.FREE:
            precondition = PlanPrecondition(
                name="http-port-free",
                status="passed",
                detail=f"{endpoint} is available",
            )
        elif environment_id is not None and client is not None:
            from odoo_instance_sdk.internal.context import _persisted_environment_runtime_owner

            owner = _persisted_environment_runtime_owner(client, environment_id, config.http_port)
            if owner is not None:
                precondition = PlanPrecondition(
                    name="http-port-free",
                    status="passed",
                    detail=f"{endpoint} is occupied by persisted environment runtime (pid={owner})",
                )
            else:
                precondition = PlanPrecondition(
                    name="http-port-free",
                    status="failed",
                    detail=f"{endpoint} is occupied (ownership unknown)",
                )
        else:
            precondition = PlanPrecondition(
                name="http-port-free",
                status="failed",
                detail=f"{endpoint} is occupied (ownership unknown)",
            )
    return SemanticPlanObservation(
        kind="semantic",
        goal="Start Odoo in the foreground",
        targets=(f"http://{config.http_interface}:{config.http_port}",),
        mutations=("spawn the Odoo foreground process",),
        preconditions=(precondition,),
        warnings=(),
    )


def _assert_http_port_free(config: StartConfig) -> None:
    from odoo_instance_sdk.internal.address import AddressState, probe_address

    try:
        state = probe_address(config.http_interface, config.http_port)
    except OSError as error:
        raise InstanceConfigurationError(
            f"cannot verify HTTP port {config.http_interface}:{config.http_port}: {error}"
        ) from error
    if state is not AddressState.FREE:
        raise InstanceConfigurationError(
            f"port-conflict: {config.http_interface}:{config.http_port} is occupied "
            "(ownership unknown)"
        )


def _command_result(
    result: ProcessResult,
    timeout: float | None,
    step: PreparedStep | None = None,
) -> CommandResult:
    stdout = (
        result.stdout.decode(errors="replace")
        if isinstance(result.stdout, bytes)
        else result.stdout
    )
    stderr = (
        result.stderr.decode(errors="replace")
        if isinstance(result.stderr, bytes)
        else result.stderr
    )
    if step is not None:
        from odoo_instance_sdk.internal.proc.redaction import (
            captured_secret_values,
            redacted_projection,
        )

        public_step = step.public_projection()
        args = list(public_step.argv)
        environment = public_step.environment_overrides
        secrets = captured_secret_values(step)
        stdout = cast("str", redacted_projection(stdout or "", secrets=secrets, field="stdout"))
        stderr = cast("str", redacted_projection(stderr or "", secrets=secrets, field="stderr"))
    else:
        from odoo_instance_sdk.internal.proc.redaction import redacted_argv

        args = list(redacted_argv(result.argv))
        environment = ()
    return CommandResult(
        args=args,
        returncode=result.returncode,
        stdout=stdout or "",
        stderr=stderr or "",
        duration=result.duration,
        cwd=result.cwd,
        environment=environment,
        timeout=timeout,
    )


def _snapshot_start_inputs(
    config: StartConfig,
    *,
    secret_config_path: str | None = None,
) -> tuple[StartConfig, tuple[str, ...], str | None, tuple[str, ...]]:
    snapshot = copy.deepcopy(config)
    secret_path = secret_config_path
    if snapshot.config_path is None and snapshot.db_password is not None:
        secret_path = secret_path or str(
            Path(tempfile.gettempdir()) / f"odoo-sdk-{uuid.uuid4().hex}.conf"
        )
    elif snapshot.config_path is not None:
        secret_path = None
    args = tuple(
        _build_cli_args(snapshot)
        if secret_path is None
        else _build_cli_args(snapshot, secret_config_path=secret_path)
    )
    secrets = tuple(value for value in (snapshot.db_password, secret_path) if value is not None)
    return snapshot, args, secret_path, secrets


def _child_secret_values(
    project_environment: Mapping[str, str], overrides: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    values = list(project_environment_secret_values(project_environment))
    for key, value in (overrides or {}).items():
        if key == MASTER_PASSWORD_KEY and value:
            values.append(value)
    return tuple(dict.fromkeys(values))


def _build_shell_script_step(
    config: StartConfig,
    *,
    executable_prefix: Sequence[str],
    default_cwd: Path | None,
    source: str,
    argv: Sequence[str] = (),
    timeout: float | None = None,
    commit: bool = False,
    nonce: str | None = None,
    secret_config_path: str | None = None,
    project_environment: Mapping[str, str] | None = None,
) -> tuple[PreparedStep, StartConfig, str | None, tuple[str, ...]]:
    """Capture one shell script's complete private process input."""
    snapshot, cli_args, secret_path, secrets = _snapshot_start_inputs(
        config, secret_config_path=secret_config_path
    )
    secrets = (*secrets, source, *_child_secret_values(project_environment or {}))
    from odoo_instance_sdk.internal.server import _build_shell_wrapper

    wrapper_nonce = nonce or uuid.uuid4().hex
    wrapper = _build_shell_wrapper(source, list(argv), commit=commit, nonce=wrapper_nonce)
    environment_snapshot, environment_overrides = captured_child_environment(
        None, project_environment=project_environment
    )
    step = PreparedStep(
        step_id="instance.shell_script",
        argv=(*executable_prefix, "shell", *cli_args),
        cwd=None if default_cwd is None else str(default_cwd),
        environment=environment_overrides,
        environment_snapshot=environment_snapshot,
        environment_overrides=environment_overrides,
        stdin=wrapper.encode(),
        wrapper_nonce=wrapper_nonce,
        secret_config_path=secret_path,
        timeout=timeout,
        secret_values=secrets,
        read_only=not commit,
        mutating=commit,
    )
    return step, snapshot, secret_path, secrets


def _runtime_owner(binding: _RuntimeBinding | None, environment_id: str | None) -> tuple[str, str]:
    if binding is not None:
        return binding.owner_kind, binding.owner_id
    return "environment", environment_id or ""


def _auxiliary_start_step(instance: OdooInstance) -> tuple[PreparedStep, StartConfig, str | None]:
    """Capture the exact private launch inputs for a restore helper."""
    config = instance.config.start_config
    if config is None:
        raise InstanceConfigurationError(
            "stopped-project restore requires a project StartConfig; run `odcli init`"
        )
    config = copy.deepcopy(config)
    cluster = instance._postgres_cluster
    if cluster is not None and cluster.owned:
        from odoo_instance_sdk.internal.dbprep.bootstrap import BOOTSTRAP_DATABASE

        config.db_name = BOOTSTRAP_DATABASE
        config.dbfilter = BOOTSTRAP_DATABASE
    snapshot, cli_args, secret_path, _ = _snapshot_start_inputs(config)
    environment_snapshot, environment_overrides = captured_child_environment(
        None, project_environment=instance.config.project_environment
    )
    step = PreparedStep(
        step_id="database.restore.auxiliary.start",
        argv=(*instance._executable_prefix(), *cli_args),
        cwd=None if instance.config.default_cwd is None else str(instance.config.default_cwd),
        environment=environment_overrides,
        environment_snapshot=environment_snapshot,
        environment_overrides=environment_overrides,
        secret_config_path=secret_path,
        secret_values=tuple(value for value in (snapshot.db_password, secret_path) if value),
        mode="long-running",
        mutating=True,
        long_running=True,
        start_new_session=True,
        inherit_stdio=True,
    )
    return step, snapshot, secret_path


def auxiliary_restore_session(instance: OdooInstance) -> AuxiliaryRestoreSession:
    step, secret_config, secret_path = _auxiliary_start_step(instance)
    return AuxiliaryRestoreSession(
        instance=instance,
        start_step=step,
        probe_action=PreparedAction(
            step_id="database.restore.auxiliary.probe",
            action="verify-auxiliary-database-manager",
            description="Verify the recorded auxiliary Database Manager identity",
            read_only=True,
        ),
        port_action=PreparedAction(
            step_id="database.restore.auxiliary.port",
            action="verify-auxiliary-port-ownership",
            description="Verify the auxiliary endpoint is free or exactly owned",
            read_only=True,
        ),
        ready_action=PreparedAction(
            step_id="database.restore.auxiliary.ready",
            action="wait-auxiliary-database-manager",
            description="Wait for the owned auxiliary Database Manager",
            read_only=True,
        ),
        cleanup_action=PreparedAction(
            step_id="database.restore.auxiliary.cleanup",
            action="stop-auxiliary-database-manager",
            description="Stop the owned auxiliary Database Manager",
            mutating=True,
        ),
        backup_request_action=PreparedAction(
            step_id="database.restore.auxiliary.backup-revalidate",
            action="revalidate-auxiliary-backup-identity",
            description="Revalidate the auxiliary identity before sending master_pwd for backup",
            read_only=True,
        ),
        restore_request_action=PreparedAction(
            step_id="database.restore.auxiliary.restore-revalidate",
            action="revalidate-auxiliary-restore-identity",
            description="Revalidate the auxiliary identity before sending master_pwd for restore",
            read_only=True,
        ),
        secret_config=secret_config,
        secret_path=secret_path,
    )


@dataclass(slots=True)
class AuxiliaryRestoreSession:
    """Own one bounded Odoo process used only by a stopped-project restore."""

    instance: OdooInstance
    start_step: PreparedStep
    probe_action: PreparedAction
    port_action: PreparedAction
    ready_action: PreparedAction
    cleanup_action: PreparedAction
    backup_request_action: PreparedAction
    restore_request_action: PreparedAction
    secret_config: StartConfig | None = None
    secret_path: str | None = None
    process: OdooProcess | None = None
    process_handle: ProcessHandle | None = None
    using_existing_runtime: bool = False

    def _probe_recorded_runtime(
        self, context: RunContext[_ContextResult], config: StartConfig
    ) -> int | None:
        if not context.planned(self.probe_action.step_id) or context.consumed(
            self.probe_action.step_id
        ):
            return _recorded_runtime_pid(self.instance, config)
        context.action(self.probe_action.step_id)
        try:
            recorded_pid = _recorded_runtime_pid(self.instance, config)
        except BaseException as error:
            context.fail_action(self.probe_action.step_id, error)
            raise
        context.complete_action(self.probe_action.step_id)
        return recorded_pid

    def _port_precondition(self, context: RunContext[_ContextResult], config: StartConfig) -> None:
        if not context.planned(self.port_action.step_id) or context.consumed(
            self.port_action.step_id
        ):
            _assert_http_port_free(config)
            return
        context.action(self.port_action.step_id)
        try:
            _assert_http_port_free(config)
        except BaseException as error:
            context.fail_action(self.port_action.step_id, error)
            raise
        context.complete_action(self.port_action.step_id)

    def _wait_for_recorded_runtime(
        self, context: RunContext[_ContextResult], recorded_pid: int
    ) -> None:
        from odoo_instance_sdk.internal.health import poll_health

        context.action(self.ready_action.step_id)
        try:
            poll_health(
                self.instance.config.base_url,
                timeout=60.0,
                alive_check=lambda: _process_alive(recorded_pid),
                database_manager=True,
            )
        except BaseException as error:
            context.fail_action(self.ready_action.step_id, error)
            from odoo_instance_sdk.exceptions import DatabaseManagerUnavailableError

            raise DatabaseManagerUnavailableError(
                "recorded auxiliary database manager failed readiness; retry after resolving "
                "the project runtime"
            ) from error
        context.complete_action(self.ready_action.step_id)

    def _cleanup_failed_start(self, handle: ProcessHandle | None, error: BaseException) -> None:
        if self.process is not None:
            # Readiness failure happens after registration.  Unregister and
            # terminate the owned process here so a timeout cannot leave a
            # listener/process behind for the next leaf.
            owned, registered_secret_path = self.instance._client.unregister_process(
                self.process.id
            )
            self.process = None
            if owned is not None:
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
                        timeout=10.0,
                    )
                except BaseException as cleanup_error:
                    error.add_note(f"auxiliary process cleanup failed: {cleanup_error}")
            try:
                cleanup_secret_config(registered_secret_path or self.secret_path)
            except BaseException as cleanup_error:
                error.add_note(f"auxiliary secret cleanup failed: {cleanup_error}")
            return
        if handle is not None:
            with contextlib.suppress(BaseException):
                terminate(handle, process_group_id=handle.process_group_id, timeout=10.0)
        try:
            cleanup_secret_config(self.secret_path)
        except BaseException as cleanup_error:
            error.add_note(f"auxiliary secret cleanup failed: {cleanup_error}")

    def ensure_started(self, context: RunContext[_ContextResult]) -> None:  # noqa: C901
        if self.process is not None:
            return
        config = self.instance.config.start_config
        if config is None:
            raise InstanceConfigurationError(
                "stopped-project restore has no auxiliary Odoo configuration; "
                "run `odcli init` and retry"
            )
        if self.using_existing_runtime:
            if _recorded_runtime_pid(self.instance, config) is None:
                from odoo_instance_sdk.exceptions import DatabaseManagerUnavailableError

                raise DatabaseManagerUnavailableError(
                    "recorded auxiliary database manager identity is no longer proven"
                )
            return
        recorded_pid = self._probe_recorded_runtime(context, config)
        if recorded_pid is not None:
            self.using_existing_runtime = True
            if context.planned(self.port_action.step_id) and not context.consumed(
                self.port_action.step_id
            ):
                context.skip(self.port_action.step_id)
            self._wait_for_recorded_runtime(context, recorded_pid)
            return
        self._port_precondition(context, config)
        if self.secret_config is not None and self.secret_path is not None:
            _write_secret_config(self.secret_config, self.secret_path)
        handle: ProcessHandle | None = None
        try:
            handle = context.spawn(self.start_step.step_id)
            process = OdooProcess(
                id=uuid.uuid4().hex,
                pid=handle.pid,
                args=list(self.start_step.argv),
                started_at=time.time(),
            )
            self.instance._client.register_process(process, handle.process, self.secret_path)
            self.process = process
            self.process_handle = handle
            context.action(self.ready_action.step_id)
            try:
                self.instance.wait_ready(process, timeout=60.0, database_manager=True)
            except BaseException as error:
                context.fail_action(self.ready_action.step_id, error)
                from odoo_instance_sdk.exceptions import DatabaseManagerUnavailableError

                tails = handle.drain_tails() if handle is not None else {"stdout": "", "stderr": ""}
                raise DatabaseManagerUnavailableError(
                    "auxiliary database manager failed readiness; "
                    "resolve the project runtime and retry, or run `odcli run`\n"
                    f"stdout_tail={tails['stdout']!r}\n"
                    f"stderr_tail={tails['stderr']!r}"
                ) from error
            context.complete_action(self.ready_action.step_id)
        except BaseException as error:
            self._cleanup_failed_start(handle, error)
            if handle is None:
                from odoo_instance_sdk.exceptions import DatabaseManagerUnavailableError

                raise DatabaseManagerUnavailableError(
                    "auxiliary database manager failed to start; "
                    "resolve the project runtime and retry, or run `odcli run`"
                ) from error
            raise

    def _skip_unconsumed_steps(self, context: RunContext[_ContextResult]) -> None:
        for step_id in (
            self.probe_action.step_id,
            self.port_action.step_id,
            self.start_step.step_id,
            self.ready_action.step_id,
            self.backup_request_action.step_id,
            self.restore_request_action.step_id,
            self.cleanup_action.step_id,
        ):
            if context.planned(step_id) and not context.consumed(step_id):
                context.skip(step_id)

    def cleanup(self, context: RunContext[_ContextResult]) -> None:
        if self.using_existing_runtime:
            self._skip_unconsumed_steps(context)
            return
        if self.process is None:
            self._skip_unconsumed_steps(context)
            cleanup_secret_config(self.secret_path)
            return
        if context.planned(self.cleanup_action.step_id) and not context.consumed(
            self.cleanup_action.step_id
        ):
            context.action(self.cleanup_action.step_id)
        secret_path = self.secret_path
        try:
            owned, registered_secret_path = self.instance._client.unregister_process(
                self.process.id
            )
            secret_path = registered_secret_path or secret_path
            if owned is not None:
                terminate(
                    ProcessHandle(
                        process=owned,
                        argv=(),
                        process_group_id=owned.pid,
                        session_id=owned.pid,
                        inherited_stdio=False,
                    ),
                    process_group_id=owned.pid,
                    timeout=10.0,
                )
        finally:
            try:
                cleanup_secret_config(secret_path)
            except BaseException:
                if sys.exc_info()[1] is None:
                    raise
        self._skip_unconsumed_steps(context)
        if context.planned(self.cleanup_action.step_id) and context.consumed(
            self.cleanup_action.step_id
        ):
            context.complete_action(self.cleanup_action.step_id)

    def _owned_runtime_proven(self, config: StartConfig) -> bool:
        if self.process is None or self.process_handle is None:
            return False
        if self.process.pid != self.process_handle.pid:
            return False
        try:
            if self.process_handle.poll() is not None:
                return False
        except (OSError, ValueError):
            return False
        return _socket_owned_by(config, self.process.pid)

    def owns_instance(self, instance: OdooInstance) -> bool:
        if instance is self.instance:
            return True
        expected = self.instance.config.start_config
        actual = instance.config.start_config
        if expected is None or actual is None:
            return False
        if expected.config_path is None or actual.config_path is None:
            return False
        return _canonical_runtime_path(expected.config_path) == _canonical_runtime_path(
            actual.config_path
        ) and instance.config.base_url.rstrip("/") == self.instance.config.base_url.rstrip("/")

    def authorize_request(
        self,
        context: RunContext[_ContextResult],
        *,
        instance: OdooInstance,
        request_action: PreparedAction,
    ) -> None:
        """Revalidate this session immediately before a password-bearing request."""
        if not self.owns_instance(instance):
            return
        if context.planned(request_action.step_id):
            context.action(request_action.step_id)
        try:
            config = self.instance.config.start_config
            proven = False
            if config is not None:
                if self.using_existing_runtime:
                    proven = _recorded_runtime_pid(self.instance, config) is not None
                else:
                    proven = self._owned_runtime_proven(config)
            if not proven:
                from odoo_instance_sdk.exceptions import DatabaseManagerUnavailableError

                raise DatabaseManagerUnavailableError(  # noqa: TRY301
                    "auxiliary database manager identity is no longer proven"
                )
        except BaseException as error:
            if context.planned(request_action.step_id):
                context.fail_action(request_action.step_id, error)
            raise
        if context.planned(request_action.step_id):
            context.complete_action(request_action.step_id)


_ACTIVE_AUXILIARY_RESTORE: ContextVar[AuxiliaryRestoreSession | None] = ContextVar(
    "odcli_auxiliary_restore", default=None
)


def _resolve_project_python(root: Path, value: str | Path | None) -> Path:
    from odoo_instance_sdk.internal.project_runtime import resolve_project_runtime

    return resolve_project_runtime(root, value, field="python")


def active_auxiliary_restore_session() -> AuxiliaryRestoreSession | None:
    return _ACTIVE_AUXILIARY_RESTORE.get()


def activate_auxiliary_restore_session(
    session: AuxiliaryRestoreSession,
) -> Token[AuxiliaryRestoreSession | None]:
    return _ACTIVE_AUXILIARY_RESTORE.set(session)


def reset_auxiliary_restore_session(token: Token[AuxiliaryRestoreSession | None]) -> None:
    _ACTIVE_AUXILIARY_RESTORE.reset(token)


def _process_alive(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except (OSError, psutil.Error):
        return False


def _attach_auxiliary_restore_runtime(
    command: _InspectableCommand[_CommandResult],
    session: AuxiliaryRestoreSession,
    *,
    before_step_id: str = "database.prepare.local-restore",
) -> _InspectableCommand[_CommandResult]:
    """Attach one auxiliary session around a captured command at an explicit anchor."""
    from odoo_instance_sdk.execution import Command, ExecutionPlan
    from odoo_instance_sdk.internal.proc import prepared_command

    if not isinstance(command, Command) or not isinstance(session, AuxiliaryRestoreSession):
        return command
    prepared_command_value = cast("Command[_CommandResult]", command)
    prepared = prepared_command_value._prepared()
    auxiliary_steps = (
        session.probe_action,
        session.port_action,
        session.start_step,
        session.ready_action,
    )
    anchor_index = next(
        (index for index, step in enumerate(prepared.steps) if step.step_id == before_step_id),
        len(prepared.steps),
    )
    request_actions = (
        (session.restore_request_action,)
        if before_step_id == "database.prepare.local-restore"
        else (session.backup_request_action, session.restore_request_action)
    )
    prepared_steps = (
        *prepared.steps[:anchor_index],
        *auxiliary_steps,
        *(request_actions if before_step_id != "checkout.catalog" else ()),
        *prepared.steps[anchor_index:],
        session.cleanup_action,
    )
    if before_step_id == "checkout.catalog":
        database_index = next(
            (
                index
                for index, step in enumerate(prepared_steps)
                if step.step_id == "checkout.database"
            ),
            len(prepared_steps) - 1,
        )
        prepared_steps = (
            *prepared_steps[:database_index],
            *request_actions,
            *prepared_steps[database_index:],
        )

    def execute(context: RunContext[_CommandResult]) -> _CommandResult:
        token = activate_auxiliary_restore_session(session)
        primary_error: BaseException | None = None
        try:
            try:
                return prepared.callback(context)
            except BaseException as error:
                primary_error = error
                raise
        finally:
            try:
                session.cleanup(context)
            except BaseException as cleanup_error:
                if primary_error is None:
                    raise
                primary_error.add_note(f"auxiliary cleanup failed: {cleanup_error}")
            finally:
                reset_auxiliary_restore_session(token)

    plan = ExecutionPlan(
        steps=tuple(step.public_projection() for step in prepared_steps),
        observations=command.plan.observations,
        warnings=command.plan.warnings,
    ).with_fingerprint()
    return cast(
        "_InspectableCommand[_CommandResult]",
        Command.from_prepared(
            plan,
            prepared_command(
                execute,
                prepared_steps,
                executor=prepared.executor,
                private_projection=prepared.private_projection,
            ),
        ),
    )


def _project_runtime_binding(
    root: Path, project: ProjectConfig, odoo_bin: Path
) -> tuple[Path | None, DeferredProjectRuntime | None]:
    from odoo_instance_sdk.internal.project_runtime import defer_project_runtime

    deferred = defer_project_runtime(root, project.python, field="python", odoo_bin=odoo_bin)
    if deferred is not None:
        return None, deferred
    return _resolve_project_python(root, project.python), None
