from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import DuplicateStepError, UnplannedStepError
from odoo_instance_sdk.internal.proc import (
    DeadlineExceeded,
    ExecutionDeadline,
    PreparedCommand,
    PreparedProcess,
    PreparedStep,
    ProcessExecutionError,
    ProcessResult,
    ProcessSpawnError,
    ProcessTimeoutError,
    RecordingExecutor,
    RunContext,
    StepEvent,
    StepObserver,
    SubprocessExecutor,
    prepared_command,
    prepared_step,
    run_captured,
    run_captured_limited,
    spawn,
)


def _python(source: str) -> tuple[str, ...]:
    return (sys.executable, "-c", source)


def test_optional_step_observer_preserves_result_and_redacts_output() -> None:
    secret = "observer-secret"
    step = PreparedStep(
        step_id="observer.step",
        argv=_python(f"print({secret!r}); print('failure', file=__import__('sys').stderr)"),
        secret_values=(secret,),
    )
    command: PreparedCommand[ProcessResult] = prepared_command(
        lambda context: context.process(step.step_id),
        (step,),
        executor=SubprocessExecutor(),
    )
    events: list[StepEvent] = []

    result = command.run(observer=events.append, observe_output=True)

    assert isinstance(result, ProcessResult)
    assert result.returncode == 0
    assert [event.kind for event in events] == ["started", "stdout", "stderr", "completed"]
    assert all(secret not in (event.chunk or "") for event in events)
    assert events[0].step_id == events[-1].step_id == step.step_id


def test_observer_redacts_a_secret_split_across_output_chunks() -> None:
    secret = "split-secret"
    step = PreparedStep(
        step_id="observer.split",
        argv=_python("print('captured')"),
        secret_values=(secret,),
    )

    class SplitChunkExecutor:
        def execute(
            self,
            prepared: PreparedStep,
            *,
            observer: StepObserver | None = None,
            observe_output: bool = False,
        ) -> ProcessResult:
            assert prepared is step
            assert observer is not None
            observer(StepEvent(step_id=step.step_id, kind="started"))
            if observe_output:
                observer(StepEvent(step_id=step.step_id, kind="stdout", chunk="split-"))
                observer(StepEvent(step_id=step.step_id, kind="stdout", chunk="secret"))
            observer(StepEvent(step_id=step.step_id, kind="completed", returncode=0))
            return ProcessResult(step.argv, 0, secret, "", 0.0, None, ())

    command: PreparedCommand[ProcessResult] = prepared_command(
        lambda context: context.process(step.step_id),
        (step,),
        executor=SplitChunkExecutor(),  # type: ignore[arg-type]
    )
    events: list[StepEvent] = []

    result = command.run(observer=events.append, observe_output=True)

    assert result.stdout == secret
    assert [event.kind for event in events] == ["started", "stdout", "completed"]
    assert events[1].chunk == "<redacted>"
    assert secret not in repr(events)


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


def test_explicit_empty_environment_does_not_inherit_ambient_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROC_EXPLICIT_SENTINEL", "ambient-secret")
    step = prepared_step(
        _python("import os, sys; sys.exit(0 if 'PROC_EXPLICIT_SENTINEL' not in os.environ else 1)"),
        env={},
        environment_policy="explicit",
    )

    result = SubprocessExecutor().execute(step)

    assert result.returncode == 0
    assert result.environment == ()


def test_explicit_empty_environment_spawn_does_not_inherit_ambient_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROC_EXPLICIT_SENTINEL", "ambient-secret")
    step = prepared_step(
        _python("import os, sys; sys.exit(0 if 'PROC_EXPLICIT_SENTINEL' not in os.environ else 1)"),
        step_id="explicit-empty-spawn",
        env={},
        environment_policy="explicit",
    )

    handle = SubprocessExecutor().spawn(step)

    assert handle.wait() == 0


def test_timeout_and_spawn_failures_are_typed() -> None:
    with pytest.raises(ProcessTimeoutError) as timeout:
        run_captured(_python("import time; time.sleep(10)"), timeout=0.01)
    assert timeout.value.timeout == 0.01

    with pytest.raises(ProcessSpawnError) as spawn_error:
        run_captured(("/definitely/missing/odoo-sdk-executable",))
    assert spawn_error.value.argv == ("/definitely/missing/odoo-sdk-executable",)


def test_process_boundary_error_diagnostics_use_the_safe_argv_projection() -> None:
    timeout_step = prepared_step(
        (*_python("import time; time.sleep(10)"), "--token", "timeout-secret"),
        step_id="timeout-secret",
        timeout=0.01,
    )
    timeout_step = replace(timeout_step, secret_values=("timeout-secret",))
    with pytest.raises(ProcessTimeoutError) as timeout:
        SubprocessExecutor().execute(timeout_step)
    assert "timeout-secret" not in str(timeout.value)
    assert timeout.value.argv[-1] == "<redacted>"

    spawn_step = prepared_step(
        "/definitely/missing/odoo-sdk-executable",
        ("--password=spawn-secret",),
        step_id="spawn-secret",
    )
    with pytest.raises(ProcessSpawnError) as spawn_error:
        SubprocessExecutor().execute(spawn_step)
    assert "spawn-secret" not in str(spawn_error.value)
    assert spawn_error.value.argv[-1] == "--password=<redacted>"


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


def test_recording_executor_emits_production_lifecycle_and_redacted_output() -> None:
    secret = "recording-secret"
    step = PreparedStep(
        step_id="recording-observed",
        argv=("tool",),
        secret_values=(secret,),
    )
    expected = ProcessResult(
        argv=step.argv,
        returncode=3,
        stdout=f"out={secret}",
        stderr="failure",
        duration=0.0,
        cwd=None,
        environment=(),
    )
    events: list[StepEvent] = []

    RecordingExecutor(results={step.step_id: expected}).execute(
        step, observer=events.append, observe_output=True
    )

    assert [event.kind for event in events] == ["started", "stdout", "stderr", "completed"]
    assert events[1].chunk == "out=<redacted>"
    assert events[2].chunk == "failure"
    assert events[-1].returncode == 3


def test_recording_executor_emits_failed_lifecycle_when_factory_raises() -> None:
    step = PreparedStep(step_id="recording-failed", argv=("tool",))

    def fail(_step: PreparedProcess) -> ProcessResult:
        raise RuntimeError("factory failed")

    events: list[StepEvent] = []
    with pytest.raises(RuntimeError, match="factory failed"):
        RecordingExecutor(result_factory=fail).execute(step, observer=events.append)

    assert [event.kind for event in events] == ["started", "failed"]
    assert events[-1].error == "factory failed"


def test_recording_executor_emits_failed_lifecycle_when_spawn_handle_is_missing() -> None:
    secret = "spawn-secret-sentinel"
    step = PreparedStep(
        step_id=f"recording-spawn-failed-{secret}",
        argv=("tool",),
        secret_values=(secret,),
    )
    events: list[StepEvent] = []

    with pytest.raises(KeyError) as raised:
        RecordingExecutor().spawn(step, observer=events.append)

    assert [event.kind for event in events] == ["started", "failed"]
    assert events[-1].error is not None
    assert secret in str(raised.value)
    assert secret not in events[-1].error
    assert "<redacted>" in events[-1].error


def test_deadline_context_records_exact_step_and_bounded_transport_inputs() -> None:
    clock_now = [0.0]

    def clock() -> float:
        return clock_now[0]

    step = PreparedStep(
        step_id="deadline-step",
        argv=("psql",),
        environment_snapshot=(("PGOPTIONS", "-c statement_timeout=1000"),),
        timeout=1.0,
    )
    executor = RecordingExecutor()
    context: RunContext[object] = RunContext((step,), executor)
    deadline = ExecutionDeadline.start(1.0, monotonic=clock)

    clock_now[0] = 0.7499
    context.process_prepared_with_deadline(step, deadline)

    assert executor.executed == [step]
    assert executor.executed[0] is step
    assert executor.effective_timeouts == [pytest.approx(0.2501)]
    assert dict(executor.effective_environment_snapshots[0])["PGOPTIONS"] == (
        "-c statement_timeout=250"
    )
    assert step.environment_snapshot == (("PGOPTIONS", "-c statement_timeout=1000"),)


def test_deadline_context_refuses_submillisecond_attempt_without_starting_process() -> None:
    clock_now = [0.0]

    def clock() -> float:
        return clock_now[0]

    step = PreparedStep(
        step_id="expired-deadline-step",
        argv=("psql",),
        environment_snapshot=(("PGOPTIONS", "-c statement_timeout=1000"),),
        timeout=1.0,
    )
    executor = RecordingExecutor()
    context: RunContext[object] = RunContext((step,), executor)
    deadline = ExecutionDeadline.start(1.0, monotonic=clock)
    clock_now[0] = 0.9995

    with pytest.raises(DeadlineExceeded):
        context.process_prepared_with_deadline(step, deadline)

    assert executor.executed == []
    assert context.consumed(step.step_id)


def test_subprocess_deadline_receives_remainder_and_floored_statement_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock_now = [0.0]
    calls: list[dict[str, object]] = []

    def clock() -> float:
        return clock_now[0]

    def fake_run(argv: list[str], **kwargs: object) -> object:
        calls.append({"argv": argv, **kwargs})
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("odoo_instance_sdk.internal.proc.executor.subprocess.run", fake_run)
    step = PreparedStep(
        step_id="subprocess-deadline-step",
        argv=("psql",),
        environment_snapshot=(("PGOPTIONS", "-c statement_timeout=1000"),),
        timeout=1.0,
    )
    deadline = ExecutionDeadline.start(1.0, monotonic=clock)
    clock_now[0] = 0.7499

    SubprocessExecutor().execute_with_deadline(step, deadline)

    assert calls[0]["timeout"] == pytest.approx(0.2501)
    child_environment = calls[0]["env"]
    assert isinstance(child_environment, dict)
    assert child_environment["PGOPTIONS"] == "-c statement_timeout=250"


def test_run_captured_limited_preserves_streams_cwd_and_environment(tmp_path: Path) -> None:
    result = run_captured_limited(
        _python(
            "import os, pathlib, sys; "
            "sys.stdout.buffer.write(pathlib.Path.cwd().name.encode()); "
            "sys.stderr.buffer.write(os.environ['LIMITED_TEST_VALUE'].encode())"
        ),
        cwd=tmp_path,
        env={"LIMITED_TEST_VALUE": "stderr"},
        max_output_bytes=32,
    )

    assert result.returncode == 0
    assert result.stdout == tmp_path.name.encode()
    assert result.stderr == b"stderr"
    assert result.cwd == str(tmp_path)
    assert result.environment == (("LIMITED_TEST_VALUE", "stderr"),)


def test_run_captured_limited_preserves_nonzero_return_code() -> None:
    result = run_captured_limited(
        _python(
            "import sys; sys.stdout.buffer.write(b'out'); sys.stderr.buffer.write(b'err'); sys.exit(9)"
        ),
        max_output_bytes=3,
    )

    assert result.returncode == 9
    assert result.stdout == b"out"
    assert result.stderr == b"err"


def test_run_captured_limited_budget_boundaries() -> None:
    empty = _python("pass")
    assert run_captured_limited(empty, max_output_bytes=0).stdout == b""
    with pytest.raises(ValueError, match="must not be negative"):
        run_captured_limited(empty, max_output_bytes=-1)

    exact = _python("import sys; sys.stdout.buffer.write(b'123'); sys.stderr.buffer.write(b'xy')")
    result = run_captured_limited(exact, max_output_bytes=3)
    assert result.stdout == b"123"
    assert result.stderr == b"xy"

    with pytest.raises(ProcessExecutionError, match="output exceeded"):
        run_captured_limited(exact, max_output_bytes=2)

    with pytest.raises(ProcessExecutionError, match="output exceeded"):
        run_captured_limited(
            _python("import sys; sys.stdout.buffer.write(b'x')"), max_output_bytes=0
        )


def test_run_captured_limited_timeout_and_spawn_errors() -> None:
    with pytest.raises(ProcessTimeoutError) as timeout:
        run_captured_limited(
            _python("import time; time.sleep(10)"), timeout=0.01, max_output_bytes=1
        )
    assert timeout.value.timeout == 0.01

    with pytest.raises(ProcessSpawnError) as spawn_error:
        run_captured_limited(("/definitely/missing/odoo-sdk-executable",), max_output_bytes=1)
    assert spawn_error.value.argv == ("/definitely/missing/odoo-sdk-executable",)


def test_run_captured_limited_active_context_uses_one_recorded_launch() -> None:
    step = prepared_step(
        _python("import sys; sys.stdout.buffer.write(b'recorded')"),
        step_id="limited",
        text=False,
    )
    expected = ProcessResult(
        argv=step.argv,
        returncode=0,
        stdout=b"recorded",
        stderr=b"",
        duration=0.0,
        cwd=step.cwd,
        environment=step.environment,
    )
    executor = RecordingExecutor(results={"limited": expected})

    def callback(_context: object) -> ProcessResult:
        result = run_captured_limited(
            step.argv,
            max_output_bytes=32,
            step_id=step.step_id,
        )
        with pytest.raises(DuplicateStepError):
            run_captured_limited(step.argv, max_output_bytes=32, step_id=step.step_id)
        return result

    command = prepared_command(callback, (step,), executor=executor)
    result = command.run()

    assert result is expected
    assert executor.executed == [step]


def test_process_prepared_rejects_same_argv_with_changed_private_inputs() -> None:
    step = prepared_step(
        ("tool", "same-argv"),
        step_id="captured",
        cwd="/captured",
        env={"PRIVATE": "captured-value"},
        stdin=b"captured-input",
        timeout=2.0,
        mode="captured",
    )
    result = ProcessResult(
        argv=step.argv,
        returncode=0,
        stdout="ok",
        stderr="",
        duration=0.0,
        cwd=step.cwd,
        environment=step.environment,
    )

    replacements = (
        replace(step, cwd="/substituted"),
        replace(step, environment_snapshot=(("PRIVATE", "substituted-value"),)),
        replace(step, stdin=b"substituted-input"),
        replace(step, timeout=3.0),
        replace(step, mode="inherited"),
        replace(step, step_id="different-id"),
    )
    for replacement in replacements:
        executor = RecordingExecutor(results={step.step_id: result})

        def reject(run_context: RunContext[object], requested: object = replacement) -> None:
            with pytest.raises(UnplannedStepError):
                run_context.process_prepared(requested)  # type: ignore[arg-type]
            run_context.skip(step.step_id)

        prepared_command(reject, (step,), executor=executor).run()
        assert executor.executed == []

    executor = RecordingExecutor(results={step.step_id: result})
    exact = prepared_command(
        lambda context: context.process_prepared(step),
        (step,),
        executor=executor,
    )
    assert exact.run() is result
    assert executor.executed == [step]
