from __future__ import annotations

import sys
import threading
from dataclasses import replace
from pathlib import Path
from typing import cast

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
from odoo_instance_sdk.internal.proc.redaction import (
    IncrementalStreamRedactor,
    redacted_projection,
)


def _python(source: str) -> tuple[str, ...]:
    return (sys.executable, "-c", source)


_REDACTION_CASES = (
    pytest.param("password=assignment-secret", (), id="password-assignment"),
    pytest.param("token: assignment-token", (), id="token-assignment"),
    pytest.param("Bearer bearer-secret", (), id="bearer"),
    pytest.param("Basic YWJjOnNlY3JldA==", (), id="basic"),
    pytest.param("Authorization: header-secret", (), id="authorization-header"),
    pytest.param("Proxy-Authorization: proxy-secret", (), id="proxy-header"),
    pytest.param("Cookie: cookie-secret", (), id="cookie-header"),
    pytest.param("Set-Cookie: set-cookie-secret", (), id="set-cookie-header"),
    pytest.param("https://user:uri-secret@example.test/path", (), id="uri-userinfo"),
    pytest.param(
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature",
        (),
        id="jwt",
    ),
    pytest.param("runtime runtime-secret", ("runtime-secret",), id="runtime-secret"),
)


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
    assert events[0].kind == "started"
    assert {event.kind for event in events[1:-1]} == {"stdout", "stderr"}
    assert events[-1].kind == "completed"
    assert all(secret not in (event.chunk or "") for event in events)
    assert events[0].step_id == events[-1].step_id == step.step_id


def test_observed_output_arrives_before_captured_process_completion() -> None:
    ready = threading.Event()
    events: list[StepEvent] = []
    command: PreparedCommand[ProcessResult] = prepared_command(
        lambda context: context.process("streaming"),
        (
            PreparedStep(
                step_id="streaming",
                argv=_python("import sys, time; print('early', flush=True); time.sleep(0.4)"),
            ),
        ),
        executor=SubprocessExecutor(),
    )

    def observe(event: StepEvent) -> None:
        events.append(event)
        if event.kind == "stdout":
            ready.set()

    result: list[ProcessResult] = []
    thread = threading.Thread(
        target=lambda: result.append(command.run(observer=observe, observe_output=True))
    )
    thread.start()
    assert ready.wait(timeout=0.2)
    assert thread.is_alive()
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert result[0].stdout == "early\n"
    assert [event.kind for event in events] == ["started", "stdout", "completed"]


def test_streaming_redaction_withholds_structural_and_runtime_candidates() -> None:
    runtime_secret = "runtime-split-secret"
    step = PreparedStep(
        step_id="streaming-redaction",
        argv=_python(
            "import sys, time; "
            "sys.stdout.write('Authorization: Bearer '); sys.stdout.flush(); "
            "time.sleep(0.05); "
            "sys.stdout.write('structural-secret\\npublic=' + 'runtime-split-'); "
            "sys.stdout.flush(); time.sleep(0.05); "
            "sys.stdout.write('secret\\n'); sys.stdout.flush()"
        ),
        secret_values=(runtime_secret,),
    )
    events: list[StepEvent] = []

    result: ProcessResult = prepared_command(
        lambda context: context.process(step.step_id),
        (step,),
        executor=SubprocessExecutor(),
    ).run(observer=events.append, observe_output=True)

    assert result.stdout is not None
    assert "structural-secret" in result.stdout
    projected = "".join(event.chunk or "" for event in events if event.kind == "stdout")
    assert "structural-secret" not in projected
    assert "runtime-split-secret" not in projected
    assert projected == "Authorization: <redacted>\npublic=<redacted>\n"


def test_timeout_retains_newest_bounded_sanitized_tails() -> None:
    secret = "timeout-tail-secret"
    step = PreparedStep(
        step_id="timeout-tail",
        argv=_python(
            "import sys, time; "
            "sys.stdout.write('o'*10000 + ' stdout-final=' + 'timeout-tail-secret\\n'); "
            "sys.stdout.flush(); "
            "sys.stderr.write('stderr-final=timeout-tail-secret\\n'); "
            "sys.stderr.flush(); time.sleep(10)"
        ),
        timeout=0.1,
        secret_values=(secret,),
    )
    with pytest.raises(ProcessTimeoutError) as raised:
        SubprocessExecutor().execute(step)

    failure = raised.value
    assert failure.duration >= failure.timeout
    assert failure.stdout_tail.endswith("stdout-final=<redacted>\n")
    assert failure.stderr_tail.endswith("stderr-final=<redacted>\n")
    assert failure.stdout_truncated is True
    assert failure.stderr_truncated is False
    assert len(failure.stdout_tail.encode()) <= 8192
    assert len(failure.stderr_tail.encode()) <= 8192
    assert secret not in str(failure)


def test_large_stdin_is_written_exactly_while_output_is_drained() -> None:
    payload = bytes(range(256)) * 32768
    result = run_captured(
        _python(
            "import sys; "
            "sys.stdout.buffer.write(b'x' * 131072); sys.stdout.flush(); "
            "data = sys.stdin.buffer.read(); "
            "sys.stdout.write('\\n' + str(len(data)) + ':' + str(data == bytes(range(256)) * 32768))"
        ),
        stdin=payload,
        timeout=5.0,
    )

    assert result.returncode == 0
    assert result.stdout is not None
    assert isinstance(result.stdout, str)
    assert result.stdout.endswith(f"\n{len(payload)}:True")


def test_stdin_write_failure_terminates_the_owned_child(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.proc.executor.os.write",
        lambda *_args: (_ for _ in ()).throw(OSError("synthetic stdin failure")),
    )

    with pytest.raises(ProcessExecutionError, match="stdin write failed"):
        run_captured(
            _python("import time; time.sleep(10)"),
            stdin=b"captured-input",
            timeout=5.0,
        )


def test_ctrl_c_closes_the_pump_and_reports_failed_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InterruptingSelector:
        def register(self, *_args: object, **_kwargs: object) -> None:
            return

        def get_map(self) -> dict[int, object]:
            return {1: object()}

        def select(self, _timeout: float | None = None) -> list[object]:
            raise KeyboardInterrupt

        def close(self) -> None:
            return

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.proc.executor.selectors.DefaultSelector",
        InterruptingSelector,
    )
    step = PreparedStep(
        step_id="ctrl-c",
        argv=_python("import time; time.sleep(10)"),
    )
    events: list[StepEvent] = []

    with pytest.raises(KeyboardInterrupt):
        SubprocessExecutor().execute(step, observer=events.append, observe_output=True)

    assert [event.kind for event in events] == ["started", "failed"]
    assert events[-1].error == "interrupted"


def test_recording_executor_streams_chunks_and_preserves_result_parity() -> None:
    step = PreparedStep(step_id="chunked", argv=("tool",), secret_values=("secret",))
    expected = ProcessResult(
        argv=step.argv,
        returncode=7,
        stdout="first\nsecret\n",
        stderr="error\n",
        duration=0.25,
        cwd=None,
        environment=(),
    )
    executor = RecordingExecutor(
        results={step.step_id: expected},
        stdout_chunks=("first", "\nsecret\n"),
        stderr_chunks=("error", "\n"),
    )
    events: list[StepEvent] = []

    command: PreparedCommand[ProcessResult] = prepared_command(
        lambda context: context.process(step.step_id),
        (step,),
        executor=executor,
    )
    result = command.run(observer=events.append, observe_output=True)

    assert result is expected
    assert [event.kind for event in events] == [
        "started",
        "stdout",
        "stderr",
        "completed",
    ]
    assert "".join(event.chunk or "" for event in events if event.kind == "stdout") == (
        "first\n<redacted>\n"
    )
    assert "".join(event.chunk or "" for event in events if event.kind == "stderr") == "error\n"


def test_recording_executor_observer_failures_and_disabled_observation_are_nonsemantic() -> None:
    step = PreparedStep(step_id="observer-isolation", argv=("tool",))
    expected = ProcessResult(step.argv, 0, "output\n", "", 0.0, None, ())
    executor = RecordingExecutor(
        results={step.step_id: expected},
        stdout_chunks=("output", "\n"),
    )

    def failing_observer(_event: StepEvent) -> None:
        raise RuntimeError("observer failure")

    command: PreparedCommand[ProcessResult] = prepared_command(
        lambda context: context.process(step.step_id),
        (step,),
        executor=executor,
    )
    assert command.run(observer=failing_observer, observe_output=True) is expected

    events: list[StepEvent] = []
    executor = RecordingExecutor(
        results={step.step_id: expected},
        stdout_chunks=("output", "\n"),
    )
    command = prepared_command(
        lambda context: context.process(step.step_id),
        (step,),
        executor=executor,
    )
    assert command.run(observer=events.append, observe_output=False) is expected
    assert [event.kind for event in events] == ["started", "completed"]


@pytest.mark.parametrize(("payload", "secrets"), _REDACTION_CASES)
def test_incremental_redaction_matches_canonical_projection_at_every_byte_split(
    payload: str, secrets: tuple[str, ...]
) -> None:
    raw = (payload + "\n").encode("utf-8")
    expected = cast("str", redacted_projection(payload, secrets=secrets, field="stdout")) + "\n"

    for split in range(1, len(raw)):
        redactor = IncrementalStreamRedactor(secrets=secrets, field="stdout")
        emitted = redactor.feed(raw[:split])
        emitted += redactor.feed(raw[split:])
        emitted += redactor.flush()

        assert emitted == expected
        for secret in secrets:
            assert secret not in emitted
        assert "<redacted>" in emitted


def test_incremental_redaction_handles_multibyte_decoder_boundaries() -> None:
    secret = "unicode-runtime-secret"
    raw = f"префикс {secret} суффикс\n".encode()
    expected = (
        cast(
            "str",
            redacted_projection(f"префикс {secret} суффикс", secrets=(secret,), field="stdout"),
        )
        + "\n"
    )

    for split in range(1, len(raw)):
        redactor = IncrementalStreamRedactor(secrets=(secret,), field="stdout")
        emitted = redactor.feed(raw[:split]) + redactor.feed(raw[split:]) + redactor.flush()
        assert emitted == expected
        assert secret not in emitted


@pytest.mark.parametrize(("payload", "secrets"), _REDACTION_CASES)
@pytest.mark.parametrize(
    ("terminal_kind", "terminal_error"),
    [("completed", None), ("failed", "timeout"), ("failed", "interrupted")],
)
def test_incremental_redaction_terminal_flush_never_releases_incomplete_secret(
    payload: str,
    secrets: tuple[str, ...],
    terminal_kind: str,
    terminal_error: str | None,
) -> None:
    from odoo_instance_sdk.internal.proc import _StreamingStepObserver

    step = PreparedStep(step_id="terminal-flush", argv=("tool",), secret_values=secrets)
    events: list[StepEvent] = []
    observer = _StreamingStepObserver(events.append, step)

    observer(StepEvent(step_id=step.step_id, kind="started"))
    split = max(1, len(payload) // 2)
    observer(StepEvent(step_id=step.step_id, kind="stdout", chunk=payload[:split]))
    observer(StepEvent(step_id=step.step_id, kind="stdout", chunk=payload[split:]))
    observer(
        StepEvent(
            step_id=step.step_id,
            kind=terminal_kind,  # type: ignore[arg-type]
            returncode=0 if terminal_kind == "completed" else None,
            error=terminal_error,
        )
    )

    projected = "".join(event.chunk or "" for event in events if event.kind == "stdout")
    expected = cast("str", redacted_projection(payload, secrets=secrets, field="stdout"))
    assert projected == expected
    for secret in secrets:
        assert secret not in projected
    assert "<redacted>" in projected


def test_captured_stdin_closes_cleanly_when_child_exits_before_consuming_it() -> None:
    result = run_captured(
        _python("import sys; sys.exit(0)"),
        stdin=b"x" * (1024 * 1024),
        timeout=2.0,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_timeout_closes_unconsumed_stdin_on_windows_process_cleanup_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.executor.sys.platform", "win32")
    with pytest.raises(ProcessTimeoutError) as raised:
        run_captured(
            _python("import time; time.sleep(10)"),
            stdin=b"x" * (1024 * 1024),
            timeout=0.05,
        )
    assert raised.value.elapsed >= raised.value.timeout


def test_limited_capture_delegates_to_the_common_pump(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[PreparedStep, int | None]] = []

    def fake_pump(
        step: PreparedStep,
        *,
        timeout: float | None,
        environment_snapshot: tuple[tuple[str, str], ...],
        observer: StepObserver | None,
        observe_output: bool,
        max_output_bytes: int | None = None,
    ) -> tuple[int, bytes, bytes, float]:
        del timeout, environment_snapshot, observer, observe_output
        calls.append((step, max_output_bytes))
        return 9, b"out", b"err", 0.5

    monkeypatch.setattr("odoo_instance_sdk.internal.proc.executor._run_pump", fake_pump)
    result = run_captured_limited(("tool",), max_output_bytes=3)

    assert result.returncode == 9
    assert result.stdout == b"out"
    assert result.stderr == b"err"
    assert calls and calls[0][1] == 3


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

    def fake_pump(
        step: PreparedStep,
        *,
        timeout: float | None,
        environment_snapshot: tuple[tuple[str, str], ...],
        observer: StepObserver | None,
        observe_output: bool,
        max_output_bytes: int | None = None,
    ) -> tuple[int, bytes, bytes, float]:
        calls.append(
            {
                "argv": list(step.argv),
                "timeout": timeout,
                "env": dict(environment_snapshot),
                "observer": observer,
                "observe_output": observe_output,
                "max_output_bytes": max_output_bytes,
            }
        )
        return 0, b"", b"", 0.0

    monkeypatch.setattr("odoo_instance_sdk.internal.proc.executor._run_pump", fake_pump)
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
