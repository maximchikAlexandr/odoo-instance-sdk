from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import msgspec
import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.pg.drop import (
    DatabaseDropSafetyError,
    build_database_drop_command,
)
from odoo_instance_sdk.internal.proc import ProcessResult, RecordingExecutor
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.postgres import PostgresCluster


def _instance(project: Path, *, database: str = "feature_db") -> OdooInstance:
    client = MagicMock(spec=OdooClient)
    client.config = OdooClientConfig(executable="odoo")
    client.get_catalog.return_value = MagicMock()
    instance = OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            configured_database_names=(database,),
            db_host="127.0.0.1",
            db_port=5432,
            db_user="odoo",
            db_password="private-password",
        ),
        _client=client,
    )
    instance._postgres_cluster = PostgresCluster.from_project(project)
    return instance


def _result(step: object, *, stdout: str = "", returncode: int = 0) -> ProcessResult:
    return ProcessResult(
        argv=step.argv,  # type: ignore[attr-defined]
        returncode=returncode,
        stdout=stdout,
        stderr="",
        duration=0.0,
        cwd=step.cwd,  # type: ignore[attr-defined]
        environment=step.environment,  # type: ignore[attr-defined]
    )


def _inspection(
    *,
    exists: bool = True,
    sessions: list[dict[str, object]] | None = None,
    template: bool = False,
) -> str:
    return json.dumps(
        {
            "exists": exists,
            "is_template": template,
            "sessions": sessions or [],
        }
    )


def _executor(
    *,
    exists: bool = True,
    template: bool = False,
    sessions: list[dict[str, object]] | None = None,
    revalidation_sessions: list[dict[str, object]] | None = None,
    revalidate_terminate_stdout: str | None = None,
    revalidate_terminate_returncode: int = 0,
    revalidate_drop_stdout: str | None = None,
    revalidate_drop_returncode: int = 0,
    drop_returncode: int = 0,
    verify_stdout: str = "t\n",
    verify_returncode: int = 0,
) -> RecordingExecutor:
    initial = _inspection(exists=exists, template=template, sessions=sessions)
    checked = _inspection(
        exists=exists,
        template=template,
        sessions=revalidation_sessions if revalidation_sessions is not None else sessions,
    )

    def result_factory(step: object) -> ProcessResult:
        step_id = step.step_id  # type: ignore[attr-defined]
        if step_id in {"database.drop.planning-inspect", "database.drop.inspect"}:
            return _result(step, stdout=initial)
        if step_id.endswith("revalidate-terminate"):
            return _result(
                step,
                stdout=revalidate_terminate_stdout or initial,
                returncode=revalidate_terminate_returncode,
            )
        if step_id.endswith("revalidate-drop"):
            return _result(
                step,
                stdout=revalidate_drop_stdout or checked,
                returncode=revalidate_drop_returncode,
            )
        if step_id.endswith("verify"):
            return _result(step, stdout=verify_stdout, returncode=verify_returncode)
        if step_id.endswith("terminate"):
            return _result(step, stdout="1\n")
        if step_id.endswith("execute"):
            return _result(step, returncode=drop_returncode)
        return _result(step)

    return RecordingExecutor(result_factory=result_factory)


@pytest.mark.unit
def test_drop_plan_is_maintenance_bound_and_redacts_credentials(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    executor = _executor()
    command = build_database_drop_command(
        _instance(project_manifest), project_manifest, "feature_db", executor=executor
    )

    assert all(step.argv[step.argv.index("-d") + 1] == "postgres" for step in command.commands)
    public = repr(command.plan)
    assert "private-password" not in public
    assert command.plan.observations[0].preconditions[0].status == "passed"  # type: ignore[union-attr]
    planning = next(
        observation
        for observation in command.plan.observations
        if getattr(observation, "kind", None) == "planning-inspection"
    )
    assert planning.read_only is True  # type: ignore[union-attr]
    assert planning.executed_during_planning is True  # type: ignore[union-attr]
    assert planning.step_ids == ("database.drop.planning-inspect",)  # type: ignore[union-attr]
    assert [step.step_id for step in command.plan.steps] == [
        "database.drop",
        "database.drop.inspect",
        "database.drop.revalidate-terminate",
        "database.drop.terminate",
        "database.drop.revalidate-drop",
        "database.drop.execute",
        "database.drop.verify",
    ]


@pytest.mark.unit
def test_drop_records_catalogue_only_after_verified_absence(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    instance = _instance(project_manifest)
    catalog = cast("Any", instance._client.get_catalog())
    executor = _executor()
    result = build_database_drop_command(
        instance, project_manifest, "feature_db", executor=executor
    ).run()

    assert result.database == "feature_db"
    assert result.terminated_sessions == 0
    catalog.record_database_dropped.assert_called_once_with("127.0.0.1", 5432, "feature_db")
    assert [step.step_id for step in executor.executed] == [
        "database.drop.planning-inspect",
        "database.drop.inspect",
        "database.drop.revalidate-drop",
        "database.drop.execute",
        "database.drop.verify",
    ]


@pytest.mark.unit
def test_drop_requires_connection_force_and_never_mutates_on_refusal(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    sessions = [{"pid": 7, "user": "odoo", "client": "127.0.0.1", "application": "test"}]
    instance = _instance(project_manifest)
    catalog = cast("Any", instance._client.get_catalog())
    executor = _executor(sessions=sessions)
    command = build_database_drop_command(
        instance, project_manifest, "feature_db", executor=executor
    )

    with pytest.raises(ConfigError, match="active session"):
        command.run()
    assert [step.step_id for step in executor.executed] == [
        "database.drop.planning-inspect",
        "database.drop.inspect",
    ]
    catalog.record_database_dropped.assert_not_called()


@pytest.mark.unit
def test_drop_projection_and_refusal_retain_sanitized_session_identities(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    sessions = [
        {"pid": 7, "user": "Bearer bearer-session-sentinel"},
        {"pid": 8, "user": "odoo", "client": "Basic basic-session-sentinel"},
        {"pid": 9, "user": "odoo", "application": "eyJjwt-session-sentinel.payload.signature"},
        {"pid": 10, "user": "odoo", "application": "Authorization: header-session-sentinel"},
        {"pid": 11, "user": "odoo\n\x1b[31m", "application": "test\x00client"},
    ]
    instance = _instance(project_manifest)
    executor = _executor(sessions=sessions)
    command = build_database_drop_command(
        instance, project_manifest, "feature_db", executor=executor
    )

    semantic = command.plan.observations[0]
    assert semantic.active_sessions == (  # type: ignore[union-attr]
        {"pid": 7, "user": "<redacted>", "client": None, "application": None},
        {"pid": 8, "user": "odoo", "client": "<redacted>", "application": None},
        {"pid": 9, "user": "odoo", "client": None, "application": "<redacted>"},
        {
            "pid": 10,
            "user": "odoo",
            "client": None,
            "application": "Authorization: <redacted>",
        },
        {"pid": 11, "user": r"odoo\x0a\x1b[31m", "client": None, "application": r"test\x00client"},
    )
    with pytest.raises(DatabaseDropSafetyError) as caught:
        command.run()
    context = msgspec.to_builtins(caught.value.failure_context)
    assert context == {
        "active_sessions": (
            {"pid": 7, "user": "<redacted>", "client": None, "application": None},
            {"pid": 8, "user": "odoo", "client": "<redacted>", "application": None},
            {"pid": 9, "user": "odoo", "client": None, "application": "<redacted>"},
            {
                "pid": 10,
                "user": "odoo",
                "client": None,
                "application": "Authorization: <redacted>",
            },
            {
                "pid": 11,
                "user": r"odoo\x0a\x1b[31m",
                "client": None,
                "application": r"test\x00client",
            },
        )
    }
    public = repr(command.plan) + repr(context)
    for sentinel in (
        "bearer-session-sentinel",
        "basic-session-sentinel",
        "jwt-session-sentinel",
        "header-session-sentinel",
    ):
        assert sentinel not in public


@pytest.mark.unit
def test_drop_refuses_templates_and_missing_targets_before_mutation(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    cases = ((_executor(template=True), "template"), (_executor(exists=False), "does not exist"))
    for executor, message in cases:
        instance = _instance(project_manifest)
        command = build_database_drop_command(
            instance, project_manifest, "feature_db", executor=executor
        )
        with pytest.raises(ConfigError, match=message):
            command.run()
        assert not any(step.step_id.endswith("execute") for step in executor.executed)
        cast("Any", instance._client.get_catalog()).record_database_dropped.assert_not_called()


@pytest.mark.unit
def test_drop_protects_configured_default_without_force(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    instance = _instance(project_manifest, database="comerta")
    executor = _executor()
    command = build_database_drop_command(instance, project_manifest, "comerta", executor=executor)

    with pytest.raises(ConfigError, match="force-default"):
        command.run()
    assert not any(step.step_id.endswith("execute") for step in executor.executed)


@pytest.mark.unit
def test_forced_drop_terminates_only_target_and_revalidates_before_drop(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    sessions = [{"pid": 7, "user": "odoo", "client": "127.0.0.1", "application": "test"}]
    instance = _instance(project_manifest)
    catalog = cast("Any", instance._client.get_catalog())
    executor = _executor(sessions=sessions, revalidation_sessions=[])
    result = build_database_drop_command(
        instance,
        project_manifest,
        "feature_db",
        force_connections=True,
        executor=executor,
    ).run()

    assert result.terminated_sessions == 1
    assert [step.step_id for step in executor.executed] == [
        "database.drop.planning-inspect",
        "database.drop.inspect",
        "database.drop.revalidate-terminate",
        "database.drop.terminate",
        "database.drop.revalidate-drop",
        "database.drop.execute",
        "database.drop.verify",
    ]
    terminate = next(
        step for step in executor.executed if step.step_id == "database.drop.terminate"
    )
    assert b"WHERE datname='feature_db'" in terminate.stdin  # type: ignore[operator]
    assert b"pg_terminate_backend" in terminate.stdin  # type: ignore[operator]
    catalog.record_database_dropped.assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize("database", ["", "*", " demo ", "postgres", "template0", "template1"])
def test_drop_rejects_invalid_or_protected_names(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path, database: str
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    with pytest.raises(ConfigError):
        build_database_drop_command(_instance(project_manifest), project_manifest, database)


@pytest.mark.unit
def test_drop_fails_closed_when_pre_drop_revalidation_changes(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    instance = _instance(project_manifest)
    catalog = cast("Any", instance._client.get_catalog())
    executor = _executor(revalidation_sessions=[{"pid": 9, "user": "other"}])
    command = build_database_drop_command(
        instance, project_manifest, "feature_db", executor=executor
    )

    with pytest.raises(ConfigError, match="active session"):
        command.run()
    assert not any(step.step_id.endswith("execute") for step in executor.executed)
    catalog.record_database_dropped.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("stdout", "returncode", "message"),
    [
        (_inspection(exists=False), 0, "does not exist"),
        (_inspection(template=True), 0, "template"),
        ("not-json", 0, "invalid data"),
        ("", 1, "inspection failed"),
    ],
)
def test_drop_refuses_every_termination_revalidation_read_failure(
    monkeypatch: pytest.MonkeyPatch,
    project_manifest: Path,
    stdout: str,
    returncode: int,
    message: str,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    sessions = [{"pid": 7, "user": "odoo"}]
    instance = _instance(project_manifest)
    catalog = cast("Any", instance._client.get_catalog())
    executor = _executor(
        sessions=sessions,
        revalidate_terminate_stdout=stdout,
        revalidate_terminate_returncode=returncode,
    )
    command = build_database_drop_command(
        instance,
        project_manifest,
        "feature_db",
        force_connections=True,
        executor=executor,
    )

    with pytest.raises(ConfigError, match=message):
        command.run()
    assert not any(step.step_id == "database.drop.terminate" for step in executor.executed)
    assert not any(step.step_id.endswith("execute") for step in executor.executed)
    catalog.record_database_dropped.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("stdout", "returncode", "message"),
    [
        (_inspection(exists=False), 0, "does not exist"),
        (_inspection(template=True), 0, "template"),
        (_inspection(sessions=[{"pid": 9, "user": "other"}]), 0, "active session"),
        ("not-json", 0, "invalid data"),
        ("", 1, "inspection failed"),
    ],
)
def test_drop_refuses_every_drop_revalidation_read_failure(
    monkeypatch: pytest.MonkeyPatch,
    project_manifest: Path,
    stdout: str,
    returncode: int,
    message: str,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    instance = _instance(project_manifest)
    catalog = cast("Any", instance._client.get_catalog())
    executor = _executor(
        revalidate_drop_stdout=stdout,
        revalidate_drop_returncode=returncode,
    )
    command = build_database_drop_command(
        instance, project_manifest, "feature_db", executor=executor
    )

    with pytest.raises(ConfigError, match=message):
        command.run()
    assert not any(step.step_id.endswith("execute") for step in executor.executed)
    catalog.record_database_dropped.assert_not_called()


@pytest.mark.unit
def test_drop_fails_closed_when_configured_default_changes_before_execution(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    manifest = project_manifest / ".odcli" / "project.toml"
    content = manifest.read_text(encoding="utf-8").replace(
        'default_source_database = "comerta"', 'default_source_database = "feature_db"'
    )
    manifest.write_text(content, encoding="utf-8")
    instance = _instance(project_manifest)
    catalog = cast("Any", instance._client.get_catalog())
    executor = _executor()
    command = build_database_drop_command(
        instance, project_manifest, "feature_db", executor=executor
    )

    with pytest.raises(ConfigError, match="force-default"):
        command.run()
    assert not any(step.step_id.endswith("execute") for step in executor.executed)
    catalog.record_database_dropped.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("drop_returncode", "verify_stdout", "verify_returncode", "message"),
    [
        (1, "", 0, "DROP DATABASE failed"),
        (0, "f\n", 0, "still exists"),
        (0, "", 1, "absence verification failed"),
        (0, "not-bool\n", 0, "still exists"),
    ],
)
def test_drop_does_not_record_failed_mutation_or_postcondition(
    monkeypatch: pytest.MonkeyPatch,
    project_manifest: Path,
    drop_returncode: int,
    verify_stdout: str,
    verify_returncode: int,
    message: str,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    instance = _instance(project_manifest)
    catalog = cast("Any", instance._client.get_catalog())
    executor = _executor(
        drop_returncode=drop_returncode,
        verify_stdout=verify_stdout,
        verify_returncode=verify_returncode,
    )
    command = build_database_drop_command(
        instance, project_manifest, "feature_db", executor=executor
    )

    with pytest.raises(ConfigError, match=message):
        command.run()
    if drop_returncode:
        assert not any(step.step_id.endswith("verify") for step in executor.executed)
    catalog.record_database_dropped.assert_not_called()
