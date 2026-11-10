from __future__ import annotations

import io
import json
import sys
import tempfile
import uuid
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Protocol
from unittest.mock import patch

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.internal.proc import (
    PreparedProcess,
    PreparedStep,
    ProcessResult,
    RecordingExecutor,
    StepEvent,
    StepObserver,
)
from odoo_instance_sdk.resources.instance import OdooInstance


def _instance(tmp_path: Path) -> OdooInstance:
    config_path = tmp_path / "odoo.conf"
    config_path.write_text("[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\n")
    client = OdooClient(config=OdooClientConfig(executable="python3"))
    return client.instance.from_config(config_path)


def _browser_query_process_result(
    prepared: PreparedProcess, *, fail_query: bool = False, truncate_output: bool = False
) -> ProcessResult:
    assert isinstance(prepared, PreparedStep)
    step = prepared
    assert step.stdin is not None

    class User:
        id = 14
        login = "fixture"
        name = "Fixture User"

    class Users:
        def sudo(self) -> Users:
            return self

        def search(self, _domain: object, *, limit: int, order: str) -> list[User]:
            assert limit == 100 and order == "id"
            if fail_query:
                raise RuntimeError("private query failure")
            return [User()]

    class Environment:
        def __init__(self) -> None:
            self.cr = SimpleNamespace(commit=lambda: None, rollback=lambda: None)

        def __getitem__(self, _key: str) -> Users:
            return Users()

    output = io.StringIO()
    returncode = 0
    try:
        with redirect_stdout(output):
            exec(
                step.stdin.decode("utf-8"),
                {
                    "env": Environment(),
                    "odoo": SimpleNamespace(api=SimpleNamespace(SUPERUSER_ID=1)),
                },
            )
    except RuntimeError:
        returncode = 1
    stdout = "Odoo 19 shell initialized\n" + output.getvalue()
    if truncate_output:
        stdout = stdout.rsplit(" __END_PAYLOAD__", 1)[0]
    return ProcessResult(
        argv=step.argv,
        returncode=returncode,
        stdout=stdout,
        stderr="",
        duration=0.01,
        cwd=step.cwd,
        environment=step.environment,
    )


def test_bounded_user_query_returns_typed_safe_projection(tmp_path: Path) -> None:
    instance = _instance(tmp_path)
    executor = RecordingExecutor(result_factory=_browser_query_process_result)
    with patch(
        "odoo_instance_sdk.resources.instance.identity.SubprocessExecutor",
        return_value=executor,
    ):
        command = instance.list_browser_login_users_command("fixture")
        users = command.run()

    assert [(user.id, user.login, user.name) for user in users] == [(14, "fixture", "Fixture User")]
    assert len(command.plan.process_steps) == 1
    assert command.plan.process_steps[0].input_preview == "<redacted>"
    assert "SUPERUSER_ID" not in repr(users)


def test_browser_user_query_refuses_failed_wrapped_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = _instance(tmp_path)
    executor = RecordingExecutor(
        result_factory=lambda step: _browser_query_process_result(step, fail_query=True)
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.identity.SubprocessExecutor",
        lambda: executor,
    )
    command = instance.list_browser_login_users_command("fixture")

    with pytest.raises(RuntimeError, match="query failed") as failure:
        command.run()

    assert "fixture" not in str(failure.value)
    assert "returncode=1" in str(failure.value)
    assert "stdout_bytes=" in str(failure.value)
    assert "framing=prefix-before-wrapper-payload" in str(failure.value)


def test_browser_user_query_rejects_missing_wrapper_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = _instance(tmp_path)
    executor = RecordingExecutor(
        result_factory=lambda step: _browser_query_process_result(step, truncate_output=True)
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.identity.SubprocessExecutor",
        lambda: executor,
    )
    command = instance.list_browser_login_users_command("fixture")

    with pytest.raises(RuntimeError, match="returned invalid data") as failure:
        command.run()

    assert "fixture" not in str(failure.value)
    assert "stdout_bytes=" in str(failure.value)
    assert "framing=prefix-before-wrapper-payload" in str(failure.value)


def test_browser_user_search_rejects_over_128_characters(tmp_path: Path) -> None:
    instance = _instance(tmp_path)
    with pytest.raises(ValueError, match="too long"):
        instance.list_browser_login_users_command("x" * 129)


def test_browser_user_search_accepts_128_and_orders_by_stable_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = _instance(tmp_path)
    captured: dict[str, object] = {}

    def capture_shell_script(_self: OdooInstance, source: str, **kwargs: object) -> object:
        captured["source"] = source
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(OdooInstance, "_shell_script_command", capture_shell_script)
    instance.list_browser_login_users_command("x" * 128)
    source = str(captured["source"])
    assert "order='id'" in source
    assert "order='name, id'" not in source
    assert captured["argv"] == ("x" * 128,)


def test_browser_user_query_uses_odoo_shell_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = _instance(tmp_path)
    captured: dict[str, object] = {}

    class User:
        id = 14
        login = "fixture"
        name = "Fixture User"

    class Users:
        def sudo(self) -> Users:
            return self

        def search(self, domain: object, *, limit: int, order: str) -> list[User]:
            captured.update(domain=domain, limit=limit, order=order)
            return [User()]

    class Env:
        def __getitem__(self, _key: str) -> Users:
            return Users()

    fake_odoo = ModuleType("odoo")
    setattr(fake_odoo, "api", SimpleNamespace(SUPERUSER_ID=1))

    def capture_shell_script(_self: OdooInstance, source: str, **_kwargs: object) -> object:
        output = io.StringIO()
        with patch.object(sys, "argv", ["odoo-shell", "fixture"]), redirect_stdout(output):
            exec(source, {"env": Env(), "odoo": fake_odoo, "sys": sys, "json": json})
        captured["output"] = output.getvalue()
        return object()

    monkeypatch.setattr(OdooInstance, "_shell_script_command", capture_shell_script)
    instance.list_browser_login_users_command("fixture")

    assert json.loads(str(captured["output"])) == [
        {"id": 14, "login": "fixture", "name": "Fixture User"}
    ]
    domain = captured["domain"]
    assert isinstance(domain, list)
    assert ("id", "!=", 1) in domain
    assert captured["limit"] == 100
    assert captured["order"] == "id"


def test_session_command_plan_never_contains_a_sid(tmp_path: Path) -> None:
    instance = _instance(tmp_path)
    command = instance.create_browser_session_command(14)
    assert command.plan.steps
    assert "fixture-sid" not in repr(command)
    assert "session_token" not in repr(command.plan)
    assert command.plan.process_steps[0].input_preview == "<redacted>"


class _TestSessionExecutor(Protocol):
    outcome: str
    step_ids: list[str]


class _TestSessionStore(Protocol):
    def get(self, sid: str) -> object | None: ...


@dataclass
class _BrowserSessionHarness:
    instance: OdooInstance
    handoff_dir: Path
    handoff_file: Path
    store: _TestSessionStore
    session_root: SimpleNamespace
    executor: _TestSessionExecutor
    removed_sessions: list[dict[str, object]]
    session: dict[str, object]
    events: list[StepEvent]


def _browser_session_harness(  # noqa: C901
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> _BrowserSessionHarness:
    instance = _instance(tmp_path)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(uuid, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    handoff_dir = tmp_path / "odcli-browser-session-fixed"
    handoff_file = handoff_dir / "sid"
    removed_sessions: list[dict[str, object]] = []
    session = {"db": "fixture-db", "uid": 14}

    class Session(dict[str, object]):
        sid = "private-sid"

    class FilesystemSessionStore:
        def __init__(self) -> None:
            self.sessions: dict[str, Session] = {}

        def new(self) -> Session:
            return Session()

        def save(self, value: Session) -> None:
            self.sessions[value.sid] = value

        def get(self, sid: str) -> Session | None:
            return self.sessions.get(sid)

        def delete(self, value: Session) -> None:
            self.sessions.pop(value.sid, None)
            removed_sessions.append(session)

    store = FilesystemSessionStore()

    class User:
        id = 14
        login = "fixture"
        active = True
        share = False

        def exists(self) -> User:
            return self

        def context_get(self) -> dict[str, object]:
            return {}

        def _compute_session_token(self, _value: Session) -> str:
            return "fixture-token"

    class Users:
        def sudo(self) -> Users:
            return self

        def browse(self, _user_id: int) -> User:
            return User()

    class Env:
        cr = SimpleNamespace(dbname="fixture-db", commit=lambda: None, rollback=lambda: None)

        def __getitem__(self, _key: str) -> Users:
            return Users()

    fake_http = ModuleType("odoo.http")
    session_root = SimpleNamespace(session_store=store)
    setattr(fake_http, "root", session_root)
    fake_odoo = ModuleType("odoo")
    setattr(fake_odoo, "api", SimpleNamespace(SUPERUSER_ID=1))
    setattr(fake_odoo, "http", fake_http)

    class BrowserSessionExecutor:
        def __init__(self) -> None:
            self.step_ids: list[str] = []
            self.outcome = "success"

        def execute(
            self,
            step: PreparedStep,
            *,
            observer: StepObserver | None = None,
            observe_output: bool = False,
        ) -> ProcessResult:
            del observe_output
            self.step_ids.append(step.step_id)
            if observer is not None:
                observer(
                    StepEvent(
                        step_id=step.step_id,
                        kind="stderr",
                        chunk=f"handoff diagnostics: {handoff_file}",
                    )
                )
            if (
                step.step_id == "instance.browser_session.cleanup"
                and self.outcome == "cleanup-failure"
            ):
                raise RuntimeError(f"cleanup failed for {handoff_file}")
            with (
                patch.dict(sys.modules, {"odoo": fake_odoo, "odoo.http": fake_http}),
                patch.object(sys, "argv", ["odoo-shell"]),
            ):
                exec(
                    (step.stdin or b"").decode("utf-8"),
                    {"env": Env(), "odoo": fake_odoo},
                )
            if step.step_id == "instance.shell_script":
                if self.outcome in {"timeout", "cleanup-failure"}:
                    raise TimeoutError("timeout private-sid")
                if self.outcome == "invalid-file":
                    handoff_file.chmod(0o644)
                return ProcessResult(
                    argv=("python3", "shell"),
                    returncode=1 if self.outcome == "process" else 0,
                    stdout="",
                    stderr=f"failure {handoff_file} private-sid",
                    duration=0.01,
                    cwd=None,
                    environment=(),
                )
            assert step.step_id == "instance.browser_session.cleanup"
            return ProcessResult(
                argv=("python3", "shell"),
                returncode=0,
                stdout="",
                stderr="",
                duration=0.01,
                cwd=None,
                environment=(),
            )

    executor = BrowserSessionExecutor()
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.browser_login.SubprocessExecutor",
        lambda: executor,
    )
    return _BrowserSessionHarness(
        instance,
        handoff_dir,
        handoff_file,
        store,
        session_root,
        executor,
        removed_sessions,
        session,
        [],
    )


def _assert_private_execution(harness: _BrowserSessionHarness) -> None:
    assert not harness.handoff_dir.exists()
    event_text = repr(harness.events)
    assert str(harness.handoff_file) not in event_text
    assert "private-sid" not in event_text


def test_session_command_success_keeps_session_and_cleans_private_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _browser_session_harness(tmp_path, monkeypatch)
    command = harness.instance.create_browser_session_command(14)
    plan_repr = repr(command.plan)
    assert str(harness.handoff_file) not in plan_repr
    assert "private-sid" not in plan_repr
    assert [step.step_id for step in command.plan.process_steps] == [
        "instance.shell_script",
        "instance.browser_session.cleanup",
    ]

    credential = command.run(observer=harness.events.append)

    assert credential.sid == "private-sid"
    assert "private-sid" not in repr(credential)
    assert harness.store.get("private-sid") is not None
    assert harness.executor.step_ids == ["instance.shell_script"]
    assert harness.removed_sessions == []
    _assert_private_execution(harness)


def test_session_command_process_failure_removes_known_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _browser_session_harness(tmp_path, monkeypatch)
    harness.executor.outcome = "process"

    with pytest.raises(RuntimeError, match="session creation failed") as error:
        harness.instance.create_browser_session_command(14).run(observer=harness.events.append)

    assert str(harness.handoff_file) not in str(error.value)
    assert "private-sid" not in str(error.value)
    assert harness.executor.step_ids == [
        "instance.shell_script",
        "instance.browser_session.cleanup",
    ]
    assert harness.removed_sessions == [harness.session]
    _assert_private_execution(harness)


def test_session_command_timeout_removes_known_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _browser_session_harness(tmp_path, monkeypatch)
    harness.executor.outcome = "timeout"

    with pytest.raises(TimeoutError, match="session creation timed out") as error:
        harness.instance.create_browser_session_command(14).run(observer=harness.events.append)

    assert str(harness.handoff_file) not in str(error.value)
    assert "private-sid" not in str(error.value)
    assert harness.executor.step_ids == [
        "instance.shell_script",
        "instance.browser_session.cleanup",
    ]
    assert harness.removed_sessions == [harness.session]
    _assert_private_execution(harness)


def test_session_command_rejects_unsafe_handoff_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _browser_session_harness(tmp_path, monkeypatch)
    harness.executor.outcome = "invalid-file"

    with pytest.raises(RuntimeError, match="handoff is invalid") as error:
        harness.instance.create_browser_session_command(14).run(observer=harness.events.append)

    assert str(harness.handoff_file) not in str(error.value)
    assert "private-sid" not in str(error.value)
    assert harness.executor.step_ids == [
        "instance.shell_script",
        "instance.browser_session.cleanup",
    ]
    assert harness.removed_sessions == []
    _assert_private_execution(harness)


def test_session_command_preserves_timeout_when_best_effort_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _browser_session_harness(tmp_path, monkeypatch)
    harness.executor.outcome = "cleanup-failure"

    with pytest.raises(TimeoutError, match="session creation timed out") as error:
        harness.instance.create_browser_session_command(14).run(observer=harness.events.append)

    assert str(harness.handoff_file) not in str(error.value)
    assert "private-sid" not in str(error.value)
    assert harness.executor.step_ids == [
        "instance.shell_script",
        "instance.browser_session.cleanup",
    ]
    assert harness.removed_sessions == []
    _assert_private_execution(harness)


def test_session_command_checks_supported_store_before_creating_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _browser_session_harness(tmp_path, monkeypatch)
    harness.session_root.session_store = object()

    with pytest.raises(RuntimeError, match="unsupported session store"):
        harness.instance.create_browser_session_command(14).run(observer=harness.events.append)

    assert harness.executor.step_ids == [
        "instance.shell_script",
        "instance.browser_session.cleanup",
    ]
    assert harness.removed_sessions == []
    _assert_private_execution(harness)
