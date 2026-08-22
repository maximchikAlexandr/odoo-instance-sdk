from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli

pytestmark = pytest.mark.real_odoo


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"real Odoo lifecycle requires {name} when ODCLI_REAL_ODOO_ENABLE=1")
    return value


def test_shared_database_lifecycle_is_opt_in_and_non_destructive(tmp_path: Path) -> None:
    if os.environ.get("ODCLI_REAL_ODOO_ENABLE") != "1":
        pytest.skip("set ODCLI_REAL_ODOO_ENABLE=1 with ODCLI_REAL_* prerequisites")
    source_project = Path(_required("ODCLI_REAL_PROJECT")).resolve()
    odoo_bin = Path(_required("ODCLI_REAL_ODOO_BIN")).resolve()
    python = Path(_required("ODCLI_REAL_PYTHON")).resolve()
    config = Path(_required("ODCLI_REAL_CONFIG")).resolve()
    database = _required("ODCLI_REAL_DATABASE")
    missing = [
        str(path) for path in (source_project, odoo_bin, python, config) if not path.exists()
    ]
    if missing:
        pytest.fail(f"real Odoo lifecycle prerequisites do not exist: {', '.join(missing)}")

    project = tmp_path / "project"
    worktree = subprocess.run(
        ["git", "-C", str(source_project), "worktree", "add", "--detach", str(project), "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    if worktree.returncode:
        pytest.fail(f"cannot create disposable project worktree: {worktree.stderr.strip()}")

    runner = CliRunner()
    env_id: str | None = None
    instance = None
    try:
        init = runner.invoke(
            cli,
            [
                "init",
                "--no-input",
                "--project",
                str(project),
                "--odoo-bin",
                str(odoo_bin),
                "--python",
                str(python),
                "--config",
                str(config),
                "--database",
                database,
            ],
        )
        assert init.exit_code == 0, init.output
        branch = f"odcli-real-{os.getpid()}"
        checkout = runner.invoke(
            cli, ["--project", str(project), "env", "checkout", branch, "--db-mode", "shared"]
        )
        assert checkout.exit_code == 0, checkout.output
        from odoo_instance_sdk import OdooClient, OdooClientConfig

        client = OdooClient(config=OdooClientConfig(executable="odoo"))
        env = next(env for env in client.environments.list(project=project) if env.branch == branch)
        env_id = str(env.id)
        instance = client.instance.from_environment(env)
        before = instance.databases.exists(database)
        assert before
        shell = subprocess.run(
            [
                sys.executable,
                "-m",
                "odoo_instance_sdk.cli",
                "--project",
                str(project),
                "--env",
                str(env.id),
                "shell",
            ],
            input=f"assert env.cr.dbname == {database!r}\nexit()\n",
            text=True,
            capture_output=True,
            check=False,
        )
        assert shell.returncode == 0, shell.stderr
        command = [
            sys.executable,
            "-m",
            "odoo_instance_sdk.cli",
            "--project",
            str(project),
            "--env",
            str(env.id),
            "run",
        ]
        run = subprocess.Popen(
            command,
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if run.poll() is not None:
                    pytest.fail(
                        f"odcli run exited before HTTP readiness: {(run.stderr or sys.stderr).read()}"
                    )
                try:
                    with urllib.request.urlopen(
                        f"http://{env.http_interface}:{env.http_port}/web", timeout=1
                    ):
                        break
                except OSError:
                    time.sleep(0.2)
            else:
                pytest.fail("odcli run did not become HTTP-ready within 30 seconds")
            os.killpg(os.getpgid(run.pid), signal.SIGINT)
            assert run.wait(timeout=20) == 130
        finally:
            if run.poll() is None:
                os.killpg(os.getpgid(run.pid), signal.SIGTERM)
                run.wait(timeout=10)
    finally:
        if env_id is not None:
            removed = runner.invoke(
                cli, ["--project", str(project), "env", "remove", env_id, "--yes"]
            )
            assert removed.exit_code == 0, removed.output
            assert instance is not None and instance.databases.exists(database)
        subprocess.run(
            ["git", "-C", str(source_project), "worktree", "remove", "--force", str(project)],
            check=False,
            capture_output=True,
            text=True,
        )
