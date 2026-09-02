from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import ConfigError, PostgresClusterNotOwnedError
from odoo_instance_sdk.internal.pg.context import resolve_database_context, resolve_database_name
from odoo_instance_sdk.internal.proc import ProcessHandle, ProcessResult, RecordingExecutor
from odoo_instance_sdk.models import SqlExecutionResult
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.postgres import PostgresCluster


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
        "/psql",
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


def _stats_output() -> str:
    return json.dumps(
        {
            "summary": {
                "database": "feature_db",
                "server_version": "16",
                "captured_at": "2026-01-01T00:00:00Z",
                "stats_since": None,
                "database_bytes": 0,
                "block_size_bytes": 8192,
            },
            "tables": [],
            "indexes": [],
            "capabilities": {"pg_buffercache": False},
            "warnings": [
                {"code": "pg_buffercache_not_installed", "message": "unavailable"},
                {"code": "cumulative_statistics", "message": "cumulative"},
            ],
        }
    )


def _bloat_output() -> str:
    return json.dumps(
        {
            "database": "feature_db",
            "captured_at": "2026-01-01T00:00:00Z",
            "tables": [],
            "indexes": [],
            "capabilities": {"pgstattuple": False},
            "warnings": [{"code": "cumulative_statistics", "message": "cumulative"}],
        }
    )


@pytest.mark.parametrize(
    ("method", "output", "expected_step"),
    [
        (
            "locks_command",
            '{"database":"feature_db","captured_at":"2026-01-01T00:00:00Z","rows":[],"warnings":[]}',
            "database.locks.psql",
        ),
        ("stats_command", _stats_output(), "database.stats.psql"),
        ("bloat_command", _bloat_output(), "database.bloat.psql"),
    ],
)
def test_diagnostic_commands_use_exact_captured_sql_and_typed_decode(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    output: str,
    expected_step: str,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    instance = _instance("feature_db")
    executor = RecordingExecutor(
        default_result=ProcessResult(
            argv=(),
            returncode=0,
            stdout=output,
            stderr="",
            duration=0.0,
            cwd=None,
            environment=(),
        )
    )

    command = getattr(instance.databases, method)("feature_db", executor=executor)
    result = command.run()

    assert result is not None
    assert executor.executed[0].step_id == expected_step
    step = executor.executed[0]
    assert step.argv[:2] == ("/psql", "-X")
    assert step.argv[-5:] == ("-q", "-t", "-A", "-v", "ON_ERROR_STOP=1")
    assert step.stdin is not None
    assert step.stdin.startswith(b"-- odoo-instance-sdk") or step.stdin.startswith(b"\\set")
    assert step.timeout == 30.0
    assert step.mode == "captured"
    assert step.inherit_stdio is False


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("locks_command", {"top": 0}),
        ("stats_command", {"timeout": 0.0}),
        ("bloat_command", {"exact_max_scan_mb": 1025}),
    ],
)
def test_diagnostic_bounds_fail_before_process_spawn(
    monkeypatch: pytest.MonkeyPatch, method: str, kwargs: dict[str, object]
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    instance = _instance("feature_db")
    executor = RecordingExecutor()

    with pytest.raises(ConfigError):
        getattr(instance.databases, method)("feature_db", executor=executor, **kwargs)
    assert executor.executed == []


def test_init_monitoring_is_owned_captured_mutation_with_one_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    instance = _instance("feature_db")
    cluster = PostgresCluster(
        _repository_root=Path("/tmp/project"),
        _project_id="project",
        _mode="compose",
        _endpoint_host="127.0.0.1",
        _endpoint_port=5433,
        _image="postgres:16",
        _user="odoo",
    )
    object.__setattr__(instance, "_postgres_cluster", cluster)
    monkeypatch.setattr(OdooInstance, "_dependency_manifest", lambda self: ((), None))
    monkeypatch.setattr(
        OdooInstance, "_ensure_dependencies_ready", lambda self, *args, **kwargs: None
    )
    output = json.dumps(
        {
            "installed": ["pg_buffercache"],
            "already_present": [],
            "skipped": [{"extension": "pgstattuple", "reason": "not_available"}],
        }
    )
    executor = RecordingExecutor(
        default_result=ProcessResult(
            argv=(),
            returncode=0,
            stdout=output,
            stderr="",
            duration=0.0,
            cwd=None,
            environment=(),
        )
    )

    command = instance.databases.init_monitoring_command("feature_db", executor=executor)
    result = command.run()

    assert result.installed == ("pg_buffercache",)
    assert result.skipped[0].reason == "not_available"
    step = executor.executed[0]
    assert step.step_id == "database.init-monitoring.psql"
    assert step.mode == "captured"
    assert step.read_only is False
    assert step.mutating is True
    assert step.stdin is not None
    assert step.stdin.count(b"SELECT json_build_object(") == 1
    assert step.stdin.find(b"FROM pg_extension") < step.stdin.find(b"FROM pg_available_extensions")


def test_init_monitoring_rejects_external_before_psql_plan() -> None:
    instance = _instance("feature_db")
    cluster = PostgresCluster(
        _repository_root=Path("/tmp/project"),
        _project_id="project",
        _mode="external",
        _endpoint_host="127.0.0.1",
        _endpoint_port=5433,
    )
    object.__setattr__(instance, "_postgres_cluster", cluster)

    with pytest.raises(PostgresClusterNotOwnedError, match="SDK-owned"):
        instance.databases.init_monitoring_command("feature_db")
