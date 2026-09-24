"""The single boundary for SDK-owned child-process effects."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from types import FrameType

import psutil

from odoo_instance_sdk.internal.proc.run import (
    _CLEANUP_TIMEOUT,
    ProcessHandle,
    SubprocessExecutor,
    _notify,
    prepared_step,
)

from . import (
    event_for_step,
)


def is_process_group_alive(process_group_id: int) -> bool:
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
        if not is_process_group_alive(process_group_id):
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
    if handle.drain is not None:
        handle.drain.stop()
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
        if is_process_group_alive(group_id):
            with contextlib.suppress(OSError):
                os.killpg(group_id, signal.SIGKILL)
            _wait_for_process_group_exit(handle, group_id, timeout=timeout)
    with contextlib.suppress(subprocess.TimeoutExpired):
        handle.wait(timeout=timeout)


def is_process_alive(pid: int, *, expected_create_time: float | None = None) -> bool:
    """Return whether the OS still exposes a process with this PID."""
    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, psutil.ZombieProcess, OSError) as exc:
        if expected_create_time is not None:
            raise RuntimeError("process identity is inaccessible") from exc
        return True
    try:
        if expected_create_time is not None and process.create_time() != expected_create_time:
            raise RuntimeError("process identity changed during termination")
        return process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.ZombieProcess:
        return False
    except (psutil.AccessDenied, OSError) as exc:
        if expected_create_time is not None:
            raise RuntimeError("process identity is inaccessible") from exc
        return True


def _wait_for_pid_exit(pid: int, *, expected_create_time: float | None, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while (
        is_process_alive(pid, expected_create_time=expected_create_time)
        and time.monotonic() < deadline
    ):
        time.sleep(0.05)


def _wait_for_pid_group_exit(process_group_id: int, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while is_process_group_alive(process_group_id):
        if time.monotonic() >= deadline:
            return
        time.sleep(0.05)


def terminate_pid(
    pid: int,
    *,
    process_group_id: int | None = None,
    expected_create_time: float | None = None,
    timeout: float = _CLEANUP_TIMEOUT,
) -> None:
    """Boundedly terminate an adopted process through the private proc seam."""
    if pid <= 0:
        raise ValueError("pid must be positive")
    if sys.platform == "win32":
        if not is_process_alive(pid, expected_create_time=expected_create_time):
            return
        args = ["/T", "/PID", str(pid)]
        taskkill = prepared_step("taskkill", args, step_id="taskkill", timeout=timeout)
        SubprocessExecutor().execute(taskkill)
        _wait_for_pid_exit(pid, expected_create_time=expected_create_time, timeout=timeout)
        if is_process_alive(pid, expected_create_time=expected_create_time):
            force = prepared_step(
                "taskkill", (*args, "/F"), step_id="taskkill.force", timeout=timeout
            )
            SubprocessExecutor().execute(force)
    else:
        group_id = process_group_id or pid
        if not is_process_alive(pid, expected_create_time=expected_create_time):
            if sys.platform != "win32" and is_process_group_alive(group_id):
                raise RuntimeError("process group remains alive after leader exit")
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(group_id, signal.SIGTERM)
        _wait_for_pid_exit(pid, expected_create_time=expected_create_time, timeout=timeout)
        if is_process_alive(
            pid, expected_create_time=expected_create_time
        ) or is_process_group_alive(group_id):
            with contextlib.suppress(ProcessLookupError):
                os.killpg(group_id, signal.SIGKILL)
            _wait_for_pid_exit(pid, expected_create_time=expected_create_time, timeout=timeout)
            _wait_for_pid_group_exit(group_id, timeout=timeout)
    if is_process_alive(pid, expected_create_time=expected_create_time) or (
        sys.platform != "win32" and is_process_group_alive(process_group_id or pid)
    ):
        raise TimeoutError(f"process {pid} did not exit within {timeout}s")


def wait_foreground(handle: ProcessHandle) -> int:  # noqa: C901
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
    result = 130 if interrupted else exit_code
    if handle.observer is not None and handle.step is not None:
        elapsed = (
            max(0.0, time.perf_counter() - handle.started_at)
            if handle.started_at is not None
            else None
        )
        _notify(
            handle.observer,
            event_for_step(
                handle.step,
                "failed" if interrupted else "completed",
                returncode=result,
                elapsed=elapsed,
                error="interrupted" if interrupted else None,
            ),
        )
    return result
