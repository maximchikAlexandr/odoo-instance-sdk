from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
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
from odoo_instance_sdk.execution import Command, ExecutionPlan, JsonValue
from odoo_instance_sdk.internal.proc import (
    PreparedStep,
    ProcessHandle,
    RecordingExecutor,
)
from odoo_instance_sdk.models import DetachedLaunchResult, ReadinessResult, StartConfig
from odoo_instance_sdk.resources.instance import OdooInstance, auxiliary_restore, runtime_identity
from odoo_instance_sdk.resources.instance.runtime import (
    _canonical_runtime_argv,
    _decode_launch_identity,
    _RuntimeBinding,
    _RuntimeIdentity,
    resolve_effective_logfile,
)
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

    def _register_project(
        self, _project_id: str, _repository_root: str | Path, _git_common_dir: str | Path
    ) -> None:
        return None

    def upsert_environment_runtime(self, environment_id: str, **kw: object) -> None:
        self.upsert_calls.append((environment_id, dict(kw)))

    def _upsert_runtime(self, owner_kind: str, owner_id: str, **kw: object) -> None:
        if owner_kind == "environment":
            self.upsert_environment_runtime(owner_id, **kw)
        else:
            self.upsert_calls.append((owner_id, dict(kw)))

    def clear_environment_runtime(self, environment_id: str) -> None:
        self.clear_calls.append(environment_id)

    def _clear_runtime(self, owner_kind: str, owner_id: str) -> None:
        self.clear_environment_runtime(owner_id)

    def get_environment_runtime(self, environment_id: str) -> None:
        return None

    def _clear_environment_runtime_if_matches(
        self, environment_id: str, *, root_pid: int, create_time: float
    ) -> bool:
        self.clear_calls.append(environment_id)
        return True


class _LegacyRuntimeCatalog(_FakeCatalog):
    def __init__(self, environment: dict[str, JsonValue], project_id: str) -> None:
        super().__init__()
        self.environment = environment
        self.project_id = project_id
        self.runtime: dict[str, JsonValue] | None = None

    def _upsert_runtime(self, owner_kind: str, owner_id: str, **kw: object) -> None:
        super()._upsert_runtime(owner_kind, owner_id, **kw)
        self.runtime = {
            **{key: cast("JsonValue", value) for key, value in kw.items()},
            "owner_kind": owner_kind,
            "owner_id": owner_id,
            "http_url": f"http://127.0.0.1:{cast('int', kw['http_port'])}",
        }

    def _monitor_snapshot_rows(self, *, project_id: str) -> object:
        assert project_id == self.project_id
        environments = ((self.environment, self.runtime),) if self.runtime is not None else ()
        return SimpleNamespace(project_runtimes=(), environments=environments)


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
    owner_kind: str = "environment",
) -> OdooInstance:
    start_cfg = StartConfig(
        http_port=http_port,
        http_interface="127.0.0.1",
        config_path=str(cwd / "odoo.conf"),
        db_name="mydb",
        logfile=logfile,
    )
    binding = (
        _RuntimeBinding(
            owner_kind="project",
            owner_id=f"project_{env_id}",
            project_id=f"project_{env_id}",
            repository_root=cwd,
            git_common_dir=cwd / ".git",
        )
        if owner_kind == "project"
        else None
    )
    return OdooInstance(
        config=InstanceConfig(
            base_url=f"http://127.0.0.1:{http_port}",
            start_config=start_cfg,
            command_prefix=command_prefix,
            default_cwd=cwd,
        ),
        _client=client,
        _environment_id=env_id if owner_kind == "environment" else None,
        _runtime_binding=binding,
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
        launch_identity_json=None,
        root_pid=pid,
        create_time=1.0,
        expected_executable=sys.executable,
        expected_argv=(sys.executable,),
        expected_cwd="/repo",
        expected_config_path="/repo/odoo.conf",
        expected_sensitive_argv_indices=(),
        expected_executable_prefix_length=1,
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
@pytest.mark.parametrize("owner_kind", ["environment", "project"])
def test_detached_launch_persists_identity_and_returns_promptly(
    env_id: str, http_port: int, tmp_path: Path, owner_kind: str
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
        owner_kind=owner_kind,
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
        result = inst.run_detached(
            http_port_selection="automatic_fallback",
            http_port_fallback_reason="occupied",
        )

    assert isinstance(result, DetachedLaunchResult)
    assert result.pid == 4242
    assert result.owner_kind == owner_kind
    assert result.owner_id == (env_id if owner_kind == "environment" else f"project_{env_id}")
    assert result.http_endpoint == f"http://127.0.0.1:{http_port}"
    assert result.http_port == http_port
    assert result.http_port_selection == "automatic_fallback"
    assert result.http_port_fallback_reason == "occupied"
    assert result.log_path == str(wt / "odoo.log")
    assert len(fake.upsert_calls) == 1
    assert isinstance(fake.upsert_calls[0][1]["launch_identity_json"], str)
    assert fake.clear_calls == []


@pytest.mark.unit
@pytest.mark.parametrize("owner_kind", ["environment", "project"])
def test_detached_persists_identity_from_exact_executed_step(
    env_id: str, http_port: int, tmp_path: Path, owner_kind: str
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    (wt / "odoo.log").write_text("")
    secret = "detached-db-secret"
    fake = _FakeCatalog()
    inst = _make_tracked_instance(
        client=_client_with_catalog(fake),
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
        http_port=http_port,
        logfile="odoo.log",
        owner_kind=owner_kind,
    )
    executor = RecordingExecutor(handles={"instance.detached": _alive_handle()})
    config = StartConfig(
        http_port=http_port,
        http_interface="127.0.0.1",
        config_path=None,
        db_name="exact-db",
        db_password=secret,
        logfile="odoo.log",
    )
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
        inst.run_detached(config)

    assert len(executor.spawned) == 1
    executed = executor.spawned[0]
    encoded = cast("str", fake.upsert_calls[0][1]["launch_identity_json"])
    identity = _decode_launch_identity(encoded)
    prefix_length = len(inst.config.command_prefix or ())
    assert identity.argv == _canonical_runtime_argv(
        executed.public_projection().argv,
        executable_prefix_length=prefix_length,
    )
    assert identity.sensitive_argv_indices == executed.sensitive_argv_indices
    assert identity.executable_prefix_length == prefix_length
    assert identity.cwd == str(Path(executed.cwd or "").resolve())
    assert secret not in encoded


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
    listener_ready = False

    def recorded(*_args: object, **kwargs: object) -> int | None:
        if kwargs["require_socket_owner"] and not listener_ready:
            return None
        return 4242

    def poll(*_args: object, **kwargs: object) -> ReadinessResult:
        nonlocal listener_ready
        assert cast("Callable[[], bool]", kwargs["alive_check"])()
        listener_ready = True
        return ReadinessResult(ok=True, elapsed=0.1, attempts=1)

    with (
        patch(
            "odoo_instance_sdk.resources.instance.planning._recorded_runtime_pid",
            side_effect=recorded,
        ) as proof,
        patch(
            "odoo_instance_sdk.internal.health.poll_health",
            side_effect=poll,
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
    assert [call.kwargs["require_socket_owner"] for call in proof.call_args_list] == [
        False,
        False,
        True,
    ]
    expected_argv = proof.call_args.kwargs["expected_argv"]
    logfile_index = expected_argv.index("--logfile")
    assert expected_argv[logfile_index + 1] == str((wt / "odoo.log").resolve())


@pytest.mark.unit
def test_environment_runtime_proof_uses_exact_owner_and_project_identity(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    checkout_root = tmp_path / "checkout"
    binding = _RuntimeBinding(
        owner_kind="environment",
        owner_id="env-a",
        project_id="project-a",
        repository_root=project_root,
        git_common_dir=project_root / ".git",
    )
    runtime: dict[str, JsonValue] = {
        "owner_kind": "environment",
        "owner_id": "env-a",
        "http_port": 18158,
        "http_url": "http://127.0.0.1:18158",
        "root_pid": 4242,
        "create_time": 123.5,
    }
    environment: dict[str, JsonValue] = {
        "project_id": "project-a",
        "repository_root": str(project_root),
        "git_common_dir": str(checkout_root / ".git"),
    }
    process = MagicMock()
    process.is_running.return_value = True
    process.status.return_value = "running"
    process.create_time.return_value = 123.5
    instance = MagicMock()
    instance.config.base_url = "http://127.0.0.1:18158"
    config = StartConfig(http_port=18158)
    with (
        patch(
            "odoo_instance_sdk.resources.instance.runtime_identity.psutil.Process",
            return_value=process,
        ),
        patch(
            "odoo_instance_sdk.resources.instance.runtime_identity._runtime_process_matches",
            return_value=True,
        ),
        patch(
            "odoo_instance_sdk.resources.instance.runtime_identity._socket_owned_by",
            return_value=True,
        ),
    ):
        assert (
            runtime_identity._runtime_row_matches(instance, config, binding, runtime, environment)
            == 4242
        )
        assert (
            runtime_identity._runtime_row_matches(
                instance, config, binding, {**runtime, "owner_id": "env-b"}, environment
            )
            is None
        )
        assert (
            runtime_identity._runtime_row_matches(
                instance, config, binding, runtime, {**environment, "project_id": "project-b"}
            )
            is None
        )
        assert (
            runtime_identity._runtime_row_matches(
                instance,
                config,
                binding,
                runtime,
                {**environment, "repository_root": str(checkout_root)},
            )
            is None
        )
        assert (
            runtime_identity._runtime_row_matches(
                instance, config, binding, {**runtime, "owner_kind": "project"}, environment
            )
            is None
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("runtime_changes", "environment_changes", "expected"),
    [
        pytest.param(
            {}, {"project_id": None}, True, id="legacy-null-matching-repository-and-common-dir"
        ),
        pytest.param(
            {},
            {"project_id": None, "repository_root": "/other"},
            False,
            id="legacy-null-wrong-repository",
        ),
        pytest.param(
            {},
            {"project_id": None, "git_common_dir": "/other/.git"},
            False,
            id="legacy-null-wrong-common-dir",
        ),
        pytest.param(
            {},
            {"project_id": "project-wrong"},
            False,
            id="explicit-wrong-project-id-does-not-fallback",
        ),
        pytest.param(
            {"owner_kind": "project"},
            {"project_id": None},
            False,
            id="legacy-null-wrong-owner-kind",
        ),
        pytest.param(
            {"owner_id": "env-wrong"},
            {"project_id": None},
            False,
            id="legacy-null-wrong-owner-id",
        ),
    ],
)
def test_environment_runtime_owner_matches_explicit_and_legacy_identities(
    tmp_path: Path,
    runtime_changes: dict[str, str],
    environment_changes: dict[str, str | None],
    expected: bool,
) -> None:
    repository_root = tmp_path / "project"
    binding = _RuntimeBinding(
        owner_kind="environment",
        owner_id="env-a",
        project_id="project-a",
        repository_root=repository_root,
        git_common_dir=repository_root / ".git",
    )
    runtime: dict[str, JsonValue] = {
        "owner_kind": "environment",
        "owner_id": "env-a",
    }
    environment: dict[str, JsonValue] = {
        "project_id": "project-a",
        "repository_root": str(repository_root),
        "git_common_dir": str(repository_root / ".git"),
    }

    assert (
        runtime_identity._runtime_owner_matches(
            binding,
            {**runtime, **runtime_changes},
            {**environment, **environment_changes},
        )
        is expected
    )


@pytest.mark.unit
def test_recorded_runtime_pid_reads_environment_binding(tmp_path: Path) -> None:
    binding = _RuntimeBinding(
        owner_kind="environment",
        owner_id="env-a",
        project_id="project-a",
        repository_root=tmp_path,
        git_common_dir=tmp_path / ".git",
    )
    instance = MagicMock()
    instance._runtime_binding = binding
    snapshot = MagicMock()
    snapshot.project_runtimes = ()
    snapshot.environments = (({"id": "env-a"}, {"root_pid": 4242}),)
    instance._client.get_catalog.return_value._monitor_snapshot_rows.return_value = snapshot
    with patch(
        "odoo_instance_sdk.resources.instance.runtime_identity._runtime_row_matches",
        return_value=4242,
    ) as row_matches:
        assert runtime_identity._recorded_runtime_pid(instance, StartConfig()) == 4242
    row_matches.assert_called_once()
    instance._client.get_catalog.return_value._monitor_snapshot_rows.assert_called_once_with(
        project_id="project-a"
    )


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
@pytest.mark.parametrize("output_format", ["json", "toon"])
def test_cli_run_detach_returns_typed_result(
    env_id: str, http_port: int, tmp_path: Path, output_format: str
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
        result = _cli_invoke(inst, ["run", "-d", "--format", output_format])

    assert result.exit_code == 0, result.output
    if output_format == "json":
        envelope = json.loads(result.stdout)
    else:
        from toon import DecodeOptions, decode

        envelope = decode(result.stdout, DecodeOptions(indent=2, strict=True))
    assert envelope["ok"] is True
    assert envelope["result"]["pid"] == 4242
    assert envelope["result"]["owner_kind"] == "environment"
    assert envelope["result"]["http_endpoint"] == f"http://127.0.0.1:{http_port}"
    assert envelope["result"]["http_port"] == http_port
    assert envelope["result"]["http_port_selection"] is None
    assert envelope["result"]["http_port_fallback_reason"] is None


@pytest.mark.unit
def test_public_cli_run_wait_ready_accepts_registered_legacy_environment(
    env_id: str, http_port: int, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    (wt / "odoo.log").write_text("")
    project_id = "project-legacy"
    environment: dict[str, JsonValue] = {
        "id": env_id,
        "project_id": None,
        "repository_root": str(wt.resolve()),
        "git_common_dir": str((wt / ".git").resolve()),
    }
    catalog = _LegacyRuntimeCatalog(environment, project_id)
    instance = _make_tracked_instance(
        client=_client_with_catalog(catalog),
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(30)"),
        http_port=http_port,
        logfile="odoo.log",
    )
    instance._runtime_binding = _RuntimeBinding(
        owner_kind="environment",
        owner_id=env_id,
        project_id=project_id,
        repository_root=wt.resolve(),
        git_common_dir=(wt / ".git").resolve(),
    )
    executor = RecordingExecutor(handles={"instance.detached": _alive_handle()})
    process = MagicMock()
    process.is_running.return_value = True
    process.status.return_value = "running"
    process.create_time.return_value = 1.0

    def poll_health(*_args: object, **kwargs: object) -> ReadinessResult:
        assert cast("Callable[[], bool]", kwargs["alive_check"])()
        return ReadinessResult(ok=True, elapsed=0.1, attempts=1)

    with (
        patch(
            "odoo_instance_sdk.resources.instance.planning.SubprocessExecutor",
            return_value=executor,
        ),
        patch(
            "odoo_instance_sdk.resources.instance.identity._process_create_time",
            return_value=1.0,
        ),
        patch.object(OdooInstance, "_ensure_dependencies_ready"),
        patch.object(OdooInstance, "_read_runtime_identity", return_value=None),
        patch(
            "odoo_instance_sdk.resources.instance.runtime_identity.psutil.Process",
            return_value=process,
        ),
        patch(
            "odoo_instance_sdk.resources.instance.runtime_identity._runtime_process_matches",
            return_value=True,
        ),
        patch(
            "odoo_instance_sdk.resources.instance.runtime_identity._socket_owned_by",
            return_value=True,
        ),
        patch(
            "odoo_instance_sdk.internal.health.poll_health",
            side_effect=poll_health,
        ),
        patch("odoo_instance_sdk.resources.instance.planning.terminate"),
        patch("odoo_instance_sdk.resources.instance.planning._verify_process_exit"),
    ):
        result = _cli_invoke(
            instance,
            [
                "--env",
                env_id,
                "run",
                "--detach",
                "--wait-ready",
                "--readiness-timeout",
                "60",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["result"]["pid"] == 4242
    assert catalog.runtime is not None
    assert catalog.runtime["owner_kind"] == "environment"
    assert catalog.runtime["owner_id"] == env_id
    assert environment["project_id"] is None
    assert catalog.clear_calls == []


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
