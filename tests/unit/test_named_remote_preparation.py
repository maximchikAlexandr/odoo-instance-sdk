from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from odoo_instance_sdk.exceptions import (
    ConfigError,
    EnvironmentConflictError,
    MasterPasswordRequiredError,
)
from odoo_instance_sdk.internal.dbprep import materialize as preparation
from odoo_instance_sdk.internal.dbprep.source import (
    _remote_password,
    remote_password_key,
    resolve_test_source,
)
from odoo_instance_sdk.internal.doctor import DoctorReport
from odoo_instance_sdk.internal.doctor import manifest as doctor_manifest
from odoo_instance_sdk.internal.process_env import sanitized_child_environment
from odoo_instance_sdk.internal.project_env import effective_project_environment
from odoo_instance_sdk.models import (
    Backup,
    BackupBranchOrigin,
    BackupFormat,
    DatabaseRefreshOptions,
)
from odoo_instance_sdk.project import (
    ProjectConfig,
    RemoteSourceConfig,
)
from odoo_instance_sdk.project import (
    TestInstanceProjectConfig as LegacyConfig,
)


def _project(
    *sources: RemoteSourceConfig, legacy: bool = False, root: Path = Path("/project")
) -> ProjectConfig:
    return ProjectConfig(
        repository_root=root,
        test_instance=(
            LegacyConfig(
                base_url="https://legacy.example",
                database="legacy_db",
                git_branch="main",
            )
            if legacy
            else None
        ),
        remote_instances=sources,
    )


def _backup(tmp_path: Path, *, source_name: str | None, branch: str) -> Backup:
    path = tmp_path / "remote.zip"
    path.write_bytes(b"backup")
    return Backup(
        id=uuid.uuid4(),
        source_base_url="https://staging.example",
        database_name="staging_db",
        format=BackupFormat.ZIP,
        filestore_requested=True,
        path=str(path),
        filename=path.name,
        size_bytes=path.stat().st_size,
        sha256="a" * 64,
        downloaded_at=datetime.now(UTC),
        source_git_branch=branch,
        source_name=source_name,
    )


def test_named_source_resolution_is_explicit_and_keeps_provenance() -> None:
    source = RemoteSourceConfig(
        name="staging",
        base_url="https://staging.example",
        database="staging_db",
        git_branch="staging",
    )

    resolved = resolve_test_source(_project(source), DatabaseRefreshOptions(remote_name="STAGING"))

    assert resolved.source_name == "staging"
    assert resolved.config.base_url == "https://staging.example"
    assert resolved.config.database == "staging_db"
    assert resolved.branch == "staging"


def test_named_source_does_not_fall_back_to_legacy_or_first_profile() -> None:
    first = RemoteSourceConfig(
        name="lab", base_url="https://lab.example", database="lab_db", git_branch="main"
    )
    project = _project(first, legacy=True)

    with pytest.raises(ConfigError, match="unknown remote source 'missing'"):
        resolve_test_source(project, DatabaseRefreshOptions(remote_name="missing"))
    with pytest.raises(ConfigError, match=r"no \[test_instance\]"):
        resolve_test_source(_project(first))


def test_named_password_uses_process_precedence_and_rejects_empty_override() -> None:
    key = remote_password_key("staging")
    assert key == "ODCLI_REMOTE_STAGING_MASTER_PASSWORD"
    environment = effective_project_environment({key: "from-file"}, {key: "from-process"})
    assert _remote_password(environment, remote_name="staging") == "from-process"

    empty = effective_project_environment({key: "from-file"}, {key: ""})
    with pytest.raises(MasterPasswordRequiredError, match=key):
        _remote_password(empty, remote_name="staging")


def test_child_environment_removes_all_named_remote_passwords() -> None:
    environment = {
        "ODCLI_REMOTE_LAB_MASTER_PASSWORD": "lab-secret",
        "ODCLI_REMOTE_STAGING_MASTER_PASSWORD": "staging-secret",
        "ODCLI_TEST_MASTER_PASSWORD": "legacy-secret",
        "SAFE": "kept",
    }

    child = sanitized_child_environment(environment)

    assert child == {"SAFE": "kept"}


def test_named_download_uses_secret_and_preserves_source_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = RemoteSourceConfig(
        name="staging",
        base_url="https://staging.example",
        database="staging_db",
        git_branch="staging",
    )
    project = _project(source, root=tmp_path)
    backup = _backup(tmp_path, source_name="staging", branch="staging")
    client = MagicMock()
    client.instance.return_value.databases.backup.return_value = backup
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setattr(preparation, "exclusive_lock", lambda _: contextlib.nullcontext())
    monkeypatch.setenv("ODCLI_REMOTE_STAGING_MASTER_PASSWORD", "staging-secret")
    monkeypatch.setenv("ODCLI_REMOTE_LAB_MASTER_PASSWORD", "lab-secret")

    result = preparation.prepare_download(
        client, project, options=DatabaseRefreshOptions(remote_name="STAGING")
    )

    client.instance.assert_called_once_with(
        "https://staging.example", master_password="staging-secret"
    )
    client.instance.return_value.databases.backup.assert_called_once_with(
        "staging_db",
        source_git_branch="staging",
        project_id="project_repo",
        source_name="staging",
    )
    assert result.backup == backup
    assert result.source_git_branch == "staging"
    assert result.branch_origin is BackupBranchOrigin.CONFIGURED


@pytest.mark.parametrize(
    ("remote_name", "password_key", "origin_key"),
    [
        (None, "ODCLI_TEST_MASTER_PASSWORD", "ODCLI_TEST_INSTANCE_ORIGIN_PINS"),
        ("staging", "ODCLI_REMOTE_STAGING_MASTER_PASSWORD", "ODCLI_REMOTE_STAGING_ORIGIN"),
    ],
)
def test_origin_variables_are_inert_during_download_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remote_name: str | None,
    password_key: str,
    origin_key: str,
) -> None:
    source = RemoteSourceConfig(
        name="staging",
        base_url="https://staging.example",
        database="staging_db",
        git_branch="staging",
    )
    project = _project(source, legacy=remote_name is None, root=tmp_path)
    backup = _backup(tmp_path, source_name=remote_name, branch="staging")
    client = MagicMock()
    client.instance.return_value.databases.backup.return_value = backup
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setattr(preparation, "exclusive_lock", lambda _: contextlib.nullcontext())
    monkeypatch.setenv(password_key, "source-secret")
    monkeypatch.setenv(origin_key, "not a valid origin")

    result = preparation.prepare_download(
        client,
        project,
        options=DatabaseRefreshOptions(remote_name=remote_name),
    )

    assert result.backup == backup
    client.instance.assert_called_once()


def test_named_source_profile_drift_is_rejected_before_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.proc import (
        PreparedProcess,
        PreparedStep,
        ProcessResult,
        RecordingExecutor,
    )

    def result_for(step: PreparedProcess) -> ProcessResult:
        prepared = cast("PreparedStep", step)
        return ProcessResult(
            argv=prepared.argv,
            returncode=0,
            stdout=str(tmp_path),
            stderr="",
            duration=0.0,
            cwd=prepared.cwd,
            environment=prepared.environment,
        )

    source = RemoteSourceConfig(
        name="staging",
        base_url="https://staging.example",
        database="staging_db",
        git_branch="staging",
    )
    project = _project(source, root=tmp_path)
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text(project.to_manifest())
    command = preparation.DatabasePreparationCoordinator(MagicMock()).prepare_command(
        tmp_path,
        options=DatabaseRefreshOptions(remote_name="staging"),
        executor=RecordingExecutor(result_factory=result_for),
    )
    changed = _project(
        RemoteSourceConfig(
            name="staging",
            base_url="https://other.example",
            database="staging_db",
            git_branch="staging",
        ),
        root=tmp_path,
    )
    (manifest_dir / "project.toml").write_text(changed.to_manifest())

    with pytest.raises(EnvironmentConflictError, match="selected remote source changed"):
        command.run()


def test_named_source_coalescing_requires_matching_source_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_config = tmp_path / "odoo.conf"
    source_config.write_text(
        "[options]\n"
        "http_interface = 127.0.0.1\n"
        "http_port = 8069\n"
        "db_host = localhost\n"
        "db_name = old\n"
        "admin_passwd = local-secret\n"
    )
    project = ProjectConfig(
        repository_root=tmp_path,
        source_config=source_config,
        default_source_database="old",
        refresh_after_hours=1,
        remote_instances=(
            RemoteSourceConfig(
                name="staging",
                base_url="https://staging.example",
                database="staging_db",
                git_branch="staging",
            ),
        ),
    )
    backup = _backup(tmp_path, source_name="staging", branch="staging")
    backup = Backup(
        id=backup.id,
        source_base_url=backup.source_base_url,
        database_name="staging_db",
        format=backup.format,
        filestore_requested=backup.filestore_requested,
        path=backup.path,
        filename=backup.filename,
        size_bytes=backup.size_bytes,
        sha256=backup.sha256,
        downloaded_at=backup.downloaded_at,
        source_git_branch="staging",
        source_name="staging",
    )
    catalog = MagicMock()
    catalog.latest_restore.return_value = backup
    client = MagicMock()
    client.get_catalog.return_value = catalog
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setattr(
        preparation,
        "_wait_for_preparation_lock",
        lambda *_args, **_kwargs: contextlib.nullcontext(),
    )
    monkeypatch.setenv("ODCLI_REMOTE_STAGING_MASTER_PASSWORD", "staging-secret")

    result = preparation.prepare_restore(
        client,
        project,
        options=DatabaseRefreshOptions(restore=True, remote_name="staging"),
        coalesce=True,
    )

    assert result.default_switched is False
    assert result.effective_default == "old"
    assert result.backup == backup
    client.instance.assert_not_called()


def test_named_source_isolation_covers_plan_result_and_error_projections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = RemoteSourceConfig(
        name="staging",
        base_url="https://staging.example",
        database="staging_db",
        git_branch="staging",
    )
    project = _project(source, root=tmp_path)
    client = MagicMock()
    backup = _backup(tmp_path, source_name="staging", branch="staging")
    client.instance.return_value.databases.backup.return_value = backup
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setattr(preparation, "exclusive_lock", lambda _: contextlib.nullcontext())
    monkeypatch.setenv("ODCLI_REMOTE_STAGING_MASTER_PASSWORD", "staging-secret")
    monkeypatch.setenv("ODCLI_REMOTE_LAB_MASTER_PASSWORD", "lab-secret")

    command = preparation.DatabasePreparationCoordinator(client).prepare_command(
        project, options=DatabaseRefreshOptions(remote_name="staging")
    )
    assert "staging-secret" not in repr(command.plan)
    assert "lab-secret" not in repr(command.plan)

    result = preparation.prepare_download(
        client, project, options=DatabaseRefreshOptions(remote_name="staging")
    )
    assert "staging-secret" not in repr(result)
    assert "lab-secret" not in repr(result)

    client.instance.return_value.databases.backup.side_effect = RuntimeError("transport failed")
    with pytest.raises(RuntimeError) as raised:
        preparation.prepare_download(
            client, project, options=DatabaseRefreshOptions(remote_name="staging")
        )
    assert "staging-secret" not in repr(raised.value)
    assert "lab-secret" not in repr(raised.value)


def _doctor_project(tmp_path: Path) -> Path:
    source_config = tmp_path / "odoo.conf"
    source_config.write_text("[options]\nadmin_passwd = local-secret\n")
    project = _project(
        RemoteSourceConfig(
            name="staging",
            base_url="https://staging.example",
            database="staging_db",
            git_branch="staging",
        ),
        root=tmp_path,
    )
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text(project.to_manifest())
    env_file = manifest_dir / ".env"
    env_file.write_text("ODCLI_REMOTE_STAGING_MASTER_PASSWORD=staging-secret\n")
    env_file.chmod(0o600)
    return tmp_path


def _run_offline_doctor(
    root: Path, monkeypatch: pytest.MonkeyPatch, *, ref_available: bool
) -> tuple[DoctorReport, MagicMock]:
    for name in (
        "_check_manifest",
        "_check_project_runtime",
        "_check_uv",
        "_check_optional_executables",
        "_check_catalog",
        "_check_orphaned",
        "_check_postgres",
    ):
        monkeypatch.setattr(doctor_manifest, name, lambda *_args, **_kwargs: None)
    if ref_available:
        monkeypatch.setattr(doctor_manifest, "rev_parse_verify", lambda *_args: "local-sha")
    else:
        monkeypatch.setattr(
            doctor_manifest,
            "rev_parse_verify",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("missing")),
        )
    client = MagicMock()
    client.environments.list.return_value = []
    return doctor_manifest.run_doctor(client, root, remote_name="staging"), client


def test_named_doctor_is_offline_and_reports_success_without_authentication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _doctor_project(tmp_path)
    report, client = _run_offline_doctor(root, monkeypatch, ref_available=True)

    check = next(item for item in report.checks if item.name == "remote.source")
    assert check.status == "ok"
    assert check.facts["password_present"] is True
    assert check.facts["local_ref_available"] is True
    assert check.facts["authentication"] == "unverified"
    client.instance.assert_not_called()
    client.get_catalog.assert_not_called()


def test_named_doctor_reports_missing_secret_and_ref_without_remote_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _doctor_project(tmp_path)
    (root / ".odcli" / ".env").write_text("")
    (root / ".odcli" / ".env").chmod(0o600)
    report, client = _run_offline_doctor(root, monkeypatch, ref_available=False)

    check = next(item for item in report.checks if item.name == "remote.source")
    assert check.status == "warn"
    assert "ODCLI_REMOTE_STAGING_MASTER_PASSWORD" in check.detail
    assert "staging" in check.detail
    assert check.facts["local_ref_available"] is False
    assert check.facts["authentication"] == "unverified"
    client.instance.assert_not_called()
    client.get_catalog.assert_not_called()


def test_named_doctor_reports_ref_only_failure_with_other_prerequisites_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _doctor_project(tmp_path)
    report, client = _run_offline_doctor(root, monkeypatch, ref_available=False)

    check = next(item for item in report.checks if item.name == "remote.source")
    assert check.status == "warn"
    assert check.facts["password_present"] is True
    assert check.facts["local_restore_prerequisites"] is True
    assert check.facts["local_ref_available"] is False
    assert "make declared local ref 'staging' available" in check.detail
    assert check.facts["authentication"] == "unverified"
    client.instance.assert_not_called()
    client.get_catalog.assert_not_called()
