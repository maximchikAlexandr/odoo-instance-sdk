from __future__ import annotations

import json
import subprocess
import sys
from inspect import unwrap
from pathlib import Path
from types import SimpleNamespace

import msgspec
import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig
from odoo_instance_sdk.resources.postgres import PostgresCluster
from tests.conftest import isolated_cli_catalogue
from tests.integration.real_odoo.focused_handlers import _stable_plan


def test_real_odoo_catalogue_follows_runtime_home_in_sdk_and_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.undo()
    request = SimpleNamespace(node=SimpleNamespace(get_closest_marker=lambda _: True))
    unwrap(isolated_cli_catalogue)(monkeypatch, tmp_path, request, tmp_path / "unused")
    runtime_home = tmp_path / "runtime" / "home"
    monkeypatch.setenv("HOME", str(runtime_home))
    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    catalog = client.get_catalog()
    try:
        expected = runtime_home / ".odcli" / "catalog.sqlite3"
        assert expected.is_file()
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "from odoo_instance_sdk.internal.paths import get_catalog_path; print(get_catalog_path())",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert Path(child.stdout.strip()) == expected
    finally:
        catalog.close()


@pytest.mark.parametrize("operation", ["shell", "reset"])
def test_focused_plan_comparison_ignores_only_execution_local_values(
    tmp_path: Path,
    operation: str,
) -> None:
    config = tmp_path / "odoo.conf"
    config.write_text("[options]\nadmin_passwd = admin\ndb_name = demo\n", encoding="utf-8")
    instance = OdooClient(config=OdooClientConfig(executable="/usr/bin/odoo")).instance.from_config(
        config,
        base_url="http://127.0.0.1:8069",
    )
    instance._postgres_cluster = PostgresCluster._from_config(
        ProjectConfig(
            repository_root=tmp_path,
            postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=5432),
        ),
        repository_root=tmp_path,
        compose_runner=None,
        project_id="project_probe",
    )
    if operation == "shell":
        plans = [json.loads(msgspec.json.encode(instance.shell_command().plan)) for _ in range(2)]
    else:
        plans = [
            json.loads(
                msgspec.json.encode(
                    instance.databases.reset_admin_password_command(
                        admin_password="focused-secret",
                        provenance="environment",
                    ).plan
                )
            )
            for _ in range(2)
        ]
    first, second = plans
    assert first["fingerprint"] != second["fingerprint"]
    assert _stable_plan(first) == _stable_plan(second)
    second["steps"][0]["argv"][-1] = "different-image"
    assert _stable_plan(first) != _stable_plan(second)


def test_database_probe_uses_cluster_home_and_restores_caller_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from functools import partial

    from tests.integration.real_odoo import focused_support as failures
    from tests.integration.real_odoo.compose import ComposeLifecycle

    # Exercise real HOME resolution, without the offline fixture's fixed SDK paths.
    monkeypatch.undo()
    project = tmp_path / "project"
    (project / ".odcli").mkdir(parents=True)
    config = ProjectConfig(
        repository_root=project,
        postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=5432),
    )
    (project / ".odcli" / "project.toml").write_text(config.to_manifest(), encoding="utf-8")
    cluster_home = tmp_path / "cluster-home"
    monkeypatch.setenv("HOME", str(cluster_home))
    expected_file = PostgresCluster.from_project(project).compose_file
    caller_home = tmp_path / "caller-home"
    monkeypatch.setenv("HOME", str(caller_home))

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args[args.index("--file") + 1] == str(expected_file)
        assert kwargs["cwd"] == expected_file.parent
        assert args[-1] == "SELECT datname FROM pg_database WHERE datname = 'demo';"
        return subprocess.CompletedProcess(args, 0, "demo\n", "")

    monkeypatch.setattr(failures, "ComposeLifecycle", partial(ComposeLifecycle, command_runner=run))
    result = failures._project_database_probe(project, "demo", home=str(cluster_home))

    assert result.returncode == 0
    assert result.stdout == "demo\n"
    assert Path.home() == caller_home
