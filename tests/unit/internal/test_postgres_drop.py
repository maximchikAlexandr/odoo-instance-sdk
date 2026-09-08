from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import msgspec
import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.commands.output import OutputMode, run_or_preview
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import BackupCatalogError, ConfigError, LockConflictError
from odoo_instance_sdk.internal.locks import exclusive_lock, postgres_cluster_lock_path
from odoo_instance_sdk.internal.pg.drop import (
    DatabaseDropPartialError,
    DatabaseDropSafetyError,
    _cleanup_proven_filestore,
    build_database_drop_command,
)
from odoo_instance_sdk.internal.postgres_compose import compose_volume_name
from odoo_instance_sdk.internal.proc import ProcessResult, RecordingExecutor
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from tests.unit.monitor_support import make_env


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


def _managed_instance(
    project: Path,
    catalog: BackupCatalog,
    tmp_path: Path,
    *,
    data_directory: Path | None,
    foreign_binding: bool = False,
) -> tuple[OdooInstance, Path]:
    manifest = project / ".odcli" / "project.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[postgres]\nmode = "compose"\nimage = "postgres:16"\nport = 5432\n',
        encoding="utf-8",
    )
    instance = _instance(project)
    cast("Any", instance._client).get_catalog.return_value = catalog
    cluster = PostgresCluster.from_project(project)
    instance._postgres_cluster = cluster
    claim = catalog._ensure_postgres_cluster_pending(
        cluster._project_id,
        cluster.compose_project_name,
        compose_volume_name(cluster._project_id),
    )
    active = catalog._activate_postgres_cluster(
        claim.cluster_id,
        cluster._project_id,
        cluster.compose_project_name,
        compose_volume_name(cluster._project_id),
    )
    backup_id = str(uuid.uuid4())
    backup_file = tmp_path / "backup.zip"
    backup_file.write_bytes(b"source-backup")
    catalog.start_download(
        backup_id,
        "http://127.0.0.1:8069",
        "feature_db",
        "zip",
        True,
        backup_file,
    )
    catalog.success_download(backup_id, backup_file.name, backup_file.stat().st_size, "")
    restore_cluster_id = active.cluster_id
    if foreign_binding:
        foreign = catalog._ensure_postgres_cluster_pending(
            "foreign-project", "foreign-compose", "foreign-volume"
        )
        restore_cluster_id = catalog._activate_postgres_cluster(
            foreign.cluster_id, "foreign-project", "foreign-compose", "foreign-volume"
        ).cluster_id
    catalog.record_restore(
        cluster.endpoint_host,
        cluster.endpoint_port,
        "feature_db",
        backup_id,
        cluster_id=restore_cluster_id,
        data_directory=data_directory,
    )
    return instance, backup_file


def _compose_instance(project: Path, catalog: BackupCatalog) -> OdooInstance:
    manifest = project / ".odcli" / "project.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[postgres]\nmode = "compose"\nimage = "postgres:16"\nport = 5432\n',
        encoding="utf-8",
    )
    instance = _instance(project)
    instance._postgres_cluster = PostgresCluster.from_project(project)
    cast("Any", instance._client).get_catalog.return_value = catalog
    return instance


@pytest.mark.unit
def test_drop_plan_is_maintenance_bound_and_redacts_credentials(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    executor = _executor()
    command = build_database_drop_command(
        _instance(project_manifest), project_manifest, "feature_db", executor=executor
    )

    assert all(
        step.argv[step.argv.index("-d") + 1] == "postgres"
        for step in command.commands
        if "-d" in step.argv
    )
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
        "database.drop.ownership.volume",
        "database.drop.ownership.container",
        "database.drop.inspect",
        "database.drop.revalidate-terminate",
        "database.drop.terminate",
        "database.drop.revalidate-drop",
        "database.drop.execute",
        "database.drop.verify",
    ]


@pytest.mark.unit
def test_drop_rich_dry_run_uses_real_builder_process_displays(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    project_manifest: Path,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    executor = _executor()
    command = build_database_drop_command(
        _instance(project_manifest), project_manifest, "feature_db", executor=executor
    )
    planned = len(executor.executed)
    displays = tuple(step.display for step in command.plan.process_steps)

    status, value = run_or_preview(
        lambda: command,
        command_name="db.drop",
        mode=OutputMode.RICH,
        dry_run=True,
    )

    rendered = capsys.readouterr().out
    assert (status, value) == (0, None)
    offset = 0
    for display in displays:
        position = rendered.index(display, offset)
        offset = position + len(display)
    assert len(executor.executed) == planned
    assert "DROP DATABASE" in rendered
    assert "argv:" not in rendered
    assert "fingerprint:" not in rendered


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
def test_proven_filestore_cleanup_is_exact_and_symlink_safe(tmp_path: Path) -> None:
    data_directory = tmp_path / "odoo-data"
    target = data_directory / "filestore" / "feature_db"
    target.mkdir(parents=True)
    (target / "blob").write_bytes(b"payload")

    state, path = _cleanup_proven_filestore(str(data_directory), "feature_db")

    assert state == "deleted"
    assert path == str(target)
    assert not target.exists()

    external = tmp_path / "external"
    external.mkdir()
    (external / "secret").write_text("retain")
    (data_directory / "filestore" / "feature_db").symlink_to(external, target_is_directory=True)

    state, path = _cleanup_proven_filestore(str(data_directory), "feature_db")

    assert state == "unknown"
    assert path == str(data_directory / "filestore" / "feature_db")
    assert (external / "secret").read_text() == "retain"


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
def test_drop_rechecks_ownership_and_serializes_against_cluster_lifecycle(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    import odoo_instance_sdk.internal.pg.drop as drop_module

    instance = _instance(project_manifest)
    executor = _executor()
    checks: list[str] = []
    original = drop_module._drop_ownership_evidence

    def record_check(*args: Any, **kwargs: Any) -> object:
        checks.append("ownership")
        return original(*args, **kwargs)

    monkeypatch.setattr(drop_module, "_drop_ownership_evidence", record_check)
    command = build_database_drop_command(
        instance, project_manifest, "feature_db", executor=executor
    )

    cluster = instance._postgres_cluster
    assert cluster is not None
    lock_path = postgres_cluster_lock_path(cluster._project_id)
    command.run()
    assert checks == ["ownership", "ownership"]

    competing_executor = _executor()
    competing = build_database_drop_command(
        instance, project_manifest, "feature_db", timeout=0.05, executor=competing_executor
    )
    with exclusive_lock(lock_path), pytest.raises(LockConflictError):
        competing.run()

    # A competing lifecycle owns the same lock and blocks before the second
    # command can reach its execution-time ownership proof or any PostgreSQL
    # inspection/mutation.
    assert checks == ["ownership", "ownership", "ownership"]
    assert [step.step_id for step in competing_executor.executed] == [
        "database.drop.planning-inspect"
    ]


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


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["missing", "pending", "volume", "binding"])
def test_drop_ownership_matrix_fails_before_any_postgres_effect(
    monkeypatch: pytest.MonkeyPatch,
    project_manifest: Path,
    tmp_path: Path,
    failure: str,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    # Ownership is a planning-time, execution-owned boundary.  Keep this
    # matrix independent of a host Docker daemon: the real Compose probe is
    # exercised by integration coverage, while this test proves the refusal
    # happens before any PostgreSQL effect.
    monkeypatch.setattr(PostgresCluster, "_inspect_cluster_volume", lambda *_a, **_k: True)
    catalog = BackupCatalog(db_path=tmp_path / f"{failure}.sqlite3")
    instance = _compose_instance(project_manifest, catalog)
    cluster = instance._postgres_cluster
    assert cluster is not None
    volume = compose_volume_name(cluster._project_id)
    claim = catalog._ensure_postgres_cluster_pending(
        cluster._project_id, cluster.compose_project_name, volume
    )
    if failure != "pending":
        catalog._activate_postgres_cluster(
            claim.cluster_id, cluster._project_id, cluster.compose_project_name, volume
        )
    if failure == "volume":
        monkeypatch.setattr(PostgresCluster, "_inspect_cluster_volume", lambda *_a, **_k: False)
    if failure == "binding":
        foreign = catalog._ensure_postgres_cluster_pending(
            "foreign-project", "foreign-compose", "foreign-volume"
        )
        foreign = catalog._activate_postgres_cluster(
            foreign.cluster_id, "foreign-project", "foreign-compose", "foreign-volume"
        )
        backup_id = str(uuid.uuid4())
        backup_file = tmp_path / "foreign.zip"
        backup_file.write_bytes(b"backup")
        catalog.start_download(
            backup_id, "http://127.0.0.1:8069", "feature_db", "zip", True, backup_file
        )
        catalog.success_download(backup_id, backup_file.name, 6, "")
        catalog.record_restore(
            cluster.endpoint_host,
            cluster.endpoint_port,
            "feature_db",
            backup_id,
            cluster_id=foreign.cluster_id,
        )
    executor = _executor()
    with pytest.raises(ConfigError):
        build_database_drop_command(instance, project_manifest, "feature_db", executor=executor)
    assert executor.executed == []
    assert (
        catalog._conn.execute(
            "SELECT COUNT(*) FROM database_events "
            "WHERE database_name='feature_db' AND event_type='dropped'"
        ).fetchone()[0]
        == 0
    )
    catalog.close()


@pytest.mark.unit
def test_drop_rejects_external_and_identity_null_restore_targets(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    catalog = BackupCatalog(db_path=tmp_path / "external.sqlite3")
    external = _instance(project_manifest)
    cast("Any", external._client).get_catalog.return_value = catalog
    executor = _executor()
    with pytest.raises(ConfigError, match="SDK-owned Compose"):
        build_database_drop_command(external, project_manifest, "feature_db", executor=executor)
    assert executor.executed == []

    catalog.close()
    catalog = BackupCatalog(db_path=tmp_path / "null.sqlite3")
    monkeypatch.setattr(PostgresCluster, "_inspect_cluster_volume", lambda *_a, **_k: True)
    instance, _backup = _managed_instance(project_manifest, catalog, tmp_path, data_directory=None)
    catalog._conn.execute("UPDATE restores SET cluster_id=NULL WHERE database_name='feature_db'")
    catalog._conn.commit()
    executor = _executor()
    with pytest.raises(ConfigError, match="exact active restore binding"):
        build_database_drop_command(instance, project_manifest, "feature_db", executor=executor)
    assert executor.executed == []
    assert (
        catalog._conn.execute(
            "SELECT COUNT(*) FROM database_events WHERE database_name='feature_db' "
            "AND event_type='dropped'"
        ).fetchone()[0]
        == 0
    )
    catalog.close()


@pytest.mark.unit
def test_drop_rejects_malformed_cluster_identity_before_postgres_effect(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    catalog = BackupCatalog(db_path=tmp_path / "malformed.sqlite3")
    instance = _compose_instance(project_manifest, catalog)
    cluster = instance._postgres_cluster
    assert cluster is not None
    volume = compose_volume_name(cluster._project_id)
    claim = catalog._ensure_postgres_cluster_pending(
        cluster._project_id, cluster.compose_project_name, volume
    )
    catalog._activate_postgres_cluster(
        claim.cluster_id, cluster._project_id, cluster.compose_project_name, volume
    )
    catalog._conn.execute(
        "UPDATE postgres_clusters SET cluster_id='malformed-cluster-id' WHERE project_id=?",
        (cluster._project_id,),
    )
    catalog._conn.commit()
    executor = _executor()

    with pytest.raises(BackupCatalogError, match="malformed identity"):
        build_database_drop_command(instance, project_manifest, "feature_db", executor=executor)
    assert executor.executed == []
    assert (
        catalog._conn.execute(
            "SELECT COUNT(*) FROM database_events WHERE event_type='dropped'"
        ).fetchone()[0]
        == 0
    )
    catalog.close()


@pytest.mark.unit
@pytest.mark.parametrize("usage", ["environment", "process"])
def test_drop_refuses_catalogue_active_environment_or_runtime(
    monkeypatch: pytest.MonkeyPatch,
    project_manifest: Path,
    tmp_path: Path,
    usage: str,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    catalog = BackupCatalog(db_path=tmp_path / f"{usage}.sqlite3")
    data_directory = tmp_path / "data"
    instance, _backup = _managed_instance(
        project_manifest, catalog, tmp_path, data_directory=data_directory
    )
    assert isinstance(instance._client.get_catalog(), BackupCatalog)
    if usage == "environment":
        catalog.create_environment(make_env("active-env", source_db_name="feature_db"))
        assert catalog.list_environments(include_removed=False)[0]["source_db_name"] == "feature_db"
    else:
        catalog._register_project(
            f"project_{repo_key(project_manifest, project_manifest / '.git')}",
            project_manifest,
            project_manifest / ".git",
        )
        catalog._upsert_runtime(
            "project",
            f"project_{repo_key(project_manifest, project_manifest / '.git')}",
            root_pid=123,
            create_time=1.0,
            started_at="2026-01-01T00:00:00",
            checkout_branch="main",
            commit_sha="abc",
            http_url="http://127.0.0.1:8069",
            http_port=8069,
            database_name="feature_db",
        )
        assert catalog._monitor_snapshot_rows().project_runtimes[0]["database_name"] == "feature_db"
    executor = _executor()

    with pytest.raises(ConfigError, match="active environment or process"):
        build_database_drop_command(instance, project_manifest, "feature_db", executor=executor)
    assert executor.executed == []
    catalog.close()


@pytest.mark.unit
def test_verified_drop_cleans_only_filestore_and_retains_source_backup(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    monkeypatch.setattr(PostgresCluster, "_inspect_cluster_volume", lambda *_a, **_k: True)
    data_directory = tmp_path / "data"
    target = data_directory / "filestore" / "feature_db"
    target.mkdir(parents=True)
    (target / "blob").write_bytes(b"filestore")
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    instance, backup_file = _managed_instance(
        project_manifest, catalog, tmp_path, data_directory=data_directory
    )
    binding = catalog._latest_restore_binding("127.0.0.1", 5432, "feature_db")
    assert binding is not None and binding["data_directory"] == str(data_directory)

    result = build_database_drop_command(
        instance, project_manifest, "feature_db", executor=_executor()
    ).run()

    assert result.filestore_state == "deleted"
    assert not target.exists()
    assert backup_file.is_file()
    assert (
        catalog._conn.execute(
            "SELECT event_type FROM database_events WHERE database_name='feature_db' "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()[0]
        == "dropped"
    )
    dropped_count = catalog._conn.execute(
        "SELECT COUNT(*) FROM database_events WHERE database_name='feature_db' "
        "AND event_type='dropped'"
    ).fetchone()[0]
    retry = build_database_drop_command(
        instance, project_manifest, "feature_db", executor=_executor()
    ).run()
    assert retry.filestore_state == "absent"
    assert (
        catalog._conn.execute(
            "SELECT COUNT(*) FROM database_events WHERE database_name='feature_db' "
            "AND event_type='dropped'"
        ).fetchone()[0]
        == dropped_count
    )
    catalog.close()


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["unknown", "symlink"])
def test_drop_preserves_unknown_or_symlink_filestore(
    monkeypatch: pytest.MonkeyPatch,
    project_manifest: Path,
    tmp_path: Path,
    kind: str,
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    monkeypatch.setattr(PostgresCluster, "_inspect_cluster_volume", lambda *_a, **_k: True)
    data_directory = tmp_path / "data"
    target = data_directory / "filestore" / "feature_db"
    target.mkdir(parents=True)
    (target / "retain").write_text("keep")
    if kind == "symlink":
        external = tmp_path / "external"
        external.mkdir()
        (external / "secret").write_text("keep")
        (target / "retain").unlink()
        target.rmdir()
        target.symlink_to(external, target_is_directory=True)
    catalog = BackupCatalog(db_path=tmp_path / f"{kind}.sqlite3")
    instance, _backup = _managed_instance(
        project_manifest,
        catalog,
        tmp_path,
        data_directory=None if kind == "unknown" else data_directory,
    )

    result = build_database_drop_command(
        instance, project_manifest, "feature_db", executor=_executor()
    ).run()

    assert result.filestore_state == "unknown"
    if kind == "symlink":
        assert target.is_symlink()
        assert (tmp_path / "external" / "secret").read_text() == "keep"
    catalog.close()


@pytest.mark.unit
def test_filestore_failure_is_typed_partial_after_database_audit(
    monkeypatch: pytest.MonkeyPatch, project_manifest: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
    monkeypatch.setattr(PostgresCluster, "_inspect_cluster_volume", lambda *_a, **_k: True)
    data_directory = tmp_path / "data"
    (data_directory / "filestore" / "feature_db").mkdir(parents=True)
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    instance, backup_file = _managed_instance(
        project_manifest, catalog, tmp_path, data_directory=data_directory
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.drop.shutil.rmtree",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("permission denied")),
    )

    with pytest.raises(DatabaseDropPartialError) as caught:
        build_database_drop_command(
            instance, project_manifest, "feature_db", executor=_executor()
        ).run()

    context = msgspec.to_builtins(caught.value.failure_context)
    assert context["database_deleted"] is True
    assert context["filestore_cleanup_failed"] is True
    assert backup_file.is_file()
    assert (
        catalog._conn.execute(
            "SELECT event_type FROM database_events WHERE database_name='feature_db' "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()[0]
        == "dropped"
    )
    catalog.close()
