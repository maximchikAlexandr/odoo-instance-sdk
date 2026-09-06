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
from typing import IO, Literal, cast

from odoo_instance_sdk.internal.process_env import (
    captured_child_environment,
    sanitized_child_environment,
)

from . import (
    BoundedProcessInputs,
    ExecutionDeadline,
    PreparedProcess,
    PreparedStep,
    StepEvent,
    StepObserver,
    bounded_process_inputs,
)
from .redaction import IncrementalStreamRedactor

_CLEANUP_TIMEOUT = 5.0
_TIMEOUT_TAIL_BYTES = 8192


class ProcessExecutionError(RuntimeError):
    """Base class for failures at the process boundary."""

    def __init__(
        self,
        argv: tuple[str, ...],
        reason: str,
        *,
        duration: float,
        secrets: Sequence[str] = (),
        sensitive_indices: Sequence[int] = (),
    ) -> None:
        from .redaction import redacted_argv, redacted_projection

        self.argv = redacted_argv(
            argv, secrets=secrets, sensitive_indices=sensitive_indices or None
        )
        self.duration = duration
        safe_reason = cast(
            "str",
            redacted_projection(reason, secrets=secrets, field="error"),
        )
        super().__init__(f"process {self.argv!r} failed to execute: {safe_reason}")


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
        secrets: Sequence[str] = (),
        sensitive_indices: Sequence[int] = (),
        stdout_tail: str | bytes = "",
        stderr_tail: str | bytes = "",
        stdout_truncated: bool = False,
        stderr_truncated: bool = False,
    ) -> None:
        self.timeout = timeout
        self.elapsed = duration
        self.stdout_truncated = stdout_truncated
        self.stderr_truncated = stderr_truncated
        from .redaction import redacted_projection

        self.stdout_tail = cast(
            "str",
            redacted_projection(stdout_tail, secrets=secrets, field="stdout"),
        )
        self.stderr_tail = cast(
            "str",
            redacted_projection(stderr_tail, secrets=secrets, field="stderr"),
        )
        super().__init__(
            argv,
            f"timeout after {timeout}s; stdout_tail={self.stdout_tail!r}; "
            f"stderr_tail={self.stderr_tail!r}",
            duration=duration,
            secrets=secrets,
            sensitive_indices=sensitive_indices,
        )


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
    overrides: tuple[tuple[str, str], ...],
    *,
    policy: str = "sanitized-inherit",
    snapshot: tuple[tuple[str, str], ...] = (),
) -> dict[str, str]:
    if policy == "explicit":
        # An explicit policy is hermetic even when the caller supplied no
        # overrides.  Passing ``None`` here would turn an intentionally empty
        # environment back into a copy of the ambient process environment.
        return sanitized_child_environment(dict(overrides))
    # A non-empty tuple is an exact environment captured by ``prepared_step``
    # or a resource command.  Do not silently merge a later ambient
    # environment into an inspected command.
    source = snapshot or overrides
    return sanitized_child_environment(dict(source) if source else None)


def _captured_error_secrets(step: PreparedStep) -> tuple[str, ...]:
    from .redaction import captured_secret_values

    return captured_secret_values(step)


def _run_pump(  # noqa: C901
    prepared: PreparedStep,
    *,
    timeout: float | None,
    environment_snapshot: tuple[tuple[str, str], ...],
    observer: StepObserver | None,
    observe_output: bool,
    max_output_bytes: int | None = None,
) -> tuple[int, bytes, bytes, float]:
    """Run one captured child while pumping stdin and both output pipes."""
    process = subprocess.Popen(
        list(prepared.argv),
        cwd=prepared.cwd,
        env=_environment(
            prepared.environment,
            policy=prepared.environment_policy,
            snapshot=environment_snapshot,
        ),
        stdin=subprocess.PIPE if prepared.stdin is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        shell=False,
    )
    stdin = process.stdin
    stdout = process.stdout
    stderr = process.stderr
    assert stdout is not None
    assert stderr is not None
    output_streams = (stdout, stderr)
    full_output = {stream: bytearray() for stream in output_streams}
    tail_output = {stream: bytearray() for stream in output_streams}
    tail_truncated = dict.fromkeys(output_streams, False)
    selector = selectors.DefaultSelector()
    started = time.perf_counter()
    stdin_offset = 0
    group_cleaned = False
    redactors: dict[str, IncrementalStreamRedactor] = {}
    if observer is not None and observe_output:
        secrets = _captured_error_secrets(prepared)
        redactors = {
            stream: IncrementalStreamRedactor(secrets=secrets, field=stream)
            for stream in ("stdout", "stderr")
        }

    def notify_chunk(stream: str, chunk: bytes) -> None:
        if not observe_output or observer is None:
            return
        redactor = redactors[stream]
        safe = redactor.feed(chunk)
        if safe:
            _notify(
                observer,
                StepEvent(
                    step_id=prepared.step_id,
                    kind=cast("Literal['stdout', 'stderr']", stream),
                    chunk=safe,
                ),
            )

    def flush_observers() -> None:
        if not observe_output or observer is None:
            return
        for stream in ("stdout", "stderr"):
            safe = redactors[stream].flush()
            if safe:
                _notify(observer, StepEvent(step_id=prepared.step_id, kind=stream, chunk=safe))

    def close_stream(stream: IO[bytes]) -> None:
        with contextlib.suppress(OSError, ValueError):
            stream.close()

    def terminate_and_reap() -> None:
        group_id = process.pid if sys.platform != "win32" else None
        if group_id is not None:
            with contextlib.suppress(OSError):
                os.killpg(group_id, signal.SIGTERM)
        elif process.poll() is None:
            with contextlib.suppress(OSError):
                process.terminate()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            if group_id is not None:
                with contextlib.suppress(OSError):
                    os.killpg(group_id, signal.SIGKILL)
            else:
                with contextlib.suppress(OSError):
                    process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=1.0)

    def drain_after_termination() -> None:
        for stream in output_streams:
            with contextlib.suppress(OSError, ValueError):
                os.set_blocking(stream.fileno(), False)
            while True:
                try:
                    chunk = os.read(stream.fileno(), 64 * 1024)
                except (BlockingIOError, OSError):
                    break
                if not chunk:
                    break
                full_output[stream].extend(chunk)
                tail = tail_output[stream]
                if len(tail) + len(chunk) > _TIMEOUT_TAIL_BYTES:
                    tail_truncated[stream] = True
                    tail[:] = (tail + chunk)[-_TIMEOUT_TAIL_BYTES:]
                else:
                    tail.extend(chunk)
                notify_chunk("stdout" if stream is stdout else "stderr", chunk)

    def timeout_error() -> ProcessTimeoutError:
        flush_observers()
        return ProcessTimeoutError(
            prepared.argv,
            timeout if timeout is not None else 0.0,
            duration=time.perf_counter() - started,
            secrets=_captured_error_secrets(prepared),
            sensitive_indices=prepared.sensitive_argv_indices,
            stdout_tail=bytes(tail_output[stdout]),
            stderr_tail=bytes(tail_output[stderr]),
            stdout_truncated=tail_truncated[stdout],
            stderr_truncated=tail_truncated[stderr],
        )

    try:
        for stream in output_streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(
                stream, selectors.EVENT_READ, data="stdout" if stream is stdout else "stderr"
            )
        if stdin is not None:
            os.set_blocking(stdin.fileno(), False)
            selector.register(stdin, selectors.EVENT_WRITE, data="stdin")

        while selector.get_map():
            if process.poll() is not None and stdin is not None:
                with contextlib.suppress(KeyError):
                    selector.unregister(stdin)
                close_stream(stdin)
                stdin = None
            if process.poll() is not None and not group_cleaned:
                terminate_and_reap()
                group_cleaned = True
            remaining = None if timeout is None else timeout - (time.perf_counter() - started)
            if remaining is not None and remaining <= 0:
                terminate_and_reap()
                drain_after_termination()
                raise timeout_error()  # noqa: TRY301
            ready = selector.select(remaining)
            if not ready:
                terminate_and_reap()
                drain_after_termination()
                raise timeout_error()  # noqa: TRY301
            ready.sort(key=lambda item: 0 if item[0].data == "stdout" else 1)
            for key, mask in ready:
                stream = cast("IO[bytes]", key.fileobj)
                if key.data == "stdin":
                    payload = prepared.stdin
                    assert payload is not None
                    try:
                        written = os.write(stream.fileno(), payload[stdin_offset:])
                    except (BrokenPipeError, OSError) as error:
                        with contextlib.suppress(KeyError):
                            selector.unregister(stream)
                        close_stream(stream)
                        stdin = None
                        if process.poll() is not None and isinstance(error, BrokenPipeError):
                            continue
                        terminate_and_reap()
                        drain_after_termination()
                        raise ProcessExecutionError(
                            prepared.argv,
                            "stdin write failed",
                            duration=time.perf_counter() - started,
                            secrets=_captured_error_secrets(prepared),
                            sensitive_indices=prepared.sensitive_argv_indices,
                        ) from error
                    stdin_offset += written
                    if stdin_offset >= len(payload):
                        with contextlib.suppress(KeyError):
                            selector.unregister(stream)
                        close_stream(stream)
                        stdin = None
                    continue
                chunk = os.read(stream.fileno(), 64 * 1024)
                if not chunk:
                    with contextlib.suppress(KeyError):
                        selector.unregister(stream)
                    close_stream(stream)
                    continue
                buffer = full_output[stream]
                if max_output_bytes is not None and len(buffer) + len(chunk) > max_output_bytes:
                    terminate_and_reap()
                    drain_after_termination()
                    raise ProcessExecutionError(  # noqa: TRY301
                        prepared.argv,
                        "output exceeded configured limit",
                        duration=time.perf_counter() - started,
                        secrets=_captured_error_secrets(prepared),
                        sensitive_indices=prepared.sensitive_argv_indices,
                    )
                buffer.extend(chunk)
                tail = tail_output[stream]
                if len(tail) + len(chunk) > _TIMEOUT_TAIL_BYTES:
                    tail_truncated[stream] = True
                    tail[:] = (tail + chunk)[-_TIMEOUT_TAIL_BYTES:]
                else:
                    tail.extend(chunk)
                notify_chunk(key.data, chunk)
        if stdin is not None:
            close_stream(stdin)
        process.wait()
        flush_observers()
        return (
            process.returncode,
            bytes(full_output[stdout]),
            bytes(full_output[stderr]),
            time.perf_counter() - started,
        )
    except BaseException:
        if not group_cleaned:
            terminate_and_reap()
            group_cleaned = True
        drain_after_termination()
        flush_observers()
        raise
    finally:
        selector.close()
        if stdin is not None:
            close_stream(stdin)
        for stream in output_streams:
            close_stream(stream)


class SubprocessExecutor:
    """Real executor for already-captured process steps."""

    def execute(
        self,
        step: PreparedProcess,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessResult:
        prepared = cast("PreparedStep", step)
        return self._execute(prepared, observer=observer, observe_output=observe_output)

    def execute_with_deadline(
        self,
        step: PreparedProcess,
        deadline: ExecutionDeadline,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessResult:
        prepared = cast("PreparedStep", step)
        return self._execute(
            prepared,
            deadline=deadline,
            observer=observer,
            observe_output=observe_output,
        )

    def _execute(  # noqa: C901
        self,
        prepared: PreparedStep,
        *,
        deadline: ExecutionDeadline | None = None,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessResult:
        bounded: BoundedProcessInputs | None = None
        if deadline is not None:
            bounded = bounded_process_inputs(prepared, deadline)
        timeout = prepared.timeout if bounded is None else bounded.timeout
        environment_snapshot = (
            prepared.environment_snapshot if bounded is None else bounded.environment_snapshot
        )
        started = time.perf_counter()
        _notify(observer, StepEvent(step_id=prepared.step_id, kind="started"))
        if prepared.mode == "captured":
            try:
                returncode, stdout, stderr, duration = _run_pump(
                    prepared,
                    timeout=timeout,
                    environment_snapshot=environment_snapshot,
                    observer=observer,
                    observe_output=observe_output,
                )
            except ProcessTimeoutError as error:
                _notify(
                    observer,
                    StepEvent(
                        step_id=prepared.step_id,
                        kind="failed",
                        error=str(error),
                    ),
                )
                raise
            except KeyboardInterrupt:
                _notify(
                    observer,
                    StepEvent(
                        step_id=prepared.step_id,
                        kind="failed",
                        error="interrupted",
                    ),
                )
                raise
            except OSError as error:
                _notify(
                    observer,
                    StepEvent(
                        step_id=prepared.step_id,
                        kind="failed",
                        error=_safe_error(prepared, error),
                    ),
                )
                raise ProcessSpawnError(
                    prepared.argv,
                    str(error),
                    duration=time.perf_counter() - started,
                    secrets=_captured_error_secrets(prepared),
                    sensitive_indices=prepared.sensitive_argv_indices,
                ) from error
            except Exception as error:
                _notify(
                    observer,
                    StepEvent(
                        step_id=prepared.step_id,
                        kind="failed",
                        error=_safe_error(prepared, error),
                    ),
                )
                raise
            stdout_bytes: str | bytes = stdout.decode() if prepared.text else stdout
            stderr_bytes: str | bytes = stderr.decode() if prepared.text else stderr
            result = ProcessResult(
                argv=prepared.argv,
                returncode=returncode,
                stdout=stdout_bytes,
                stderr=stderr_bytes,
                duration=duration,
                cwd=prepared.cwd,
                environment=prepared.environment,
            )
            _notify(
                observer,
                StepEvent(
                    step_id=prepared.step_id,
                    kind="completed",
                    returncode=result.returncode,
                ),
            )
            return result
        try:
            text_mode = prepared.text and prepared.stdin is None
            completed = subprocess.run(
                list(prepared.argv),
                cwd=prepared.cwd,
                env=_environment(
                    prepared.environment,
                    policy=prepared.environment_policy,
                    snapshot=environment_snapshot,
                ),
                input=prepared.stdin,
                capture_output=prepared.mode == "captured",
                text=text_mode,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            _notify(
                observer,
                StepEvent(step_id=prepared.step_id, kind="failed", error="timeout"),
            )
            raise ProcessTimeoutError(
                prepared.argv,
                timeout if timeout is not None else 0.0,
                duration=time.perf_counter() - started,
                secrets=_captured_error_secrets(prepared),
                sensitive_indices=prepared.sensitive_argv_indices,
            ) from None
        except OSError as error:
            _notify(
                observer,
                StepEvent(
                    step_id=prepared.step_id,
                    kind="failed",
                    error=_safe_error(prepared, error),
                ),
            )
            raise ProcessSpawnError(
                prepared.argv,
                str(error),
                duration=time.perf_counter() - started,
                secrets=_captured_error_secrets(prepared),
                sensitive_indices=prepared.sensitive_argv_indices,
            ) from error
        except Exception as error:
            _notify(
                observer,
                StepEvent(
                    step_id=prepared.step_id,
                    kind="failed",
                    error=_safe_error(prepared, error),
                ),
            )
            raise
        stdout_value: str | bytes | None = cast(
            "str | bytes | None", getattr(completed, "stdout", "")
        )
        stderr_value: str | bytes | None = cast(
            "str | bytes | None", getattr(completed, "stderr", "")
        )
        if prepared.text:
            if isinstance(stdout_value, bytes):
                stdout_value = stdout_value.decode()
            if isinstance(stderr_value, bytes):
                stderr_value = stderr_value.decode()
        result = ProcessResult(
            argv=prepared.argv,
            returncode=getattr(completed, "returncode", 0),
            stdout=stdout_value,
            stderr=stderr_value,
            duration=time.perf_counter() - started,
            cwd=prepared.cwd,
            environment=prepared.environment,
        )
        if observe_output:
            _notify_output(observer, prepared, stdout_value, "stdout")
            _notify_output(observer, prepared, stderr_value, "stderr")
        _notify(
            observer,
            StepEvent(
                step_id=prepared.step_id,
                kind="completed",
                returncode=result.returncode,
            ),
        )
        return result

    def spawn(
        self,
        step: PreparedStep,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessHandle:
        inherited = step.inherit_stdio
        started = time.perf_counter()
        _notify(observer, StepEvent(step_id=step.step_id, kind="started"))
        try:
            process = subprocess.Popen(
                list(step.argv),
                cwd=step.cwd,
                env=_environment(
                    step.environment,
                    policy=step.environment_policy,
                    snapshot=step.environment_snapshot,
                ),
                stdin=None if inherited else subprocess.PIPE,
                stdout=None if inherited else subprocess.PIPE,
                stderr=None if inherited else subprocess.PIPE,
                start_new_session=step.start_new_session,
                shell=False,
            )
        except OSError as error:
            _notify(
                observer,
                StepEvent(step_id=step.step_id, kind="failed", error=_safe_error(step, error)),
            )
            raise ProcessSpawnError(
                step.argv,
                str(error),
                duration=time.perf_counter() - started,
                secrets=_captured_error_secrets(step),
                sensitive_indices=step.sensitive_argv_indices,
            ) from error
        group_id = process.pid if step.start_new_session and sys.platform != "win32" else None
        return ProcessHandle(
            process=process,
            argv=step.argv,
            process_group_id=group_id,
            session_id=group_id,
            inherited_stdio=inherited,
        )


def _notify(observer: StepObserver | None, event: StepEvent) -> None:
    """Observers are diagnostics and must never change process semantics."""
    if observer is None:
        return
    try:
        observer(event)
    except Exception:
        return


def _notify_output(
    observer: StepObserver | None,
    step: PreparedStep,
    value: str | bytes | None,
    stream: str,
) -> None:
    if observer is None or value in (None, "", b""):
        return
    from .redaction import redacted_projection

    text = value.decode(errors="replace") if isinstance(value, bytes) else value
    safe = cast(
        "str", redacted_projection(text, secrets=_captured_error_secrets(step), field=stream)
    )
    if stream == "stdout":
        _notify(observer, StepEvent(step_id=step.step_id, kind="stdout", chunk=safe))
    else:
        _notify(observer, StepEvent(step_id=step.step_id, kind="stderr", chunk=safe))


def _safe_error(step: PreparedStep, error: BaseException) -> str:
    from .redaction import redacted_projection

    return cast(
        "str",
        redacted_projection(
            str(error),
            secrets=_captured_error_secrets(step),
            field="error",
        ),
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
    read_only: bool = False,
    mutating: bool = False,
    interactive: bool = False,
    long_running: bool = False,
    start_new_session: bool = False,
    inherit_stdio: bool = False,
    secret_values: Sequence[str] = (),
) -> PreparedStep:
    prefix = [executable] if isinstance(executable, str) else list(executable)
    captured_environment, environment_overrides = captured_child_environment(env)
    if environment_policy == "explicit":
        captured_environment = environment_overrides
    private_overrides = tuple(sorted((env or {}).items()))
    return PreparedStep(
        step_id=step_id,
        argv=(*prefix, *args),
        cwd=None if cwd is None else str(cwd),
        environment=private_overrides,
        environment_snapshot=captured_environment,
        environment_overrides=environment_overrides,
        environment_policy=environment_policy,
        stdin=stdin,
        timeout=timeout,
        mode=mode,
        text=text,
        read_only=read_only,
        mutating=mutating,
        interactive=interactive,
        long_running=long_running,
        secret_values=tuple(secret_values),
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
    step_id: str = "process",
    read_only: bool = False,
    mutating: bool = False,
    interactive: bool = False,
    long_running: bool = False,
    secret_values: Sequence[str] = (),
) -> ProcessResult:
    step = prepared_step(
        executable,
        args,
        cwd=cwd,
        env=env,
        stdin=stdin,
        timeout=timeout,
        text=text,
        step_id=step_id,
        read_only=read_only,
        mutating=mutating,
        interactive=interactive,
        long_running=long_running,
        secret_values=secret_values,
    )
    from . import active_context

    context = active_context()
    if context is not None:
        return cast("ProcessResult", context.process_prepared(step))
    return SubprocessExecutor().execute(step)


def run_captured_limited(
    executable: str | Sequence[str],
    args: Sequence[str] = (),
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    max_output_bytes: int,
    step_id: str = "process",
    read_only: bool = False,
    mutating: bool = False,
    interactive: bool = False,
    long_running: bool = False,
    secret_values: Sequence[str] = (),
) -> ProcessResult:
    """Run a captured process while bounding both output streams.

    Callers that consume untrusted command output can terminate a child as soon
    as its output budget is exceeded, rather than buffering an unbounded stream;
    the same private pump owns ordinary captured execution too.
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
        step_id=step_id,
        read_only=read_only,
        mutating=mutating,
        interactive=interactive,
        long_running=long_running,
        secret_values=secret_values,
    )
    from . import active_context

    context = active_context()
    if context is not None:
        return cast("ProcessResult", context.process_prepared(step))
    started = time.perf_counter()
    try:
        returncode, stdout, stderr, duration = _run_pump(
            step,
            timeout=timeout,
            environment_snapshot=step.environment_snapshot,
            observer=None,
            observe_output=False,
            max_output_bytes=max_output_bytes,
        )
    except OSError as error:
        raise ProcessSpawnError(
            step.argv,
            str(error),
            duration=time.perf_counter() - started,
            secrets=_captured_error_secrets(step),
            sensitive_indices=step.sensitive_argv_indices,
        ) from error
    return ProcessResult(
        argv=step.argv,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration=duration,
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

    # Reap the direct child before checking the group.  On Darwin a terminated
    # group leader can remain visible to ``killpg(..., 0)`` while it is a
    # zombie; waiting on the owned handle first makes signal-driven foreground
    # cleanup bounded instead of waiting through the full TERM/KILL windows.
    try:
        handle.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            os.killpg(group_id, signal.SIGKILL)
        _wait_for_process_group_exit(handle, group_id, timeout=timeout)
    else:
        if _process_group_is_alive(group_id):
            with contextlib.suppress(OSError):
                os.killpg(group_id, signal.SIGKILL)
            _wait_for_process_group_exit(handle, group_id, timeout=timeout)
    with contextlib.suppress(subprocess.TimeoutExpired):
        handle.wait(timeout=timeout)


def wait_foreground(handle: ProcessHandle) -> int:
    interrupted = False
    process_group_id = handle.process_group_id or handle.pid
    previous = signal.getsignal(signal.SIGINT)

    def on_sigint(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        nonlocal interrupted
        interrupted = True
        # Let the normal wait path reap the direct child.  Calling the
        # bounded group-reaper from a Python signal handler can observe the
        # just-terminated group leader as a Darwin zombie and block the CLI;
        # the group signal is the forwarding action, while cleanup below is
        # performed after ``wait`` has reaped the child.
        with contextlib.suppress(OSError):
            os.killpg(process_group_id, signal.SIGTERM)

    if sys.platform != "win32":
        signal.signal(signal.SIGINT, on_sigint)
    try:
        try:
            if sys.platform == "win32":
                exit_code = handle.wait()
            else:
                while True:
                    try:
                        exit_code = handle.wait(timeout=0.1)
                        break
                    except subprocess.TimeoutExpired:
                        if interrupted:
                            terminate(handle, process_group_id=process_group_id)
                            exit_code = handle.wait()
                            break
        except KeyboardInterrupt:
            interrupted = True
            with contextlib.suppress(BaseException):
                terminate(handle, process_group_id=process_group_id)
            exit_code = 130
    finally:
        if interrupted:
            with contextlib.suppress(BaseException):
                terminate(handle, process_group_id=process_group_id)
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
    "wait_foreground",
]
