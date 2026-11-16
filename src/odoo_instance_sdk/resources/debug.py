"""Public headless debug commands for one ready environment."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypeVar, cast

import msgspec
import psutil

from odoo_instance_sdk.exceptions import DebugError
from odoo_instance_sdk.execution import Command, ExecutionPlan, JsonValue
from odoo_instance_sdk.internal.dbprep.bootstrap import (
    ensure_project_bootstrap_tmp,
    project_bootstrap_tmp_plan,
)
from odoo_instance_sdk.internal.debug.breakpoints import (
    breakpoint_identity,
    parse_breakpoint,
    parse_breakpoints,
)
from odoo_instance_sdk.internal.debug.limits import PROTOCOL_VERSION, STARTUP_TIMEOUT_SECONDS
from odoo_instance_sdk.internal.debug.preflight import assert_debug_runtime
from odoo_instance_sdk.internal.debug.session import (
    claim_session,
    read_metadata,
    release_session,
    rpc_call,
    session_directory,
    session_is_live,
    write_metadata,
    write_session_file,
)
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    ProcessExecutor,
    ProcessResult,
    RunContext,
    SubprocessExecutor,
)
from odoo_instance_sdk.internal.proc.run import ProcessHandle
from odoo_instance_sdk.internal.server import _write_secret_config, cleanup_secret_config
from odoo_instance_sdk.models import DevelopmentEnvironment
from odoo_instance_sdk.models.debug import DebugSnapshot, DebugValue
from odoo_instance_sdk.resources.environment import EnvironmentResource
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.instance.auxiliary_restore import (
    _snapshot_start_inputs,
    resolve_runtime_argv_extra,
)

DebugModel = TypeVar("DebugModel", DebugSnapshot, DebugValue)


def debug_target_argv(
    prefix: tuple[str, ...], runtime_args: tuple[str, ...], *, log_dir: Path
) -> tuple[str, ...]:
    """Capture the debugpy command the daemon launches unchanged."""
    if len(prefix) < 2:
        raise DebugError(
            "environment-required",
            "debug requires the recorded Python and Odoo entry point",
        )
    return (
        prefix[0],
        "-m",
        "debugpy",
        "--log-to",
        str(log_dir),
        "--listen",
        "127.0.0.1:0",
        "--wait-for-client",
        *prefix[1:],
        *runtime_args,
    )


class DebugResource:
    """One debug session bound to a ready environment."""

    def __init__(self, instance: OdooInstance, environment: DevelopmentEnvironment) -> None:
        self._instance = instance
        self._environment = environment

    def start(
        self, *, breakpoints: Sequence[str] = (), extra_args: Sequence[str] = ()
    ) -> DebugSnapshot:
        """Start one headless session and return its first snapshot."""
        return self.start_command(breakpoints=breakpoints, extra_args=extra_args).run()

    def start_command(
        self,
        *,
        breakpoints: Sequence[str] = (),
        extra_args: Sequence[str] = (),
        executor: ProcessExecutor | None = None,
    ) -> Command[DebugSnapshot]:
        """Capture the immutable start plan, including the debugpy target argv."""
        instance = self._instance
        config = instance.config.start_config
        if config is None:
            raise DebugError("environment-required", "debug requires a ready environment")
        cwd = Path(instance.config.default_cwd or self._environment.worktree_path)
        snapshot_config, cli_args, secret_path, secrets = _snapshot_start_inputs(config)
        runtime_args = (
            *cli_args,
            *resolve_runtime_argv_extra(instance.config.default_run_args, extra_args),
        )
        prefix = instance._executable_prefix()
        directory = session_directory(str(self._environment.id))
        target_argv = debug_target_argv(prefix, runtime_args, log_dir=directory / "debugpy-log")
        dependency_steps, dependency_temporary = instance._dependency_manifest()
        bootstrap_plan = project_bootstrap_tmp_plan(instance)
        bootstrap_steps = bootstrap_plan.execution_steps if bootstrap_plan is not None else ()
        steps: tuple[PreparedStep | PreparedAction, ...] = (
            *dependency_steps,
            *bootstrap_steps,
            PreparedAction(
                step_id="debug.preflight",
                action="debug.preflight",
                description="Check workers, reload, and the HTTP port",
                read_only=True,
            ),
            PreparedStep(
                step_id="debug.debugpy-check",
                argv=(prefix[0], "-c", "import debugpy"),
                cwd=str(cwd),
                timeout=30,
                read_only=True,
            ),
            PreparedAction(
                step_id="debug.record-use",
                action="debug.record-use",
                description="Record environment use",
                mutating=True,
            ),
            PreparedAction(
                step_id="debug.claim",
                action="debug.claim",
                description="Claim the environment debug session",
                mutating=True,
            ),
            PreparedStep(
                step_id="debug.daemon",
                argv=(
                    sys.executable,
                    "-m",
                    "odoo_instance_sdk.internal.debug.serve",
                    str(directory),
                ),
                cwd=str(cwd),
                long_running=True,
                start_new_session=True,
                mutating=True,
            ),
            PreparedStep(
                step_id="debug.target",
                argv=target_argv,
                cwd=str(cwd),
                secret_values=secrets,
                long_running=True,
                start_new_session=True,
                mutating=True,
            ),
        )
        environment = self._environment
        session_id = f"dbg_{uuid.uuid4().hex}"
        initial = tuple(breakpoints)

        def execute(context: RunContext[DebugSnapshot]) -> DebugSnapshot:
            instance._ensure_dependencies_ready(
                context,
                dependency_steps=dependency_steps,
                temporary_path=dependency_temporary,
            )
            ensure_project_bootstrap_tmp(instance, context, plan=bootstrap_plan)
            _skip_unconsumed(context, dependency_steps)
            context.action("debug.preflight")
            assert_debug_runtime(config, runtime_args)
            parse_breakpoints(initial, cwd)
            context.complete_action("debug.preflight")
            checked = context.process("debug.debugpy-check")
            if not isinstance(checked, ProcessResult) or checked.returncode != 0:
                context.skip("debug.record-use")
                context.skip("debug.claim")
                context.skip("debug.daemon")
                context.skip("debug.target")
                raise DebugError(
                    "debugpy-unavailable",
                    "the recorded environment does not provide debugpy",
                    hint="Install debugpy in the environment Python",
                )
            context.action("debug.record-use")
            EnvironmentResource(_client=instance._client).record_use(environment)
            context.complete_action("debug.record-use")
            with instance._artifact_lock():
                context.action("debug.claim")
                if session_is_live(directory):
                    active = _active_snapshot(directory)
                    context.skip("debug.daemon")
                    context.skip("debug.target")
                    context.complete_action("debug.claim")
                    raise DebugError(
                        "session-already-active",
                        "a debug session is already running for this environment",
                        hint="Use debug context, continue, or stop",
                        snapshot=active,
                    )
                claim_session(str(environment.id))
                log_dir = directory / "debugpy-log"
                log_dir.mkdir(mode=0o700, exist_ok=True)
                os.chmod(log_dir, 0o700)
                if secret_path is not None:
                    _write_secret_config(snapshot_config, secret_path)
                captured = context.prepared("debug.target").argv
                write_session_file(
                    directory / "manifest.json",
                    _manifest(
                        session_id=session_id,
                        environment_id=str(environment.id),
                        cwd=cwd,
                        http_interface=config.http_interface,
                        http_port=config.http_port,
                        argv=captured,
                        breakpoints=_breakpoint_rows(initial, cwd, temporary=False),
                        secret_path=secret_path,
                    ),
                )
                context.complete_action("debug.claim")
                handle = context.spawn("debug.daemon")
                _write_daemon_metadata(directory, handle, session_id, str(environment.id))
            context.skip("debug.target")
            try:
                return _wait_until_ready(directory, handle, str(environment.id))
            except DebugError:
                if secret_path is not None:
                    cleanup_secret_config(secret_path)
                raise

        return Command.create(
            ExecutionPlan(steps=tuple(step.public_projection() for step in steps)).with_fingerprint(
                secrets=secrets
            ),
            execute,
            steps,
            executor=executor or SubprocessExecutor(),
        )

    def context(self, *, wait: float | None = None) -> DebugSnapshot:
        """Return the current snapshot, optionally waiting for the next stop."""
        return self.context_command(wait=wait).run()

    def context_command(self, *, wait: float | None = None) -> Command[DebugSnapshot]:
        """Capture a read-only context request."""
        return self._rpc_command(
            "context",
            {"wait": wait is not None, "timeout": wait or 30},
            model=DebugSnapshot,
            step_id="debug.context",
            timeout=_rpc_timeout(wait),
        )

    def continue_session(
        self,
        *,
        wait: float | None = None,
        breakpoints: Sequence[str] = (),
        remove: Sequence[str] = (),
        clear: bool = False,
        temporary: str | None = None,
        trigger: Sequence[str] = (),
    ) -> DebugSnapshot:
        """Resume or edit breakpoints and return the resulting snapshot."""
        return self.continue_session_command(
            wait=wait,
            breakpoints=breakpoints,
            remove=remove,
            clear=clear,
            temporary=temporary,
            trigger=trigger,
        ).run()

    def continue_session_command(
        self,
        *,
        wait: float | None = None,
        breakpoints: Sequence[str] = (),
        remove: Sequence[str] = (),
        clear: bool = False,
        temporary: str | None = None,
        trigger: Sequence[str] = (),
    ) -> Command[DebugSnapshot]:
        """Capture one continue request, including a skipped trigger process."""
        params = {
            "wait": wait is not None,
            "timeout": wait or 30,
            **_edit_params(self._cwd(), breakpoints, remove, clear, temporary),
        }
        return self._rpc_command(
            "continue",
            params,
            model=DebugSnapshot,
            step_id="debug.continue",
            timeout=_rpc_timeout(wait),
            trigger=tuple(trigger),
        )

    def step(self, kind: str, *, timeout: float = 30) -> DebugSnapshot:
        """Step one stopped frame and wait for the next stop."""
        return self.step_command(kind, timeout=timeout).run()

    def step_command(self, kind: str, *, timeout: float = 30) -> Command[DebugSnapshot]:
        """Capture one step request."""
        return self._rpc_command(
            "step",
            {"kind": kind, "timeout": timeout},
            model=DebugSnapshot,
            step_id="debug.step",
            timeout=_rpc_timeout(timeout),
        )

    def pause(self, *, timeout: float = 30) -> DebugSnapshot:
        """Request a pause and wait for the stopped snapshot."""
        return self.pause_command(timeout=timeout).run()

    def pause_command(self, *, timeout: float = 30) -> Command[DebugSnapshot]:
        """Capture one pause request."""
        return self._rpc_command(
            "pause",
            {"timeout": timeout},
            model=DebugSnapshot,
            step_id="debug.pause",
            timeout=_rpc_timeout(timeout),
        )

    def eval(self, expression: str, *, frame: int = 0, depth: int = 0) -> DebugValue:
        """Evaluate one expression in the stopped target."""
        return self.eval_command(expression, frame=frame, depth=depth).run()

    def eval_command(
        self, expression: str, *, frame: int = 0, depth: int = 0
    ) -> Command[DebugValue]:
        """Capture one evaluation request."""
        return self._rpc_command(
            "eval",
            {"expression": expression, "frame": frame, "depth": depth},
            model=DebugValue,
            step_id="debug.eval",
            timeout=30,
        )

    def restart(self) -> DebugSnapshot:
        """Relaunch the same target and keep the session breakpoints."""
        return self.restart_command().run()

    def restart_command(self) -> Command[DebugSnapshot]:
        """Capture one restart request."""
        return self._rpc_command(
            "restart",
            {},
            model=DebugSnapshot,
            step_id="debug.restart",
            timeout=STARTUP_TIMEOUT_SECONDS + 5,
        )

    def stop(self) -> DebugSnapshot:
        """Stop the session and remove its private files."""
        return self.stop_command().run()

    def stop_command(self) -> Command[DebugSnapshot]:
        """Capture one stop request."""
        return self._rpc_command(
            "stop",
            {},
            model=DebugSnapshot,
            step_id="debug.stop",
            timeout=30,
        )

    def _cwd(self) -> Path:
        configured = self._instance.config.default_cwd
        if configured is not None:
            return Path(configured)
        return Path(self._environment.worktree_path)

    def _rpc_command(
        self,
        method: str,
        params: Mapping[str, JsonValue],
        *,
        model: type[DebugModel],
        step_id: str,
        timeout: float,
        trigger: tuple[str, ...] = (),
        executor: ProcessExecutor | None = None,
    ) -> Command[DebugModel]:
        environment_id = str(self._environment.id)
        cwd = self._cwd()
        action = PreparedAction(
            step_id=step_id,
            action=step_id,
            description=f"Send debug RPC {method}",
            mutating=method != "context",
            read_only=method == "context",
        )
        steps: list[PreparedStep | PreparedAction] = [action]
        if trigger:
            steps.append(
                PreparedStep(
                    step_id="debug.trigger",
                    argv=trigger,
                    cwd=str(cwd),
                    long_running=True,
                    start_new_session=True,
                    mutating=True,
                )
            )
        captured_params = dict(params)

        def execute(context: RunContext[DebugModel]) -> DebugModel:
            context.action(step_id)
            request = dict(captured_params)
            if trigger:
                request["trigger"] = list(context.prepared("debug.trigger").argv)
                context.skip("debug.trigger")
            payload = rpc_call(
                _require_socket(environment_id),
                method,
                request,
                timeout=timeout,
            )
            context.complete_action(step_id)
            if not isinstance(payload, dict):
                raise DebugError("protocol-failure", "debug session returned a non-object")
            return msgspec.convert(payload, model)

        frozen = tuple(steps)
        return Command.create(
            ExecutionPlan(
                steps=tuple(step.public_projection() for step in frozen)
            ).with_fingerprint(),
            execute,
            frozen,
            executor=executor or SubprocessExecutor(),
        )


def _parse_many(texts: Sequence[str], cwd: Path) -> tuple[tuple[Path, int, str | None], ...]:
    return tuple(parse_breakpoint(text, cwd) for text in texts)


def _breakpoint_rows(
    texts: Sequence[str], cwd: Path, *, temporary: bool
) -> list[dict[str, JsonValue]]:
    return [
        {"path": str(path), "line": line, "condition": condition, "temporary": temporary}
        for path, line, condition in _parse_many(texts, cwd)
    ]


def _edit_params(
    cwd: Path,
    add: Sequence[str],
    remove: Sequence[str],
    clear: bool,
    temporary: str | None,
) -> dict[str, JsonValue]:
    rows = _breakpoint_rows(add, cwd, temporary=False)
    if temporary is not None:
        path, line, condition = parse_breakpoint(temporary, cwd)
        rows.append({"path": str(path), "line": line, "condition": condition, "temporary": True})
    removed = [
        {"path": str(path), "line": line}
        for path, line, _condition in (breakpoint_identity(text, cwd) for text in remove)
    ]
    return {
        "add": cast("JsonValue", rows),
        "remove": cast("JsonValue", removed),
        "clear": clear,
    }


def _manifest(
    *,
    session_id: str,
    environment_id: str,
    cwd: Path,
    http_interface: str,
    http_port: int,
    argv: tuple[str, ...],
    breakpoints: list[dict[str, JsonValue]],
    secret_path: str | None,
) -> dict[str, JsonValue]:
    return {
        "protocol": PROTOCOL_VERSION,
        "session_id": session_id,
        "environment_id": environment_id,
        "cwd": str(cwd),
        "http_interface": http_interface,
        "http_port": http_port,
        "target_argv": list(argv),
        "breakpoints": cast("JsonValue", breakpoints),
        "secret_path": secret_path,
    }


def _write_daemon_metadata(
    directory: Path, handle: ProcessHandle, session_id: str, environment_id: str
) -> None:
    started = float(psutil.Process(handle.pid).create_time())
    write_metadata(
        directory,
        {
            "protocol": PROTOCOL_VERSION,
            "session_id": session_id,
            "environment_id": environment_id,
            "pid": handle.pid,
            "pid_start": started,
            "socket": str(directory / "session.sock"),
        },
    )


def _wait_until_ready(directory: Path, handle: ProcessHandle, environment_id: str) -> DebugSnapshot:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS + 5
    ready = directory / "ready.json"
    error = directory / "error.json"
    while time.monotonic() < deadline:
        if error.is_file():
            raise _error_from_file(error, environment_id)
        if ready.is_file():
            payload = json.loads(ready.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise DebugError("protocol-failure", "debug daemon returned an invalid snapshot")
            return msgspec.convert(payload, DebugSnapshot)
        if handle.poll() is not None:
            release_session(environment_id)
            raise DebugError("protocol-failure", "debug daemon exited during startup")
        time.sleep(0.05)
    handle.terminate()
    release_session(environment_id)
    raise DebugError("protocol-failure", "debug daemon did not become ready")


def _error_from_file(path: Path, environment_id: str) -> DebugError:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    release_session(environment_id)
    if not isinstance(payload, dict):
        return DebugError("protocol-failure", "debug daemon failed")
    code = payload.get("code")
    message = payload.get("message")
    hint = payload.get("hint")
    state = payload.get("state")
    return DebugError(
        code if isinstance(code, str) else "protocol-failure",
        message if isinstance(message, str) else "debug daemon failed",
        hint=hint if isinstance(hint, str) else None,
        state=state if isinstance(state, str) else None,
    )


def _active_snapshot(directory: Path) -> DebugSnapshot | None:
    metadata = read_metadata(directory)
    if metadata is None or not isinstance(metadata.get("socket"), str):
        return None
    try:
        payload = rpc_call(
            Path(cast("str", metadata["socket"])),
            "context",
            {"wait": False, "timeout": 1},
            timeout=5,
        )
    except DebugError:
        return None
    if not isinstance(payload, dict):
        return None
    return msgspec.convert(payload, DebugSnapshot)


def _require_socket(environment_id: str) -> Path:
    directory = session_directory(environment_id)
    if not session_is_live(directory):
        raise DebugError(
            "session-not-found",
            "no debug session is running for this environment",
            hint="Run odcli debug start",
        )
    metadata = read_metadata(directory)
    raw = None if metadata is None else metadata.get("socket")
    if not isinstance(raw, str):
        raise DebugError("session-not-found", "debug session has no socket")
    return Path(raw)


def _rpc_timeout(wait: float | None) -> float:
    if wait is None:
        return 10
    return wait + 5


def _skip_unconsumed(
    context: RunContext[DebugSnapshot], steps: tuple[PreparedStep | PreparedAction, ...]
) -> None:
    for step in steps:
        if context.planned(step.step_id) and not context.consumed(step.step_id):
            context.skip(step.step_id)
