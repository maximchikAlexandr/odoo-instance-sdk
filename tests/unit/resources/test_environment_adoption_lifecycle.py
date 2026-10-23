from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.internal.proc import (
    PreparedProcess,
    ProcessHandle,
    ProcessResult,
    RecordingExecutor,
)
from odoo_instance_sdk.models import (
    Backup,
    BackupFormat,
    Database,
    EnvironmentState,
    NoBackup,
    StartConfig,
)
from odoo_instance_sdk.resources.environment import (
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
)
from odoo_instance_sdk.resources.instance import OdooInstance
from tests.unit.resources.adoption_test_support import _external_checkout, _process_result


def _adoption_copy_instance(env_client: object) -> OdooInstance:
    lifecycle_client = MagicMock()
    lifecycle_client.get_catalog.return_value = env_client.get_catalog()  # type: ignore[attr-defined]
    lifecycle_client.unregister_process.return_value = (None, None)
    instance = OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:0",
            start_config=StartConfig(http_port=0),
            command_prefix=("/usr/bin/python", "/project/odoo-bin"),
            default_cwd=Path.cwd(),
            db_host="localhost",
            db_port=5432,
            db_user="odoo",
        ),
        _client=lifecycle_client,
    )
    instance.databases = MagicMock()
    instance.databases.list.return_value = (Database(name="comerta", backup=NoBackup()),)
    instance.databases.names.return_value = ("comerta",)
    instance.databases.exists.return_value = False
    return instance


def _record_backup(env_client: object, backup: Backup) -> None:
    catalog = env_client.get_catalog()  # type: ignore[attr-defined]
    catalog.start_download(
        str(backup.id),
        backup.source_base_url,
        backup.database_name,
        backup.format.value,
        backup.filestore_requested,
        Path(backup.path),
        source_git_branch=backup.source_git_branch,
        source_name=backup.source_name,
    )
    catalog.success_download(
        str(backup.id),
        backup.filename,
        backup.size_bytes,
        backup.sha256,
        downloaded_at=backup.downloaded_at,
    )


def _adoption_retained_backup(tmp_path: Path) -> Backup:
    path = tmp_path / "adoption-retained.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"db_name": "comerta", "version": "19.0"}))
        archive.writestr("dump.sql", "select 1;\n")
        archive.writestr("filestore/comerta/asset", b"payload")
    return Backup(
        id=uuid.uuid4(),
        source_base_url="https://staging.example",
        database_name="comerta",
        format=BackupFormat.ZIP,
        filestore_requested=True,
        path=str(path),
        filename=path.name,
        size_bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        downloaded_at=datetime(2026, 9, 26, 21, 30, tzinfo=UTC),
        source_git_branch="main",
        source_name="staging",
    )


def _add_compose_config(project_manifest: Path) -> None:
    manifest = project_manifest / ".odcli" / "project.toml"
    manifest.write_text(
        manifest.read_text()
        + "\n[postgres]\n"
        + 'mode = "compose"\n'
        + 'image = "postgres:16"\n'
        + "port = 5432\n"
    )


def _real_adoption_command(  # noqa: C901
    env_client: object,
    project_manifest: Path,
    checkout: Path,
    fake_python: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    source_kind: str,
    tmp_path: Path,
    restore_error: BaseException | None = None,
    source_error: BaseException | None = None,
) -> tuple[object, RecordingExecutor, OdooInstance, Backup | None, Path, list[str]]:
    from odoo_instance_sdk.resources.instance import InstanceFactory, OdooInstance

    _add_compose_config(project_manifest)
    local = _adoption_copy_instance(env_client)
    backup = _adoption_retained_backup(tmp_path)
    remote = MagicMock()
    if source_kind == "named":
        remote_backup = Backup(
            id=backup.id,
            source_base_url=backup.source_base_url,
            database_name=backup.database_name,
            format=backup.format,
            filestore_requested=backup.filestore_requested,
            path=backup.path,
            filename=backup.filename,
            size_bytes=backup.size_bytes,
            sha256=backup.sha256,
            downloaded_at=backup.downloaded_at,
            source_git_branch=backup.source_git_branch,
            source_name="staging",
        )

        def acquire(*_args: object, **_kwargs: object) -> Backup:
            if source_error is not None:
                raise source_error
            _record_backup(env_client, remote_backup)
            return remote_backup

        remote.databases.backup.side_effect = acquire
        monkeypatch.setattr(InstanceFactory, "__call__", lambda *_args, **_kwargs: remote)
        monkeypatch.setenv("ODCLI_REMOTE_STAGING_MASTER_PASSWORD", "staging-secret")
        selected = remote_backup
    else:
        _record_backup(env_client, backup)
        selected = backup

    filestore = checkout / ".odcli" / "filestore" / f"{source_kind}_adoption"
    restored_targets: list[str] = []

    def restore_success(selected_backup: Backup, target: str, **_kwargs: object) -> None:
        from odoo_instance_sdk.internal.proc import active_context

        restored_targets.append(target)
        context = active_context()
        assert context is not None
        context.process("database.restore.exists-after")
        filestore.mkdir(parents=True, exist_ok=True)
        (filestore / "asset").write_bytes(b"restored")
        env_client.get_catalog().record_restore(  # type: ignore[attr-defined]
            "localhost", 5432, target, str(selected_backup.id), database_oid=1
        )

    restore_effect: object = restore_error if restore_error is not None else restore_success
    cast("Any", local.databases._restore_after_verified_absence).side_effect = restore_effect
    monkeypatch.setattr(InstanceFactory, "from_config", MagicMock(return_value=local))
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.auxiliary_restore._assert_http_port_free",
        lambda _config: None,
    )
    monkeypatch.setattr(
        OdooInstance,
        "wait_ready",
        lambda _self, _process, *, timeout, version_info=False, database_manager=False: None,
    )

    process = MagicMock()
    process.pid = 123
    handle = ProcessHandle(
        process=process,
        argv=(),
        process_group_id=123,
        session_id=123,
        inherited_stdio=False,
    )
    resource = env_client.environments  # type: ignore[attr-defined]
    original = type(resource)._command_from_snapshot
    captured: dict[str, object] = {}

    def build(self: object, snapshot: object, *, executor: object | None = None) -> object:
        plan = snapshot.private  # type: ignore[attr-defined]

        def result_for(step: PreparedProcess) -> ProcessResult:
            if step.step_id == "checkout.validate.git.toplevel":
                return _process_result(step, str(plan.repo_root))
            if step.step_id == "checkout.validate.git.common-dir":
                return _process_result(step, plan.git_common_dir)
            if step.step_id == "checkout.validate.git.base":
                value = subprocess.check_output(
                    ["git", "-C", str(plan.worktree), "rev-parse", plan.base_ref], text=True
                )
                return _process_result(step, value)
            if step.step_id == "checkout.validate.git.head":
                value = subprocess.check_output(
                    ["git", "-C", str(plan.worktree), "rev-parse", "HEAD"], text=True
                )
                return _process_result(step, value)
            if step.step_id == "checkout.validate.git.status":
                value = subprocess.check_output(
                    ["git", "-C", str(plan.worktree), "status", "--porcelain"], text=True
                )
                return _process_result(step, value)
            if step.step_id == "database.restore.exists-after":
                return _process_result(step, "1")
            return _process_result(step)

        recording = RecordingExecutor(
            result_factory=result_for, handles={"database.restore.auxiliary.start": handle}
        )
        captured["executor"] = recording
        return original(self, snapshot, executor=recording)

    monkeypatch.setattr(type(resource), "_command_from_snapshot", build)
    options = EnvironmentCheckoutOptions(
        db_mode=EnvironmentDatabaseMode.COPY,
        base_ref="main",
        remote_name="staging" if source_kind == "named" else None,
        backup_id=None if source_kind == "named" else selected.id,
        python=str(fake_python),
        target_database=f"{source_kind}_adoption",
    )
    command = resource.adopt_command(project_manifest, checkout, options=options)
    return (
        command,
        cast("RecordingExecutor", captured["executor"]),
        local,
        selected,
        filestore,
        restored_targets,
    )


def _assert_real_adoption_success(
    command: object,
    executor: RecordingExecutor,
    local: OdooInstance,
    backup: Backup | None,
    filestore: Path,
    restored_targets: list[str],
    env_client: object,
    project_manifest: Path,
) -> tuple[str, ...]:
    events: list[object] = []
    environment = command.run(observer=events.append)  # type: ignore[attr-defined]

    assert environment.state is EnvironmentState.READY
    assert environment.code_ownership.value == "caller_owned"
    assert backup is not None
    assert len(restored_targets) == 1
    assert environment.target_db_name == restored_targets[0]
    assert filestore.is_dir()
    assert (filestore / "asset").read_bytes() == b"restored"
    from odoo_instance_sdk.internal.odoo_config import parse_odoo_config

    generated = parse_odoo_config(Path(environment.generated_config_path))
    assert Path(generated["data_dir"]) == filestore.parent.resolve()
    assert filestore.is_relative_to(Path(environment.checkout_repository_root))
    assert cast("Any", local.databases._restore_after_verified_absence).call_count == 1
    cast("Any", local._client.register_process).assert_called_once()
    cast("Any", local._client.unregister_process).assert_called_once()
    assert len(executor.spawned) == 1
    assert [step.step_id for step in executor.spawned] == ["database.restore.auxiliary.start"]
    assert [step.step_id for step in executor.executed].count("database.restore.exists-before") == 1
    assert [step.step_id for step in executor.executed].count("database.restore.exists-after") == 1
    action_events = [(event.step_id, event.kind) for event in events]  # type: ignore[attr-defined]
    for step_id in (
        "database.restore.auxiliary.probe",
        "database.restore.auxiliary.port",
        "database.restore.auxiliary.ready",
        "database.restore.auxiliary.cleanup",
    ):
        assert action_events.count((step_id, "started")) == 1
        assert action_events.count((step_id, "completed")) == 1
    assert action_events.count(("database.restore.auxiliary.start", "started")) == 1
    assert env_client.environments.list(project=project_manifest)[0].state is EnvironmentState.READY  # type: ignore[attr-defined]
    return tuple(step.step_id for step in command.plan.steps)  # type: ignore[attr-defined]


def test_adopt_command_executes_named_remote_copy_with_real_auxiliary_session(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    command, executor, local, backup, filestore, restored_targets = _real_adoption_command(
        env_client,
        project_manifest,
        checkout,
        fake_python,
        monkeypatch,
        source_kind="named",
        tmp_path=tmp_path,
    )
    plan_ids = _assert_real_adoption_success(
        command,
        executor,
        local,
        backup,
        filestore,
        restored_targets,
        env_client,
        project_manifest,
    )
    assert "database.backup.wait" in plan_ids
    assert "database.backup.transfer" in plan_ids


def test_adopt_command_executes_retained_backup_copy_with_real_auxiliary_session(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    command, executor, local, backup, filestore, restored_targets = _real_adoption_command(
        env_client,
        project_manifest,
        checkout,
        fake_python,
        monkeypatch,
        source_kind="retained",
        tmp_path=tmp_path,
    )
    plan_ids = _assert_real_adoption_success(
        command,
        executor,
        local,
        backup,
        filestore,
        restored_targets,
        env_client,
        project_manifest,
    )
    assert "database.backup.wait" not in plan_ids
    assert "database.backup.transfer" not in plan_ids


def test_adopt_command_source_acquisition_failure_cleans_real_auxiliary_session(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    command, executor, _local, _backup, filestore, restored_targets = _real_adoption_command(
        env_client,
        project_manifest,
        checkout,
        fake_python,
        monkeypatch,
        source_kind="named",
        tmp_path=tmp_path,
        source_error=OSError("source acquisition failed"),
    )

    with pytest.raises(OSError, match="source acquisition failed"):
        command.run()  # type: ignore[attr-defined]

    assert len(executor.spawned) == 1
    cast("Any", _local._client.register_process).assert_called_once()
    cast("Any", _local._client.unregister_process).assert_called_once()
    assert env_client._processes == {}  # type: ignore[attr-defined]
    environments = env_client.environments.list(project=project_manifest)  # type: ignore[attr-defined]
    assert environments and all(item.state is not EnvironmentState.READY for item in environments)
    assert not restored_targets
    assert not filestore.exists()
    assert checkout.is_dir()


@pytest.mark.parametrize("failure", [OSError("restore failed"), KeyboardInterrupt("cancelled")])
def test_adopt_command_restore_failure_or_cancellation_cleans_real_auxiliary_session(
    env_client: object,
    project_manifest: Path,
    fake_python: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    checkout = _external_checkout(project_manifest, tmp_path, linked=False)
    command, executor, _local, _backup, filestore, restored_targets = _real_adoption_command(
        env_client,
        project_manifest,
        checkout,
        fake_python,
        monkeypatch,
        source_kind="retained",
        tmp_path=tmp_path,
        restore_error=failure,
    )

    with pytest.raises(type(failure), match=str(failure)):
        command.run()  # type: ignore[attr-defined]

    assert len(executor.spawned) == 1
    cast("Any", _local._client.register_process).assert_called_once()
    cast("Any", _local._client.unregister_process).assert_called_once()
    assert env_client._processes == {}  # type: ignore[attr-defined]
    environments = env_client.environments.list(project=project_manifest)  # type: ignore[attr-defined]
    assert environments and all(item.state is not EnvironmentState.READY for item in environments)
    assert not restored_targets
    assert not filestore.exists()
    assert checkout.is_dir()
