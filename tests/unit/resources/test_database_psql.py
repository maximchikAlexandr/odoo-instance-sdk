from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.pg.context import resolve_database_context, resolve_database_name
from odoo_instance_sdk.internal.proc import ProcessHandle, ProcessResult, RecordingExecutor
from odoo_instance_sdk.models import SqlExecutionResult
from odoo_instance_sdk.resources.instance import OdooInstance


def _instance(*names: str) -> OdooInstance:
    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    return OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            configured_database_names=names,
            db_host="127.0.0.1",
            db_port=5433,
            db_user="odoo",
            db_password="private-password",
        ),
        _client=client,
    )


@pytest.mark.unit
def test_psql_command_captures_bound_identity_and_inherited_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    instance = _instance("feature_db")
    process = MagicMock()
    process.pid = 1234
    process.wait.return_value = 17
    handle = ProcessHandle(process, (), None, None, True)
    executor = RecordingExecutor(handles={"database.psql": handle})

    command = instance.databases.psql_command(("-c", "SELECT 1"), executor=executor)
    step = command.commands[0]

    assert step.argv == (
        "psql",
        "-X",
        "-h",
        "127.0.0.1",
        "-p",
        "5433",
        "-U",
        "odoo",
        "-d",
        "feature_db",
        "-c",
        "SELECT 1",
    )
    assert step.mode == "foreground"
    assert step.interactive is True
    assert executor.spawned == []
    assert command.run() == 17
    assert executor.spawned[0].inherit_stdio is True
    assert executor.spawned[0].start_new_session is True


@pytest.mark.unit
def test_execute_sql_delegates_to_captured_process_and_sanitizes_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    instance = _instance("feature_db")
    executor = RecordingExecutor(
        default_result=ProcessResult(
            argv=(),
            returncode=3,
            stdout=b"value\n",
            stderr=b"password=private-password /tmp/secret.conf",
            duration=0.0,
            cwd=None,
            environment=(),
        )
    )

    result = instance.databases.execute_sql_command(
        "SELECT current_database();\n", timeout=2.5, executor=executor
    ).run()

    assert result == SqlExecutionResult(returncode=3, stdout="value\n", stderr="<redacted> <path>")
    step = executor.executed[0]
    assert step.stdin == b"SELECT current_database();\n"
    assert step.timeout == 2.5
    assert step.mode == "captured"


@pytest.mark.unit
def test_database_resolver_defaults_and_rejects_ambiguous_identity() -> None:
    assert resolve_database_name(("feature_db",)) == "feature_db"
    assert resolve_database_name((), project_default="project_db") == "project_db"
    assert resolve_database_name(("first", "second"), explicit="same_cluster") == "same_cluster"
    with pytest.raises(ConfigError, match="ambiguous"):
        resolve_database_name(("first", "second"))


@pytest.mark.unit
def test_database_context_is_bound_to_instance_cluster() -> None:
    instance = _instance("feature_db")
    context = resolve_database_context(instance)
    assert (context.database, context.host, context.port, context.user) == (
        "feature_db",
        "127.0.0.1",
        5433,
        "odoo",
    )
