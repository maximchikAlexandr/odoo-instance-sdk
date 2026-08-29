from __future__ import annotations

import sys
from pathlib import Path

import pytest

from odoo_instance_sdk.internal.proc import (
    ProcessResult,
    ProcessSpawnError,
    ProcessTimeoutError,
    RecordingExecutor,
    SubprocessExecutor,
    prepared_step,
    run_captured,
    spawn,
)


def _python(source: str) -> tuple[str, ...]:
    return (sys.executable, "-c", source)


def test_captured_text_preserves_argv_cwd_stdin_and_sanitized_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "must-not-cross-boundary")
    argv = _python(
        "import os, pathlib, sys; "
        "print(pathlib.Path.cwd()); "
        "print(os.environ['PROC_TEST_VALUE']); "
        "print('secret' if 'ODCLI_TEST_MASTER_PASSWORD' in os.environ else 'clean'); "
        "print(sys.stdin.read())"
    )
    result = run_captured(
        argv,
        cwd=tmp_path,
        env={
            "PROC_TEST_VALUE": "override",
            "ODCLI_TEST_MASTER_PASSWORD": "override-secret",
        },
        stdin=b"input-bytes",
    )

    assert result.argv == argv
    assert result.returncode == 0
    assert result.stdout is not None
    assert str(tmp_path) in result.stdout
    assert "override" in result.stdout
    assert "clean" in result.stdout
    assert "input-bytes" in result.stdout
    assert result.cwd == str(tmp_path)
    assert result.environment == (
        ("ODCLI_TEST_MASTER_PASSWORD", "override-secret"),
        ("PROC_TEST_VALUE", "override"),
    )
    assert result.duration >= 0


def test_captured_bytes_and_nonzero_result() -> None:
    step = prepared_step(
        _python("import sys; sys.stdout.buffer.write(b'\\xff'); sys.exit(7)"), text=False
    )
    result = SubprocessExecutor().execute(step)

    assert result.returncode == 7
    assert result.stdout == b"\xff"
    assert result.stderr == b""


def test_timeout_and_spawn_failures_are_typed() -> None:
    with pytest.raises(ProcessTimeoutError) as timeout:
        run_captured(_python("import time; time.sleep(10)"), timeout=0.01)
    assert timeout.value.timeout == 0.01

    with pytest.raises(ProcessSpawnError) as spawn_error:
        run_captured(("/definitely/missing/odoo-sdk-executable",))
    assert spawn_error.value.argv == ("/definitely/missing/odoo-sdk-executable",)


def test_inherited_spawn_owns_stdio_and_process_group() -> None:
    handle = spawn(_python("import sys; sys.exit(3)"), inherit_stdio=True)
    assert handle.stdin is None
    assert handle.stdout is None
    assert handle.stderr is None
    assert handle.process_group_id == handle.pid
    assert handle.session_id == handle.pid
    assert handle.wait() == 3

    captured = spawn(_python("import sys; sys.exit(4)"), inherit_stdio=False)
    assert captured.stdin is not None
    assert captured.stdout is not None
    assert captured.stderr is not None
    assert captured.wait() == 4


def test_recording_executor_returns_typed_result_and_exact_private_step() -> None:
    expected = ProcessResult(
        argv=("tool", "--flag"),
        returncode=4,
        stdout="out",
        stderr="err",
        duration=0.25,
        cwd="/work",
        environment=(("TOKEN", "private"),),
    )
    executor = RecordingExecutor(results={"step": expected})
    step = prepared_step(
        "tool",
        ["--flag"],
        step_id="step",
        cwd="/work",
        env={"TOKEN": "private"},
        stdin=b"secret input",
        timeout=2.0,
    )

    result = executor.execute(step)

    assert result is expected
    assert executor.executed == [step]
    assert executor.executed[0].stdin == b"secret input"
    assert executor.executed[0].timeout == 2.0
