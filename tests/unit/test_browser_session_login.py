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
from odoo_instance_sdk.internal.proc import ProcessResult, RecordingExecutor
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


@pytest.mark.parametrize("failure", ["process", "timeout", "invalid-file"])
def test_session_command_cleans_private_file_and_known_session_best_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    instance = _instance(tmp_path)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(uuid, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    handoff_dir = tmp_path / "odcli-browser-session-fixed"
    handoff_file = handoff_dir / "sid"
    captured: dict[str, object] = {}

    def capture_shell_script(_self: OdooInstance, _source: str, **kwargs: object) -> object:
        captured.update(kwargs)
        return kwargs["context_callback_override"]

    cleanup_calls: list[tuple[str, tuple[str, ...]]] = []
    removed_sessions: list[dict[str, object]] = []

    def cleanup_shell_script(
        _self: OdooInstance,
        source: str,
        *,
        argv: tuple[str, ...] = (),
        **_kwargs: object,
    ) -> SimpleNamespace:
        cleanup_calls.append((source, argv))
        session = {"db": "fixture-db", "uid": 14}

        class FilesystemSessionStore:
            def get(self, sid: str) -> dict[str, object] | None:
                return session if sid == "private-sid" else None

            def delete(self, current: dict[str, object]) -> None:
                removed_sessions.append(current)

        fake_odoo = ModuleType("odoo")
        fake_http = ModuleType("odoo.http")
        setattr(fake_http, "root", SimpleNamespace(session_store=FilesystemSessionStore()))
        setattr(fake_odoo, "http", fake_http)
        with (
            patch.dict(sys.modules, {"odoo": fake_odoo, "odoo.http": fake_http}),
            patch.object(sys, "argv", ["odoo-shell", *argv]),
        ):
            exec(
                source,
                {"env": SimpleNamespace(cr=SimpleNamespace(dbname="fixture-db"))},
            )

        def fail_cleanup() -> None:
            raise RuntimeError("cleanup shell failure")

        return SimpleNamespace(run=fail_cleanup)

    monkeypatch.setattr(OdooInstance, "_shell_script_command", capture_shell_script)
    monkeypatch.setattr(OdooInstance, "run_shell_script_command", cleanup_shell_script)
    callback = instance.create_browser_session_command(14)
    execute = callback  # The patched command seam returns the context callback.

    class Context:
        def process(self, _step_id: str) -> ProcessResult:
            handoff_dir.mkdir(mode=0o700)
            handoff_file.write_text("private-sid", encoding="ascii")
            handoff_file.chmod(0o600)
            if failure == "invalid-file":
                handoff_file.chmod(0o644)
            if failure == "timeout":
                raise TimeoutError("simulated process timeout")
            return ProcessResult(
                argv=("python3", "shell"),
                returncode=1 if failure == "process" else 0,
                stdout="",
                stderr="private-sid must not be surfaced",
                duration=0.01,
                cwd=None,
                environment=(),
            )

    expected_exception: type[Exception] = TimeoutError if failure == "timeout" else RuntimeError
    expected_message = {
        "process": "session creation failed",
        "timeout": "simulated process timeout",
        "invalid-file": "handoff is invalid",
    }[failure]
    with pytest.raises(expected_exception, match=expected_message) as error:
        execute(Context())  # type: ignore[operator]

    assert "private-sid" not in str(error.value)
    assert not handoff_dir.exists()
    assert len(cleanup_calls) == 1
    cleanup_source, cleanup_argv = cleanup_calls[0]
    assert "store.delete(session)" in cleanup_source
    assert "session.get('db') == env.cr.dbname" in cleanup_source
    assert "session.get('uid') == expected_uid" in cleanup_source
    assert cleanup_argv == (str(handoff_file), "14")
    assert removed_sessions == (
        [{"db": "fixture-db", "uid": 14}] if failure != "invalid-file" else []
    )
    assert "private-sid" not in repr(captured)


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
