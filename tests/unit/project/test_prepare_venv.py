from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal import venv_origin
from odoo_instance_sdk.internal.proc import (
    PreparedStep,
    ProcessOutputLimitError,
    ProcessResult,
    RecordingExecutor,
    SubprocessExecutor,
    prepared_step,
)
from odoo_instance_sdk.internal.proc.redaction import redacted_projection
from odoo_instance_sdk.internal.venv_origin import OriginStage, origin_record_path
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.project_init import prepare_venv_command


def _result(step: PreparedStep, returncode: int = 0, stderr: str = "") -> ProcessResult:
    return ProcessResult(
        argv=step.argv,
        returncode=returncode,
        stdout="",
        stderr=stderr,
        duration=0.0,
        cwd=step.cwd,
        environment=step.environment,
    )


def _project(tmp_path: Path, *, requirements: bool = False) -> Path:
    (tmp_path / ".odcli").mkdir()
    if requirements:
        (tmp_path / "requirements.txt").write_text("requests==2.32.5\n", encoding="utf-8")
    config = ProjectConfig(
        repository_root=tmp_path,
        requirements=("requirements.txt",) if requirements else (),
    )
    (tmp_path / ".odcli" / "project.toml").write_text(config.to_manifest(), encoding="utf-8")
    return tmp_path


def test_prepare_existing_empty_venv_persists_interpreter_after_success(tmp_path: Path) -> None:
    (tmp_path / ".odcli").mkdir()
    interpreter = tmp_path / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("placeholder", encoding="utf-8")
    (tmp_path / ".odcli" / "project.toml").write_text(
        ProjectConfig(repository_root=tmp_path).to_manifest(), encoding="utf-8"
    )

    command = prepare_venv_command(tmp_path, python="3.12", venv=".venv")
    assert [step.step_id for step in command.plan.steps] == [
        "prepare-venv.validate",
        "prepare-venv.origin",
        "prepare-venv.manifest",
    ]
    with pytest.raises(ConfigError, match="valid virtual environment"):
        command.run()

    assert ProjectConfig.load(tmp_path).python is None


def test_prepare_validates_existing_venv_before_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    interpreter = project / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("placeholder", encoding="utf-8")
    (interpreter.parent.parent / "pyvenv.cfg").write_text(
        "home = /runtime\nimplementation = CPython\nversion_info = 3.12\nuv = 0.13.0\n",
        encoding="utf-8",
    )
    executor = RecordingExecutor()
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    result = prepare_venv_command(project, python="3.12", venv=".venv").run()

    assert result["interpreter"] == str(interpreter.resolve())
    assert [step.step_id for step in executor.executed] == ["prepare-venv.validate"]
    assert ProjectConfig.load(project).python == interpreter.resolve()


@pytest.mark.parametrize("failed_step", ["compile", "install"])
def test_prepare_failure_keeps_manifest_and_existing_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_step: str
) -> None:
    project = _project(tmp_path, requirements=True)
    interpreter = project / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("placeholder", encoding="utf-8")
    (interpreter.parent.parent / "pyvenv.cfg").write_text(
        "home = /runtime\nimplementation = CPython\nversion_info = 3.12\nuv = 0.13.0\n",
        encoding="utf-8",
    )
    lock = project / ".odcli" / "requirements.lock"
    lock.write_text("old lock\n", encoding="utf-8")
    lock.with_name("requirements.lock.prepare").write_text("stale lock\n", encoding="utf-8")
    manifest_before = (project / ".odcli" / "project.toml").read_bytes()

    def fail_compile(step: object) -> ProcessResult:
        prepared = cast("PreparedStep", step)
        if "compile" in prepared.argv:
            Path(prepared.argv[-1]).write_text("new lock\n", encoding="utf-8")
        return _result(
            prepared,
            1 if failed_step in prepared.step_id else 0,
            f"{failed_step} failed",
        )

    executor = RecordingExecutor(result_factory=fail_compile)
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    with pytest.raises(ConfigError, match=failed_step):
        prepare_venv_command(project, python="3.12", venv=".venv").run()

    assert (project / ".odcli" / "project.toml").read_bytes() == manifest_before
    assert lock.read_text(encoding="utf-8") == "old lock\n"
    assert not (project / ".odcli" / "requirements.lock.prepare").exists()


def test_prepare_publishes_lock_only_after_install_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, requirements=True)
    interpreter = project / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("placeholder", encoding="utf-8")
    (interpreter.parent.parent / "pyvenv.cfg").write_text(
        "home = /runtime\nimplementation = CPython\nversion_info = 3.12\nuv = 0.13.0\n",
        encoding="utf-8",
    )

    def write_compile_output(step: object) -> ProcessResult:
        prepared = cast("PreparedStep", step)
        if "compile" in prepared.argv:
            Path(prepared.argv[-1]).write_text("new lock\n", encoding="utf-8")
        return _result(prepared)

    executor = RecordingExecutor(result_factory=write_compile_output)
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)
    prepare_venv_command(project, python="3.12", venv=".venv").run()

    assert (project / ".odcli" / "requirements.lock").read_text(encoding="utf-8") == "new lock\n"
    assert not (project / ".odcli" / "requirements.lock.prepare").exists()


def test_prepare_plan_projects_windows_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    from odoo_instance_sdk.resources.environment import checkout_artifacts

    venv = project / ".venv"
    artifacts = cast("Any", checkout_artifacts)
    monkeypatch.setattr(artifacts.os, "name", "nt")
    assert checkout_artifacts._owned_python_executable(venv).endswith(".venv/Scripts/python.exe")


def test_windows_project_ignore_backend_avoids_posix_descriptor_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import PosixPath

    from odoo_instance_sdk.internal import project_manifest

    manifest_module = cast("Any", project_manifest)
    monkeypatch.setattr(manifest_module, "Path", PosixPath)
    monkeypatch.setattr(manifest_module.os, "name", "nt")
    ignore = project_manifest.ensure_project_ignore(tmp_path)

    assert ignore.read_text(encoding="utf-8") == ".env\nodoo.conf\n"


def test_prepare_rejects_stale_manifest_before_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _project(tmp_path)
    command = prepare_venv_command(project, python="3.12", venv=".venv")
    (project / ".odcli" / "project.toml").write_text(
        ProjectConfig(repository_root=project, python="other").to_manifest(), encoding="utf-8"
    )
    executor = RecordingExecutor()
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    with pytest.raises(ConfigError, match="manifest changed"):
        command.run()

    assert not executor.executed
    assert ProjectConfig.load(project).python == "other"


def test_prepare_persists_origin_on_real_platform_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    sdk_root = tmp_path / "sdk-root"
    sdk_root.mkdir()
    monkeypatch.setattr(venv_origin, "get_user_root", lambda ensure_exists: sdk_root)
    uv = shutil.which("uv")
    assert uv is not None

    result = prepare_venv_command(project, python=sys.executable, venv=".venv").run()

    origin = origin_record_path(project)
    record = json.loads(origin.read_text(encoding="utf-8"))
    assert result["interpreter"] == record["interpreter"]
    assert record["candidate_kind"] in {"regular", "symlink"}
    assert record["candidate_digest"]
    assert record["venv_cfg_digest"]


def test_windows_origin_backend_uses_checked_path_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sdk_root = tmp_path / "sdk-root"
    sdk_root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(venv_origin, "get_user_root", lambda ensure_exists: sdk_root)
    monkeypatch.setattr(os, "name", "nt")

    stage = venv_origin.stage_origin_bytes(project, b"windows-origin\n")
    venv_origin.publish_origin(stage)

    assert venv_origin.read_origin_bytes(project) == b"windows-origin\n"
    assert not (sdk_root / "venv-origins" / stage.staged_name).exists()


def _windows_output_step(
    source: str,
    *,
    per_stream: int,
    combined: int,
    secret_values: tuple[str, ...] = (),
) -> PreparedStep:
    return prepared_step(
        (sys.executable, "-c", source),
        step_id="real-platform-output-limit",
        text=False,
        max_output_bytes=per_stream,
        max_combined_output_bytes=combined,
        secret_values=secret_values,
    )


def _windows_process_spy(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    spawned: list[Any] = []
    original_popen = subprocess.Popen

    def capture_popen(*args: Any, **kwargs: Any) -> Any:
        process = original_popen(*args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", capture_popen)
    return spawned


def _assert_windows_output_limit(
    step: PreparedStep,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.run.sys.platform", "win32")
    spawned = _windows_process_spy(monkeypatch)
    started = time.perf_counter()
    with pytest.raises(ProcessOutputLimitError, match="output exceeded") as raised:
        SubprocessExecutor().execute(step)
    elapsed = time.perf_counter() - started

    assert elapsed < 3.0
    assert len(cast("str", getattr(raised.value, "stdout_tail")).encode()) <= 8192
    assert len(cast("str", getattr(raised.value, "stderr_tail")).encode()) <= 8192
    assert len(spawned) == 1
    assert spawned[0].poll() is not None


def test_real_platform_backend_observer_failure_returns_process_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.run.sys.platform", "win32")
    secret = "observer-secret"
    step = _windows_output_step(
        "import sys; sys.stdout.buffer.write(b'observer-'); sys.stdout.flush()",
        per_stream=1024,
        combined=2048,
        secret_values=(secret,),
    )

    def failing_observer(_event: Any) -> None:
        raise RuntimeError("observer failure")

    result = SubprocessExecutor().execute(step, observer=failing_observer, observe_output=True)

    assert result.returncode == 0
    assert result.stdout == b"observer-"


def test_real_platform_backend_observer_failure_keeps_output_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.run.sys.platform", "win32")
    secret = "observer-secret"
    step = _windows_output_step(
        f"import sys, time; sys.stdout.buffer.write(b'prefix={secret}\\n' + b'x' * 4096); "
        "sys.stdout.flush(); time.sleep(30)",
        per_stream=64,
        combined=128,
        secret_values=(secret,),
    )
    spawned = _windows_process_spy(monkeypatch)
    thread_failures: list[Any] = []
    monkeypatch.setattr(threading, "excepthook", thread_failures.append)

    def failing_observer(_event: Any) -> None:
        raise RuntimeError("observer failure")

    started = time.perf_counter()
    with pytest.raises(ProcessOutputLimitError, match="output exceeded") as raised:
        SubprocessExecutor().execute(step, observer=failing_observer, observe_output=True)
    elapsed = time.perf_counter() - started
    error = raised.value

    assert elapsed < 3.0
    assert not thread_failures
    assert len(cast("str", getattr(error, "stdout_tail")).encode()) <= 8192
    assert secret not in str(error)
    assert "<redacted>" in cast("str", getattr(error, "stdout_tail"))
    assert len(spawned) == 1
    assert spawned[0].poll() is not None


def test_real_platform_backend_observes_redacted_split_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.run.sys.platform", "win32")
    secret = "split-observer-secret"
    step = _windows_output_step(
        "import sys, time; out=sys.stdout.buffer; err=sys.stderr.buffer; "
        "out.write(b'out-prefix='); out.flush(); time.sleep(.05); "
        "out.write(b'split-observer-secret\\n'); out.flush(); "
        "err.write(b'err-prefix='); err.flush(); time.sleep(.05); "
        "err.write(b'split-observer-secret\\n'); err.flush()",
        per_stream=1024,
        combined=2048,
        secret_values=(secret,),
    )
    events: list[Any] = []

    result = SubprocessExecutor().execute(step, observer=events.append, observe_output=True)

    assert result.returncode == 0
    assert secret not in repr(events)
    for stream, raw in (
        ("stdout", f"out-prefix={secret}\n"),
        ("stderr", f"err-prefix={secret}\n"),
    ):
        chunks = "".join(event.chunk or "" for event in events if event.kind == stream)
        assert chunks == cast("str", redacted_projection(raw, secrets=(secret,), field=stream))
        assert any(event.kind == stream for event in events)


def test_real_platform_backend_reaps_per_stream_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_windows_output_limit(
        _windows_output_step(
            "import sys, time; sys.stdout.buffer.write(b'o' * 4096); "
            "sys.stdout.flush(); time.sleep(30)",
            per_stream=1024,
            combined=8192,
        ),
        monkeypatch,
    )


def test_real_platform_backend_reaps_combined_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_windows_output_limit(
        _windows_output_step(
            "import sys, time; sys.stdout.buffer.write(b'o' * 2048); "
            "sys.stderr.buffer.write(b'e' * 2048); sys.stdout.flush(); "
            "sys.stderr.flush(); time.sleep(30)",
            per_stream=4096,
            combined=1024,
        ),
        monkeypatch,
    )


def test_real_platform_backend_reaps_long_lived_producer_after_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_windows_output_limit(
        _windows_output_step(
            "import sys, time; sys.stdout.buffer.write(b'x' * 4096); "
            "sys.stdout.flush(); time.sleep(30)",
            per_stream=64,
            combined=128,
        ),
        monkeypatch,
    )


class _RollbackScript:
    def __init__(self, original_publish: Any, original_restore: Any) -> None:
        self._original_publish = original_publish
        self._original_restore = original_restore
        self._project_lock = threading.Lock()
        self._first_publish = True
        self.events = {
            name: threading.Event()
            for name in (
                "publish_entered",
                "second_started",
                "second_waiting_for_lock",
                "second_finished",
            )
        }
        self.errors: dict[str, list[BaseException]] = {"first": [], "second": []}
        self.threads: list[threading.Thread] = []
        self.observed_lock_state: list[bool] = []

    @contextmanager
    def lock(self, path: Path) -> Iterator[object]:
        if not path.name.startswith("project-manifest-"):
            yield object()
            return
        if self._project_lock.locked():
            self.events["second_waiting_for_lock"].set()
        with self._project_lock:
            yield object()

    def publish(self, stage: OriginStage) -> None:
        if self._first_publish:
            self._first_publish = False
            self.events["publish_entered"].set()
            if not self.events["second_waiting_for_lock"].wait(timeout=2):
                raise AssertionError("retry did not wait while first run held the lock")
            raise OSError("injected concurrent publish failure")
        self._original_publish(stage)

    def restore(self, *args: object, **kwargs: object) -> None:
        self.observed_lock_state.append(self._project_lock.locked())
        self._original_restore(*args, **kwargs)

    def _run(self, name: str, command: Any) -> None:
        if name == "second":
            self.events["second_started"].set()
        try:
            command.run()
        except BaseException as error:  # pragma: no cover - diagnostic only
            self.errors[name].append(error)
        finally:
            if name == "second":
                self.events["second_finished"].set()

    def run_concurrently(self, first: Any, second: Any) -> None:
        first_thread = threading.Thread(target=self._run, args=("first", first))
        second_thread = threading.Thread(target=self._run, args=("second", second))
        self.threads = [first_thread, second_thread]
        first_thread.start()
        assert self.events["publish_entered"].wait(timeout=2)
        second_thread.start()
        assert self.events["second_started"].wait(timeout=2)
        assert self.events["second_waiting_for_lock"].wait(timeout=2)
        assert not self.events["second_finished"].is_set()
        first_thread.join(timeout=5)
        second_thread.join(timeout=5)


def test_prepare_rollback_is_serialized_before_a_concurrent_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    interpreter = project / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("runtime", encoding="utf-8")
    (interpreter.parent.parent / "pyvenv.cfg").write_text(
        "home = /runtime\nimplementation = CPython\nversion_info = 3.12\nuv = 0.13.0\n",
        encoding="utf-8",
    )
    executor = RecordingExecutor()
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)
    project_init = cast("Any", __import__("odoo_instance_sdk.project_init", fromlist=["*"]))
    script = _RollbackScript(
        project_init.publish_origin, project_init.restore_persisted_prepare_state
    )
    monkeypatch.setattr("odoo_instance_sdk.project_init.exclusive_lock", script.lock)
    monkeypatch.setattr("odoo_instance_sdk.project_init.publish_origin", script.publish)
    monkeypatch.setattr(
        "odoo_instance_sdk.project_init.restore_persisted_prepare_state", script.restore
    )
    first = prepare_venv_command(project, python="first", venv=".venv")
    second = prepare_venv_command(project, python="second", venv=".venv")
    script.run_concurrently(first, second)

    assert all(not thread.is_alive() for thread in script.threads)
    assert len(script.errors["first"]) == 1
    assert isinstance(script.errors["first"][0], OSError)
    assert "concurrent publish failure" in str(script.errors["first"][0])
    assert not script.errors["second"]
    assert script.observed_lock_state == [True]
    record = json.loads(origin_record_path(project).read_text(encoding="utf-8"))
    assert record["requested"] == "second"
