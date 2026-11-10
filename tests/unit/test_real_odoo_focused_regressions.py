from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from inspect import unwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import msgspec
import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.exceptions import PostgresImageNotTrustedError
from odoo_instance_sdk.internal.postgres_compose import ComposeRunner
from odoo_instance_sdk.internal.proc import RecordingExecutor
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig
from odoo_instance_sdk.resources.postgres import PostgresCluster
from tests.conftest import isolated_cli_catalogue
from tests.integration.real_odoo.cleanup import ResourceLedger
from tests.integration.real_odoo.focused_handlers import _stable_plan
from tests.integration.real_odoo.test_critical_path import (
    _reap_owned_child,
    _start_owned,
    _stop_owned,
)
from tests.unit.owned_child_lifecycle import OwnedChildInstance

POSTGRES_DIGEST = "docker.io/library/postgres@sha256:" + "a" * 64


class BrowserTrustComposeRunner(ComposeRunner):
    """Keep the public shell command's PostgreSQL preflight deterministic."""

    requires_docker = False

    def __init__(self) -> None:
        self.started = False

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        argv = tuple(args)
        joined = " ".join(argv)
        if "image inspect" in joined:
            return subprocess.CompletedProcess(argv, 0, POSTGRES_DIGEST, "")
        if " ps " in f" {joined} ":
            rows = json.dumps({"Name": "postgres", "State": "running"}) if self.started else ""
            return subprocess.CompletedProcess(argv, 0, rows, "")
        if " up " in f" {joined} ":
            self.started = True
        return subprocess.CompletedProcess(argv, 0, "healthy", "")

    def inspect_volume_identity(self, _volume: str, _cluster_id: str, _project_id: str) -> bool:
        return True


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


def test_browser_public_shell_uses_runtime_home_after_per_case_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.integration.real_odoo.test_browser_session_login import _runtime_environment

    # Exercise the same HOME-based paths as real_odoo instead of the general
    # unit suite's fixed per-worker path patches.
    monkeypatch.undo()
    runtime_home = tmp_path / "runtime" / "home"
    runtime_environment = {
        "HOME": str(runtime_home),
        "XDG_CONFIG_HOME": str(runtime_home.parent / "xdg-config"),
        "XDG_DATA_HOME": str(runtime_home.parent / "xdg-data"),
        "XDG_CACHE_HOME": str(runtime_home.parent / "xdg-cache"),
        "XDG_STATE_HOME": str(runtime_home.parent / "xdg-state"),
        "ODCLI_E2E_RUN_ID": "browser-home-binding",
        "ODCLI_E2E_CATALOG": str(runtime_home / ".odcli" / "catalog.sqlite3"),
    }
    project_root = tmp_path / "project"
    project_root.mkdir()
    manifest_dir = project_root / ".odcli"
    manifest_dir.mkdir()
    project_config = ProjectConfig(
        repository_root=project_root,
        postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=55432),
    )
    (manifest_dir / "project.toml").write_text(project_config.to_manifest(), encoding="utf-8")
    runner = BrowserTrustComposeRunner()

    with _runtime_environment(runtime_environment):
        cluster = PostgresCluster.from_project(project_root, compose_runner=runner)
        cluster.approve_image(POSTGRES_DIGEST)

    config_path = tmp_path / "odoo.conf"
    config_path.write_text(
        "[options]\nadmin_passwd = test\ndb_name = demo\n"
        "db_host = 127.0.0.1\ndb_port = 55432\ndb_user = odoo\n",
        encoding="utf-8",
    )
    instance = OdooClient(config=OdooClientConfig(executable="odoo")).instance.from_config(
        config_path,
        base_url="http://127.0.0.1:8069",
    )
    instance._postgres_cluster = cluster
    isolated_home = tmp_path / "per-case" / "home"
    monkeypatch.setenv("HOME", str(isolated_home))
    executor = RecordingExecutor()
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.identity.SubprocessExecutor",
        lambda: executor,
    )

    with pytest.raises(PostgresImageNotTrustedError, match="not approved for this user"):
        instance.run_shell_script_command("print('browser fixture probe')").run()

    with _runtime_environment(runtime_environment):
        result = instance.run_shell_script_command("print('browser fixture probe')").run()

    assert result.returncode == 0
    assert runner.started
    assert executor.executed
    assert dict(executor.executed[-1].environment_snapshot)["HOME"] == str(runtime_home)
    assert os.environ["HOME"] == str(isolated_home)


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


def test_owned_detached_child_is_reaped_by_its_parent(tmp_path: Path) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.05)"],
        cwd=tmp_path,
        start_new_session=True,
    )
    result: list[int | None] = []
    reaper = threading.Thread(target=_reap_owned_child, args=(child.pid, result))

    reaper.start()
    reaper.join(timeout=2.0)

    assert not reaper.is_alive()
    assert len(result) == 1 and result[0] not in (None, -1)


def _stop_owned_process_case(instance: object, runtime: object, owned: object) -> None:
    _stop_owned(instance, owned)  # type: ignore[arg-type]


def _unwind_owned_process_case(instance: object, runtime: object, owned: object) -> None:
    runtime.ledger.unwind()  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("run_id", "cleanup"),
    [
        pytest.param("owned-process-first", _stop_owned_process_case, id="first-stop"),
        pytest.param("owned-process-second", _unwind_owned_process_case, id="second-unwind"),
        pytest.param("owned-process-third", _unwind_owned_process_case, id="third-unwind"),
    ],
)
def test_each_owned_process_is_reaped_after_public_stop_or_ledger_unwind(
    run_id: str, cleanup: Callable[[Any, Any, Any], None]
) -> None:
    class Runtime:
        def __init__(self, run_id: str) -> None:
            self.run_id = run_id
            self.ledger = ResourceLedger(run_id)

    runtime = Runtime(run_id)
    instance = OwnedChildInstance()
    owned = _start_owned(instance, runtime)

    cleanup(instance, runtime, owned)
    runtime.ledger.unwind()

    assert owned.reap_result and owned.reap_result[0] != -1
    assert not owned.reaper.is_alive()
    assert runtime.ledger.records == ()
