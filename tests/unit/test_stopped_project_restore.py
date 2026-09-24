from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import DatabaseManagerUnavailableError, InstanceConfigurationError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    PrivateJsonValue,
    ProcessHandle,
    RecordingExecutor,
)
from odoo_instance_sdk.models import Backup, BackupFormat, StartConfig
from odoo_instance_sdk.resources.database import DatabaseResource
from odoo_instance_sdk.resources.instance import OdooInstance, auxiliary_restore_session


def _instance(tmp_path: Path) -> OdooInstance:
    config_path = tmp_path / "odoo.conf"
    config_path.write_text("[options]\nadmin_passwd = private\n")
    client = MagicMock()
    return OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:0",
            start_config=StartConfig(config_path=str(config_path), http_port=0),
            command_prefix=("/usr/bin/python", "/project/odoo-bin"),
            default_cwd=tmp_path,
        ),
        _client=client,
    )


def session_steps(session: object) -> tuple[PreparedStep | PreparedAction, ...]:
    from odoo_instance_sdk.resources.instance import AuxiliaryRestoreSession

    assert isinstance(session, AuxiliaryRestoreSession)
    return (
        session.probe_action,
        session.port_action,
        session.start_step,
        session.ready_action,
        session.backup_request_action,
        session.restore_request_action,
        session.cleanup_action,
    )


def run_session(
    session: object,
    callback: Callable[[Any], None],
    executor: RecordingExecutor,
) -> None:
    steps = session_steps(session)
    command = Command.create(
        ExecutionPlan(steps=tuple(step.public_projection() for step in steps)),
        callback,
        steps,
        executor=executor,
    )
    command.run()


def recorded_project_runtime(
    *, owner_id: str, http_port: int, pid: int, create_time: float, http_url: str
) -> dict[str, int | float | str]:
    return {
        "owner_kind": "project",
        "owner_id": owner_id,
        "http_port": http_port,
        "http_url": http_url,
        "root_pid": pid,
        "create_time": create_time,
    }


def test_auxiliary_restore_session_captures_bounded_runtime_and_cleans_owned_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = _instance(tmp_path)
    session = auxiliary_restore_session(instance)
    handle = ProcessHandle(
        process=MagicMock(),
        argv=session.start_step.argv,
        process_group_id=123,
        session_id=123,
        inherited_stdio=False,
    )
    executor = RecordingExecutor(handles={session.start_step.step_id: handle})
    cast("Any", instance._client.unregister_process).return_value = (None, None)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore._assert_http_port_free",
        lambda _config: None,
    )
    wait_ready = MagicMock(return_value=MagicMock(ok=True))
    monkeypatch.setattr(OdooInstance, "wait_ready", wait_ready)

    from odoo_instance_sdk.internal.proc import RunContext

    def callback(context: RunContext[PrivateJsonValue]) -> None:
        session.ensure_started(context)
        session.cleanup(context)

    run_session(session, callback, executor)

    assert session.start_step.long_running is True
    assert session.start_step.inherit_stdio is True
    assert "--database=__odcli_restore__" not in session.start_step.argv
    assert "--db-filter=^$" not in session.start_step.argv
    assert executor.spawned == [session.start_step]
    assert wait_ready.call_args.kwargs == {"timeout": 60.0, "database_manager": True}
    cast("Any", instance._client.register_process).assert_called_once()
    cast("Any", instance._client.unregister_process).assert_called_once()


def test_foreign_listener_is_rejected_before_auxiliary_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = _instance(tmp_path)
    session = auxiliary_restore_session(instance)
    executor = RecordingExecutor(handles={})
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore._assert_http_port_free",
        MagicMock(side_effect=InstanceConfigurationError("port-conflict: ownership unknown")),
    )

    from odoo_instance_sdk.internal.proc import RunContext

    def callback(context: RunContext[PrivateJsonValue]) -> None:
        session.ensure_started(context)

    with pytest.raises(InstanceConfigurationError, match="port-conflict"):
        run_session(session, callback, executor)
    assert executor.spawned == []
    cast("Any", instance._client.register_process).assert_not_called()


def test_responsive_unrecorded_manager_is_rejected_without_http_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = _instance(tmp_path)
    session = auxiliary_restore_session(instance)
    http_client = MagicMock()
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.database.backup_restore_parts.queries.httpx.Client",
        http_client,
    )
    port_check = MagicMock(side_effect=InstanceConfigurationError("port-conflict"))
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore._assert_http_port_free",
        port_check,
    )
    executor = RecordingExecutor(handles={})

    from odoo_instance_sdk.internal.proc import RunContext

    def callback(context: RunContext[PrivateJsonValue]) -> None:
        session.ensure_started(context)
        session.cleanup(context)

    command = Command.create(
        ExecutionPlan(steps=tuple(step.public_projection() for step in session_steps(session))),
        callback,
        session_steps(session),
        executor=executor,
    )
    with pytest.raises(InstanceConfigurationError, match="port-conflict"):
        command.run()
    assert session.using_existing_runtime is False
    assert executor.spawned == []
    port_check.assert_called_once()
    http_client.assert_not_called()
    cast("Any", instance._client.register_process).assert_not_called()
    cast("Any", instance._client.unregister_process).assert_not_called()


def test_recorded_running_project_runtime_is_reused_without_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.proc import RunContext
    from odoo_instance_sdk.resources.instance.runtime import _RuntimeBinding

    instance = _instance(tmp_path)
    instance._runtime_binding = _RuntimeBinding(
        owner_kind="project",
        owner_id="project_demo",
        project_id="project_demo",
        repository_root=tmp_path,
        git_common_dir=tmp_path / ".git",
    )
    session = auxiliary_restore_session(instance)
    process = MagicMock()
    process.is_running.return_value = True
    process.status.return_value = "running"
    process.create_time.return_value = 123.5
    process.exe.return_value = instance._executable_prefix()[0]
    process.cmdline.return_value = list(session.start_step.argv)
    process.cwd.return_value = str(tmp_path)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.identity.psutil.Process", lambda _pid: process
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore.psutil.net_connections",
        lambda kind: (SimpleNamespace(status="LISTEN", laddr=("127.0.0.1", 0), pid=42),),
    )
    snapshot = MagicMock()
    snapshot.project_runtimes = (
        recorded_project_runtime(
            owner_id="project_demo",
            http_port=0,
            http_url="http://127.0.0.1:0",
            pid=42,
            create_time=123.5,
        ),
    )
    snapshot.environments = ()
    cast("Any", instance._client.get_catalog())._monitor_snapshot_rows.return_value = snapshot
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.health.poll_health", lambda *args, **kwargs: None
    )
    executor = RecordingExecutor(handles={})

    def callback(context: RunContext[PrivateJsonValue]) -> None:
        session.ensure_started(context)
        session.cleanup(context)

    run_session(session, callback, executor)

    assert session.using_existing_runtime is True
    assert executor.spawned == []
    cast("Any", instance._client.register_process).assert_not_called()
    cast("Any", instance._client.unregister_process).assert_not_called()


@pytest.mark.parametrize(
    ("listeners", "expected"),
    [
        ((43,), None),
        ((42, 43), None),
    ],
)
def test_recorded_runtime_requires_unambiguous_exact_socket_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    listeners: tuple[int, ...],
    expected: int | None,
) -> None:
    from odoo_instance_sdk.resources.instance.auxiliary_restore import _recorded_runtime_pid
    from odoo_instance_sdk.resources.instance.runtime import _RuntimeBinding

    instance = _instance(tmp_path)
    instance._runtime_binding = _RuntimeBinding(
        owner_kind="project",
        owner_id="project_demo",
        project_id="project_demo",
        repository_root=tmp_path,
        git_common_dir=tmp_path / ".git",
    )
    session = auxiliary_restore_session(instance)
    process = MagicMock()
    process.is_running.return_value = True
    process.status.return_value = "running"
    process.create_time.return_value = 123.5
    process.exe.return_value = instance._executable_prefix()[0]
    process.cmdline.return_value = list(session.start_step.argv)
    process.cwd.return_value = str(tmp_path)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore.psutil.Process",
        lambda _pid: process,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore.psutil.net_connections",
        lambda kind: tuple(
            SimpleNamespace(status="LISTEN", laddr=("127.0.0.1", 0), pid=pid) for pid in listeners
        ),
    )
    snapshot = MagicMock()
    snapshot.project_runtimes = (
        recorded_project_runtime(
            owner_id="project_demo",
            http_port=0,
            http_url="http://127.0.0.1:0",
            pid=42,
            create_time=123.5,
        ),
    )
    snapshot.environments = ()
    cast("Any", instance._client.get_catalog())._monitor_snapshot_rows.return_value = snapshot

    config = instance.config.start_config
    assert config is not None
    assert _recorded_runtime_pid(instance, config) is expected


def test_uninspectable_socket_rejects_recorded_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.resources.instance.auxiliary_restore import _recorded_runtime_pid
    from odoo_instance_sdk.resources.instance.runtime import _RuntimeBinding

    instance = _instance(tmp_path)
    instance._runtime_binding = _RuntimeBinding(
        owner_kind="project",
        owner_id="project_demo",
        project_id="project_demo",
        repository_root=tmp_path,
        git_common_dir=tmp_path / ".git",
    )
    session = auxiliary_restore_session(instance)
    process = MagicMock()
    process.is_running.return_value = True
    process.status.return_value = "running"
    process.create_time.return_value = 123.5
    process.exe.return_value = instance._executable_prefix()[0]
    process.cmdline.return_value = list(session.start_step.argv)
    process.cwd.return_value = str(tmp_path)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore.psutil.Process",
        lambda _pid: process,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore.psutil.net_connections",
        MagicMock(side_effect=PermissionError("socket inspection denied")),
    )
    snapshot = MagicMock()
    snapshot.project_runtimes = (
        recorded_project_runtime(
            owner_id="project_demo",
            http_port=0,
            http_url="http://127.0.0.1:0",
            pid=42,
            create_time=123.5,
        ),
    )
    snapshot.environments = ()
    cast("Any", instance._client.get_catalog())._monitor_snapshot_rows.return_value = snapshot

    config = instance.config.start_config
    assert config is not None
    assert _recorded_runtime_pid(instance, config) is None


def test_backup_does_not_open_http_after_auxiliary_identity_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.resources.instance.auxiliary_restore import (
        activate_auxiliary_restore_session,
        reset_auxiliary_restore_session,
    )

    instance = _instance(tmp_path)
    session = auxiliary_restore_session(instance)
    proof = MagicMock(side_effect=[42, None])
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore._recorded_runtime_pid",
        proof,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.health.poll_health", lambda *args, **kwargs: None
    )
    http = MagicMock()

    @contextlib.contextmanager
    def fake_http(_self: DatabaseResource, timeout: float | None = None) -> Any:
        del timeout
        yield http

    monkeypatch.setattr(DatabaseResource, "_http", fake_http)

    from odoo_instance_sdk.internal.proc import RunContext

    def callback(context: RunContext[PrivateJsonValue]) -> None:
        token = activate_auxiliary_restore_session(session)
        try:
            session.ensure_started(context)
            instance.databases._download_backup_part(
                "source",
                "master-secret",
                tmp_path / "backup.part",
                backup_id=str(uuid.uuid4()),
                timeout=1.0,
                format=BackupFormat.ZIP,
                filestore=True,
            )
        finally:
            session.cleanup(context)
            reset_auxiliary_restore_session(token)

    with pytest.raises(DatabaseManagerUnavailableError, match="identity is no longer proven"):
        run_session(session, callback, RecordingExecutor(handles={}))
    http.stream.assert_not_called()


def test_restore_does_not_post_after_auxiliary_identity_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.resources.instance.auxiliary_restore import (
        activate_auxiliary_restore_session,
        reset_auxiliary_restore_session,
    )

    backup_path = tmp_path / "restore.zip"
    backup_path.write_bytes(b"backup")
    backup = Backup(
        id=uuid.uuid4(),
        source_base_url="http://127.0.0.1:0",
        database_name="source",
        format=BackupFormat.ZIP,
        filestore_requested=True,
        path=str(backup_path),
        filename=backup_path.name,
        size_bytes=backup_path.stat().st_size,
        sha256="backup-sha",
        downloaded_at=datetime.now(UTC),
    )
    instance = _instance(tmp_path)
    object.__setattr__(instance.config, "master_password", "private")
    instance.databases.master_password = "private"
    session = auxiliary_restore_session(instance)
    proof = MagicMock(side_effect=[42, None])
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore._recorded_runtime_pid",
        proof,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.health.poll_health", lambda *args, **kwargs: None
    )
    http = MagicMock()

    @contextlib.contextmanager
    def fake_http(_self: DatabaseResource, timeout: float | None = None) -> Any:
        del timeout
        yield http

    monkeypatch.setattr(DatabaseResource, "_http", fake_http)
    monkeypatch.setattr(DatabaseResource, "exists", lambda *_args, **_kwargs: False)

    from odoo_instance_sdk.internal.proc import RunContext

    def callback(context: RunContext[PrivateJsonValue]) -> None:
        token = activate_auxiliary_restore_session(session)
        try:
            session.ensure_started(context)
            instance.databases.restore(backup, "target")
        finally:
            session.cleanup(context)
            reset_auxiliary_restore_session(token)

    with pytest.raises(DatabaseManagerUnavailableError, match="identity is no longer proven"):
        run_session(session, callback, RecordingExecutor(handles={}))
    http.post.assert_not_called()


def test_auxiliary_readiness_failure_has_exact_recovery_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = _instance(tmp_path)
    session = auxiliary_restore_session(instance)
    handle = ProcessHandle(
        process=MagicMock(),
        argv=session.start_step.argv,
        process_group_id=456,
        session_id=456,
        inherited_stdio=False,
    )
    executor = RecordingExecutor(handles={session.start_step.step_id: handle})
    owned = MagicMock()
    owned.pid = 456
    cast("Any", instance._client.unregister_process).return_value = (owned, "secret.conf")
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore._assert_http_port_free",
        lambda _config: None,
    )
    monkeypatch.setattr(
        OdooInstance, "wait_ready", MagicMock(side_effect=RuntimeError("health failed"))
    )
    terminate = MagicMock()
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore.terminate", terminate
    )

    from odoo_instance_sdk.internal.proc import RunContext

    def callback(context: RunContext[PrivateJsonValue]) -> None:
        try:
            session.ensure_started(context)
        finally:
            session.cleanup(context)

    command = Command.create(
        ExecutionPlan(steps=tuple(step.public_projection() for step in session_steps(session))),
        callback,
        session_steps(session),
        executor=executor,
    )

    with pytest.raises(DatabaseManagerUnavailableError, match=r"odcli run"):
        command.run()
    cast("Any", instance._client.unregister_process).assert_called_once()
    terminate.assert_called_once()
    assert session.process is None


def test_auxiliary_spawn_failure_has_exact_recovery_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = _instance(tmp_path)
    session = auxiliary_restore_session(instance)
    executor = RecordingExecutor(handles={})
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore._assert_http_port_free",
        lambda _config: None,
    )

    from odoo_instance_sdk.internal.proc import RunContext

    def callback(context: RunContext[PrivateJsonValue]) -> None:
        session.ensure_started(context)

    command = Command.create(
        ExecutionPlan(steps=tuple(step.public_projection() for step in session_steps(session))),
        callback,
        session_steps(session),
        executor=executor,
    )

    with pytest.raises(DatabaseManagerUnavailableError, match=r"odcli run"):
        command.run()
    assert executor.spawned == [session.start_step]
    cast("Any", instance._client.register_process).assert_not_called()


def test_stopped_manager_is_started_before_first_database_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = _instance(tmp_path)
    session = auxiliary_restore_session(instance)
    handle = ProcessHandle(
        process=MagicMock(),
        argv=session.start_step.argv,
        process_group_id=789,
        session_id=789,
        inherited_stdio=False,
    )
    executor = RecordingExecutor(handles={session.start_step.step_id: handle})
    cast("Any", instance._client.unregister_process).return_value = (None, None)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore._assert_http_port_free",
        lambda _config: None,
    )
    monkeypatch.setattr(
        OdooInstance,
        "wait_ready",
        lambda _self, _proc, *, timeout, version_info=False, database_manager=False: MagicMock(
            ok=True
        ),
    )

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, list[str]]:
            return {"result": ["restored"]}

    http = MagicMock()
    http.post.return_value = FakeResponse()

    @contextlib.contextmanager
    def fake_http(_self: DatabaseResource, timeout: float | None = None) -> Any:
        del timeout
        yield http

    monkeypatch.setattr(DatabaseResource, "_http", fake_http)

    from odoo_instance_sdk.internal.proc import RunContext
    from odoo_instance_sdk.resources.instance import (
        activate_auxiliary_restore_session,
        reset_auxiliary_restore_session,
    )

    def callback(context: RunContext[PrivateJsonValue]) -> tuple[str, ...]:
        token = activate_auxiliary_restore_session(session)
        try:
            return instance.databases.names()
        finally:
            session.cleanup(context)
            reset_auxiliary_restore_session(token)

    command = Command.create(
        ExecutionPlan(steps=tuple(step.public_projection() for step in session_steps(session))),
        callback,
        session_steps(session),
        executor=executor,
    )

    assert command.run() == ("restored",)
    assert http.post.call_count == 1
    assert executor.spawned == [session.start_step]


def test_foreign_listener_is_rejected_before_database_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = _instance(tmp_path)
    session = auxiliary_restore_session(instance)
    executor = RecordingExecutor(handles={})
    http = MagicMock()

    @contextlib.contextmanager
    def fake_http(_self: DatabaseResource, timeout: float | None = None) -> Any:
        del timeout
        yield http

    monkeypatch.setattr(DatabaseResource, "_http", fake_http)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore._assert_http_port_free",
        MagicMock(side_effect=InstanceConfigurationError("port-conflict: ownership unknown")),
    )

    from odoo_instance_sdk.internal.proc import RunContext
    from odoo_instance_sdk.resources.instance import (
        activate_auxiliary_restore_session,
        reset_auxiliary_restore_session,
    )

    def callback(context: RunContext[PrivateJsonValue]) -> tuple[str, ...]:
        token = activate_auxiliary_restore_session(session)
        try:
            return instance.databases.names()
        finally:
            session.cleanup(context)
            reset_auxiliary_restore_session(token)

    command = Command.create(
        ExecutionPlan(steps=tuple(step.public_projection() for step in session_steps(session))),
        callback,
        session_steps(session),
        executor=executor,
    )

    with pytest.raises(InstanceConfigurationError, match="port-conflict"):
        command.run()
    http.post.assert_not_called()
    assert executor.spawned == []


def test_cleanup_removes_secret_when_owned_termination_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = _instance(tmp_path)
    session = auxiliary_restore_session(instance)
    handle = ProcessHandle(
        process=MagicMock(),
        argv=session.start_step.argv,
        process_group_id=321,
        session_id=321,
        inherited_stdio=False,
    )
    owned = MagicMock()
    owned.pid = 321
    executor = RecordingExecutor(handles={session.start_step.step_id: handle})
    cast("Any", instance._client.unregister_process).return_value = (owned, "secret.conf")
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore._assert_http_port_free",
        lambda _config: None,
    )
    monkeypatch.setattr(
        OdooInstance,
        "wait_ready",
        lambda _self, _proc, *, timeout, version_info=False, database_manager=False: MagicMock(
            ok=True
        ),
    )
    cleanup = MagicMock()
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore.cleanup_secret_config", cleanup
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore.terminate",
        MagicMock(side_effect=RuntimeError("termination failed")),
    )

    from odoo_instance_sdk.internal.proc import RunContext

    def callback(context: RunContext[PrivateJsonValue]) -> None:
        session.ensure_started(context)
        session.cleanup(context)

    command = Command.create(
        ExecutionPlan(steps=tuple(step.public_projection() for step in session_steps(session))),
        callback,
        session_steps(session),
        executor=executor,
    )

    with pytest.raises(RuntimeError, match="termination failed"):
        command.run()
    cleanup.assert_called_once_with("secret.conf")


def test_restore_adapter_resets_session_when_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.proc import RunContext
    from odoo_instance_sdk.resources.instance import active_auxiliary_restore_session
    from odoo_instance_sdk.resources.instance.auxiliary_restore import (
        _attach_auxiliary_restore_runtime,
    )

    instance = _instance(tmp_path)
    session = auxiliary_restore_session(instance)

    def callback(_context: RunContext[PrivateJsonValue]) -> None:
        raise RuntimeError("restore failed")

    inner = Command.create(ExecutionPlan(), callback)
    monkeypatch.setattr(
        type(session), "cleanup", MagicMock(side_effect=RuntimeError("cleanup failed"))
    )
    command = _attach_auxiliary_restore_runtime(inner, session)

    with pytest.raises(RuntimeError, match="restore failed") as raised:
        command.run()
    assert "auxiliary cleanup failed: cleanup failed" in str(raised.value.__notes__)
    assert active_auxiliary_restore_session() is None


def test_restore_adapter_prepares_auxiliary_before_local_restore_and_cleans_last(
    tmp_path: Path,
) -> None:
    from odoo_instance_sdk.resources.instance.auxiliary_restore import (
        _attach_auxiliary_restore_runtime,
    )

    instance = _instance(tmp_path)
    session = auxiliary_restore_session(instance)
    inner_steps = (
        PreparedAction(
            step_id="database.prepare.catalogue-backup",
            action="inspect-catalogue-backup",
            description="Inspect backup",
            read_only=True,
        ),
        PreparedAction(
            step_id="database.prepare.local-restore",
            action="restore-local-database",
            description="Restore database",
            mutating=True,
        ),
    )
    inner = Command.create(
        ExecutionPlan(steps=tuple(step.public_projection() for step in inner_steps)),
        lambda _context: None,
        inner_steps,
    )

    command = cast("Command[None]", _attach_auxiliary_restore_runtime(inner, session))
    step_ids = tuple(step.step_id for step in command.plan.steps)
    assert step_ids == (
        "database.prepare.catalogue-backup",
        "database.restore.auxiliary.probe",
        "database.restore.auxiliary.port",
        "database.restore.auxiliary.start",
        "database.restore.auxiliary.ready",
        "database.restore.auxiliary.restore-revalidate",
        "database.prepare.local-restore",
        "database.restore.auxiliary.cleanup",
    )
    assert tuple(step.step_id for step in command._prepared().steps) == step_ids
