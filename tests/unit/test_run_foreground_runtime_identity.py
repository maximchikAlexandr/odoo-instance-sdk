from __future__ import annotations

import contextlib
import os
import signal
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
from odoo_instance_sdk.internal.proc import ProcessHandle, RecordingExecutor, SubprocessExecutor
from odoo_instance_sdk.internal.process_metrics import collect_process_tree
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog, CatalogValue


class _FakeCatalog:
    """Stand-in catalog with slots-free methods for patch-free assertions."""

    def __init__(self, *, upsert_raises: Exception | None = None) -> None:
        self.upsert_calls: list[tuple[str, dict[str, object]]] = []
        self.clear_calls: list[str] = []
        self._upsert_raises = upsert_raises

    def upsert_environment_runtime(self, environment_id: str, **kw: object) -> None:
        self.upsert_calls.append((environment_id, dict(kw)))
        if self._upsert_raises is not None:
            raise self._upsert_raises

    def clear_environment_runtime(self, environment_id: str) -> None:
        self.clear_calls.append(environment_id)

    def get_environment_runtime(self, environment_id: str) -> None:
        return None


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


def _init_git_worktree(path: Path) -> tuple[str, str]:
    """Init a tiny git repo with one commit; return (branch, commit_sha)."""
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    (path / "f.txt").write_text("x")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=True
    ).stdout.strip()
    return branch, sha


def _make_tracked_instance(
    *,
    client: OdooClient,
    env_id: str,
    cwd: Path,
    command_prefix: tuple[str, ...],
) -> OdooInstance:
    start_cfg = StartConfig(
        http_port=8069,
        http_interface="127.0.0.1",
        config_path=str(cwd / "odoo.conf"),
        db_name="mydb",
    )
    return OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            start_config=start_cfg,
            command_prefix=command_prefix,
            default_cwd=cwd,
        ),
        _client=client,
        _environment_id=env_id,
    )


def _make_manual_instance(client: OdooClient, cwd: Path) -> OdooInstance:
    start_cfg = StartConfig(
        http_port=8069,
        http_interface="127.0.0.1",
        config_path=str(cwd / "odoo.conf"),
        db_name="mydb",
    )
    return OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            start_config=start_cfg,
            command_prefix=(sys.executable, "-c", "import sys; sys.exit(0)"),
            default_cwd=cwd,
        ),
        _client=client,
    )


def _client_with_catalog(catalog: object) -> OdooClient:
    c = OdooClient(config=OdooClientConfig(executable="odoo"))
    c._catalog = cast("BackupCatalog | None", catalog)
    return c


@pytest.fixture()
def real_catalog(tmp_path: Path) -> BackupCatalog:
    return BackupCatalog(db_path=tmp_path / "cat.sqlite3")


@pytest.fixture()
def env_id(real_catalog: BackupCatalog) -> str:
    eid = str(uuid.uuid4())
    real_catalog.create_environment(_make_env(eid))
    return eid


@pytest.mark.unit
def test_persist_after_spawn_and_clear_on_normal_exit(
    env_id: str, tmp_path: Path, real_catalog: BackupCatalog
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    client = _client_with_catalog(real_catalog)
    inst = _make_tracked_instance(
        client=client,
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import sys; sys.exit(0)"),
    )
    exit_code = inst.run_foreground(args=("--stop-after-init",))
    assert exit_code == 0
    # cleared in finally
    assert real_catalog.get_environment_runtime(env_id) is None


@pytest.mark.unit
def test_persist_called_with_expected_fields(
    env_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt = tmp_path / "wt"
    branch, sha = _init_git_worktree(wt)
    fake = _FakeCatalog()
    client = _client_with_catalog(fake)
    inst = _make_tracked_instance(
        client=client,
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import sys; sys.exit(0)"),
    )
    monkeypatch.setattr("odoo_instance_sdk.resources.instance._process_create_time", lambda _: 1.0)

    exit_code = inst.run_foreground(args=("--stop-after-init",))

    assert exit_code == 0
    assert len(fake.upsert_calls) == 1
    eid, kw = fake.upsert_calls[0]
    assert eid == env_id
    assert isinstance(kw["root_pid"], int)
    assert kw["root_pid"] > 0
    assert isinstance(kw["create_time"], float)
    assert isinstance(kw["started_at"], str)
    assert kw["checkout_branch"] == branch
    assert kw["commit_sha"] == sha
    assert kw["http_url"] == "http://127.0.0.1:8069"
    assert kw["http_port"] == 8069
    assert kw["database_name"] == "mydb"
    assert fake.clear_calls == [env_id]


@pytest.mark.unit
def test_clear_on_nonzero_exit(env_id: str, tmp_path: Path, real_catalog: BackupCatalog) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    client = _client_with_catalog(real_catalog)
    inst = _make_tracked_instance(
        client=client,
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import sys; sys.exit(7)"),
    )
    exit_code = inst.run_foreground()
    assert exit_code == 7
    assert real_catalog.get_environment_runtime(env_id) is None


@pytest.mark.unit
def test_clear_on_crash_exception_propagates(env_id: str, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    fake = _FakeCatalog()
    client = _client_with_catalog(fake)
    inst = _make_tracked_instance(
        client=client,
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import sys; sys.exit(0)"),
    )

    boom = RuntimeError("wait blew up")
    with (
        patch("odoo_instance_sdk.internal.server.wait_foreground_process", side_effect=boom),
        pytest.raises(RuntimeError, match="wait blew up"),
    ):
        inst.run_foreground()

    assert fake.clear_calls == [env_id]


@pytest.mark.unit
def test_clear_on_keyboard_interrupt(env_id: str, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    fake = _FakeCatalog()
    client = _client_with_catalog(fake)
    inst = _make_tracked_instance(
        client=client,
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import sys; sys.exit(0)"),
    )

    with (
        patch(
            "odoo_instance_sdk.internal.server.wait_foreground_process",
            side_effect=KeyboardInterrupt,
        ),
        pytest.raises(KeyboardInterrupt),
    ):
        inst.run_foreground(args=("--dev=reload",))

    assert fake.clear_calls == [env_id]


@pytest.mark.unit
def test_foreground_keyboard_interrupt_cleans_up_the_owned_process_group(
    tmp_path: Path,
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    client = _client_with_catalog(_FakeCatalog())
    inst = _make_manual_instance(client, wt)
    process = MagicMock()
    process.pid = 4242
    handle = ProcessHandle(process, (), 4242, 9898, True)
    executor = RecordingExecutor(handles={"instance.foreground": handle})

    with (
        patch("odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor),
        patch(
            "odoo_instance_sdk.internal.server.wait_foreground_process",
            side_effect=KeyboardInterrupt,
        ),
        patch("odoo_instance_sdk.resources.instance.terminate") as terminate,
        pytest.raises(KeyboardInterrupt),
    ):
        inst.run_foreground(args=("--dev=reload",))

    terminate.assert_called_once_with(handle, process_group_id=9898, timeout=5.0)


@pytest.mark.unit
def test_foreground_artifact_lock_wraps_secret_write_spawn_wait_and_cleanup(
    tmp_path: Path,
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    client = _client_with_catalog(_FakeCatalog())
    inst = _make_manual_instance(client, wt)
    config = StartConfig(
        http_port=8069,
        http_interface="127.0.0.1",
        config_path=None,
        db_password="secret",
    )
    events: list[str] = []

    @contextlib.contextmanager
    def artifact_lock(_self: OdooInstance) -> Iterator[None]:
        events.append("lock-enter")
        try:
            yield
        finally:
            events.append("lock-exit")

    process = MagicMock()
    process.pid = 4242
    handle = ProcessHandle(process, (), 4242, 4242, True)

    class EventRecordingExecutor(RecordingExecutor):
        def spawn(self, step: object) -> ProcessHandle:
            events.append("spawn")
            return super().spawn(step)  # type: ignore[arg-type]

    executor = EventRecordingExecutor(handles={"instance.foreground": handle})

    def write_secret(*_args: object, **_kwargs: object) -> None:
        events.append("secret-write")

    def cleanup_secret(*_args: object, **_kwargs: object) -> None:
        events.append("secret-cleanup")

    def wait_process(_process: object) -> int:
        events.append("wait")
        return 0

    with (
        patch.object(OdooInstance, "_artifact_lock", artifact_lock),
        patch("odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor),
        patch(
            "odoo_instance_sdk.resources.instance._write_secret_config", side_effect=write_secret
        ),
        patch(
            "odoo_instance_sdk.resources.instance.cleanup_secret_config",
            side_effect=cleanup_secret,
        ),
        patch(
            "odoo_instance_sdk.internal.server.wait_foreground_process", side_effect=wait_process
        ),
    ):
        assert inst.run_foreground(config, args=("--stop-after-init",)) == 0

    assert events == [
        "lock-enter",
        "secret-write",
        "spawn",
        "wait",
        "secret-cleanup",
        "lock-exit",
    ]


@pytest.mark.unit
def test_manual_instance_no_persist_no_clear(tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    fake = _FakeCatalog()
    client = _client_with_catalog(fake)
    inst = _make_manual_instance(client, wt)

    exit_code = inst.run_foreground()

    assert exit_code == 0
    assert fake.upsert_calls == []
    assert fake.clear_calls == []


@pytest.mark.unit
def test_core_psutil_persists_exact_identity(
    env_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    fake = _FakeCatalog()
    client = _client_with_catalog(fake)
    inst = _make_tracked_instance(
        client=client,
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import sys; sys.exit(0)"),
    )

    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance._process_create_time", lambda _: 123.0
    )
    assert inst.run_foreground() == 0
    assert fake.upsert_calls[0][1]["create_time"] == 123.0


@pytest.mark.unit
def test_persisted_live_identity_is_accepted_by_default_process_collector(
    env_id: str, tmp_path: Path
) -> None:
    """The writer and default collector must share psutil's exact clock."""
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    catalog = _FakeCatalog()
    inst = _make_tracked_instance(
        client=_client_with_catalog(catalog),
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(60)"),
    )

    def inspect_live_identity(proc: Any) -> int:
        assert len(catalog.upsert_calls) == 1
        identity = catalog.upsert_calls[0][1]
        result = collect_process_tree(
            cast("int", identity["root_pid"]),
            cast("float", identity["create_time"]),
            prev_cpu_point=None,
        )
        assert result is not None
        proc.terminate()
        return cast("int", proc.wait(timeout=5))

    with patch(
        "odoo_instance_sdk.internal.server.wait_foreground_process",
        side_effect=inspect_live_identity,
    ):
        assert inst.run_foreground() == -signal.SIGTERM


@pytest.mark.unit
def test_persist_failure_aborts_run_but_still_clears_runtime(env_id: str, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    fake = _FakeCatalog(upsert_raises=RuntimeError("catalog down"))
    client = _client_with_catalog(fake)
    inst = _make_tracked_instance(
        client=client,
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import time; time.sleep(60)"),
    )

    spawned: list[object] = []
    original_spawn = SubprocessExecutor.spawn

    def record_spawn(executor: SubprocessExecutor, step: Any) -> Any:
        handle = original_spawn(executor, step)
        spawned.append(handle.process)
        return handle

    with (
        patch.object(SubprocessExecutor, "spawn", record_spawn),
        pytest.raises(RuntimeError, match="catalog down"),
    ):
        inst.run_foreground()

    # Clear is attempted even when mandatory persistence fails.
    assert fake.clear_calls == [env_id]
    proc = cast("Any", spawned[0])
    assert proc.poll() is not None
    with pytest.raises(ProcessLookupError):
        os.kill(proc.pid, 0)


@pytest.mark.unit
def test_persist_failure_preserves_original_error_when_cleanup_fails(
    env_id: str, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _init_git_worktree(wt)
    fake = _FakeCatalog(upsert_raises=RuntimeError("catalog down"))
    inst = _make_tracked_instance(
        client=_client_with_catalog(fake),
        env_id=env_id,
        cwd=wt,
        command_prefix=(sys.executable, "-c", "import sys; sys.exit(0)"),
    )
    with (
        patch(
            "odoo_instance_sdk.resources.instance.terminate",
            side_effect=OSError("cleanup failed"),
        ),
        pytest.raises(RuntimeError, match="catalog down"),
    ):
        inst.run_foreground()
    assert fake.clear_calls == [env_id]
