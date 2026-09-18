from __future__ import annotations

import json
import socket
import sys
import uuid
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner, Result

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.commands.context import ResolvedContext, RuntimeSource
from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
from odoo_instance_sdk.exceptions import InstanceConfigurationError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.proc import (
    ProcessHandle,
    RecordingExecutor,
)
from odoo_instance_sdk.models import DetachedLaunchResult, StartConfig
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog, CatalogValue


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _init_git_worktree(path: Path) -> None:
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    (path / "f.txt").write_text("x")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


class _FakeCatalog:
    def __init__(self) -> None:
        self.upsert_calls: list[tuple[str, dict[str, object]]] = []
        self.clear_calls: list[str] = []

    def upsert_environment_runtime(self, environment_id: str, **kw: object) -> None:
        self.upsert_calls.append((environment_id, dict(kw)))

    def _upsert_runtime(self, owner_kind: str, owner_id: str, **kw: object) -> None:
        assert owner_kind == "environment"
        self.upsert_environment_runtime(owner_id, **kw)

    def clear_environment_runtime(self, environment_id: str) -> None:
        self.clear_calls.append(environment_id)

    def _clear_runtime(self, owner_kind: str, owner_id: str) -> None:
        assert owner_kind == "environment"
        self.clear_environment_runtime(owner_id)

    def get_environment_runtime(self, environment_id: str) -> None:
        return None

    def _clear_environment_runtime_if_matches(
        self, environment_id: str, *, root_pid: int, create_time: float
    ) -> bool:
        self.clear_calls.append(environment_id)
        return True


def _make_env(env_id: str) -> dict[str, CatalogValue]:
    return {
        "id": env_id,
        "name": "test",
        "repository_root": "/repo",
        "git_common_dir": "/repo/.git",
        "branch": "main",
        "base_ref": "HEAD",
        "worktree_path": "/wt",
        "generated_config_path": "/wt/odoo.conf",
        "python_environment_path": "/venv",
        "python_environment_owned": False,
        "dependency_lock_path": "/lock",
        "db_mode": "shared",
        "source_db_name": "mydb",
        "target_db_name": None,
        "backup_id": None,
        "runtime_json": "{}",
        "state": "ready",
        "created_at": "2026-01-01T00:00:00",
        "last_used_at": None,
        "removed_at": None,
        "last_error": None,
    }


def _client_with_catalog(catalog: object) -> OdooClient:
    c = OdooClient(config=OdooClientConfig(executable="odoo"))
    c._catalog = cast("BackupCatalog | None", catalog)
    return c


def _make_tracked_instance(
    *,
    client: OdooClient,
    env_id: str,
    cwd: Path,
    command_prefix: tuple[str, ...],
    http_port: int,
    logfile: str,
) -> OdooInstance:
    start_cfg = StartConfig(
        http_port=http_port,
        http_interface="127.0.0.1",
        config_path=str(cwd / "odoo.conf"),
        db_name="mydb",
        logfile=logfile,
    )
    return OdooInstance(
        config=InstanceConfig(
            base_url=f"http://127.0.0.1:{http_port}",
            start_config=start_cfg,
            command_prefix=command_prefix,
            default_cwd=cwd,
        ),
        _client=client,
        _environment_id=env_id,
    )


def _alive_handle(pid: int = 4242) -> ProcessHandle:
    process = MagicMock()
    process.pid = pid
    process.poll.return_value = None
    return ProcessHandle(process, (), pid, pid, False)


def _dead_handle(pid: int = 4242) -> ProcessHandle:
    process = MagicMock()
    process.pid = pid
    process.poll.return_value = 1
    return ProcessHandle(process, (), pid, pid, False)


@pytest.fixture()
def real_catalog(tmp_path: Path) -> BackupCatalog:
    return BackupCatalog(db_path=tmp_path / "cat.sqlite3")


@pytest.fixture()
def env_id(real_catalog: BackupCatalog) -> str:
    eid = str(uuid.uuid4())
    real_catalog.create_environment(_make_env(eid))
    return eid


@pytest.fixture()
def http_port() -> int:
    return _free_port()


@pytest.mark.unit
def test_detached_launch_persists_identity_and_returns_promptly(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    (wt / "odoo.log").write_text("")
    fake = _FakeCatalog()
    client = _client_with_catalog(fake)
    inst = _make_tracked_instance(
        client=client,
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
        http_port=http_port,
        logfile="odoo.log",
    )
    executor = RecordingExecutor(handles={"instance.detached": _alive_handle()})
    with (
        patch("odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor),
        patch("odoo_instance_sdk.resources.instance._process_create_time", return_value=1.0),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
    ):
        result = inst.run_detached()

    assert isinstance(result, DetachedLaunchResult)
    assert result.pid == 4242
    assert result.owner_kind == "environment"
    assert result.owner_id == env_id
    assert result.http_endpoint == f"http://127.0.0.1:{http_port}"
    assert result.log_path == str(wt / "odoo.log")
    assert len(fake.upsert_calls) == 1
    assert fake.clear_calls == []


@pytest.mark.unit
def test_detached_immediate_exit_returns_error_and_no_stale_record(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    (wt / "odoo.log").write_text("")
    fake = _FakeCatalog()
    client = _client_with_catalog(fake)
    inst = _make_tracked_instance(
        client=client,
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import sys; sys.exit(1)"),
        http_port=http_port,
        logfile="odoo.log",
    )
    executor = RecordingExecutor(handles={"instance.detached": _dead_handle()})
    with (
        patch("odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor),
        patch("odoo_instance_sdk.resources.instance._process_create_time", return_value=1.0),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
        pytest.raises(InstanceConfigurationError, match="exited immediately"),
    ):
        inst.run_detached()

    assert fake.upsert_calls == []
    assert fake.clear_calls == [env_id]


@pytest.mark.unit
def test_detached_no_logfile_fails_fast_before_spawn(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    fake = _FakeCatalog()
    client = _client_with_catalog(fake)
    start_cfg = StartConfig(
        http_port=http_port,
        http_interface="127.0.0.1",
        config_path=str(wt / "odoo.conf"),
        db_name="mydb",
        logfile=None,
    )
    inst = OdooInstance(
        config=InstanceConfig(
            base_url=f"http://127.0.0.1:{http_port}",
            start_config=start_cfg,
            command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
            default_cwd=wt,
        ),
        _client=client,
        _environment_id=env_id,
    )
    with pytest.raises(InstanceConfigurationError, match="logfile"):
        inst.run_detached_command()


@pytest.mark.unit
def test_detached_dry_run_does_not_spawn(env_id: str, http_port: int, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    (wt / "odoo.log").write_text("")
    fake = _FakeCatalog()
    client = _client_with_catalog(fake)
    inst = _make_tracked_instance(
        client=client,
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
        http_port=http_port,
        logfile="odoo.log",
    )
    executor = RecordingExecutor(handles={"instance.detached": _alive_handle()})
    with (
        patch("odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
    ):
        command = inst.run_detached_command()

    plan_argv = command.plan.process_steps[0].argv
    assert plan_argv[0] == sys.executable
    assert executor.spawned == []
    assert executor.executed == []
    assert fake.upsert_calls == []


@pytest.mark.unit
def test_detached_delegates_to_command_sibling(env_id: str, http_port: int, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    (wt / "odoo.log").write_text("")
    fake = _FakeCatalog()
    client = _client_with_catalog(fake)
    inst = _make_tracked_instance(
        client=client,
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
        http_port=http_port,
        logfile="odoo.log",
    )
    stub = MagicMock()
    stub.run.return_value = DetachedLaunchResult(
        pid=99,
        owner_kind="environment",
        owner_id=env_id,
        http_endpoint=f"http://127.0.0.1:{http_port}",
        log_path=str(wt / "odoo.log"),
    )
    with patch.object(OdooInstance, "run_detached_command", return_value=stub) as construct:
        result = inst.run_detached(args=("--dev=reload",))

    construct.assert_called_once_with(None, args=("--dev=reload",), cwd=None, env=None)
    stub.run.assert_called_once_with()
    assert result.pid == 99


@pytest.mark.unit
def test_detached_iter_logs_reads_bound_logfile(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    logfile = wt / "odoo.log"
    logfile.write_text("line one\nline two\n")
    fake = _FakeCatalog()
    client = _client_with_catalog(fake)
    inst = _make_tracked_instance(
        client=client,
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
        http_port=http_port,
        logfile="odoo.log",
    )
    lines = list(inst.iter_logs(tail=2, follow=False))
    assert lines == ["line one\n", "line two\n"]


@pytest.mark.unit
def test_detached_stop_targets_persisted_runtime(
    env_id: str, http_port: int, tmp_path: Path, real_catalog: BackupCatalog
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    (wt / "odoo.log").write_text("")
    client = _client_with_catalog(real_catalog)
    inst = _make_tracked_instance(
        client=client,
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
        http_port=http_port,
        logfile="odoo.log",
    )
    executor = RecordingExecutor(handles={"instance.detached": _alive_handle()})
    with (
        patch("odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor),
        patch("odoo_instance_sdk.resources.instance._process_create_time", return_value=1.0),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
    ):
        inst.run_detached()

    row = real_catalog.get_environment_runtime(env_id)
    assert row is not None
    assert int(str(row["root_pid"])) == 4242


def _resolved_context(client: object, source: object, instance: object) -> ResolvedContext:
    return ResolvedContext(
        client=cast("OdooClient", client),
        source=cast("RuntimeSource", source),
        instance=cast("OdooInstance", instance),
        provenance="explicit",
    )


def _cli_invoke(instance: object, args: list[str]) -> Result:
    project = MagicMock()
    with patch(
        "odoo_instance_sdk.cli.cli_context.ready_instance",
        return_value=_resolved_context(MagicMock(), project, instance),
    ):
        return CliRunner().invoke(cli, args)


@pytest.mark.unit
def test_cli_run_detach_returns_typed_result(env_id: str, http_port: int, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    (wt / "odoo.log").write_text("")
    fake = _FakeCatalog()
    client = _client_with_catalog(fake)
    inst = _make_tracked_instance(
        client=client,
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
        http_port=http_port,
        logfile="odoo.log",
    )
    executor = RecordingExecutor(handles={"instance.detached": _alive_handle()})
    with (
        patch("odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor),
        patch("odoo_instance_sdk.resources.instance._process_create_time", return_value=1.0),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
    ):
        result = _cli_invoke(inst, ["run", "-d", "--format", "json"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["result"]["pid"] == 4242
    assert envelope["result"]["owner_kind"] == "environment"
    assert envelope["result"]["http_endpoint"] == f"http://127.0.0.1:{http_port}"


@pytest.mark.unit
def test_cli_run_detach_dry_run_emits_plan_without_spawning(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    (wt / "odoo.log").write_text("")
    fake = _FakeCatalog()
    client = _client_with_catalog(fake)
    inst = _make_tracked_instance(
        client=client,
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
        http_port=http_port,
        logfile="odoo.log",
    )
    executor = RecordingExecutor(handles={"instance.detached": _alive_handle()})
    with (
        patch("odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
    ):
        result = _cli_invoke(inst, ["run", "-d", "--dry-run", "--format", "json"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["dry_run"] is True
    assert envelope["result"]["steps"]
    assert executor.spawned == []


@pytest.mark.unit
def test_cli_run_dash_d_after_delimiter_is_native(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    (wt / "odoo.log").write_text("")
    fake = _FakeCatalog()
    client = _client_with_catalog(fake)
    inst = _make_tracked_instance(
        client=client,
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
        http_port=http_port,
        logfile="odoo.log",
    )
    captured: dict[str, object] = {}

    def capture_run_foreground(*_args: object, args: tuple[str, ...] = ()) -> Command[int]:
        captured["args"] = args
        return Command.create(ExecutionPlan(), lambda _c: 0, ())

    with (
        patch.object(inst, "run_foreground_command", side_effect=capture_run_foreground),
        patch.object(inst, "run_detached_command") as detach_mock,
        patch(
            "odoo_instance_sdk.commands.context.ResolvedContext.check_port_free",
            return_value=True,
        ),
    ):
        result = _cli_invoke(inst, ["run", "--", "-d"])

    assert result.exit_code == 0, result.output
    detach_mock.assert_not_called()
    assert captured["args"] == ("-d",)


@pytest.mark.unit
def test_cli_run_foreground_unchanged_without_detach(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    (wt / "odoo.log").write_text("")
    fake = _FakeCatalog()
    client = _client_with_catalog(fake)
    inst = _make_tracked_instance(
        client=client,
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import sys; sys.exit(0)"),
        http_port=http_port,
        logfile="odoo.log",
    )
    foreground_called = False

    def capture_run_foreground(*_args: object, args: tuple[str, ...] = ()) -> Command[int]:
        nonlocal foreground_called
        foreground_called = True
        return Command.create(ExecutionPlan(), lambda _c: 0, ())

    with (
        patch.object(inst, "run_foreground_command", side_effect=capture_run_foreground),
        patch.object(inst, "run_detached_command") as detach_mock,
        patch(
            "odoo_instance_sdk.commands.context.ResolvedContext.check_port_free",
            return_value=True,
        ),
    ):
        result = _cli_invoke(inst, ["run"])

    assert result.exit_code == 0, result.output
    assert foreground_called is True
    detach_mock.assert_not_called()
