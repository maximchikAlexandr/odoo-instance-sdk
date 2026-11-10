from __future__ import annotations

import json
import sys
import tempfile
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.internal.proc import (
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


def test_bounded_user_query_returns_typed_safe_projection(tmp_path: Path) -> None:
    instance = _instance(tmp_path)
    payload = json.dumps([{"id": 14, "login": "fixture", "name": "Fixture User"}])
    executor = RecordingExecutor(
        results={
            "instance.shell_script": ProcessResult(
                argv=("python3", "shell"),
                returncode=0,
                stdout=payload,
                stderr="",
                duration=0.01,
                cwd=None,
                environment=(),
            )
        }
    )
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


def test_session_command_plan_never_contains_a_sid(tmp_path: Path) -> None:
    instance = _instance(tmp_path)
    command = instance.create_browser_session_command(14)
    assert command.plan.steps
    assert "fixture-sid" not in repr(command)
    assert "session_token" not in repr(command.plan)
    assert command.plan.process_steps[0].input_preview == "<redacted>"


@pytest.mark.parametrize(
    "failure", ["success", "process", "timeout", "invalid-file", "cleanup-failure"]
)
def test_session_command_cleans_private_file_and_known_session_best_effort(  # noqa: C901
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
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
    setattr(fake_http, "root", SimpleNamespace(session_store=store))
    fake_odoo = ModuleType("odoo")
    setattr(fake_odoo, "http", fake_http)

    class BrowserSessionExecutor:
        def __init__(self) -> None:
            self.step_ids: list[str] = []

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
            if step.step_id == "instance.browser_session.cleanup" and failure == "cleanup-failure":
                raise RuntimeError(f"cleanup failed for {handoff_file}")
            with (
                patch.dict(sys.modules, {"odoo": fake_odoo, "odoo.http": fake_http}),
                patch.object(sys, "argv", ["odoo-shell"]),
            ):
                exec(
                    (step.stdin or b"").decode("utf-8"),
                    {"env": Env(), "SUPERUSER_ID": 1},
                )
            if step.step_id == "instance.shell_script":
                if failure in {"timeout", "cleanup-failure"}:
                    raise TimeoutError(f"timeout {handoff_file} private-sid")
                if failure == "invalid-file":
                    handoff_file.chmod(0o644)
                return ProcessResult(
                    argv=("python3", "shell"),
                    returncode=1 if failure == "process" else 0,
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
        "odoo_instance_sdk.resources.instance.identity.SubprocessExecutor",
        lambda: executor,
    )
    command = instance.create_browser_session_command(14)
    plan_repr = repr(command.plan)
    assert str(handoff_file) not in plan_repr
    assert "private-sid" not in plan_repr
    assert [step.step_id for step in command.plan.process_steps] == [
        "instance.shell_script",
        "instance.browser_session.cleanup",
    ]

    observer_events: list[StepEvent] = []
    if failure == "success":
        credential = command.run(observer=observer_events.append)
        assert credential.sid == "private-sid"
        assert "private-sid" not in repr(credential)
        assert store.get("private-sid") is not None
        assert executor.step_ids == ["instance.shell_script"]
        assert removed_sessions == []
    else:
        expected_exception: type[Exception] = (
            TimeoutError if failure in {"timeout", "cleanup-failure"} else RuntimeError
        )
        expected_message = {
            "process": "session creation failed",
            "timeout": "session creation timed out",
            "invalid-file": "handoff is invalid",
            "cleanup-failure": "session creation timed out",
        }[failure]
        with pytest.raises(expected_exception, match=expected_message) as error:
            command.run(observer=observer_events.append)

        assert str(handoff_file) not in str(error.value)
        assert "private-sid" not in str(error.value)
        assert executor.step_ids == ["instance.shell_script", "instance.browser_session.cleanup"]
        assert removed_sessions == (
            [session] if failure not in {"invalid-file", "cleanup-failure"} else []
        )
    assert not handoff_dir.exists()
    event_text = repr(observer_events)
    assert str(handoff_file) not in event_text
    assert "private-sid" not in event_text


def test_session_command_checks_supported_store_before_creating_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = _instance(tmp_path)
    source: list[str] = []

    def capture_shell_script(_self: OdooInstance, script: str, **_kwargs: object) -> object:
        source.append(script)
        return object()

    monkeypatch.setattr(OdooInstance, "_shell_script_command", capture_shell_script)
    instance.create_browser_session_command(14)
    assert "type(store).__name__ != 'FilesystemSessionStore'" in source[0]
    assert source[0].index("unsupported session store") < source[0].index("session = store.new()")
    assert "not user.active or user.share or user.id == SUPERUSER_ID" in source[0]

    class User:
        id = 14
        active = True
        share = False

        def exists(self) -> User:
            return self

    class Users:
        def sudo(self) -> Users:
            return self

        def browse(self, _user_id: int) -> User:
            return User()

    fake_odoo = ModuleType("odoo")
    fake_http = ModuleType("odoo.http")
    setattr(fake_http, "root", SimpleNamespace(session_store=object()))
    setattr(fake_odoo, "http", fake_http)
    namespace = {"env": {"res.users": Users()}, "SUPERUSER_ID": 1}
    with (
        patch.dict(sys.modules, {"odoo": fake_odoo, "odoo.http": fake_http}),
        patch.object(sys, "argv", ["odoo-shell", "/private/sid", "14"]),
        pytest.raises(RuntimeError, match="unsupported session store"),
    ):
        exec(source[0], namespace)
