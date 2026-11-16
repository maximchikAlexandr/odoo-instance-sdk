"""Unit coverage for the shared GreenMail mail service."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from odoo_instance_sdk.exceptions import (
    ConfigError,
    MailConnectionError,
    MailNotOwnedError,
    MailTimeoutError,
)
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.greenmail import (
    create_mailbox,
    mail_recipient,
    mailbox_password,
    owner_token,
    request_path,
    secret_path,
    wait_ready,
    write_private,
)
from odoo_instance_sdk.internal.greenmail_shell import ODOO_MAIL_SCRIPT
from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep
from odoo_instance_sdk.internal.proc.run import ProcessResult
from odoo_instance_sdk.internal.proc.testing import RecordingExecutor
from odoo_instance_sdk.models.command import CommandResult
from odoo_instance_sdk.resources.mail import (
    _converted,
    mail_configure_command,
    mail_down_command,
    mail_status_command,
    mail_up_command,
)

pytestmark = pytest.mark.unit


def _result(stdout: str, *, returncode: int = 0, stderr: str = "") -> ProcessResult:
    return ProcessResult(
        argv=(),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration=0.0,
        cwd=None,
        environment=(),
    )


def _document(*, owner: str, running: bool = True, host_ip: str = "127.0.0.1") -> str:
    ports = {
        "3025/tcp": [{"HostIp": host_ip, "HostPort": "13025"}],
        "3143/tcp": [{"HostIp": host_ip, "HostPort": "13143"}],
        "8080/tcp": [{"HostIp": host_ip, "HostPort": "18080"}],
    }
    return json.dumps(
        [
            {
                "Config": {"Labels": {"odcli.mail.managed": "true", "odcli.mail.owner": owner}},
                "State": {"Running": running},
                "NetworkSettings": {"Ports": ports},
            }
        ]
    )


def _user_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ODCLI_USER_ROOT", str(tmp_path))


def _executed(executor: RecordingExecutor) -> set[str]:
    return {step.step_id for step in executor.executed}


def test_repeated_up_reuses_the_owned_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _user_root(monkeypatch, tmp_path)
    monkeypatch.setattr("odoo_instance_sdk.internal.greenmail.http_status", lambda *_a, **_k: 200)
    document = _document(owner=owner_token())
    executor = RecordingExecutor(
        results={
            "mail.docker.inspect": _result(document),
            "mail.docker.network.inspect": _result(document),
        }
    )
    status = mail_up_command(executor=executor).run()
    assert status.reused is True
    assert status.state == "running"
    assert "mail.docker.run" not in _executed(executor)
    assert "password" not in status.__struct_fields__


def test_foreign_container_is_not_replaced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _user_root(monkeypatch, tmp_path)
    foreign = _document(owner="someone-else")
    executor = RecordingExecutor(results={"mail.docker.inspect": _result(foreign)})
    with pytest.raises(MailNotOwnedError):
        mail_up_command(executor=executor).run()
    assert "mail.docker.run" not in _executed(executor)
    down = RecordingExecutor(results={"mail.docker.inspect": _result(foreign)})
    with pytest.raises(MailNotOwnedError):
        mail_down_command(executor=down).run()
    assert "mail.docker.remove" not in _executed(down)


def test_owned_down_removes_only_the_named_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _user_root(monkeypatch, tmp_path)
    executor = RecordingExecutor(
        results={"mail.docker.inspect": _result(_document(owner=owner_token()))}
    )
    status = mail_down_command(executor=executor).run()
    assert status.state == "stopped"
    assert "mail.docker.remove" in _executed(executor)


def test_status_hides_foreign_ports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _user_root(monkeypatch, tmp_path)
    executor = RecordingExecutor(
        results={"mail.docker.inspect": _result(_document(owner="someone-else"))}
    )
    status = mail_status_command(executor=executor).run()
    assert status.state == "not_owned"
    assert status.smtp is None
    assert "password" not in json.dumps(list(status.__struct_fields__))


def test_readiness_distinguishes_timeout_from_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.greenmail.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("odoo_instance_sdk.internal.greenmail.time.monotonic", lambda: 100.0)
    monkeypatch.setattr("odoo_instance_sdk.internal.greenmail.http_status", lambda *_a, **_k: 503)
    with pytest.raises(MailTimeoutError):
        wait_ready(18080, 100.0)
    monkeypatch.setattr("odoo_instance_sdk.internal.greenmail.http_status", lambda *_a, **_k: 500)
    with pytest.raises(MailConnectionError):
        wait_ready(18080, 100.0)


def test_mailbox_replacement_does_not_purge(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def http_status(
        url: str, method: str = "GET", body: bytes | None = None, timeout: float = 3
    ) -> int:
        calls.append((method, url))
        posts = sum(item[0] == "POST" for item in calls)
        return 400 if method == "POST" and posts == 1 else 200

    monkeypatch.setattr("odoo_instance_sdk.internal.greenmail.http_status", http_status)
    create_mailbox(18080, "odcli-demo@example.test", "secret-value")
    joined = json.dumps(calls)
    assert calls[1] == ("DELETE", "http://127.0.0.1:18080/api/user/odcli-demo@example.test")
    assert "/api/mail/purge" not in joined
    assert "/api/service/reset" not in joined


def test_parallel_recipients_and_secret_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _user_root(monkeypatch, tmp_path)
    assert mail_recipient("demo", "one") != mail_recipient("demo", "two")
    email = "odcli-demo@example.test"
    password = mailbox_password(email)
    assert password == mailbox_password(email)
    secret = secret_path()
    if os.name == "posix":
        assert stat.S_IMODE(secret.stat().st_mode) == 0o600
        assert stat.S_IMODE(secret.parent.stat().st_mode) == 0o700
    assert password.encode("utf-8") not in secret.read_bytes()


def test_production_sources_do_not_purge_greenmail() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8") for path in Path("src/odoo_instance_sdk").rglob("*.py")
    )
    assert "/api/mail/purge" not in text
    assert "/api/service/reset" not in text


class _Row:
    def __init__(self, values: dict[str, object]) -> None:
        self._exists = True
        identifier = values.get("id", 0)
        self.id = identifier if isinstance(identifier, int) else 0
        self.active = bool(values.get("active", True))
        for key, value in values.items():
            setattr(self, key, value)

    def write(self, values: dict[str, object]) -> None:
        for key, value in values.items():
            setattr(self, key, value)

    def exists(self) -> _Row | None:
        return self if self._exists else None

    def unlink(self) -> None:
        self._exists = False


class _Rows:
    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows

    def __bool__(self) -> bool:
        return bool(self._rows)

    def __iter__(self) -> object:
        return iter(self._rows)

    def __getattr__(self, name: str) -> object:
        if len(self._rows) != 1:
            raise AttributeError(name)
        return getattr(self._rows[0], name)


class _Model:
    def __init__(self, fields: set[str]) -> None:
        self._fields = fields
        self._rows: list[_Row] = []
        self._next = 1

    def with_context(self, **_kwargs: object) -> _Model:
        return self

    def search(self, domain: list[tuple[str, str, object]], limit: int | None = None) -> _Rows:
        matched = [
            row
            for row in self._rows
            if row._exists
            and all(
                getattr(row, field, None) == expected for field, op, expected in domain if op == "="
            )
        ]
        return _Rows(matched[:limit] if limit else matched)

    def browse(self, row_id: int) -> _Row:
        for row in self._rows:
            if row.id == row_id and row._exists:
                return row
        missing = _Row({"id": row_id, "active": False})
        missing._exists = False
        return missing

    def create(self, values: dict[str, object]) -> _Row:
        payload = dict(values)
        payload.setdefault("id", self._next)
        self._next += 1
        row = _Row(payload)
        self._rows.append(row)
        return row


class _Env(dict[str, _Model]):
    def __getitem__(self, name: str) -> _Model:
        return dict.__getitem__(self, name)


def _env() -> _Env:
    env = _Env()
    env["ir.mail_server"] = _Model({"from_filter", "smtp_authentication", "smtp_debug"})
    env["fetchmail.server"] = _Model(set())
    env["ir.model.data"] = _Model(set())
    env["ir.module.module"] = _Model(set())
    env["ir.model"] = _Model(set())
    env["ir.mail_server"].create({"id": 1, "name": "Old", "active": True})
    env["fetchmail.server"].create({"id": 1, "name": "Old", "active": True})
    return env


def _request(tmp_path: Path, *, probe: bool = False) -> dict[str, object]:
    credentials = tmp_path / "credentials.json"
    credentials.write_text(json.dumps({"password": "mailbox-secret"}), encoding="utf-8")
    return {
        "database": "demo",
        "mailbox": "odcli-demo@example.test",
        "credentials_path": str(credentials),
        "smtp_host": "127.0.0.1",
        "smtp_port": 13025,
        "imap_host": "127.0.0.1",
        "imap_port": 13143,
        "placement": "host",
        "probe": probe,
        "operation": "configure",
        "snapshot_path": str(tmp_path / "snapshot.json"),
    }


def _run_script(tmp_path: Path, env: _Env, request: dict[str, object]) -> dict[str, object]:
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    previous = list(sys.argv)
    sys.argv = ["odoo", str(path)]
    namespace: dict[str, object] = {"env": env}
    try:
        exec(ODOO_MAIL_SCRIPT, namespace)
    finally:
        sys.argv = previous
    result = namespace["result"]
    assert isinstance(result, dict)
    return result


def test_shell_refuses_missing_fetchmail_before_writes(tmp_path: Path) -> None:
    env = _env()
    with pytest.raises(RuntimeError, match="mail_fetchmail_missing"):
        _run_script(tmp_path, env, _request(tmp_path))
    assert len(env["ir.mail_server"]._rows) == 1
    assert not (tmp_path / "snapshot.json").exists()


def test_shell_probe_timeout_precedes_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "socket.create_connection", lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError())
    )
    env = _env()
    with pytest.raises(RuntimeError, match="mail_timeout"):
        _run_script(tmp_path, env, _request(tmp_path, probe=True))
    assert len(env["ir.mail_server"]._rows) == 1


def test_shell_configure_is_repeatable_and_undo_restores(tmp_path: Path) -> None:
    env = _env()
    env["ir.module.module"].create({"name": "fetchmail", "state": "installed"})
    env["ir.model"].create({"model": "discuss.channel"})
    request = _request(tmp_path)
    first = _run_script(tmp_path, env, request)
    assert "password" not in first
    managed = [
        row
        for row in env["ir.mail_server"]._rows
        if row._exists and getattr(row, "name", "") == "OdCLI GreenMail"
    ]
    assert len(managed) == 1
    assert getattr(managed[0], "smtp_debug", None) is False
    old = env["ir.mail_server"]._rows[0]
    assert old.active is False
    snapshot = (tmp_path / "snapshot.json").read_text(encoding="utf-8")
    assert "mailbox-secret" not in snapshot
    _run_script(tmp_path, env, request)
    assert (tmp_path / "snapshot.json").read_text(encoding="utf-8") == snapshot
    undone = _run_script(tmp_path, env, {**request, "operation": "undo"})
    assert undone["undone"] is True
    assert old.active is True
    assert not any(
        row._exists and getattr(row, "name", "") == "OdCLI GreenMail"
        for row in env["ir.mail_server"]._rows
    )


def test_converter_does_not_echo_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _user_root(monkeypatch, tmp_path)
    request = _request(tmp_path)
    write_private(request_path("demo", "configure"), json.dumps(request))
    stdout = (
        "__ODCLI_PAYLOAD__abc__ "
        + json.dumps({"ok": False, "user_error": {"message": "mail_connection mailbox-secret"}})
        + " __END_PAYLOAD__abc__"
    )
    with pytest.raises(MailConnectionError) as caught:
        _converted(
            "demo",
            "configure",
            CommandResult(args=[], returncode=1, stdout=stdout, stderr="", duration=0.0),
        )
    assert "mailbox-secret" not in str(caught.value)


def _instance(names: tuple[str, ...]) -> SimpleNamespace:
    calls: list[object] = []

    def shell(source: str, **kwargs: object) -> Command[None]:
        calls.append(kwargs)
        raw = kwargs["extra_steps"]
        if not isinstance(raw, tuple):
            raise TypeError("expected prepared steps")
        extra = [step for step in raw if isinstance(step, (PreparedStep, PreparedAction))]
        steps = (
            *extra,
            PreparedStep(step_id="instance.shell_script", argv=("odoo", "shell"), mutating=True),
            PreparedAction(
                step_id="instance.shell_script.transaction",
                action="commit",
                description="Commit the Odoo shell transaction",
                mutating=True,
            ),
        )
        plan = ExecutionPlan(steps=tuple(step.public_projection() for step in steps))
        return Command.create(plan, lambda _context: None, steps)

    shell.calls = calls  # type: ignore[attr-defined]
    return SimpleNamespace(
        config=SimpleNamespace(
            configured_database_names=names,
            db_host="127.0.0.1",
            db_port=5432,
            db_user="odoo",
            db_password=None,
            default_cwd=None,
            start_config=SimpleNamespace(http_port=8069),
        ),
        _postgres_cluster=None,
        _shell_script_command=shell,
    )


def test_ambiguous_database_does_not_open_a_shell() -> None:
    instance = _instance(("alpha", "beta"))
    with pytest.raises(ConfigError, match="ambiguous"):
        mail_configure_command(instance)  # type: ignore[arg-type]
    assert instance._shell_script_command.calls == []


def test_undo_plan_does_not_touch_greenmail() -> None:
    command = mail_configure_command(_instance(("demo",)), undo=True)  # type: ignore[arg-type]
    rendered = " ".join(step.step_id for step in command.plan.steps)
    assert "instance.shell_script" in rendered
    assert "mail.docker.run" not in rendered
    assert "mail.docker.remove" not in rendered


def test_configure_plan_includes_service_and_shell() -> None:
    command = mail_configure_command(_instance(("demo",)))  # type: ignore[arg-type]
    identifiers = [step.step_id for step in command.plan.steps]
    assert "mail.docker.run" in identifiers
    assert "mail.runtime.docker" in identifiers
    assert "instance.shell_script" in identifiers
