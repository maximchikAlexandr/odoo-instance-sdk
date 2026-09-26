from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import uuid
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner, Result

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.commands.cli_parts import callbacks
from odoo_instance_sdk.commands.context import ResolvedContext, RuntimeSource
from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
from odoo_instance_sdk.exceptions import (
    DetachedLaunchCleanupError,
    InstanceConfigurationError,
    LogfileUnwritableError,
    ReadinessTimeoutError,
)
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.proc import (
    PreparedStep,
    ProcessHandle,
    RecordingExecutor,
)
from odoo_instance_sdk.models import DetachedLaunchResult, ReadinessResult, StartConfig
from odoo_instance_sdk.resources.instance import OdooInstance, auxiliary_restore, runtime_identity
from odoo_instance_sdk.resources.instance.runtime import _RuntimeIdentity, resolve_effective_logfile
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


def _runtime_identity(pid: int = 4242) -> _RuntimeIdentity:
    return _RuntimeIdentity(
        owner_kind="environment",
        owner_id="env",
        project_id="",
        environment_id="env",
        root_pid=pid,
        create_time=1.0,
        expected_executable=sys.executable,
        expected_argv=(sys.executable,),
        expected_cwd="/repo",
        expected_config_path="/repo/odoo.conf",
        live_create_time=1.0,
        live_executable=sys.executable,
        live_argv=(sys.executable,),
        live_cwd="/repo",
        live_config_path="/repo/odoo.conf",
        process_group_id=pid,
    )


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
        patch(
            "odoo_instance_sdk.resources.instance.planning.SubprocessExecutor",
            return_value=executor,
        ),
        patch(
            "odoo_instance_sdk.resources.instance.identity._process_create_time", return_value=1.0
        ),
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
        patch(
            "odoo_instance_sdk.resources.instance.planning.SubprocessExecutor",
            return_value=executor,
        ),
        patch(
            "odoo_instance_sdk.resources.instance.identity._process_create_time", return_value=1.0
        ),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
        pytest.raises(InstanceConfigurationError, match="exited immediately"),
    ):
        inst.run_detached()

    assert fake.upsert_calls == []
    assert fake.clear_calls == [env_id]


@pytest.mark.unit
def test_detached_no_logfile_resolves_fallback_next_to_config(
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
    executor = RecordingExecutor(handles={"instance.detached": _alive_handle()})
    with (
        patch(
            "odoo_instance_sdk.resources.instance.planning.SubprocessExecutor",
            return_value=executor,
        ),
        patch(
            "odoo_instance_sdk.resources.instance.identity._process_create_time", return_value=1.0
        ),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
    ):
        result = inst.run_detached()

    assert isinstance(result, DetachedLaunchResult)
    assert result.log_path == str((wt / "odoo.log").resolve())
    assert (wt / "odoo.log").is_file()
    spawned_argv = executor.spawned[0].argv
    assert "--logfile" in spawned_argv
    logfile_index = spawned_argv.index("--logfile")
    assert spawned_argv[logfile_index + 1] == str((wt / "odoo.log").resolve())


@pytest.mark.unit
def test_detached_unwritable_logfile_fails_before_spawn(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    fake = _FakeCatalog()
    client = _client_with_catalog(fake)
    # Point the fallback at a path whose parent is a regular file, so mkdir fails.
    bad_parent = wt / "blocker"
    bad_parent.write_text("not a directory")
    start_cfg = StartConfig(
        http_port=http_port,
        http_interface="127.0.0.1",
        config_path=str(bad_parent / "odoo.conf"),
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

    with pytest.raises(LogfileUnwritableError, match="logfile_unwritable"):
        inst.run_detached()


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
        patch(
            "odoo_instance_sdk.resources.instance.planning.SubprocessExecutor",
            return_value=executor,
        ),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
    ):
        command = inst.run_detached_command()

    plan_argv = command.plan.process_steps[0].argv
    assert plan_argv[0] == sys.executable
    assert {
        "instance.detached.assert_port",
        "instance.detached.spawn",
        "instance.detached.confirm_alive",
        "instance.detached.persist",
    }.issubset({step.step_id for step in command.plan.steps})
    assert executor.spawned == []
    assert executor.executed == []
    assert fake.upsert_calls == []


@pytest.mark.unit
def test_detached_readiness_timeout_requires_waiting(env_id: str, http_port: int) -> None:
    instance = OdooInstance(
        config=InstanceConfig(
            base_url=f"http://127.0.0.1:{http_port}",
            start_config=StartConfig(http_port=http_port),
        ),
        _client=_client_with_catalog(_FakeCatalog()),
        _environment_id=env_id,
    )

    with pytest.raises(InstanceConfigurationError, match="requires wait_ready"):
        instance.run_detached_command(readiness_timeout=1.0)


@pytest.mark.unit
def test_detached_readiness_plan_captures_wait_and_cleanup(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    fake = _FakeCatalog()
    instance = _make_tracked_instance(
        client=_client_with_catalog(fake),
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
        http_port=http_port,
        logfile="odoo.log",
    )

    with patch.object(OdooInstance, "_ensure_dependencies_ready"):
        command = instance.run_detached_command(wait_ready=True, readiness_timeout=3.5)

    action_ids = {step.step_id for step in command.plan.steps}
    assert {
        "instance.detached.readiness",
        "instance.detached.cleanup",
    }.issubset(action_ids)


@pytest.mark.unit
def test_detached_readiness_defaults_to_disabled_compatibility(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    (wt / "odoo.log").write_text("")
    instance = _make_tracked_instance(
        client=_client_with_catalog(_FakeCatalog()),
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
        http_port=http_port,
        logfile="odoo.log",
    )
    executor = RecordingExecutor(handles={"instance.detached": _alive_handle()})
    with (
        patch(
            "odoo_instance_sdk.resources.instance.planning.SubprocessExecutor",
            return_value=executor,
        ),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
        patch.object(OdooInstance, "_wait_for_detached_ready") as wait_ready,
    ):
        instance.run_detached()
    wait_ready.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), float("-inf"), True])
def test_detached_readiness_requires_finite_positive_timeout(
    env_id: str, http_port: int, timeout: float
) -> None:
    instance = OdooInstance(
        config=InstanceConfig(
            base_url=f"http://127.0.0.1:{http_port}",
            start_config=StartConfig(http_port=http_port),
        ),
        _client=_client_with_catalog(_FakeCatalog()),
        _environment_id=env_id,
    )
    with pytest.raises(InstanceConfigurationError, match="finite positive"):
        instance.run_detached_command(wait_ready=True, readiness_timeout=timeout)


@pytest.mark.unit
def test_detached_readiness_plan_records_default_sixty_second_timeout(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    instance = _make_tracked_instance(
        client=_client_with_catalog(_FakeCatalog()),
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
        http_port=http_port,
        logfile="odoo.log",
    )
    with patch.object(OdooInstance, "_ensure_dependencies_ready"):
        command = instance.run_detached_command(wait_ready=True)
    readiness = next(
        step for step in command.plan.steps if step.step_id == "instance.detached.readiness"
    )
    assert getattr(readiness, "details", None) == {"timeout": 60.0}


@pytest.mark.unit
def test_detached_readiness_success_waits_and_keeps_runtime(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    (wt / "odoo.log").write_text("")
    fake = _FakeCatalog()
    instance = _make_tracked_instance(
        client=_client_with_catalog(fake),
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
        http_port=http_port,
        logfile="odoo.log",
    )
    executor = RecordingExecutor(handles={"instance.detached": _alive_handle()})
    identity = _runtime_identity()
    with (
        patch(
            "odoo_instance_sdk.resources.instance.planning.SubprocessExecutor",
            return_value=executor,
        ),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
        patch.object(OdooInstance, "_read_runtime_identity", return_value=identity),
        patch.object(
            OdooInstance,
            "_wait_for_detached_ready",
            return_value=ReadinessResult(ok=True, elapsed=0.1, attempts=1, final_status="200"),
        ) as wait_ready,
        patch.object(OdooInstance, "_clear_runtime_identity_if_matches") as clear_runtime,
    ):
        result = instance.run_detached(wait_ready=True)
    assert result.pid == 4242
    wait_ready.assert_called_once()
    clear_runtime.assert_not_called()
    assert fake.clear_calls == []


@pytest.mark.unit
def test_detached_readiness_early_exit_does_not_clear_unpersisted_identity(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    (wt / "odoo.log").write_text("")
    instance = _make_tracked_instance(
        client=_client_with_catalog(_FakeCatalog()),
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import sys; sys.exit(1)"),
        http_port=http_port,
        logfile="odoo.log",
    )
    executor = RecordingExecutor(handles={"instance.detached": _dead_handle()})
    with (
        patch(
            "odoo_instance_sdk.resources.instance.planning.SubprocessExecutor",
            return_value=executor,
        ),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
        patch("odoo_instance_sdk.resources.instance.planning.terminate") as terminate_process,
        patch.object(OdooInstance, "_clear_runtime_identity") as clear_runtime,
        pytest.raises(InstanceConfigurationError, match="exited immediately"),
    ):
        instance.run_detached(wait_ready=True)
    terminate_process.assert_called_once_with(
        executor.handles["instance.detached"], process_group_id=4242, timeout=5.0
    )
    clear_runtime.assert_not_called()


@pytest.mark.unit
def test_detached_readiness_timeout_clears_only_captured_identity_after_group_exit(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    (wt / "odoo.log").write_text("")
    instance = _make_tracked_instance(
        client=_client_with_catalog(_FakeCatalog()),
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
        http_port=http_port,
        logfile="odoo.log",
    )
    executor = RecordingExecutor(handles={"instance.detached": _alive_handle()})
    identity = _runtime_identity()
    with (
        patch(
            "odoo_instance_sdk.resources.instance.planning.SubprocessExecutor",
            return_value=executor,
        ),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
        patch.object(OdooInstance, "_read_runtime_identity", return_value=identity),
        patch.object(
            OdooInstance,
            "_wait_for_detached_ready",
            side_effect=ReadinessTimeoutError(1.0),
        ),
        patch("odoo_instance_sdk.resources.instance.planning.terminate") as terminate_process,
        patch("odoo_instance_sdk.resources.instance.planning._verify_process_exit") as verify_exit,
        patch.object(OdooInstance, "_clear_runtime_identity_if_matches") as clear_runtime,
        pytest.raises(ReadinessTimeoutError, match="Readiness not reached"),
    ):
        instance.run_detached(wait_ready=True)
    terminate_process.assert_called_once()
    verify_exit.assert_called_once_with(4242)
    clear_runtime.assert_called_once_with(identity)


@pytest.mark.unit
def test_detached_readiness_cleanup_failure_retains_identity_and_diagnostics(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    (wt / "odoo.log").write_text("")
    instance = _make_tracked_instance(
        client=_client_with_catalog(_FakeCatalog()),
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
        http_port=http_port,
        logfile="odoo.log",
    )
    executor = RecordingExecutor(handles={"instance.detached": _alive_handle()})
    identity = _runtime_identity()
    with (
        patch(
            "odoo_instance_sdk.resources.instance.planning.SubprocessExecutor",
            return_value=executor,
        ),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
        patch.object(OdooInstance, "_read_runtime_identity", return_value=identity),
        patch.object(
            OdooInstance,
            "_wait_for_detached_ready",
            side_effect=ReadinessTimeoutError(1.0),
        ),
        patch.object(ProcessHandle, "drain_tails", return_value={"stdout": "out", "stderr": "err"}),
        patch(
            "odoo_instance_sdk.resources.instance.planning.terminate",
            side_effect=RuntimeError("group still alive"),
        ),
        patch.object(OdooInstance, "_clear_runtime_identity_if_matches") as clear_runtime,
        pytest.raises(DetachedLaunchCleanupError) as raised,
    ):
        instance.run_detached(wait_ready=True)
    assert raised.value.pid == 4242
    assert "surviving owned process pid=4242" in str(raised.value)
    cause = raised.value.__cause__
    assert cause is not None
    notes = " ".join(cause.__notes__ or [])
    assert "environment=" in notes
    assert "database='mydb'" in notes
    assert "logfile=" in notes
    assert "stdout_tail='out'" in notes
    assert "stderr_tail='err'" in notes
    clear_runtime.assert_not_called()


@pytest.mark.unit
def test_detached_readiness_does_not_terminate_unrelated_process(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    (wt / "odoo.log").write_text("")
    instance = _make_tracked_instance(
        client=_client_with_catalog(_FakeCatalog()),
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
        http_port=http_port,
        logfile="odoo.log",
    )
    executor = RecordingExecutor(handles={"instance.detached": _alive_handle(4242)})
    identity = _runtime_identity()
    with (
        patch(
            "odoo_instance_sdk.resources.instance.planning.SubprocessExecutor",
            return_value=executor,
        ),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
        patch.object(OdooInstance, "_read_runtime_identity", return_value=identity),
        patch.object(
            OdooInstance,
            "_wait_for_detached_ready",
            side_effect=InstanceConfigurationError("wrong listener"),
        ),
        patch("odoo_instance_sdk.resources.instance.planning.terminate") as terminate_process,
        patch("odoo_instance_sdk.resources.instance.planning._verify_process_exit"),
        patch.object(OdooInstance, "_clear_runtime_identity_if_matches"),
        pytest.raises(InstanceConfigurationError, match="wrong listener"),
    ):
        instance.run_detached(wait_ready=True)
    assert terminate_process.call_args.args[0] is executor.handles["instance.detached"]
    assert terminate_process.call_args.args[0].pid == 4242


@pytest.mark.unit
def test_detached_readiness_passes_effective_logfile_argv_to_identity_proof(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    (wt / "odoo.log").write_text("")
    instance = _make_tracked_instance(
        client=_client_with_catalog(_FakeCatalog()),
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
        http_port=http_port,
        logfile="odoo.log",
    )
    with (
        patch(
            "odoo_instance_sdk.resources.instance.planning._recorded_runtime_pid", return_value=4242
        ) as proof,
        patch(
            "odoo_instance_sdk.internal.health.poll_health",
            return_value=ReadinessResult(ok=True, elapsed=0.1, attempts=1),
        ),
    ):
        command = instance.run_detached_command(wait_ready=True)
        step = cast(
            "PreparedStep",
            next(step for step in command.plan.steps if step.step_id == "instance.detached"),
        )
        config = instance.config.start_config
        assert config is not None
        result = instance._wait_for_detached_ready(
            config,
            step,
            timeout=60.0,
            pid=4242,
        )
    assert result.ok
    expected_argv = proof.call_args.kwargs["expected_argv"]
    logfile_index = expected_argv.index("--logfile")
    assert expected_argv[logfile_index + 1] == str((wt / "odoo.log").resolve())


@pytest.mark.unit
def test_auxiliary_restore_consumes_shared_runtime_identity_proof() -> None:
    assert auxiliary_restore._recorded_runtime_pid is runtime_identity._recorded_runtime_pid


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
        patch(
            "odoo_instance_sdk.resources.instance.planning.SubprocessExecutor",
            return_value=executor,
        ),
        patch(
            "odoo_instance_sdk.resources.instance.identity._process_create_time", return_value=1.0
        ),
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
        patch(
            "odoo_instance_sdk.resources.instance.planning.SubprocessExecutor",
            return_value=executor,
        ),
        patch(
            "odoo_instance_sdk.resources.instance.identity._process_create_time", return_value=1.0
        ),
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
        patch(
            "odoo_instance_sdk.resources.instance.planning.SubprocessExecutor",
            return_value=executor,
        ),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
        patch(
            "odoo_instance_sdk.commands.cli_parts.callbacks.shutil.which",
            return_value="/usr/bin/pg_dump",
        ),
    ):
        result = _cli_invoke(inst, ["run", "-d", "--dry-run", "--format", "json"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["dry_run"] is True
    assert envelope["result"]["steps"]
    run_step = next(
        step for step in envelope["result"]["steps"] if step["step_id"] == "instance.detached"
    )
    overrides = dict(run_step["environment_overrides"])
    if os.name != "nt":
        assert overrides["ODCLI_REAL_PG_DUMP"] == "<redacted>"
        assert overrides["PATH"] == "<redacted>"
    assert executor.spawned == []


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX pg_dump shim")
@pytest.mark.parametrize("file_args", [("--file=dump.sql",), ("--file", "dump.sql")])
def test_pg_dump_shim_repairs_file_stdout_without_changing_pipe(
    tmp_path: Path, file_args: tuple[str, ...]
) -> None:
    real = tmp_path / "real-pg-dump"
    real.write_text("#!/bin/sh\nprintf ok\n", encoding="utf-8")
    real.chmod(0o755)
    shim = Path(callbacks.__file__).resolve().parents[2] / "internal/pg_dump_compat/pg_dump"
    environment = {**os.environ, "ODCLI_REAL_PG_DUMP": str(real)}

    with open(os.devnull) as read_only_stdout:
        broken_result = subprocess.run(
            [str(real), *file_args], stdout=read_only_stdout, check=False
        )
        file_result = subprocess.run(
            [str(shim), *file_args], env=environment, stdout=read_only_stdout, check=False
        )
    pipe_result = subprocess.run(
        [str(shim), "--format=c"], env=environment, capture_output=True, check=False
    )

    assert broken_result.returncode != 0
    assert file_result.returncode == 0
    assert pipe_result.returncode == 0
    assert pipe_result.stdout == b"ok"


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

    def capture_run_foreground(
        *_args: object, args: tuple[str, ...] = (), env: dict[str, str] | None = None
    ) -> Command[int]:
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

    def capture_run_foreground(
        *_args: object, args: tuple[str, ...] = (), env: dict[str, str] | None = None
    ) -> Command[int]:
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


@pytest.mark.unit
def test_detached_explicit_logfile_has_priority_over_fallback(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    explicit = tmp_path / "explicit.log"
    explicit.write_text("")
    fake = _FakeCatalog()
    client = _client_with_catalog(fake)
    inst = _make_tracked_instance(
        client=client,
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
        http_port=http_port,
        logfile=str(explicit),
    )
    executor = RecordingExecutor(handles={"instance.detached": _alive_handle()})
    with (
        patch(
            "odoo_instance_sdk.resources.instance.planning.SubprocessExecutor",
            return_value=executor,
        ),
        patch(
            "odoo_instance_sdk.resources.instance.identity._process_create_time", return_value=1.0
        ),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
    ):
        result = inst.run_detached()

    assert result.log_path == str(explicit.resolve())
    # The fallback next to the config is NOT created when an explicit logfile wins.
    assert not (wt / "odoo.log").exists()


@pytest.mark.unit
def test_detached_dry_run_shows_resolved_logfile_without_creating(
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
    executor = RecordingExecutor(handles={"instance.detached": _alive_handle()})
    with (
        patch(
            "odoo_instance_sdk.resources.instance.planning.SubprocessExecutor",
            return_value=executor,
        ),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
    ):
        command = inst.run_detached_command()

    plan_argv = command.plan.process_steps[0].argv
    assert "--logfile" in plan_argv
    logfile_index = plan_argv.index("--logfile")
    assert plan_argv[logfile_index + 1] == str((wt / "odoo.log").resolve())
    # dry-run must not create the directory or file
    assert not (wt / "odoo.log").exists()
    assert executor.spawned == []


@pytest.mark.unit
def test_detached_main_checkout_fallback_next_to_config(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    """Main checkout (no logfile) falls back to odoo.log next to the effective config."""
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    fake = _FakeCatalog()
    client = _client_with_catalog(fake)
    config_file = wt / "odoo.conf"
    config_file.write_text("[options]\nhttp_port = 8069\n")
    start_cfg = StartConfig(
        http_port=http_port,
        http_interface="127.0.0.1",
        config_path=str(config_file),
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
    executor = RecordingExecutor(handles={"instance.detached": _alive_handle()})
    with (
        patch(
            "odoo_instance_sdk.resources.instance.planning.SubprocessExecutor",
            return_value=executor,
        ),
        patch(
            "odoo_instance_sdk.resources.instance.identity._process_create_time", return_value=1.0
        ),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
    ):
        result = inst.run_detached()

    assert result.log_path == str((wt / "odoo.log").resolve())
    assert (wt / "odoo.log").is_file()


@pytest.mark.unit
def test_two_isolated_environments_use_different_logfiles(
    env_client: OdooClient,
    project_manifest: Path,
    fake_python: Path,
    source_config: Path,
) -> None:
    from odoo_instance_sdk.resources.environment import (
        EnvironmentCheckoutOptions,
        EnvironmentDatabaseMode,
    )

    env_a = env_client.environments.checkout(
        project_manifest,
        "feat/logs-a",
        options=EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
            source_database="comerta",
        ),
    )
    env_b = env_client.environments.checkout(
        project_manifest,
        "feat/logs-b",
        options=EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
            source_database="comerta",
        ),
    )
    log_a = Path(env_a.generated_config_path).parent / "odoo.log"
    log_b = Path(env_b.generated_config_path).parent / "odoo.log"
    assert log_a.resolve() != log_b.resolve()
    assert log_a.is_file()
    assert log_b.is_file()
    assert StartConfig.from_odoo_config(env_a.generated_config_path).logfile == str(log_a.resolve())
    assert StartConfig.from_odoo_config(env_b.generated_config_path).logfile == str(log_b.resolve())


@pytest.mark.unit
def test_old_isolated_environment_without_logfile_uses_fallback(
    env_client: OdooClient,
    project_manifest: Path,
    fake_python: Path,
    source_config: Path,
    tmp_path: Path,
) -> None:
    from odoo_instance_sdk.resources.environment import (
        EnvironmentCheckoutOptions,
        EnvironmentDatabaseMode,
    )

    env = env_client.environments.checkout(
        project_manifest,
        "feat/logs-old",
        options=EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
            source_database="comerta",
        ),
    )
    # Simulate an old/partial isolated config by stripping the logfile line.
    cfg_path = Path(env.generated_config_path)
    text = cfg_path.read_text()
    cfg_path.write_text(text.replace("logfile = ", "# logfile = "))
    assert StartConfig.from_odoo_config(cfg_path).logfile is None

    inst = env_client.instance.from_environment(env)
    start_config = inst.config.start_config
    assert start_config is not None
    resolved = resolve_effective_logfile(start_config, inst.config.default_cwd)
    assert resolved == (cfg_path.parent / "odoo.log").resolve()
