"""Serial debugpy coverage for one headless session through separate CLI processes."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import textwrap
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from odoo_instance_sdk import OdooClient
from odoo_instance_sdk.resources.environment import EnvironmentDatabaseMode, EnvironmentState

_FAKE_ODOO = textwrap.dedent(
    """\
    import http.server
    import sys


    def work(name):
        state = name
        return state


    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            work("queued")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):
            return


    def main():
        port = int(sys.argv[sys.argv.index("--http-port") + 1])
        work("draft")
        server = http.server.HTTPServer(("127.0.0.1", port), Handler)
        server.serve_forever()


    if __name__ == "__main__":
        main()
    """
)
_CLIENT = textwrap.dedent(
    """\
    import urllib.request

    urllib.request.urlopen("http://127.0.0.1:{port}", timeout=30).read()
    """
)


def _line(source: str, text: str) -> int:
    for number, line in enumerate(source.splitlines(), start=1):
        if text in line:
            return number
    raise AssertionError(text)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _run(
    binary: str, args: list[str], env: dict[str, str], timeout: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [binary, *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _document(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert completed.returncode == 0, completed.stdout + completed.stderr
    parsed = json.loads(completed.stdout)
    assert isinstance(parsed, dict)
    return parsed


def _result(document: dict[str, object]) -> dict[str, object]:
    result = document["result"]
    assert isinstance(result, dict)
    return result


@pytest.mark.integration
@pytest.mark.serial
@pytest.mark.timeout(180)
def test_headless_debug_session_through_cli_processes(
    env_client: OdooClient, tmp_path: Path
) -> None:
    """Drive start, wait, eval, step, breakpoints, restart, interrupt, and stop."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    script = repo / "odoo-bin"
    script.write_text(_FAKE_ODOO, encoding="utf-8")
    port = _free_port()
    config = repo / "odoo.conf"
    config.write_text(
        textwrap.dedent(
            f"""\
            [options]
            http_interface = 127.0.0.1
            http_port = {port}
            db_name = demo
            workers = 0
            addons_path = {repo}
            """
        ),
        encoding="utf-8",
    )
    env_id = str(uuid.uuid4())
    env_client.get_catalog().create_environment(
        {
            "id": env_id,
            "name": "demo:debug",
            "repository_root": str(repo),
            "git_common_dir": str(repo / ".git"),
            "branch": "debug",
            "base_ref": "main",
            "worktree_path": str(repo),
            "generated_config_path": str(config),
            "python_environment_path": sys.executable,
            "python_environment_owned": False,
            "dependency_lock_path": str(repo / "requirements.lock"),
            "http_interface": "127.0.0.1",
            "http_port": port,
            "db_mode": EnvironmentDatabaseMode.SHARED.value,
            "source_db_name": "demo",
            "target_db_name": None,
            "backup_id": None,
            "runtime_json": json.dumps({"odoo_bin": str(script), "runtime_cwd": str(repo)}),
            "state": EnvironmentState.READY.value,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    (repo / "requirements.lock").write_text("", encoding="utf-8")
    short = Path(f"/tmp/od18{os.getpid()}")
    shutil.rmtree(short, ignore_errors=True)
    short.mkdir(mode=0o700)
    source_path = tmp_path / "data" / "catalog.sqlite3"
    source = sqlite3.connect(source_path)
    destination = sqlite3.connect(short / "catalog.sqlite3")
    try:
        source.backup(destination)
    finally:
        source.close()
        destination.close()
    child_env = os.environ.copy()
    child_env["ODCLI_USER_ROOT"] = str(short)
    binary = str(Path(sys.executable).with_name("odcli"))
    draft = _line(_FAKE_ODOO, "return state")
    queued = _line(_FAKE_ODOO, 'work("queued")')
    client = repo / "client.py"
    client.write_text(_CLIENT.format(port=port), encoding="utf-8")
    unrelated = subprocess.Popen(["sleep", "300"])
    session_dir = short / "debug" / env_id
    try:
        started = _document(
            _run(
                binary,
                [
                    "--env",
                    env_id,
                    "debug",
                    "start",
                    "--break",
                    f'odoo-bin:{draft}:state == "draft"',
                    "--format",
                    "json",
                ],
                child_env,
                70,
            )
        )
        result = _result(started)
        session_id = result["session_id"]
        assert result["state"] == "stopped"
        evaluated = _result(
            _document(
                _run(
                    binary,
                    ["--env", env_id, "debug", "eval", "state", "--format", "json"],
                    child_env,
                    20,
                )
            )
        )
        assert evaluated["value"] in {"draft", "'draft'"}
        stepped = _result(
            _document(
                _run(
                    binary,
                    [
                        "--env",
                        env_id,
                        "debug",
                        "step",
                        "over",
                        "--timeout",
                        "10",
                        "--format",
                        "json",
                    ],
                    child_env,
                    20,
                )
            )
        )
        assert stepped["state"] == "stopped"
        invalid = _run(
            binary,
            ["--env", env_id, "debug", "continue", "--break", "missing.py:1", "--format", "json"],
            child_env,
            20,
        )
        assert invalid.returncode == 1, invalid.stdout + invalid.stderr
        current = _document(
            _run(
                binary,
                ["--env", env_id, "debug", "context", "--format", "json"],
                child_env,
                20,
            )
        )
        breakpoints = _result(current)["breakpoints"]
        assert any(item["line"] == draft for item in breakpoints)
        resumed = _document(
            _run(
                binary,
                ["--env", env_id, "debug", "continue", "--format", "json"],
                child_env,
                20,
            )
        )
        assert _result(resumed)["state"] == "running"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), 0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("debug target did not open its HTTP port")
        blocked = _document(
            _run(
                binary,
                [
                    "--env",
                    env_id,
                    "debug",
                    "continue",
                    "--to",
                    f"odoo-bin:{queued}",
                    "--wait",
                    "20",
                    "--format",
                    "json",
                    "--",
                    sys.executable,
                    str(client),
                ],
                child_env,
                30,
            )
        )
        blocked_result = _result(blocked)
        trigger = blocked_result["trigger"]
        assert blocked_result["state"] == "stopped"
        assert isinstance(trigger, dict)
        assert trigger["state"] == "running"
        duplicate = _run(
            binary,
            [
                "--env",
                env_id,
                "debug",
                "continue",
                "--format",
                "json",
                "--",
                sys.executable,
                str(client),
            ],
            child_env,
            20,
        )
        assert duplicate.returncode == 1
        assert json.loads(duplicate.stdout)["error"]["code"] == "trigger-already-running"
        restarted = _document(
            _run(
                binary,
                ["--env", env_id, "debug", "restart", "--format", "json"],
                child_env,
                70,
            )
        )
        restarted_result = _result(restarted)
        assert restarted_result["session_id"] == session_id
        restarted_breaks = restarted_result["breakpoints"]
        assert isinstance(restarted_breaks, list)
        assert any(item["line"] == draft for item in restarted_breaks)
        waiting = subprocess.Popen(
            [binary, "--env", env_id, "debug", "continue", "--wait", "30", "--format", "json"],
            env=child_env,
        )
        time.sleep(1)
        waiting.send_signal(signal.SIGINT)
        assert waiting.wait(timeout=15) == 130
        alive = _document(
            _run(
                binary,
                ["--env", env_id, "debug", "context", "--format", "json"],
                child_env,
                20,
            )
        )
        assert _result(alive)["session_id"] == session_id
        stopped = _document(
            _run(
                binary,
                ["--env", env_id, "debug", "stop", "--format", "json"],
                child_env,
                20,
            )
        )
        assert stopped["ok"] is True
        assert not session_dir.exists()
        assert unrelated.poll() is None
    finally:
        _run(binary, ["--env", env_id, "debug", "stop", "--format", "json"], child_env, 20)
        if unrelated.poll() is None:
            unrelated.kill()
            unrelated.wait(timeout=5)
        shutil.rmtree(short, ignore_errors=True)
