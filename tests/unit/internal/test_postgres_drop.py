from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.pg.drop import build_database_drop_command
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
) -> RecordingExecutor:
    initial = _inspection(exists=exists, template=template, sessions=sessions)
    checked = _inspection(
        exists=exists,
        template=template,
        sessions=revalidation_sessions if revalidation_sessions is not None else sessions,
    )

    def result_factory(step: object) -> ProcessResult:
        step_id = step.step_id  # type: ignore[attr-defined]
        if step_id.endswith("inspect"):
            return _result(step, stdout=initial)
        if step_id.endswith("revalidate-terminate"):
            return _result(step, stdout=initial)
        if step_id.endswith("revalidate-drop"):
            return _result(step, stdout=checked)
        if step_id.endswith("verify"):
            return _result(step, stdout="t\n")
        if step_id.endswith("terminate"):
            return _result(step, stdout="1\n")
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
        "database.drop.inspect",
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
        "database.drop.inspect",
        "database.drop.inspect",
    ]
    catalog.record_database_dropped.assert_not_called()


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
        "database.drop.inspect",
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
@pytest.mark.parametrize("database", ["", "*", "postgres", "template0", "template1"])
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
