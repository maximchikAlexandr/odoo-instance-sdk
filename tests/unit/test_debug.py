"""Unit coverage for headless debug models, RPC, plans, and the state machine."""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import msgspec
import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands.context import ResolvedContext
from odoo_instance_sdk.exceptions import DebugError
from odoo_instance_sdk.execution import JsonValue, ProcessStep
from odoo_instance_sdk.internal.debug.adapter import DebugAdapter
from odoo_instance_sdk.internal.debug.breakpoints import parse_breakpoint
from odoo_instance_sdk.internal.debug.controller import DebugController
from odoo_instance_sdk.internal.debug.dap import DapClient, FrameParser, encode_frame
from odoo_instance_sdk.internal.debug.launch import TargetLauncher
from odoo_instance_sdk.internal.debug.limits import MAX_LOCALS, MAX_STRING
from odoo_instance_sdk.internal.debug.preflight import assert_debug_runtime
from odoo_instance_sdk.internal.debug.session import (
    claim_session,
    dispatch_message,
    serve_rpc,
    session_directory,
    write_metadata,
)
from odoo_instance_sdk.internal.debug.snapshot import bound_locals, bound_output, redact_text
from odoo_instance_sdk.internal.proc import ProcessResult, RecordingExecutor
from odoo_instance_sdk.internal.proc.redaction import REDACTION_MARKER
from odoo_instance_sdk.internal.proc.run import ProcessHandle
from odoo_instance_sdk.models.config import StartConfig
from odoo_instance_sdk.models.debug import DebugSnapshot, DebugValue
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.debug import DebugResource, debug_target_argv
from odoo_instance_sdk.resources.environment import DevelopmentEnvironment


def test_snapshot_json_round_trip_has_no_adapter_ids() -> None:
    snapshot = DebugSnapshot(
        session_id="dbg_1",
        environment_id="env-1",
        state="stopped",
        stop_reason="breakpoint",
        breakpoints=(),
    )
    payload = cast("dict[str, JsonValue]", msgspec.to_builtins(snapshot))
    restored = msgspec.convert(payload, DebugSnapshot)
    encoded = json.dumps(payload)
    assert restored == snapshot
    assert "threadId" not in encoded
    assert "variablesReference" not in encoded


@pytest.mark.parametrize(
    ("text", "condition"),
    [
        ("app.py:12", None),
        ("app.py:12:state == 'a:b'", "state == 'a:b'"),
    ],
)
def test_breakpoint_condition_may_contain_colons(
    tmp_path: Path, text: str, condition: str | None
) -> None:
    source = tmp_path / "app.py"
    source.write_text("x = 1\n", encoding="utf-8")
    path, line, parsed = parse_breakpoint(text, tmp_path)
    assert path == source.resolve()
    assert line == 12
    assert parsed == condition


@pytest.mark.parametrize(
    ("text", "code"),
    [("app.py:0", "breakpoint-invalid"), ("missing.py:1", "breakpoint-invalid")],
)
def test_breakpoint_rejects_bad_line_and_missing_file(tmp_path: Path, text: str, code: str) -> None:
    with pytest.raises(DebugError) as caught:
        parse_breakpoint(text, tmp_path)
    assert caught.value.code == code


def test_snapshot_limits_and_secret_redaction() -> None:
    rows = tuple((f"name{index}", "value", "str") for index in range(MAX_LOCALS + 2))
    locals_, truncated = bound_locals((*rows, ("password", "hunter2", "str")))
    assert truncated is True
    assert len(locals_) == MAX_LOCALS
    assert redact_text("db_password", "hunter2") == REDACTION_MARKER
    assert redact_text("note", "password=hunter2") == REDACTION_MARKER
    assert len(redact_text("note", "x" * (MAX_STRING + 5))) == MAX_STRING
    output = bound_output("a" * 40000, "b" * 40000)
    assert output.truncated is True
    assert len("\n".join(output.stdout).encode()) <= 32768


def test_dap_parser_splits_frames_and_client_correlates_events() -> None:
    first = encode_frame({"seq": 1, "type": "event", "event": "initialized"})
    second = encode_frame(
        {"seq": 2, "type": "response", "request_seq": 7, "success": True, "body": {}}
    )
    parser = FrameParser()
    assert parser.feed(first[:8]) == []
    assert parser.feed(first[8:] + second[:4])[0]["event"] == "initialized"
    assert parser.feed(second[4:])[0]["request_seq"] == 7

    server, client = socket.socketpair()
    dap = DapClient(client)

    def respond() -> None:
        raw = bytearray()
        while b"\r\n\r\n" not in raw:
            raw.extend(server.recv(1024))
        server.sendall(encode_frame({"seq": 1, "type": "event", "event": "initialized"}))
        server.sendall(
            encode_frame(
                {
                    "seq": 2,
                    "type": "response",
                    "request_seq": 1,
                    "success": True,
                    "command": "initialize",
                    "body": {"ok": True},
                }
            )
        )

    threading.Thread(target=respond, daemon=True).start()
    body = dap.request("initialize", timeout=2)
    assert body == {"ok": True}
    assert dap.events.get(timeout=2)["event"] == "initialized"
    server.close()
    with pytest.raises(DebugError) as caught:
        dap.request("threads", timeout=2)
    assert caught.value.code == "protocol-failure"
    dap.close()


def test_dap_request_timeout() -> None:
    _server, client = socket.socketpair()
    dap = DapClient(client)
    with pytest.raises(DebugError) as caught:
        dap.request("threads", timeout=0.05)
    assert caught.value.code == "protocol-failure"
    dap.close()


def test_rpc_version_mismatch_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path("/tmp/odcli-debug-rpc")
    root.mkdir(mode=0o700, exist_ok=True)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.debug.session.get_data_root",
        lambda *, ensure_exists=False: root,
    )
    seen: list[str] = []

    def handler(message: dict[str, JsonValue], _cancel: threading.Event) -> JsonValue:
        seen.append(str(message["method"]))
        return {"ok": True}

    directory = claim_session("env-a")
    socket_path = directory / "session.sock"
    stop = threading.Event()
    thread = threading.Thread(target=serve_rpc, args=(socket_path, handler, stop), daemon=True)
    thread.start()
    deadline = time.monotonic() + 2
    while not socket_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    raw = {"protocol": 99, "id": "1", "method": "context", "params": {}}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(socket_path))
        client.sendall(json.dumps(raw).encode() + b"\n")
        body = json.loads(client.recv(1024).decode())
    assert body["error"]["code"] == "protocol-mismatch"
    assert seen == []
    stop.set()
    thread.join(timeout=2)
    response = dispatch_message(raw, lambda _message, _cancel: {"unused": True})
    assert response["ok"] is False


def test_stale_session_removal_keeps_other_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ODCLI_USER_ROOT", str(tmp_path))
    stale = claim_session("env-stale")
    write_metadata(stale, {"pid": 2**31 - 1, "pid_start": 1, "socket": str(stale / "session.sock")})
    other = claim_session("env-other")
    (other / "keep.txt").write_text("keep", encoding="utf-8")
    reclaimed = claim_session("env-stale")
    assert reclaimed.is_dir()
    assert not (reclaimed / "metadata.json").exists()
    assert (other / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert session_directory("env-other") == other


def test_debug_target_argv_uses_port_zero(tmp_path: Path) -> None:
    argv = debug_target_argv(
        ("/venv/bin/python", "/odoo/odoo-bin"),
        ("--config", "/odoo/odoo.conf"),
        log_dir=tmp_path / "debugpy-log",
    )
    assert argv[:7] == (
        "/venv/bin/python",
        "-m",
        "debugpy",
        "--log-to",
        str(tmp_path / "debugpy-log"),
        "--listen",
        "127.0.0.1:0",
    )
    assert argv[7:] == ("--wait-for-client", "/odoo/odoo-bin", "--config", "/odoo/odoo.conf")


def test_start_plan_dry_run_does_not_spawn_and_delegates_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ODCLI_USER_ROOT", str(tmp_path))
    instance = MagicMock()
    instance.config.start_config = StartConfig(config_path=str(tmp_path / "odoo.conf"))
    instance.config.default_cwd = tmp_path
    instance.config.default_run_args = ()
    instance._executable_prefix.return_value = ("/venv/bin/python", "/odoo/odoo-bin")
    instance._dependency_manifest.return_value = ((), None)
    environment = cast(
        "DevelopmentEnvironment",
        SimpleNamespace(id="env-1", worktree_path=str(tmp_path)),
    )
    resource = DebugResource(instance, environment)
    command = resource.start_command()
    target = next(step for step in command.plan.process_steps if step.step_id == "debug.target")
    assert isinstance(target, ProcessStep)
    assert "127.0.0.1:0" in target.argv
    assert "--listen" in target.argv
    calls: list[str] = []

    def fake_start(self: DebugResource, **_kwargs: object) -> SimpleNamespace:
        calls.append("start_command")
        return SimpleNamespace(run=lambda: "snapshot")

    monkeypatch.setattr(DebugResource, "start_command", fake_start)
    assert resource.start() == "snapshot"
    assert calls == ["start_command"]


@pytest.mark.parametrize(
    ("config", "argv", "code"),
    [
        (StartConfig(workers=2, config_path="odoo.conf"), (), "workers-unsupported"),
        (StartConfig(dev_mode="reload", config_path="odoo.conf"), (), "reload-unsupported"),
        (StartConfig(config_path="odoo.conf"), ("--dev", "all"), "reload-unsupported"),
    ],
)
def test_preflight_rejects_workers_and_reload(
    config: StartConfig, argv: tuple[str, ...], code: str
) -> None:
    with pytest.raises(DebugError) as caught:
        assert_debug_runtime(config, argv)
    assert caught.value.code == code


def test_missing_debugpy_and_port_conflict_happen_before_odoo_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ODCLI_USER_ROOT", str(tmp_path))
    instance = MagicMock()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    instance.config.start_config = StartConfig(
        http_port=free_port, config_path=str(tmp_path / "odoo.conf")
    )
    instance.config.default_cwd = tmp_path
    instance.config.default_run_args = ()
    instance._executable_prefix.return_value = ("/venv/bin/python", "/odoo/odoo-bin")
    instance._dependency_manifest.return_value = ((), None)
    environment = cast(
        "DevelopmentEnvironment",
        SimpleNamespace(id="env-1", worktree_path=str(tmp_path)),
    )
    executor = RecordingExecutor(
        results={
            "debug.debugpy-check": ProcessResult(
                argv=("/venv/bin/python", "-c", "import debugpy"),
                returncode=1,
                stdout="",
                stderr="ModuleNotFoundError",
                duration=0.0,
                cwd=str(tmp_path),
                environment=(),
            )
        }
    )
    resource = DebugResource(instance, environment)
    with pytest.raises(DebugError) as missing:
        resource.start_command(executor=executor).run()
    assert missing.value.code == "debugpy-unavailable"
    assert executor.spawned == []

    occupied = StartConfig(http_port=9, http_interface="127.0.0.1", config_path="odoo.conf")
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        occupied = msgspec.structs.replace(occupied, http_port=holder.getsockname()[1])
        with pytest.raises(DebugError) as conflict:
            assert_debug_runtime(occupied, ())
    assert conflict.value.code == "port-conflict"


class _Process:
    def __init__(self) -> None:
        self.pid = 40
        self.code: int | None = None
        self.argv: tuple[str, ...] = ()

    def poll(self) -> int | None:
        return self.code

    def drain_tails(self) -> dict[str, str]:
        return {"stdout": "ready\n", "stderr": ""}


class _Launcher(TargetLauncher):
    def __init__(self) -> None:
        self.started: list[tuple[str, ...]] = []
        self.stopped = 0

    def start(self, argv: tuple[str, ...], cwd: Path) -> ProcessHandle:
        self.started.append(argv)
        process = _Process()
        process.argv = argv
        return cast("ProcessHandle", process)

    def listen_port(self, handle: ProcessHandle, timeout: float) -> int:
        del handle, timeout
        return 5678

    def stop(self, handle: ProcessHandle | None) -> int | None:
        self.stopped += 1
        return 0


class _Adapter(DebugAdapter):
    def __init__(self) -> None:
        self.events: list[dict[str, JsonValue]] = []
        self.resumed: list[str] = []
        self.fail = False
        self._client = None

    def connect(self, port: int) -> None:
        del port

    def set_breakpoints(
        self, groups: dict[str, tuple[tuple[int, str | None], ...]]
    ) -> tuple[tuple[str, int, bool, str | None], ...]:
        if self.fail:
            raise DebugError("protocol-failure", "adapter rejected breakpoints")
        return tuple(
            (path, line, True, None)
            for path, entries in groups.items()
            for line, _condition in entries
        )

    def configuration_done(self) -> None:
        return None

    def wait_event(self, timeout: float) -> dict[str, JsonValue] | None:
        del timeout
        return self.events.pop(0) if self.events else None

    def resume(self, kind: str) -> None:
        self.resumed.append(kind)

    def pause(self) -> None:
        self.resumed.append("pause")

    def frames(self) -> tuple[tuple[int, str, int, str], ...]:
        return ((0, "app.py", 1, "main"),)

    def locals(self, frame: int) -> tuple[tuple[str, str, str, int], ...]:
        del frame
        return (("password", "hunter2", "str", 0),)

    def evaluate(
        self, expression: str, frame: int, depth: int
    ) -> tuple[str, str, tuple[object, ...], bool]:
        del expression, frame, depth
        return ("1", "int", (), False)

    def close(self) -> None:
        return None


def _controller(tmp_path: Path, adapter: _Adapter, launcher: _Launcher) -> DebugController:
    source = tmp_path / "app.py"
    source.write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "session_id": "dbg_test",
                "environment_id": "env-1",
                "cwd": str(tmp_path),
                "http_interface": "127.0.0.1",
                "http_port": 0,
                "target_argv": ["/venv/bin/python", "-m", "debugpy"],
                "breakpoints": [{"path": str(source), "line": 1, "condition": None}],
            }
        ),
        encoding="utf-8",
    )
    return DebugController(tmp_path, adapter=adapter, launcher=launcher)


def test_state_machine_continue_eval_and_atomic_breakpoints(tmp_path: Path) -> None:
    adapter = _Adapter()
    adapter.events.append({"event": "stopped", "body": {"reason": "breakpoint", "threadId": 1}})
    launcher = _Launcher()
    controller = _controller(tmp_path, adapter, launcher)
    snapshot = controller.bootstrap()
    assert snapshot.state == "stopped"
    assert snapshot.locals[0].value == REDACTION_MARKER
    assert launcher.started[0] == ("/venv/bin/python", "-m", "debugpy")
    running = controller.handle(
        {"method": "continue", "params": {"wait": False, "timeout": 1}},
        threading.Event(),
    )
    assert isinstance(running, dict)
    assert running["state"] == "running"
    assert adapter.resumed == ["continue"]
    again = controller.handle(
        {"method": "continue", "params": {"wait": False, "timeout": 1}},
        threading.Event(),
    )
    assert isinstance(again, dict)
    assert adapter.resumed == ["continue"]
    with pytest.raises(DebugError) as caught:
        controller.handle(
            {"method": "eval", "params": {"expression": "1", "frame": 0, "depth": 0}},
            threading.Event(),
        )
    assert caught.value.code == "not-stopped"
    controller._state = "stopped"
    value = controller.handle(
        {"method": "eval", "params": {"expression": "1", "frame": 0, "depth": 0}},
        threading.Event(),
    )
    assert isinstance(value, dict)
    assert value["value"] == "1"
    missing = tmp_path / "missing.py"
    before = list(controller._breakpoints)
    with pytest.raises(DebugError) as invalid:
        controller.handle(
            {
                "method": "continue",
                "params": {
                    "add": [
                        {"path": str(tmp_path / "app.py"), "line": 1},
                        {"path": str(missing), "line": 4},
                    ]
                },
            },
            threading.Event(),
        )
    assert invalid.value.code == "breakpoint-invalid"
    assert controller._breakpoints == before


def test_stop_does_not_signal_an_unrelated_pid(tmp_path: Path) -> None:
    adapter = _Adapter()
    adapter.events.append({"event": "stopped", "body": {"reason": "step"}})
    launcher = _Launcher()
    controller = _controller(tmp_path, adapter, launcher)
    controller.bootstrap()
    unrelated = 424242
    controller.handle({"method": "stop", "params": {}}, threading.Event())
    assert launcher.stopped == 2
    assert unrelated not in {item for started in launcher.started for item in started}


def test_value_model_round_trip() -> None:
    value = DebugValue(expression="1", value="1", type="int")
    assert msgspec.convert(msgspec.to_builtins(value), DebugValue) == value


def test_project_context_fails_before_a_session_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = ProjectConfig(
        repository_root=tmp_path,
        python=sys.executable,
        odoo_bin=tmp_path / "odoo-bin",
    )
    resolved = ResolvedContext(
        client=cast("object", SimpleNamespace()),
        instance=cast("object", SimpleNamespace()),
        source=project,
        provenance="explicit",
    )
    monkeypatch.setattr("odoo_instance_sdk.commands.debug.ready_instance", lambda _ctx: resolved)
    result = CliRunner().invoke(cli, ["debug", "context", "--format", "json"])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["error"]["code"] == "environment-required"
    assert list(tmp_path.rglob("session.sock")) == []
