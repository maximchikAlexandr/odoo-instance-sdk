from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import msgspec
import pytest

from odoo_instance_sdk.execution import ProcessStep
from odoo_instance_sdk.internal import database_replacement
from odoo_instance_sdk.internal.database_replacement import build_copy_replacement_command
from odoo_instance_sdk.internal.proc import (
    PreparedProcess,
    PreparedStep,
    ProcessResult,
    RecordingExecutor,
    RunContext,
)
from odoo_instance_sdk.models import (
    DevelopmentEnvironment,
    EnvironmentDatabaseMode,
    EnvironmentState,
    StartConfig,
)
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

if TYPE_CHECKING:
    from odoo_instance_sdk import OdooClient


def _replacement_fixture(  # noqa: C901
    tmp_path: Path, *, sessions: bool = False
) -> tuple[OdooClient, DevelopmentEnvironment, BackupCatalog, uuid.UUID, RecordingExecutor]:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    claim = catalog._ensure_postgres_cluster_pending(
        "project-a", "odcli_pg_project-a", "pgdata_project-a"
    )
    active = catalog._activate_postgres_cluster(
        claim.cluster_id, "project-a", "odcli_pg_project-a", "pgdata_project-a"
    )
    old_id = uuid.uuid4()
    new_id = uuid.uuid4()
    old_backup_path = tmp_path / "old.zip"
    new_backup_path = tmp_path / "new.zip"
    for archive_path, marker in ((old_backup_path, b"old"), (new_backup_path, b"new")):
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"db_name": "source"}))
            archive.writestr("dump.sql", "-- retained dump\n")
            archive.writestr("filestore/source/marker", marker)
    for backup_id, path in ((old_id, old_backup_path), (new_id, new_backup_path)):
        catalog.start_download(str(backup_id), "https://example.test", "source", "zip", True, path)
        catalog.success_download(
            str(backup_id),
            path.name,
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )

    environment_id = uuid.uuid4()
    env_root = tmp_path / "environment"
    generated = env_root / "odoo.conf"
    generated.parent.mkdir()
    data_dir = env_root / "data"
    (data_dir / "filestore" / "copy_target").mkdir(parents=True)
    generated.write_text(
        "[options]\n"
        "db_name = copy_target\n"
        "db_host = 127.0.0.1\n"
        "db_port = 5432\n"
        "db_user = odoo\n"
        "admin_passwd = local-secret\n"
        f"data_dir = {data_dir}\n"
    )
    environment = DevelopmentEnvironment(
        id=environment_id,
        name="repo:PROJ-1",
        repository_root=str(tmp_path),
        git_common_dir=str(tmp_path / ".git"),
        branch="PROJ-1",
        base_ref="main",
        worktree_path=str(env_root / "worktree"),
        generated_config_path=str(generated),
        python_environment_path=str(env_root / "venv"),
        python_environment_owned=False,
        dependency_lock_path=str(env_root / "requirements.lock"),
        http_interface="127.0.0.1",
        http_port=18069,
        db_mode=EnvironmentDatabaseMode.COPY,
        source_db_name="source",
        target_db_name="copy_target",
        backup_id=old_id,
        state=EnvironmentState.READY,
        created_at=datetime.now(UTC),
    )
    catalog.create_environment(
        {
            "id": str(environment.id),
            "name": environment.name,
            "repository_root": environment.repository_root,
            "git_common_dir": environment.git_common_dir,
            "branch": environment.branch,
            "base_ref": environment.base_ref,
            "worktree_path": environment.worktree_path,
            "generated_config_path": environment.generated_config_path,
            "python_environment_path": environment.python_environment_path,
            "python_environment_owned": False,
            "dependency_lock_path": environment.dependency_lock_path,
            "http_interface": environment.http_interface,
            "http_port": environment.http_port,
            "db_mode": "copy",
            "source_db_name": "source",
            "target_db_name": "copy_target",
            "backup_id": str(old_id),
            "runtime_json": json.dumps({"odoo_bin": "/odoo-bin", "runtime_cwd": str(env_root)}),
            "state": "ready",
            "created_at": environment.created_at.isoformat(),
        }
    )
    catalog.record_restore(
        "127.0.0.1",
        5432,
        "copy_target",
        str(old_id),
        cluster_id=active.cluster_id,
        data_directory=data_dir,
    )

    cluster = SimpleNamespace(
        owned=True,
        _project_id="project-a",
        endpoint_host="127.0.0.1",
        endpoint_port=5432,
        _restore_provenance=lambda: (str(active.cluster_id), None),
    )
    instance = MagicMock()
    instance._postgres_cluster = cluster
    instance.config = SimpleNamespace(
        configured_database_names=("copy_target",),
        start_config=SimpleNamespace(data_dir=str(data_dir)),
        db_user="odoo",
        db_password="db-secret",
        default_cwd=env_root,
        project_environment={},
        command_prefix=("/usr/bin/python3", "/odoo-bin"),
    )

    def restore_side_effect(*_args: object, **_kwargs: object) -> None:
        (data_dir / "filestore" / "copy_target").mkdir()

    instance.databases._restore_after_verified_absence.side_effect = restore_side_effect
    instance.databases._restore_impl_locked.side_effect = restore_side_effect
    client = MagicMock()
    client.get_catalog.return_value = catalog
    client.instance.from_environment.return_value = instance

    def result_factory(step: PreparedProcess) -> ProcessResult:
        step_id = step.step_id
        if step_id in {"database.replace.inspect", "database.replace.revalidate"}:
            payload = {"target_exists": True, "rollback_exists": False, "sessions": []}
            if sessions:
                payload["sessions"] = [{"pid": 42}]
            stdout = json.dumps(payload)
        elif step_id == "database.replace.pre-cleanup.verify":
            stdout = json.dumps({"target_exists": True, "rollback_exists": True, "sessions": []})
        elif step_id == "database.replace.move-database.verify":
            stdout = json.dumps({"target_exists": False, "rollback_exists": True, "sessions": []})
        elif step_id == "database.replace.cleanup-rollback.verify":
            stdout = json.dumps({"target_exists": True, "rollback_exists": False, "sessions": []})
        elif step_id == "database.restore.exists-after":
            stdout = "t"
        else:
            stdout = ""
        return ProcessResult(
            argv=step.argv,
            returncode=0,
            stdout=stdout,
            stderr="",
            duration=0.0,
            cwd=getattr(step, "cwd", None),
            environment=getattr(step, "environment", ()),
        )

    executor = RecordingExecutor(result_factory=result_factory)
    return cast("OdooClient", client), environment, catalog, new_id, executor


def _mock_environment_instance(client: OdooClient) -> MagicMock:
    factory = cast("MagicMock", client.instance.from_environment)
    return cast("MagicMock", factory.return_value)


def _environment_row(catalog: BackupCatalog, environment: DevelopmentEnvironment) -> sqlite3.Row:
    row = catalog.get_environment(str(environment.id))
    assert row is not None
    return row


def test_replacement_dry_run_captures_rollback_and_compensation_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
    )
    client, environment, catalog, backup_id, executor = _replacement_fixture(tmp_path)
    command = build_copy_replacement_command(client, environment, backup_id, executor=executor)

    step_ids = tuple(step.step_id for step in command.plan.steps)
    assert "database.replace.move-database" in step_ids
    assert "database.replace.move-filestore" in step_ids
    assert "database.replace.publish-provenance" in step_ids
    assert "database.replace.compensate.restore-database" in step_ids
    assert _environment_row(catalog, environment)["backup_id"] == str(environment.backup_id)
    catalog.close()


def test_replacement_plan_defers_archive_payload_until_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
    )
    client, environment, catalog, backup_id, executor = _replacement_fixture(tmp_path)

    command = build_copy_replacement_command(client, environment, backup_id, executor=executor)

    restore_steps = [
        step
        for step in command._prepared().steps
        if getattr(step, "step_id", "").startswith("database.replace.restore")
    ]
    assert restore_steps
    assert all(getattr(step, "stdin", None) is None for step in restore_steps)
    assert all("restore.sql" not in getattr(step, "step_id", "") for step in restore_steps)
    restore = next(step for step in restore_steps if step.step_id.endswith("restore.psql"))
    restore = cast("PreparedStep", restore)
    assert restore.argv[0].endswith("/psql")
    assert restore.argv[-2:-1] == ("--file",)
    assert "ON_ERROR_STOP=1" in restore.argv
    catalog.close()


def test_replacement_rejects_same_shape_backup_byte_swap_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
    )
    client, environment, catalog, backup_id, executor = _replacement_fixture(tmp_path)
    command = build_copy_replacement_command(client, environment, backup_id, executor=executor)

    archive_path = tmp_path / "new.zip"
    original_size = archive_path.stat().st_size
    original_factory = cast("Callable[[PreparedProcess], ProcessResult]", executor.result_factory)

    def mutate_after_revalidation(step: PreparedProcess) -> ProcessResult:
        result = original_factory(step)
        if step.step_id == "database.replace.revalidate":
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"db_name": "source"}))
                archive.writestr("dump.sql", "-- changed dump!\n")
                archive.writestr("filestore/source/marker", b"new")
            assert archive_path.stat().st_size == original_size
        return result

    executor.result_factory = mutate_after_revalidation

    with pytest.raises(Exception, match=r"selected backup|content hash"):
        command.run()

    executed = {step.step_id for step in executor.executed}
    assert "database.replace.move-database" not in executed
    assert "database.replace.restore.create" not in executed
    assert "database.replace.restore.psql" not in executed
    catalog.close()


def test_replacement_active_sessions_fail_before_any_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
    )
    client, environment, catalog, backup_id, executor = _replacement_fixture(
        tmp_path, sessions=True
    )

    with pytest.raises(Exception, match="active target database sessions"):
        build_copy_replacement_command(client, environment, backup_id, executor=executor)

    assert [step.step_id for step in executor.executed] == ["database.replace.inspect"]
    assert not _mock_environment_instance(client).databases._restore_after_verified_absence.called
    assert _environment_row(catalog, environment)["backup_id"] == str(environment.backup_id)
    catalog.close()


def test_replacement_success_publishes_new_backup_and_removes_rollback_filestore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
    )
    client, environment, catalog, backup_id, executor = _replacement_fixture(tmp_path)
    command = build_copy_replacement_command(client, environment, backup_id, executor=executor)

    result = command.run()

    assert result.backup_id == backup_id
    assert result.database == "copy_target"
    row = catalog.get_environment(str(environment.id))
    assert row is not None
    assert row["backup_id"] == str(backup_id)
    assert not (tmp_path / "environment" / "data" / "filestore" / "copy_target").is_symlink()
    assert len(tuple((tmp_path / "environment" / "data" / "filestore").iterdir())) == 1
    assert not _mock_environment_instance(client).databases._restore_impl_locked.called
    assert {step.step_id for step in executor.executed} >= {
        "database.replace.restore.create",
        "database.replace.restore.psql",
    }
    assert "database.replace.restore.validate" in {
        step.step_id for step in command._prepared().steps
    }
    catalog.close()


def test_replacement_partial_cleanup_targets_rollback_database_not_restored_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
    )
    client, environment, catalog, backup_id, executor = _replacement_fixture(tmp_path)
    command = build_copy_replacement_command(client, environment, backup_id, executor=executor)

    cleanup = next(
        step
        for step in command.plan.steps
        if step.step_id == "database.replace.cleanup-rollback.database"
    )
    assert isinstance(cleanup, ProcessStep)

    assert 'DROP DATABASE IF EXISTS "copy_target";' not in cleanup.display
    assert "odcli_rb_" in cleanup.display
    catalog.close()


def test_replacement_uses_local_restore_pipeline_without_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
    )
    client, environment, catalog, backup_id, executor = _replacement_fixture(tmp_path)
    database = _mock_environment_instance(client).databases
    database._restore_after_verified_absence.side_effect = AssertionError(
        "replacement must not call the lock-taking restore wrapper"
    )
    command = build_copy_replacement_command(client, environment, backup_id, executor=executor)

    command.run()

    database._restore_impl_locked.assert_not_called()
    database._restore_after_verified_absence.assert_not_called()
    assert "database.replace.restore.psql" in [step.step_id for step in executor.executed]
    catalog.close()


def test_replacement_revalidates_generated_config_after_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
    )
    client, environment, catalog, backup_id, executor = _replacement_fixture(tmp_path)
    command = build_copy_replacement_command(client, environment, backup_id, executor=executor)

    Path(environment.generated_config_path).write_text(
        Path(environment.generated_config_path)
        .read_text()
        .replace("db_name = copy_target", "db_name = changed_target")
    )

    with pytest.raises(Exception, match="generated environment config changed"):
        command.run()

    assert not _mock_environment_instance(client).databases._restore_impl_locked.called
    assert _environment_row(catalog, environment)["backup_id"] == str(environment.backup_id)
    catalog.close()


def test_replacement_planning_target_absence_fails_before_command_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
    )
    client, environment, catalog, backup_id, executor = _replacement_fixture(tmp_path)
    original_factory = cast("Callable[[PreparedProcess], ProcessResult]", executor.result_factory)
    assert original_factory is not None

    def absent_target(step: PreparedProcess) -> ProcessResult:
        result = original_factory(step)
        if step.step_id == "database.replace.inspect":
            return replace(
                result,
                stdout=json.dumps(
                    {"target_exists": False, "rollback_exists": False, "sessions": []}
                ),
            )
        return result

    executor.result_factory = absent_target
    with pytest.raises(Exception, match="target database is unavailable"):
        build_copy_replacement_command(client, environment, backup_id, executor=executor)

    assert [step.step_id for step in executor.executed] == ["database.replace.inspect"]
    assert _environment_row(catalog, environment)["backup_id"] == str(environment.backup_id)
    catalog.close()


def test_replacement_parses_move_postcondition_before_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
    )
    client, environment, catalog, backup_id, executor = _replacement_fixture(tmp_path)
    original_factory = cast("Callable[[PreparedProcess], ProcessResult]", executor.result_factory)
    assert original_factory is not None

    def bad_move_verification(step: PreparedProcess) -> ProcessResult:
        result = original_factory(step)
        if step.step_id == "database.replace.move-database.verify":
            return replace(
                result,
                stdout=json.dumps(
                    {"target_exists": True, "rollback_exists": False, "sessions": []}
                ),
            )
        return result

    executor.result_factory = bad_move_verification
    command = build_copy_replacement_command(client, environment, backup_id, executor=executor)

    with pytest.raises(Exception, match="prior database move verification failed"):
        command.run()

    assert not _mock_environment_instance(client).databases._restore_impl_locked.called
    assert _environment_row(catalog, environment)["backup_id"] == str(environment.backup_id)
    catalog.close()


def test_replacement_incomplete_compensation_persists_sanitized_cleanup_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
    )
    client, environment, catalog, backup_id, executor = _replacement_fixture(tmp_path)
    original_factory = cast("Callable[[PreparedProcess], ProcessResult]", executor.result_factory)
    assert original_factory is not None

    def failed_restore(step: PreparedProcess) -> ProcessResult:
        result = original_factory(step)
        if step.step_id == "database.replace.restore.psql":
            return replace(result, returncode=1, stderr="restore failed")
        return result

    executor.result_factory = failed_restore
    original_rename = database_replacement._rename

    def fail_database_compensation(
        context: RunContext[None], step_id: str, *, message: str
    ) -> None:
        if step_id == "database.replace.compensate.restore-database":
            raise RuntimeError("compensation unavailable")
        original_rename(context, step_id, message=message)

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.database_replacement._rename", fail_database_compensation
    )
    command = build_copy_replacement_command(client, environment, backup_id, executor=executor)

    with pytest.raises(Exception, match="PostgreSQL restore failed") as error:
        command.run()

    context = getattr(error.value, "failure_context")
    assert context.cleanup_failed is True
    row = catalog.get_environment(str(environment.id))
    assert row is not None
    assert row["state"] == "cleanup_failed"
    assert "copy replacement cleanup_failed" in str(row["last_error"])
    assert "copy_target" in str(row["last_error"])
    assert f"backup={backup_id}" in str(row["last_error"])
    catalog.close()


def test_replacement_keeps_new_pair_after_rollback_cleanup_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
    )
    client, environment, catalog, backup_id, executor = _replacement_fixture(tmp_path)
    original_rmtree = shutil.rmtree

    def fail_rollback(path: str | os.PathLike[str], *args: object, **kwargs: object) -> None:
        del args, kwargs
        if str(path).endswith("odcli_rb_" + environment.id.hex[:20]):
            raise OSError("rollback filestore cleanup fault")
        original_rmtree(path)

    monkeypatch.setattr(shutil, "rmtree", fail_rollback)
    command = build_copy_replacement_command(client, environment, backup_id, executor=executor)

    with pytest.raises(OSError, match="rollback filestore cleanup fault"):
        command.run()

    row = catalog.get_environment(str(environment.id))
    assert row is not None
    assert row["state"] == "cleanup_failed"
    assert row["backup_id"] == str(backup_id)
    filestore_root = tmp_path / "environment" / "data" / "filestore"
    assert (filestore_root / "copy_target").is_dir()
    assert (filestore_root / f"copy_target_odcli_rb_{environment.id.hex[:20]}").is_dir()
    catalog.close()


def test_replacement_cleanup_failed_published_topology_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
    )
    client, environment, catalog, backup_id, executor = _replacement_fixture(tmp_path)
    original_rmtree = shutil.rmtree
    calls = 0

    def fail_once(path: str | os.PathLike[str], *args: object, **kwargs: object) -> None:
        del args, kwargs
        nonlocal calls
        if str(path).endswith("odcli_rb_" + environment.id.hex[:20]) and calls == 0:
            calls += 1
            raise OSError("rollback filestore cleanup fault")
        original_rmtree(path)

    monkeypatch.setattr(shutil, "rmtree", fail_once)
    command = build_copy_replacement_command(client, environment, backup_id, executor=executor)
    with pytest.raises(OSError, match="rollback filestore cleanup fault"):
        command.run()

    retry_environment = msgspec.structs.replace(
        environment,
        backup_id=backup_id,
        state=EnvironmentState.CLEANUP_FAILED,
    )
    retry = build_copy_replacement_command(client, retry_environment, backup_id, executor=executor)
    result = retry.run()

    assert result.backup_id == backup_id
    filestore_root = tmp_path / "environment" / "data" / "filestore"
    assert (filestore_root / "copy_target").is_dir()
    assert tuple(filestore_root.iterdir()) == (filestore_root / "copy_target",)
    catalog.close()


def test_replacement_final_verification_fault_keeps_published_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
    )
    client, environment, catalog, backup_id, executor = _replacement_fixture(tmp_path)
    original_factory = cast("Callable[[PreparedProcess], ProcessResult]", executor.result_factory)
    assert original_factory is not None

    def failed_final_verify(step: PreparedProcess) -> ProcessResult:
        result = original_factory(step)
        if step.step_id == "database.replace.cleanup-rollback.verify":
            return replace(
                result,
                stdout=json.dumps(
                    {"target_exists": False, "rollback_exists": False, "sessions": []}
                ),
            )
        return result

    executor.result_factory = failed_final_verify
    command = build_copy_replacement_command(client, environment, backup_id, executor=executor)

    with pytest.raises(Exception, match="cleanup postconditions failed"):
        command.run()

    row = catalog.get_environment(str(environment.id))
    assert row is not None
    assert row["state"] == "cleanup_failed"
    assert row["backup_id"] == str(backup_id)
    assert (tmp_path / "environment" / "data" / "filestore" / "copy_target").is_dir()
    catalog.close()


def test_replacement_nonzero_admin_reset_does_not_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
    )
    client, environment, catalog, backup_id, executor = _replacement_fixture(tmp_path)
    instance = _mock_environment_instance(client)
    instance.config.start_config = StartConfig(
        config_path=environment.generated_config_path,
        db_name="copy_target",
        db_host="127.0.0.1",
        db_port=5432,
        db_user="odoo",
        db_password="db-secret",
        data_dir=str(tmp_path / "environment" / "data"),
    )
    original_factory = cast("Callable[[PreparedProcess], ProcessResult]", executor.result_factory)
    assert original_factory is not None

    def failed_reset(step: PreparedProcess) -> ProcessResult:
        result = original_factory(step)
        if step.step_id == "instance.shell_script":
            return replace(result, returncode=1, stderr="reset failed")
        return result

    executor.result_factory = failed_reset
    command = build_copy_replacement_command(
        client, environment, backup_id, reset_admin_password=True, executor=executor
    )

    with pytest.raises(Exception, match="administrator password reset failed"):
        command.run()

    assert _environment_row(catalog, environment)["backup_id"] == str(environment.backup_id)
    assert "database.replace.publish-provenance" not in {
        event.step_id for event in executor.executed
    }
    catalog.close()
