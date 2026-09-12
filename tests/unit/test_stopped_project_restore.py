from __future__ import annotations

import contextlib
from pathlib import Path
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
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.resources.database import DatabaseResource
from odoo_instance_sdk.resources.instance import OdooInstance, auxiliary_restore_session


def _instance(tmp_path: Path) -> OdooInstance:
    config_path = tmp_path / "odoo.conf"
    config_path.write_text("[options]\nadmin_passwd = private\n")
    client = MagicMock()
    return OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            start_config=StartConfig(config_path=str(config_path), http_port=8069),
            command_prefix=("/usr/bin/python", "/project/odoo-bin"),
            default_cwd=tmp_path,
        ),
        _client=client,
    )


def session_steps(session: object) -> tuple[PreparedStep | PreparedAction, ...]:
    from odoo_instance_sdk.resources.instance import AuxiliaryRestoreSession

    assert isinstance(session, AuxiliaryRestoreSession)
    return (session.start_step, session.ready_action, session.cleanup_action)


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
        "odoo_instance_sdk.resources.instance._assert_http_port_free", lambda _config: None
    )
    monkeypatch.setattr(
        OdooInstance,
        "wait_ready",
        lambda _self, _proc, *, timeout: MagicMock(ok=True),
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
    command.run()

    assert session.start_step.long_running is True
    assert session.start_step.inherit_stdio is False
    assert executor.spawned == [session.start_step]
    cast("Any", instance._client.register_process).assert_called_once()
    cast("Any", instance._client.unregister_process).assert_called_once()


def test_foreign_listener_is_rejected_before_auxiliary_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = _instance(tmp_path)
    session = auxiliary_restore_session(instance)
    executor = RecordingExecutor(handles={})
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance._assert_http_port_free",
        MagicMock(side_effect=InstanceConfigurationError("port-conflict: ownership unknown")),
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

    with pytest.raises(InstanceConfigurationError, match="port-conflict"):
        command.run()
    assert executor.spawned == []
    cast("Any", instance._client.register_process).assert_not_called()


def test_recorded_running_project_runtime_is_reused_without_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.proc import RunContext
    from odoo_instance_sdk.resources.instance import _RuntimeBinding

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
    monkeypatch.setattr("odoo_instance_sdk.resources.instance.psutil.Process", lambda _pid: process)
    snapshot = MagicMock()
    snapshot.project_runtimes = (
        {
            "owner_id": "project_demo",
            "http_port": 8069,
            "root_pid": 42,
            "create_time": 123.5,
        },
    )
    cast("Any", instance._client.get_catalog())._monitor_snapshot_rows.return_value = snapshot
    executor = RecordingExecutor(handles={})

    def callback(context: RunContext[PrivateJsonValue]) -> None:
        session.ensure_started(context)
        session.cleanup(context)

    command = Command.create(
        ExecutionPlan(steps=tuple(step.public_projection() for step in session_steps(session))),
        callback,
        session_steps(session),
        executor=executor,
    )
    command.run()

    assert session.using_existing_runtime is True
    assert executor.spawned == []
    cast("Any", instance._client.register_process).assert_not_called()
    cast("Any", instance._client.unregister_process).assert_not_called()


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
    cast("Any", instance._client.unregister_process).return_value = (None, None)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance._assert_http_port_free", lambda _config: None
    )
    monkeypatch.setattr(
        OdooInstance, "wait_ready", MagicMock(side_effect=RuntimeError("health failed"))
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


def test_auxiliary_spawn_failure_has_exact_recovery_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = _instance(tmp_path)
    session = auxiliary_restore_session(instance)
    executor = RecordingExecutor(handles={})
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance._assert_http_port_free", lambda _config: None
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
        "odoo_instance_sdk.resources.instance._assert_http_port_free", lambda _config: None
    )
    monkeypatch.setattr(
        OdooInstance, "wait_ready", lambda _self, _proc, *, timeout: MagicMock(ok=True)
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
        "odoo_instance_sdk.resources.instance._assert_http_port_free",
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
        "odoo_instance_sdk.resources.instance._assert_http_port_free", lambda _config: None
    )
    monkeypatch.setattr(
        OdooInstance, "wait_ready", lambda _self, _proc, *, timeout: MagicMock(ok=True)
    )
    cleanup = MagicMock()
    monkeypatch.setattr("odoo_instance_sdk.resources.instance.cleanup_secret_config", cleanup)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.terminate",
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
    from odoo_instance_sdk.commands.db import _attach_auxiliary_restore_runtime
    from odoo_instance_sdk.internal.proc import RunContext
    from odoo_instance_sdk.resources.instance import active_auxiliary_restore_session

    instance = _instance(tmp_path)
    session = auxiliary_restore_session(instance)

    def callback(_context: RunContext[PrivateJsonValue]) -> None:
        raise RuntimeError("restore failed")

    inner = Command.create(ExecutionPlan(), callback)
    monkeypatch.setattr(
        type(session), "cleanup", MagicMock(side_effect=RuntimeError("cleanup failed"))
    )
    command = _attach_auxiliary_restore_runtime(inner, session)

    with pytest.raises(RuntimeError, match="cleanup failed"):
        command.run()
    assert active_auxiliary_restore_session() is None


def test_restore_adapter_prepares_auxiliary_before_local_restore_and_cleans_last(
    tmp_path: Path,
) -> None:
    from odoo_instance_sdk.commands.db import _attach_auxiliary_restore_runtime

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
        "database.restore.auxiliary.start",
        "database.restore.auxiliary.ready",
        "database.prepare.local-restore",
        "database.restore.auxiliary.cleanup",
    )
    assert tuple(step.step_id for step in command._prepared().steps) == step_ids
