from __future__ import annotations

import json
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


def _optional_required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"real Odoo test requires {name} when ODCLI_REAL_ODOO_ENABLE=1")
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


def test_real_odoo_test_command_is_disposable_and_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the complete selector/preflight/spec/runner/result path when opted in."""
    if os.environ.get("ODCLI_REAL_ODOO_ENABLE") != "1":
        pytest.skip("set ODCLI_REAL_ODOO_ENABLE=1 with ODCLI_REAL_* prerequisites")

    source_project = Path(_optional_required("ODCLI_REAL_PROJECT")).resolve()
    odoo_bin = Path(_optional_required("ODCLI_REAL_ODOO_BIN")).resolve()
    python = Path(_optional_required("ODCLI_REAL_PYTHON")).resolve()
    config = Path(_optional_required("ODCLI_REAL_CONFIG")).resolve()
    database = _optional_required("ODCLI_REAL_DATABASE")
    module = _optional_required("ODCLI_REAL_MODULE")
    addon_root = Path(os.environ.get("ODCLI_REAL_ADDON_ROOT", "addons"))
    module_path = source_project / addon_root / module
    manifest = module_path / "__manifest__.py"
    required_paths = (source_project, odoo_bin, python, config, module_path, manifest)
    missing = [str(path) for path in required_paths if not path.exists()]
    if missing or not manifest.is_file():
        pytest.skip(
            "real Odoo disposable test prerequisites are not present: " + ", ".join(missing)
        )

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
        branch = f"odcli-real-test-{os.getpid()}"
        checkout = runner.invoke(
            cli, ["--project", str(project), "env", "checkout", branch, "--db-mode", "shared"]
        )
        assert checkout.exit_code == 0, checkout.output

        from odoo_instance_sdk import OdooClient, OdooClientConfig
        from odoo_instance_sdk.internal.test_selection import preflight_installed_modules

        client = OdooClient(config=OdooClientConfig(executable="odoo"))
        env = next(env for env in client.environments.list(project=project) if env.branch == branch)
        env_id = str(env.id)
        instance = client.instance.from_environment(env)
        assert instance.databases.exists(database)
        try:
            preflight_installed_modules(instance, (module,))
        except Exception as exc:
            if "not installed" in str(exc):
                pytest.skip(f"real Odoo prerequisite module is not installed: {module}")
            raise

        assert instance.run_foreground(args=("--stop-after-init",)) == 0

        monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
        before_status = subprocess.run(
            ["git", "-C", str(env.worktree_path), "status", "--porcelain"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "--env",
                env_id,
                "test",
                module,
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0, result.output
        document = json.loads(result.stdout)
        payload = document["result"]
        assert document["ok"] is True
        assert payload["selection"]["kind"] == "module"
        assert payload["modules"] == [module]
        assert payload["test_tags"] == f"/{module}"
        assert set(payload["counts"]) == {"tests", "successful", "failed", "errors", "skipped"}
        assert payload["exit_code"] == 0

        after_status = subprocess.run(
            ["git", "-C", str(env.worktree_path), "status", "--porcelain"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        assert after_status == before_status
        assert instance.databases.exists(database)
        preflight_installed_modules(instance, (module,))
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


def test_project_database_preparation_is_opt_in_and_disposable(tmp_path: Path) -> None:
    """Exercise the complete remote-backup/local-restore contract in isolation."""
    if os.environ.get("ODCLI_REAL_ODOO_ENABLE") != "1":
        pytest.skip("set ODCLI_REAL_ODOO_ENABLE=1 with ODCLI_REAL_* prerequisites")
    remote_url = _required("ODCLI_REAL_TEST_BASE_URL")
    remote_database = _required("ODCLI_REAL_TEST_DATABASE")
    master_password = _required("ODCLI_TEST_MASTER_PASSWORD")
    source_project = Path(_required("ODCLI_REAL_PROJECT")).resolve()
    odoo_bin = Path(_required("ODCLI_REAL_ODOO_BIN")).resolve()
    python = Path(_required("ODCLI_REAL_PYTHON")).resolve()
    config = Path(_required("ODCLI_REAL_CONFIG")).resolve()
    missing = [
        str(path) for path in (source_project, odoo_bin, python, config) if not path.exists()
    ]
    if missing:
        pytest.fail(f"real Odoo lifecycle prerequisites do not exist: {', '.join(missing)}")

    project = tmp_path / "project-preparation"
    worktree = subprocess.run(
        ["git", "-C", str(source_project), "worktree", "add", "--detach", str(project), "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    if worktree.returncode:
        pytest.fail(f"cannot create disposable project worktree: {worktree.stderr.strip()}")

    runner = CliRunner()
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
                remote_database,
            ],
        )
        assert init.exit_code == 0, init.output
        manifest = project / ".odcli" / "project.toml"
        with manifest.open("a", encoding="utf-8") as stream:
            stream.write(
                "\n[test_instance]\n"
                f'base_url = "{remote_url}"\n'
                f'database = "{remote_database}"\n'
                'git_branch = "main"\n'
            )

        os.environ["ODCLI_TEST_MASTER_PASSWORD"] = master_password
        refresh = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "db",
                "refresh",
                "--restore",
                "--reset-admin-password",
                "--format",
                "json",
            ],
        )
        assert refresh.exit_code == 0, refresh.output
        document = json.loads(refresh.stdout)
        prepared = document["result"]
        assert prepared["backup"]["format"] == "zip"
        assert prepared["backup"]["filestore_requested"] is True
        assert prepared["source_git_branch"] == "main"
        assert prepared["restored_database"]
        assert prepared["admin_password_reset"] is True
        assert prepared["default_switched"] is True
        assert not list(project.glob(".odcli-refresh-*.conf"))
    finally:
        os.environ.pop("ODCLI_TEST_MASTER_PASSWORD", None)
        subprocess.run(
            ["git", "-C", str(source_project), "worktree", "remove", "--force", str(project)],
            check=False,
            capture_output=True,
            text=True,
        )
