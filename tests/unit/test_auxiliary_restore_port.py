from __future__ import annotations

import copy
import socket
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from odoo_instance_sdk.exceptions import DatabaseManagerUnavailableError, InstanceConfigurationError
from odoo_instance_sdk.internal.proc import PrivateJsonValue, ProcessHandle, RecordingExecutor
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.resources.instance import (
    AuxiliaryRestoreSession,
    OdooInstance,
    auxiliary_restore_session,
)
from tests.unit.test_stopped_project_restore import _instance, run_session


@pytest.fixture
def auxiliary_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    instance = _instance(tmp_path)
    primary = socket.socket()
    primary.bind(("127.0.0.1", 0))
    primary.listen()
    primary_port = primary.getsockname()[1]
    instance.config = replace(
        instance.config,
        base_url=f"http://127.0.0.1:{primary_port}",
        start_config=StartConfig(
            config_path=str(tmp_path / "odoo.conf"),
            http_port=primary_port,
        ),
    )
    original_config = copy.deepcopy(instance.config.start_config)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore._recorded_runtime_pid",
        lambda *_args: None,
    )
    session = auxiliary_restore_session(instance)
    handle = ProcessHandle(
        process=MagicMock(),
        argv=session.start_step.argv,
        process_group_id=123,
        session_id=123,
        inherited_stdio=False,
    )
    cast("Any", handle.process).poll.return_value = None
    executor = RecordingExecutor(handles={session.start_step.step_id: handle})
    cast("Any", instance._client.unregister_process).return_value = (None, None)
    ready = MagicMock()

    def wait_ready(helper: OdooInstance, *_args: Any, **_kwargs: Any) -> Any:
        assert helper.config.base_url == session.request_base_url
        assert helper.config.base_url != instance.config.base_url
        return ready()

    monkeypatch.setattr(OdooInstance, "wait_ready", wait_ready)
    http = MagicMock()
    http.post.return_value.json.return_value = {"result": ["restored"]}
    transport = MagicMock()
    transport.return_value.__enter__.return_value = http
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.transport.factory.open_odoo_http_client",
        transport,
    )
    ownership = MagicMock(return_value=False)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore._socket_owned_by",
        ownership,
    )
    try:
        yield SimpleNamespace(
            instance=instance,
            session=session,
            executor=executor,
            primary=primary,
            primary_port=primary_port,
            original_config=original_config,
            ready=ready,
            http=http,
            transport=transport,
            ownership=ownership,
        )
    finally:
        primary.close()


def _assert_routed_requests(case: SimpleNamespace) -> None:
    instance = cast("OdooInstance", case.instance)
    session = cast("AuxiliaryRestoreSession", case.session)
    helper_url = instance.databases._url("restore")
    assert helper_url != f"{instance.config.base_url}/web/database/restore"
    assert helper_url.startswith("http://127.0.0.1:")
    assert instance.databases.names() == ("restored",)
    assert case.transport.call_args.args == (session.request_base_url,)
    assert case.http.post.call_args.args == (f"{session.request_base_url}/web/database/list",)
    unrelated = replace(
        instance,
        config=replace(instance.config, base_url="http://127.0.0.1:12345"),
    )
    assert unrelated.databases._url("list") == "http://127.0.0.1:12345/web/database/list"


def _run_owned_runtime_case(case: SimpleNamespace, authorize: Any) -> None:
    from odoo_instance_sdk.internal.proc import RunContext
    from odoo_instance_sdk.resources.instance.auxiliary_restore import (
        activate_auxiliary_restore_session,
        reset_auxiliary_restore_session,
    )

    instance = cast("OdooInstance", case.instance)
    session = cast("AuxiliaryRestoreSession", case.session)

    def callback(context: RunContext[PrivateJsonValue]) -> None:
        token = activate_auxiliary_restore_session(session)
        try:
            session.ensure_started(context)
            _assert_routed_requests(case)
            authorize(case, context)
            session.cleanup(context)
        finally:
            reset_auxiliary_restore_session(token)

    assert session.secret_config is not None
    argv = session.start_step.argv
    assert argv[argv.index("--http-port") + 1] == str(session.secret_config.http_port)
    assert argv[argv.index("--http-interface") + 1] == "127.0.0.1"
    run_session(session, callback, case.executor)
    case.ready.assert_called_once()
    assert instance.config.start_config == case.original_config
    assert case.primary.getsockname()[1] == case.primary_port
    assert len(case.executor.spawned) == 1
    cast("Any", instance._client.unregister_process).assert_called_once()


def _rejects_unowned_request(case: SimpleNamespace, context: Any) -> None:
    session = cast("AuxiliaryRestoreSession", case.session)
    instance = cast("OdooInstance", case.instance)
    with pytest.raises(DatabaseManagerUnavailableError, match="identity"):
        session.authorize_request(
            context,
            instance=instance,
            request_action=session.restore_request_action,
        )


def _accepts_owned_request(case: SimpleNamespace, context: Any) -> None:
    session = cast("AuxiliaryRestoreSession", case.session)
    instance = cast("OdooInstance", case.instance)
    case.ownership.return_value = True
    session.authorize_request(
        context,
        instance=instance,
        request_action=session.restore_request_action,
    )
    assert case.ownership.call_args.args[0].http_port != case.primary_port


def test_auxiliary_restore_rejects_lost_helper_ownership(auxiliary_case: SimpleNamespace) -> None:
    _run_owned_runtime_case(auxiliary_case, _rejects_unowned_request)


def test_auxiliary_restore_authorizes_owned_helper(auxiliary_case: SimpleNamespace) -> None:
    _run_owned_runtime_case(auxiliary_case, _accepts_owned_request)


def test_auxiliary_captured_wildcard_port_race_fails_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.proc import RunContext

    instance = _instance(tmp_path)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore._recorded_runtime_pid",
        lambda *_args: None,
    )
    executor = RecordingExecutor(handles={})
    with socket.socket() as intruder:
        intruder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        intruder.bind(("0.0.0.0", 0))
        intruder.listen()
        occupied_port = intruder.getsockname()[1]
        allocator = MagicMock(return_value=occupied_port)
        monkeypatch.setattr("odoo_instance_sdk.internal.port_allocation.find_free_port", allocator)
        session = auxiliary_restore_session(instance)
        allocator.assert_called_once_with("http", None, reserved_ports=(0,))
        assert session.secret_config is not None
        captured_argv = session.start_step.argv

        def callback(context: RunContext[PrivateJsonValue]) -> None:
            session.ensure_started(context)

        with pytest.raises(InstanceConfigurationError, match="port-conflict"):
            run_session(session, callback, executor)
        assert intruder.getsockname()[1] == occupied_port
        assert session.start_step.argv == captured_argv
        assert executor.spawned == []
        cast("Any", instance._client.register_process).assert_not_called()
        cast("Any", instance._client.unregister_process).assert_not_called()
