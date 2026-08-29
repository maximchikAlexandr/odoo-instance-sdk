"""The single boundary for SDK-owned child-process effects."""

from __future__ import annotations

import contextlib
import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import IO, cast

from odoo_instance_sdk.internal.process_env import sanitized_child_environment

from . import PreparedProcess, PreparedStep

_CLEANUP_TIMEOUT = 5.0


class ProcessExecutionError(RuntimeError):
    """Base class for failures at the process boundary."""

    def __init__(self, argv: tuple[str, ...], reason: str, *, duration: float) -> None:
        self.argv = argv
        self.duration = duration
        super().__init__(f"process {argv!r} failed to execute: {reason}")


class ProcessSpawnError(ProcessExecutionError):
    """The operating system could not spawn a captured process."""


class ProcessTimeoutError(ProcessExecutionError):
    """A captured process exceeded its bounded timeout."""

    def __init__(
        self,
        argv: tuple[str, ...],
        timeout: float,
        *,
        duration: float,
    ) -> None:
        self.timeout = timeout
        super().__init__(argv, f"timeout after {timeout}s", duration=duration)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Typed result of one process invocation."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str | bytes | None
    stderr: str | bytes | None
    duration: float
    cwd: str | None
    environment: tuple[tuple[str, str], ...]


@dataclass(slots=True)
class ProcessHandle:
    """Owned handle for an asynchronously spawned process."""

    process: subprocess.Popen[bytes]
    argv: tuple[str, ...]
    process_group_id: int | None
    session_id: int | None
    inherited_stdio: bool

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def stdin(self) -> IO[bytes] | None:
        return self.process.stdin

    @property
    def stdout(self) -> IO[bytes] | None:
        return self.process.stdout

    @property
    def stderr(self) -> IO[bytes] | None:
        return self.process.stderr

    def poll(self) -> int | None:
        return self.process.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self.process.wait(timeout=timeout)

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes | None, bytes | None]:
        return self.process.communicate(input=input, timeout=timeout)


def owned_handle(
    process: subprocess.Popen[bytes] | ProcessHandle,
    *,
    process_group_id: int | None = None,
) -> ProcessHandle:
    if isinstance(process, ProcessHandle):
        return process
    group_id = process_group_id
    if group_id is None and sys.platform != "win32":
        group_id = process.pid
    return ProcessHandle(
        process=process,
        argv=(),
        process_group_id=group_id,
        session_id=group_id,
        inherited_stdio=False,
    )


def _environment(
    overrides: tuple[tuple[str, str], ...], *, policy: str = "sanitized-inherit"
) -> dict[str, str]:
    if policy == "explicit":
        return sanitized_child_environment(dict(overrides))
    child = sanitized_child_environment()
    child.update(dict(overrides))
    return sanitized_child_environment(child)


class SubprocessExecutor:
    """Real executor for already-captured process steps."""

    def execute(self, step: PreparedProcess) -> ProcessResult:
        prepared = cast("PreparedStep", step)
        started = time.perf_counter()
        try:
            text_mode = prepared.text and prepared.stdin is None
            completed = subprocess.run(
                list(prepared.argv),
                cwd=prepared.cwd,
                env=_environment(prepared.environment, policy=prepared.environment_policy),
                input=prepared.stdin,
                capture_output=prepared.mode == "captured",
                text=text_mode,
                timeout=prepared.timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ProcessTimeoutError(
                prepared.argv,
                prepared.timeout if prepared.timeout is not None else 0.0,
                duration=time.perf_counter() - started,
            ) from error
        except OSError as error:
            raise ProcessSpawnError(
                prepared.argv, str(error), duration=time.perf_counter() - started
            ) from error
        stdout = getattr(completed, "stdout", "")
        stderr = getattr(completed, "stderr", "")
        if prepared.text:
            if isinstance(stdout, bytes):
                stdout = stdout.decode()
            if isinstance(stderr, bytes):
                stderr = stderr.decode()
        return ProcessResult(
            argv=prepared.argv,
            returncode=getattr(completed, "returncode", 0),
            stdout=stdout,
            stderr=stderr,
            duration=time.perf_counter() - started,
            cwd=prepared.cwd,
            environment=prepared.environment,
        )

    def spawn(self, step: PreparedStep) -> ProcessHandle:
        inherited = step.inherit_stdio
        process = subprocess.Popen(
            list(step.argv),
            cwd=step.cwd,
            env=_environment(step.environment, policy=step.environment_policy),
            stdin=None if inherited else subprocess.PIPE,
            stdout=None if inherited else subprocess.PIPE,
            stderr=None if inherited else subprocess.PIPE,
            start_new_session=step.start_new_session,
            shell=False,
        )
        group_id = process.pid if step.start_new_session and sys.platform != "win32" else None
        return ProcessHandle(
            process=process,
            argv=step.argv,
            process_group_id=group_id,
            session_id=group_id,
            inherited_stdio=inherited,
        )


def prepared_step(
    executable: str | Sequence[str],
    args: Sequence[str] = (),
    *,
    step_id: str = "process",
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    environment_policy: str = "sanitized-inherit",
    stdin: bytes | None = None,
    timeout: float | None = None,
    mode: str = "captured",
    text: bool = True,
    start_new_session: bool = False,
    inherit_stdio: bool = False,
) -> PreparedStep:
    prefix = [executable] if isinstance(executable, str) else list(executable)
    return PreparedStep(
        step_id=step_id,
        argv=(*prefix, *args),
        cwd=None if cwd is None else str(cwd),
        environment=tuple(sorted((env or {}).items())),
        environment_policy=environment_policy,
        stdin=stdin,
        timeout=timeout,
        mode=mode,
        text=text,
        start_new_session=start_new_session,
        inherit_stdio=inherit_stdio,
    )


def run_captured(
    executable: str | Sequence[str],
    args: Sequence[str] = (),
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    stdin: bytes | None = None,
    timeout: float | None = None,
    text: bool = True,
) -> ProcessResult:
    step = prepared_step(
        executable,
        args,
        cwd=cwd,
        env=env,
        stdin=stdin,
        timeout=timeout,
        text=text,
    )
    from . import active_context

    context = active_context()
    if context is not None:
        return cast("ProcessResult", context.process_prepared(step))
    return SubprocessExecutor().execute(step)


def run_captured_limited(  # noqa: C901
    executable: str | Sequence[str],
    args: Sequence[str] = (),
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    max_output_bytes: int,
) -> ProcessResult:
    """Run a captured process while bounding both output streams.

    This is the one bounded exception to ``subprocess.run`` in the executor:
    callers that consume untrusted command output can terminate a child as soon
    as its output budget is exceeded, rather than buffering an unbounded stream.
    """
    if max_output_bytes < 0:
        raise ValueError("max_output_bytes must not be negative")
    step = prepared_step(
        executable,
        args,
        cwd=cwd,
        env=env,
        timeout=timeout,
        text=False,
    )
    from . import active_context

    context = active_context()
    if context is not None:
        return cast("ProcessResult", context.process_prepared(step))
    started = time.perf_counter()
    try:
        process = subprocess.Popen(
            list(step.argv),
            cwd=step.cwd,
            env=_environment(step.environment, policy=step.environment_policy),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
    except OSError as error:
        raise ProcessSpawnError(
            step.argv, str(error), duration=time.perf_counter() - started
        ) from error

    stdout = process.stdout
    stderr = process.stderr
    assert stdout is not None
    assert stderr is not None
    streams = {stdout: bytearray(), stderr: bytearray()}
    selector = selectors.DefaultSelector()

    def terminate_and_reap() -> None:
        if process.poll() is None:
            with contextlib.suppress(OSError):
                process.terminate()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                process.kill()
            process.wait()

    try:
        selector.register(stdout, selectors.EVENT_READ)
        selector.register(stderr, selectors.EVENT_READ)
        while selector.get_map():
            remaining = None if timeout is None else timeout - (time.perf_counter() - started)
            if remaining is not None and remaining <= 0:
                terminate_and_reap()
                assert timeout is not None
                raise ProcessTimeoutError(
                    step.argv, timeout, duration=time.perf_counter() - started
                )
            ready = selector.select(remaining)
            if not ready:
                terminate_and_reap()
                raise ProcessTimeoutError(
                    step.argv,
                    timeout if timeout is not None else 0.0,
                    duration=time.perf_counter() - started,
                )
            for key, _ in ready:
                stream = cast("IO[bytes]", key.fileobj)
                chunk = os.read(stream.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                buffer = streams[stream]
                if len(buffer) + len(chunk) > max_output_bytes:
                    terminate_and_reap()
                    raise ProcessExecutionError(
                        step.argv,
                        "output exceeded configured limit",
                        duration=time.perf_counter() - started,
                    )
                buffer.extend(chunk)
        process.wait()
    finally:
        selector.close()
        for stream in (stdout, stderr):
            if not stream.closed:
                stream.close()

    return ProcessResult(
        argv=step.argv,
        returncode=process.returncode,
        stdout=bytes(streams[stdout]),
        stderr=bytes(streams[stderr]),
        duration=time.perf_counter() - started,
        cwd=step.cwd,
        environment=step.environment,
    )


def spawn(
    executable: str | Sequence[str],
    args: Sequence[str] = (),
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    mode: str = "foreground",
    inherit_stdio: bool = True,
) -> ProcessHandle:
    step = prepared_step(
        executable,
        args,
        cwd=cwd,
        env=env,
        mode=mode,
        start_new_session=True,
        inherit_stdio=inherit_stdio,
    )
    return SubprocessExecutor().spawn(step)


def _process_group_is_alive(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_process_group_exit(
    handle: ProcessHandle, process_group_id: int, *, timeout: float
) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        handle.poll()
        if not _process_group_is_alive(process_group_id):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


def _taskkill(handle: ProcessHandle, *, force: bool) -> None:
    args = ["/T", "/PID", str(handle.pid)]
    if force:
        args.append("/F")
    taskkill = prepared_step("taskkill", args, step_id="taskkill", timeout=5.0)
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        SubprocessExecutor().execute(taskkill)


def terminate(
    handle: ProcessHandle,
    *,
    process_group_id: int | None = None,
    timeout: float = _CLEANUP_TIMEOUT,
) -> None:
    if sys.platform == "win32":
        _taskkill(handle, force=False)
        try:
            handle.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _taskkill(handle, force=True)
            with contextlib.suppress(subprocess.TimeoutExpired):
                handle.wait(timeout=timeout)
        return

    group_id = process_group_id or handle.process_group_id
    if group_id is None:
        with contextlib.suppress(ProcessLookupError):
            group_id = os.getpgid(handle.pid)
    if group_id is None:
        with contextlib.suppress(subprocess.TimeoutExpired):
            handle.wait(timeout=timeout)
        return
    with contextlib.suppress(OSError):
        os.killpg(group_id, signal.SIGTERM)
    if not _wait_for_process_group_exit(handle, group_id, timeout=timeout):
        with contextlib.suppress(OSError):
            os.killpg(group_id, signal.SIGKILL)
        _wait_for_process_group_exit(handle, group_id, timeout=timeout)
    with contextlib.suppress(subprocess.TimeoutExpired):
        handle.wait(timeout=timeout)


def wait(handle: ProcessHandle) -> int:
    return handle.wait()


def wait_with_cleanup(handle: ProcessHandle) -> int:
    try:
        return wait(handle)
    except BaseException:
        with contextlib.suppress(BaseException):
            terminate(handle)
        raise


def wait_foreground(handle: ProcessHandle) -> int:
    interrupted = False
    process_group_id = handle.process_group_id or handle.pid
    previous = signal.getsignal(signal.SIGINT)

    def on_sigint(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        nonlocal interrupted
        interrupted = True
        with contextlib.suppress(BaseException):
            terminate(handle, process_group_id=process_group_id)

    if sys.platform != "win32":
        signal.signal(signal.SIGINT, on_sigint)
    try:
        try:
            exit_code = handle.wait()
        except KeyboardInterrupt:
            interrupted = True
            with contextlib.suppress(BaseException):
                terminate(handle, process_group_id=process_group_id)
            exit_code = 130
    finally:
        if sys.platform != "win32":
            signal.signal(signal.SIGINT, previous)
    return 130 if interrupted else exit_code


__all__ = [
    "ProcessExecutionError",
    "ProcessHandle",
    "ProcessResult",
    "ProcessSpawnError",
    "ProcessTimeoutError",
    "SubprocessExecutor",
    "owned_handle",
    "prepared_step",
    "run_captured",
    "run_captured_limited",
    "spawn",
    "terminate",
    "wait",
    "wait_foreground",
    "wait_with_cleanup",
]
