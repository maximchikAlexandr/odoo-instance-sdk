from __future__ import annotations

import contextlib
import hashlib
import io
import os
import shutil
import subprocess
import uuid
import warnings
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace, TracebackType
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import pytest

from odoo_instance_sdk.exceptions import (
    ConfigError,
    InstanceConfigurationError,
    LockConflictError,
    MasterPasswordRequiredError,
    OmittedStepError,
    UnplannedStepError,
)
from odoo_instance_sdk.execution import Command
from odoo_instance_sdk.internal.proc import PreparedStep, RecordingExecutor
from odoo_instance_sdk.models import (
    Backup,
    BackupBranchOrigin,
    BackupFormat,
    BackupFreshness,
    BackupProvenanceStatus,
    DatabasePreparationResult,
    DatabaseRefreshOptions,
)
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig
from odoo_instance_sdk.project import TestInstanceProjectConfig as ConfigTestInstance

if TYPE_CHECKING:
    from odoo_instance_sdk.resources.instance import OdooInstance


@pytest.fixture(autouse=True)
def _approved_test_instance_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ODCLI_TEST_INSTANCE_ORIGIN_PINS", "https://example.test:443")


def _project(tmp_path: Path, *, branch: str | None = "develop") -> ProjectConfig:
    return ProjectConfig(
        repository_root=tmp_path,
        test_instance=ConfigTestInstance(
            base_url="https://example.test",
            database="remote_test",
            git_branch=branch,
        ),
        default_source_database="source",
    )


def _backup(tmp_path: Path, *, downloaded_at: datetime) -> Backup:
    path = tmp_path / "backup.zip"
    path.write_bytes(b"backup")
    return Backup(
        id=uuid.uuid4(),
        source_base_url="https://example.test",
        database_name="remote_test",
        format=BackupFormat.ZIP,
        filestore_requested=True,
        path=str(path),
        filename=path.name,
        size_bytes=6,
        sha256="a" * 64,
        downloaded_at=downloaded_at,
        source_git_branch="develop",
    )


def test_runtime_binding_accepts_non_executable_odoo_bin(tmp_path: Path) -> None:
    from odoo_instance_sdk.internal.database_preparation import resolve_runtime_binding

    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    odoo_bin = tmp_path / "odoo-bin"
    odoo_bin.write_text("from odoo.cli import main\nmain()\n")
    project = ProjectConfig(repository_root=tmp_path, python=python, odoo_bin=odoo_bin)

    binding = resolve_runtime_binding(project, tmp_path)

    assert binding.odoo_bin == str(odoo_bin)


def test_preparation_command_captures_restore_process_manifest_before_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore planning exposes Git, compose, and psql children in one snapshot."""
    from odoo_instance_sdk.internal.database_preparation import DatabasePreparationCoordinator

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which",
        lambda _name: "/usr/bin/psql",
    )
    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\n"
        "http_interface = 127.0.0.1\n"
        "http_port = 8069\n"
        "db_host = 127.0.0.1\n"
        "db_port = 5432\n"
        "db_user = odoo\n"
        "db_password = private\n"
    )
    project = ProjectConfig(
        repository_root=tmp_path,
        source_config=source,
        postgres=PostgresProjectConfig(
            mode="compose", image="postgres:16", port=55432, user="odoo"
        ),
        test_instance=ConfigTestInstance(base_url="https://example.test", database="remote"),
    )

    command = DatabasePreparationCoordinator(MagicMock()).prepare_command(
        project,
        options=DatabaseRefreshOptions(restore=True),
    )

    process_ids = tuple(step.step_id for step in command.plan.process_steps)
    assert process_ids[:2] == (
        "database.prepare.git.toplevel",
        "database.prepare.git.common-dir",
    )
    assert process_ids[2:10] == (
        "postgres.ensure.image.pull",
        "postgres.ensure.image.inspect",
        "postgres.ensure.status.ps",
        "postgres.ensure.status.health",
        "postgres.ensure.config",
        "postgres.ensure.up",
        "postgres.ensure.final.ps",
        "postgres.ensure.final.health",
    )
    assert process_ids[-3:] == (
        "database.restore.exists-reservation",
        "database.restore.exists-before",
        "database.restore.exists-after",
    )


def test_selected_native_dump_uses_pg_restore_process_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from odoo_instance_sdk.internal.backup_validation import DumpValidationResult
    from odoo_instance_sdk.internal.database_preparation import (
        build_selected_backup_restore_steps,
        capture_selected_backup_restore,
    )

    dump_path = tmp_path / "production.dump"
    dump_path.write_bytes(b"PGDMP\x01production-format")
    backup = Backup(
        id=uuid.uuid4(),
        source_base_url="https://example.test",
        database_name="remote_test",
        format=BackupFormat.DUMP,
        filestore_requested=False,
        path=str(dump_path),
        filename=dump_path.name,
        size_bytes=dump_path.stat().st_size,
        sha256=hashlib.sha256(dump_path.read_bytes()).hexdigest(),
        downloaded_at=datetime.now(UTC),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.backup_validation.validate_dump",
        lambda *_args, **_kwargs: DumpValidationResult(valid=True),
        raising=False,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.database_preparation.shutil.which",
        lambda name: "/usr/bin/pg_restore" if name == "pg_restore" else "/usr/bin/psql",
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which",
        lambda name: "/usr/bin/pg_restore" if name == "pg_restore" else "/usr/bin/psql",
    )
    payload = capture_selected_backup_restore(backup)
    instance = SimpleNamespace(
        _postgres_cluster=SimpleNamespace(
            endpoint_host="127.0.0.1", endpoint_port=5432, _user="odoo"
        ),
        config=SimpleNamespace(db_user="odoo", db_password="secret", default_cwd=tmp_path),
    )

    create, validate, restore = build_selected_backup_restore_steps(
        cast("OdooInstance", instance),
        target_database="copy_target",
        dump_path=payload.dump_path,
        backup_format=payload.format,
    )

    assert create.step_id == "database.replace.restore.create"
    assert validate.step_id == "database.replace.restore.validate"
    assert restore.step_id == "database.replace.restore.pg-restore"
    assert restore.argv[0] == "/usr/bin/pg_restore"
    assert restore.argv[1:3] == ("--exit-on-error", "--single-transaction")


def test_selected_native_dump_uses_verified_snapshot_after_source_path_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation
    from odoo_instance_sdk.internal.backup_validation import DumpValidationResult

    dump_path = tmp_path / "production.dump"
    replacement_path = tmp_path / "replacement.dump"
    original = b"PGDMP\x01verified-native"
    dump_path.write_bytes(original)
    replacement_path.write_bytes(b"PGDMP\x01substituted-native")
    backup = Backup(
        id=uuid.uuid4(),
        source_base_url="https://example.test",
        database_name="remote_test",
        format=BackupFormat.DUMP,
        filestore_requested=False,
        path=str(dump_path),
        filename=dump_path.name,
        size_bytes=len(original),
        sha256=hashlib.sha256(original).hexdigest(),
        downloaded_at=datetime.now(UTC),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.backup_validation.validate_dump",
        lambda *_args, **_kwargs: DumpValidationResult(valid=True),
        raising=False,
    )
    payload = database_preparation.capture_selected_backup_restore(backup)
    real_open = os.open
    swapped = False

    def open_source_once(path: str | os.PathLike[str], flags: int, *args: int) -> int:
        nonlocal swapped
        descriptor = real_open(path, flags, *args)
        if Path(path) == dump_path and not swapped:
            swapped = True
            dump_path.unlink()
            replacement_path.rename(dump_path)
        return descriptor

    monkeypatch.setattr(os, "open", open_source_once)
    database_preparation.materialize_selected_backup_dump(payload)

    assert payload.dump_path.read_bytes() == original


@pytest.mark.skipif(shutil.which("pg_restore") is None, reason="PostgreSQL client is required")
def test_native_pg_restore_rejects_odoo_plain_sql_fixture(tmp_path: Path) -> None:
    """The native archive transport must never be used for an Odoo SQL dump."""
    plain_sql = tmp_path / "dump.sql"
    plain_sql.write_text("CREATE TABLE replacement_probe (id integer);\n")

    result = subprocess.run(
        [str(shutil.which("pg_restore")), "--list", str(plain_sql)],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )

    assert result.returncode != 0


@pytest.mark.skipif(shutil.which("psql") is None, reason="PostgreSQL client is required")
def test_selected_odoo_zip_executes_real_psql_plain_sql_transport(tmp_path: Path) -> None:
    """A supported Odoo ZIP selects real psql with bounded SQL-file input."""
    from odoo_instance_sdk.internal.database_preparation import (
        _preparation_process_steps,
        capture_selected_backup_restore,
        materialize_selected_backup_dump,
    )

    archive_path = tmp_path / "odoo.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", '{"db_name": "remote_test", "db_version": "19.0"}')
        archive.writestr("dump.sql", "CREATE TABLE replacement_probe (id integer);\n")
        archive.writestr("filestore/remote_test/marker", "fixture")
    backup = Backup(
        id=uuid.uuid4(),
        source_base_url="https://example.test",
        database_name="remote_test",
        format=BackupFormat.ZIP,
        filestore_requested=True,
        path=str(archive_path),
        filename=archive_path.name,
        size_bytes=archive_path.stat().st_size,
        sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        downloaded_at=datetime.now(UTC),
    )
    payload = capture_selected_backup_restore(backup)
    materialize_selected_backup_dump(payload)
    instance = SimpleNamespace(
        _postgres_cluster=SimpleNamespace(endpoint_host="127.0.0.1", endpoint_port=1, _user="odoo"),
        config=SimpleNamespace(db_user="odoo", db_password=None, default_cwd=tmp_path),
    )
    _, validate, restore = _preparation_process_steps(
        tmp_path,
        options=DatabaseRefreshOptions(restore=True),
        restore_inputs=("copy_target", payload.dump_path),
        selected_environment=MagicMock(repository_root=tmp_path),
        selected_instance=cast("OdooInstance", instance),
        selected_restore=payload,
    )

    assert validate.step_id == "database.replace.restore.validate"
    assert restore.step_id == "database.replace.restore.psql"
    restore = cast("PreparedStep", restore)
    assert restore.argv[0] == str(shutil.which("psql"))
    assert "ON_ERROR_STOP=1" in restore.argv
    assert restore.argv[-2:] == ("--file", str(payload.dump_path))
    result = subprocess.run(
        list(restore.argv),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode != 0


def test_validate_zip_rejects_duplicate_members_and_policy_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import backup_validation

    archive_path = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", '{"db_name":"remote_test"}')
        archive.writestr("dump.sql", "select 1;\n")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            archive.writestr("dump.sql", "select 2;\n")

    duplicate = backup_validation.validate_zip(archive_path)
    assert not duplicate.valid
    assert any("Duplicate ZIP member" in error for error in duplicate.errors)

    monkeypatch.setattr(backup_validation, "_MAX_ZIP_ENTRY_BYTES", 1)
    limited = backup_validation.validate_zip(archive_path)
    assert not limited.valid
    assert any("too large" in error for error in limited.errors)


def test_validate_zip_counts_duplicate_central_directory_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import backup_validation

    archive_path = tmp_path / "member-count.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", '{"db_name":"remote_test"}')
        archive.writestr("dump.sql", "select 1;\n")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            archive.writestr("dump.sql", "select 2;\n")
    monkeypatch.setattr(backup_validation, "_MAX_ZIP_ENTRIES", 2)

    result = backup_validation.validate_zip(archive_path)

    assert not result.valid
    assert "ZIP contains too many members" in result.errors
    assert not any("Duplicate ZIP member" in error for error in result.errors)


def test_validate_zip_bounds_policy_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import backup_validation

    archive_path = tmp_path / "diagnostics.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", '{"db_name":"remote_test"}')
        archive.writestr("dump.sql", "select 1;\n")
        for index in range(40):
            archive.writestr(f"member-{index}", b"payload")
    monkeypatch.setattr(backup_validation, "_SUPPORTED_ZIP_COMPRESSION", set())

    result = backup_validation.validate_zip(archive_path)

    assert not result.valid
    assert len(result.errors) == backup_validation._MAX_ZIP_DIAGNOSTICS


def test_validate_zip_rejects_aggregate_uncompressed_size_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import backup_validation

    archive_path = tmp_path / "aggregate-size.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", '{"db_name":"remote_test"}')
        archive.writestr("dump.sql", "select 1;\n")
    monkeypatch.setattr(backup_validation, "_MAX_ZIP_TOTAL_BYTES", 1)

    result = backup_validation.validate_zip(archive_path)

    assert not result.valid
    assert any("uncompressed size is too large" in error for error in result.errors)
    assert not any("too many members" in error for error in result.errors)


def test_validate_zip_rejects_compression_ratio_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import backup_validation

    archive_path = tmp_path / "compression-ratio.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", '{"db_name":"remote_test"}')
        archive.writestr("dump.sql", "x" * 4096)
    monkeypatch.setattr(backup_validation, "_MAX_ZIP_COMPRESSION_RATIO", 1)

    result = backup_validation.validate_zip(archive_path)

    assert not result.valid
    assert any("compression ratio is unsafe" in error for error in result.errors)
    assert not any("uncompressed size is too large" in error for error in result.errors)


def test_validate_zip_rejects_oversized_manifest_before_json_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import backup_validation

    archive_path = tmp_path / "manifest-limit.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", '{"db_name":"' + ("x" * 128) + '"}')
        archive.writestr("dump.sql", "select 1;\n")
    monkeypatch.setattr(backup_validation, "_MAX_MANIFEST_BYTES", 32, raising=False)
    monkeypatch.setattr(
        zipfile.ZipFile,
        "read",
        lambda *_args, **_kwargs: pytest.fail("manifest must be streamed"),
    )

    result = backup_validation.validate_zip(archive_path)

    assert not result.valid
    assert any("manifest.json is too large" in error for error in result.errors)


@pytest.mark.parametrize("attribute", ["flag_bits", "compress_type"])
def test_validate_zip_rejects_encrypted_or_unsupported_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attribute: str
) -> None:
    from copy import copy

    from odoo_instance_sdk.internal import backup_validation

    archive_path = tmp_path / "policy.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", '{"db_name":"remote_test"}')
        archive.writestr("dump.sql", "select 1;\n")

    original_infolist = zipfile.ZipFile.infolist

    def modified_infolist(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
        infos = [copy(info) for info in original_infolist(archive)]
        if attribute == "flag_bits":
            infos[-1].flag_bits |= 0x1
        else:
            infos[-1].compress_type = 99
        return infos

    monkeypatch.setattr(zipfile.ZipFile, "infolist", modified_infolist)
    result = backup_validation.validate_zip(archive_path)
    assert not result.valid
    expected = "Encrypted ZIP member" if attribute == "flag_bits" else "Unsupported ZIP compression"
    assert any(expected in error for error in result.errors)


def test_validate_zip_rejects_insufficient_available_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from odoo_instance_sdk.internal import backup_validation

    archive_path = tmp_path / "space.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", '{"db_name":"remote_test"}')
        archive.writestr("dump.sql", "select 1;\n")
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=0),
    )
    result = backup_validation.validate_zip(archive_path)
    assert not result.valid
    assert any("ZIP requires more space" in error for error in result.errors)


def test_selected_dump_stream_counter_cleans_up_lying_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation

    archive_path = tmp_path / "lying.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", '{"db_name":"remote_test"}')
        archive.writestr("dump.sql", "select 1;\n")
        archive.writestr("filestore/remote_test/marker", b"fixture")
    backup = Backup(
        id=uuid.uuid4(),
        source_base_url="https://example.test",
        database_name="remote_test",
        format=BackupFormat.ZIP,
        filestore_requested=True,
        path=str(archive_path),
        filename=archive_path.name,
        size_bytes=archive_path.stat().st_size,
        sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        downloaded_at=datetime.now(UTC),
    )
    payload = database_preparation.capture_selected_backup_restore(backup)
    real_open = database_preparation._open_verified_zip

    class LyingZip:
        def __enter__(self) -> LyingZip:
            self.archive = real_open(archive_path)
            self.archive.__enter__()
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            self.archive.__exit__(exc_type, exc_value, traceback)

        def open(self, _name: str) -> io.BytesIO:
            return io.BytesIO(b"select 1;\nextra bytes")

    monkeypatch.setattr(database_preparation, "_open_verified_zip", lambda _path: LyingZip())
    with pytest.raises(ConfigError, match=r"exceeds|size changed"):
        database_preparation.materialize_selected_backup_dump(payload)
    assert not payload.dump_path.exists()


def test_selected_filestore_stream_counter_removes_partial_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation

    archive_path = tmp_path / "lying-filestore.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", '{"db_name":"remote_test"}')
        archive.writestr("dump.sql", "select 1;\n")
        archive.writestr("filestore/remote_test/marker", b"fixture")
    backup = Backup(
        id=uuid.uuid4(),
        source_base_url="https://example.test",
        database_name="remote_test",
        format=BackupFormat.ZIP,
        filestore_requested=True,
        path=str(archive_path),
        filename=archive_path.name,
        size_bytes=archive_path.stat().st_size,
        sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        downloaded_at=datetime.now(UTC),
    )
    payload = database_preparation.capture_selected_backup_restore(backup)
    real_open = database_preparation._open_verified_zip

    class LyingZip:
        def __enter__(self) -> LyingZip:
            self.archive = real_open(payload.verified_snapshot_path)
            self.archive.__enter__()
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            self.archive.__exit__(exc_type, exc_value, traceback)

        def namelist(self) -> list[str]:
            return self.archive.namelist()

        def getinfo(self, name: str) -> zipfile.ZipInfo:
            return self.archive.getinfo(name)

        def open(self, _info: zipfile.ZipInfo) -> io.BytesIO:
            return io.BytesIO(b"fixture plus unexpected bytes")

    monkeypatch.setattr(database_preparation, "_open_verified_zip", lambda _path: LyingZip())
    destination = tmp_path / "filestore"

    with pytest.raises(ConfigError, match=r"exceeds|size changed"):
        database_preparation.materialize_selected_backup_filestore(destination, payload)

    assert not destination.exists()


def test_selected_restore_consumes_verified_snapshot_after_source_path_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path replacement after the final source open cannot alter restore bytes."""
    from odoo_instance_sdk.internal import database_preparation

    archive_path = tmp_path / "snapshot.zip"
    replacement_path = tmp_path / "replacement.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", '{"db_name":"remote_test"}')
        archive.writestr("dump.sql", "select 'original';\n")
        archive.writestr("filestore/remote_test/marker", b"original")
    with zipfile.ZipFile(replacement_path, "w") as archive:
        archive.writestr("manifest.json", '{"db_name":"remote_test"}')
        archive.writestr("dump.sql", "select 'substituted';\n")
        archive.writestr("filestore/remote_test/marker", b"substituted")
    backup = Backup(
        id=uuid.uuid4(),
        source_base_url="https://example.test",
        database_name="remote_test",
        format=BackupFormat.ZIP,
        filestore_requested=True,
        path=str(archive_path),
        filename=archive_path.name,
        size_bytes=archive_path.stat().st_size,
        sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        downloaded_at=datetime.now(UTC),
    )
    payload = database_preparation.capture_selected_backup_restore(backup)
    real_open = os.open
    swapped = False

    def open_source_once(path: str | os.PathLike[str], flags: int, *args: int) -> int:
        nonlocal swapped
        descriptor = real_open(path, flags, *args)
        if Path(path) == archive_path and not swapped:
            swapped = True
            archive_path.unlink()
            replacement_path.rename(archive_path)
        return descriptor

    monkeypatch.setattr(os, "open", open_source_once)
    database_preparation.materialize_selected_backup_dump(payload)
    filestore = tmp_path / "filestore"
    database_preparation.materialize_selected_backup_filestore(filestore, payload)

    assert payload.dump_path.read_text() == "select 'original';\n"
    assert (filestore / "marker").read_bytes() == b"original"


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required")
def test_restore_uv_selector_is_not_resolved_while_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.database_preparation import DatabasePreparationCoordinator

    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\nhttp_interface = 127.0.0.1\nhttp_port = 8069\nadmin_passwd = local-secret\n"
    )
    odoo = tmp_path / "odoo-bin"
    odoo.write_text("#!/bin/sh\nexit 0\n")
    odoo.chmod(0o755)
    project = ProjectConfig(
        repository_root=tmp_path,
        python="3.12",
        odoo_bin=odoo,
        source_config=source,
        default_source_database="source",
        test_instance=ConfigTestInstance(base_url="https://example.test", database="remote"),
    )
    uv_resolution = MagicMock(side_effect=AssertionError("uv must run at execution time"))
    monkeypatch.setattr("odoo_instance_sdk.internal.server.run_command", uv_resolution)

    command = DatabasePreparationCoordinator(MagicMock()).refresh_database_command(
        project,
        options=DatabaseRefreshOptions(restore=True, reset_admin_password=True),
    )

    uv_resolution.assert_not_called()
    uv_path = shutil.which("uv")
    assert uv_path is not None
    shell_step = next(
        step for step in command.plan.process_steps if step.step_id == "instance.shell_script"
    )
    assert shell_step.argv[:8] == (
        str(Path(uv_path).absolute()),
        "run",
        "--no-project",
        "--python",
        "3.12",
        "--",
        "python",
        str(odoo),
    )


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required")
def test_restore_uv_selector_executes_the_captured_shell_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command, executor, _project, _backup, _write = _production_restore_command(
        tmp_path,
        monkeypatch,
        python_value="3.12",
    )

    command.run()

    shell_step = next(
        step for step in command.plan.process_steps if step.step_id == "instance.shell_script"
    )
    from odoo_instance_sdk.internal.proc.redaction import project_process_step

    assert project_process_step(executor.executed[-1]) == shell_step
    assert executor.executed[-1].argv[:8] == shell_step.argv[:8]
    assert tuple(step.step_id for step in executor.executed) == tuple(
        step.step_id for step in command.plan.process_steps
    )


def test_preparation_command_runs_captured_git_steps_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real preparation command consumes the exact inspected process inputs."""
    from odoo_instance_sdk.internal.database_preparation import DatabasePreparationCoordinator
    from odoo_instance_sdk.internal.proc import PreparedProcess, ProcessResult, RecordingExecutor

    backup = _backup(tmp_path, downloaded_at=datetime.now(UTC))
    remote = MagicMock()
    remote.databases.backup.return_value = backup
    client = MagicMock()
    client.instance.return_value = remote

    def result_for(prepared: PreparedProcess) -> ProcessResult:
        argv = prepared.argv
        return ProcessResult(
            argv=argv,
            returncode=0,
            stdout=str(tmp_path),
            stderr="",
            duration=0.0,
            cwd=None,
            environment=(),
        )

    executor = RecordingExecutor(result_factory=result_for)
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")
    command = DatabasePreparationCoordinator(client).prepare_command(
        _project(tmp_path), executor=executor
    )

    result = command.run()

    assert result.backup == backup
    planned = command.plan.process_steps
    assert tuple(step.step_id for step in executor.executed) == tuple(
        step.step_id for step in planned
    )
    assert len(executor.executed) == len({step.step_id for step in executor.executed})


def _production_restore_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    odoo_returncode: int = 0,
    python_value: str | Path | None = None,
) -> tuple[
    Command[DatabasePreparationResult],
    RecordingExecutor,
    ProjectConfig,
    Backup,
    MagicMock,
]:
    """Build the public preparation command with real active adapters."""
    import shutil

    from odoo_instance_sdk.internal import database_preparation as preparation
    from odoo_instance_sdk.internal.database_preparation import (
        RestorePreflight,
        resolve_test_source,
    )
    from odoo_instance_sdk.internal.proc import (
        PreparedProcess,
        PreparedStep,
        ProcessResult,
        RecordingExecutor,
        active_context,
    )
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    real_which = shutil.which
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which",
        lambda name: "/usr/bin/psql" if name == "psql" else real_which(name),
    )
    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\n"
        "http_interface = 127.0.0.1\n"
        "http_port = 8069\n"
        "db_name = source\n"
        "admin_passwd = local-secret\n"
        "db_host = 127.0.0.1\n"
        "db_port = 5432\n"
        "db_user = odoo\n"
        "db_password = db-secret\n"
    )
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    odoo = tmp_path / "odoo-bin"
    odoo.write_text("#!/bin/sh\n")
    odoo.chmod(0o755)
    project = ProjectConfig(
        repository_root=tmp_path,
        python=python if python_value is None else python_value,
        odoo_bin=odoo,
        source_config=source,
        postgres=PostgresProjectConfig(
            mode="compose", image="postgres:16", port=55432, user="odoo"
        ),
        default_source_database="old",
        test_instance=ConfigTestInstance(base_url="https://example.test", database="remote"),
    )
    backup = _backup(tmp_path, downloaded_at=datetime.now(UTC))
    remote = MagicMock()
    remote.databases.backup.return_value = backup
    client = MagicMock()
    client.instance.return_value = remote
    local = MagicMock()
    local.databases.restore.side_effect = _consume_restore_probes
    cluster = PostgresCluster._from_config(
        project,
        repository_root=tmp_path,
        compose_runner=None,
        project_id="<runtime>",
    )
    options = DatabaseRefreshOptions(restore=True, reset_admin_password=True)
    source_resolution = resolve_test_source(project, options)
    runtime = preparation.resolve_runtime_binding(project, tmp_path)

    @contextlib.contextmanager
    def fake_preflight(
        _client: object,
        _project: object,
        *,
        options: DatabaseRefreshOptions,
        wait_for_lock: bool = True,
        coalesce: bool = False,
        target_database: str | None = None,
    ) -> Iterator[RestorePreflight]:
        del _client, _project, wait_for_lock, coalesce
        context = active_context()
        assert context is not None
        context.action("database.prepare.lock")
        context.process("database.prepare.git.toplevel")
        context.process("database.prepare.git.common-dir")
        for step in cluster._ensure_running_steps(60.0):
            if isinstance(step, PreparedStep):
                context.process(step.step_id)
        yield RestorePreflight(
            project=project,
            project_id="<runtime>",
            source=source_resolution,
            source_config=source,
            local_instance=local,
            runtime=runtime,
            postgres_cluster=cluster,
            target_database=target_database or "remote_refresh_test",
        )

    def result_for(prepared: PreparedProcess) -> ProcessResult:
        prepared = cast("PreparedStep", prepared)
        return ProcessResult(
            argv=prepared.argv,
            returncode=odoo_returncode if prepared.step_id == "instance.shell_script" else 0,
            stdout="",
            stderr="odoo failed" if prepared.step_id == "instance.shell_script" else "",
            duration=0.0,
            cwd=prepared.cwd,
            environment=prepared.environment,
        )

    write = MagicMock()
    executor = RecordingExecutor(result_factory=result_for)
    monkeypatch.setattr(preparation, "_restore_preflight", fake_preflight)
    monkeypatch.setattr(ProjectConfig, "load", MagicMock(return_value=project))
    monkeypatch.setattr("odoo_instance_sdk.internal.project_manifest.write_manifest", write)
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")
    command = preparation.DatabasePreparationCoordinator(client).prepare_command(
        project, options=options, executor=executor
    )
    return command, executor, project, backup, write


def _consume_restore_probes(*_args: object, **_kwargs: object) -> MagicMock:
    from odoo_instance_sdk.internal.proc import active_context

    context = active_context()
    assert context is not None
    context.process("database.restore.exists-reservation")
    context.process("database.restore.exists-before")
    context.process("database.restore.exists-after")
    return MagicMock()


def test_production_restore_command_consumes_compose_psql_and_odoo_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command, executor, _project_config, backup, write = _production_restore_command(
        tmp_path, monkeypatch
    )

    result = command.run()

    assert result.backup == backup
    planned = command.plan.process_steps
    assert tuple(step.step_id for step in executor.executed) == tuple(
        step.step_id for step in planned
    )
    from odoo_instance_sdk.internal.proc.redaction import project_process_step

    recorded_shell = project_process_step(executor.executed[-1])
    planned_shell = planned[-1]
    assert recorded_shell.input_preview == planned_shell.input_preview
    assert recorded_shell.cwd == planned_shell.cwd
    assert recorded_shell.environment_policy == planned_shell.environment_policy
    assert recorded_shell.environment_overrides == planned_shell.environment_overrides
    assert recorded_shell.timeout == planned_shell.timeout
    assert recorded_shell.mutating == planned_shell.mutating
    assert executor.executed[-1].wrapper_nonce is not None
    assert executor.executed[-1].wrapper_nonce.encode() in (executor.executed[-1].stdin or b"")
    assert len(executor.executed) == len({step.step_id for step in executor.executed})
    assert write.called


def test_production_restore_reset_uses_python_selector_from_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    resolved_python = shutil.which("python3")
    if resolved_python is None:
        pytest.skip("python3 is required")

    command, executor, _project_config, _backup_value, _write = _production_restore_command(
        tmp_path,
        monkeypatch,
        python_value="python3",
    )

    command.run()

    assert executor.executed[-1].argv[0] == str(Path(resolved_python).absolute())


def test_production_restore_command_rolls_back_after_odoo_child_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command, executor, project, backup, write = _production_restore_command(
        tmp_path, monkeypatch, odoo_returncode=1
    )

    with pytest.raises(Exception, match="Administrator password reset failed") as failure:
        command.run()

    assert getattr(failure.value, "failure_context").retained_backup_id == backup.id
    target_step = next(
        step
        for step in command.plan.process_steps
        if step.step_id == "database.restore.exists-before"
    )
    assert getattr(failure.value, "failure_context").retained_database in target_step.argv[-1]
    assert project.default_source_database == "old"
    write.assert_not_called()
    assert tuple(step.step_id for step in executor.executed) == tuple(
        step.step_id for step in command.plan.process_steps
    )


def test_preparation_command_rejects_omitted_captured_child() -> None:
    """A successful callback cannot blanket-skip a required prepared child."""
    from odoo_instance_sdk.internal.database_preparation import DatabasePreparationCoordinator
    from odoo_instance_sdk.internal.proc import PreparedStep, RecordingExecutor

    child = PreparedStep(step_id="database.prepare.restore", argv=("restore",))
    command = DatabasePreparationCoordinator(MagicMock())._action_command(
        "database.prepare",
        "Prepare a database",
        lambda: "done",
        executor=RecordingExecutor(),
        steps=(child,),
    )

    with pytest.raises(OmittedStepError, match=r"database\.prepare\.restore"):
        command.run()


def test_preparation_command_rejects_substituted_child_before_launch() -> None:
    """A callback cannot replace an inspected child with an unplanned argv."""
    from odoo_instance_sdk.internal.database_preparation import DatabasePreparationCoordinator
    from odoo_instance_sdk.internal.proc import PreparedStep, RecordingExecutor, active_context

    executor = RecordingExecutor()
    planned = PreparedStep(step_id="database.prepare.restore", argv=("restore",))

    def substitute() -> str:
        context = active_context()
        assert context is not None
        context.process_prepared(PreparedStep(step_id="substituted", argv=("other",)))
        return "never"

    command = DatabasePreparationCoordinator(MagicMock())._action_command(
        "database.prepare",
        "Prepare a database",
        substitute,
        executor=executor,
        steps=(planned,),
    )

    with pytest.raises(UnplannedStepError, match="substituted"):
        command.run()
    assert executor.executed == []


def test_preparation_command_failure_consumes_restore_and_rollback_steps() -> None:
    """A restore failure records compensation without replaying its child."""
    from odoo_instance_sdk.internal.database_preparation import DatabasePreparationCoordinator
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        ProcessResult,
        RecordingExecutor,
        active_context,
    )

    restore = PreparedStep(step_id="database.prepare.restore", argv=("restore",))
    rollback = PreparedAction(
        step_id="database.prepare.rollback",
        action="compensate-preparation-failure",
        read_only=True,
    )
    executor = RecordingExecutor(
        default_result=ProcessResult(
            argv=restore.argv,
            returncode=1,
            stdout="",
            stderr="restore failed",
            duration=0.0,
            cwd=None,
            environment=(),
        )
    )

    def fail_restore() -> str:
        context = active_context()
        assert context is not None
        result = context.process(restore.step_id)
        assert isinstance(result, ProcessResult)
        context.action(rollback.step_id)
        raise RuntimeError("restore failed")

    command = DatabasePreparationCoordinator(MagicMock())._action_command(
        "database.prepare",
        "Prepare a database",
        fail_restore,
        executor=executor,
        steps=(restore, rollback),
        optional_steps=(rollback.step_id,),
    )

    with pytest.raises(RuntimeError, match="restore failed"):
        command.run()
    assert [step.step_id for step in executor.executed] == [restore.step_id]


def test_lock_paths_are_project_and_target_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_locks_dir", lambda: tmp_path)
    from odoo_instance_sdk.internal.locks import (
        database_preparation_artifact_lock_path,
        database_preparation_lock_path,
    )

    project_lock = database_preparation_lock_path("repo_123")
    target_lock = database_preparation_artifact_lock_path("repo_123", "db_refresh")
    assert project_lock == tmp_path / "database-preparation-repo_123.lock"
    assert target_lock == tmp_path / "database-preparation-repo_123-db_refresh.lock"
    with pytest.raises(ConfigError):
        database_preparation_artifact_lock_path("repo_123", "bad/name")


def test_cli_adapters_do_not_own_preparation_locks() -> None:
    commands = Path(__file__).parents[3] / "src" / "odoo_instance_sdk" / "commands"
    for source in commands.glob("*.py"):
        text = source.read_text()
        assert "database_preparation_lock_path" not in text
        assert "database_preparation_artifact_lock_path" not in text


@pytest.mark.parametrize(
    ("explicit", "configured", "expected", "origin"),
    [
        (" release/19 ", "develop", "release/19", BackupBranchOrigin.EXPLICIT),
        (None, " develop ", "develop", BackupBranchOrigin.CONFIGURED),
        (None, None, None, BackupBranchOrigin.UNKNOWN),
    ],
)
def test_resolve_test_source_branch_precedence(
    tmp_path: Path,
    explicit: str | None,
    configured: str | None,
    expected: str | None,
    origin: BackupBranchOrigin,
) -> None:
    from odoo_instance_sdk.internal.database_preparation import resolve_test_source

    project = _project(tmp_path, branch=configured)
    source = resolve_test_source(project, DatabaseRefreshOptions(source_branch=explicit))
    assert source.branch == expected
    assert source.origin is origin


@pytest.mark.parametrize(
    ("expected", "recorded", "status"),
    [
        ("main", "main", BackupProvenanceStatus.MATCHED),
        ("refs/heads/main", "main", BackupProvenanceStatus.MATCHED),
        ("main", "develop", BackupProvenanceStatus.MISMATCHED),
        ("main", None, BackupProvenanceStatus.UNKNOWN),
    ],
)
def test_provenance_comparison_normalizes_only_heads_prefix(
    expected: str, recorded: str | None, status: BackupProvenanceStatus
) -> None:
    from odoo_instance_sdk.internal.database_preparation import compare_provenance

    comparison = compare_provenance(expected, recorded)
    assert comparison.status is status


def test_freshness_boundaries(tmp_path: Path) -> None:
    from odoo_instance_sdk.internal.database_preparation import classify_freshness
    from odoo_instance_sdk.models import NoBackup

    now = datetime(2026, 8, 27, 12, tzinfo=UTC)
    assert classify_freshness(None, 1, now=now) is BackupFreshness.MISSING
    assert classify_freshness(NoBackup(), 1, now=now) is BackupFreshness.MISSING
    missing = _backup(tmp_path, downloaded_at=now)
    Path(missing.path).unlink()
    assert classify_freshness(missing, 1, now=now) is BackupFreshness.UNAVAILABLE
    assert (
        classify_freshness(_backup(tmp_path, downloaded_at=now - timedelta(hours=1)), 1, now=now)
        is BackupFreshness.STALE
    )
    assert (
        classify_freshness(_backup(tmp_path, downloaded_at=now), 1, now=now)
        is BackupFreshness.FRESH
    )
    assert (
        classify_freshness(_backup(tmp_path, downloaded_at=now - timedelta(days=10)), None, now=now)
        is BackupFreshness.FRESH
    )


def test_target_name_is_valid_and_utf8_bounded() -> None:
    from odoo_instance_sdk.internal.database_preparation import generate_target_database
    from odoo_instance_sdk.internal.db_name import validate_db_name

    name = generate_target_database(
        "source_" + "x" * 100,
        now=datetime(2026, 9, 3, 9, 4, 20, tzinfo=UTC),
        suffix="2ee3a458a068",
    )
    validate_db_name(name)
    assert len(name.encode("utf-8")) <= 63
    assert name.endswith("_20260903090420_2ee3a458a068")
    assert "_refresh_" not in name


def test_target_name_omits_refresh_marker() -> None:
    from odoo_instance_sdk.internal.database_preparation import generate_target_database

    assert (
        generate_target_database(
            "KOM-307_4",
            now=datetime(2026, 9, 3, 9, 4, 20, tzinfo=UTC),
            suffix="2ee3a458a068",
        )
        == "KOM-307_4_20260903090420_2ee3a458a068"
    )


def test_target_reservation_rechecks_collisions() -> None:
    from odoo_instance_sdk.internal.database_preparation import reserve_target_database

    candidates = iter(("source_refresh_one", "source_refresh_two"))
    seen: list[str] = []

    def generate(_: str) -> str:
        return next(candidates)

    def exists(name: str) -> bool:
        seen.append(name)
        return name.endswith("one")

    assert reserve_target_database("remote", exists, generator=generate) == "source_refresh_two"
    assert seen == ["source_refresh_one", "source_refresh_two"]


def test_manifest_conflicts_ignore_repository_identity() -> None:
    from odoo_instance_sdk.internal.database_preparation import relevant_manifest_conflicts

    left = ProjectConfig(repository_root=Path("/one"), default_source_database="a")
    right = ProjectConfig(repository_root=Path("/two"), default_source_database="b")
    assert relevant_manifest_conflicts(left, right) == ("default_source_database",)


def test_download_preparation_reads_secret_before_lock_and_never_requires_local_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    project = _project(tmp_path)
    loaded = MagicMock(return_value=project)
    monkeypatch.setattr(ProjectConfig, "load", loaded)
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setattr(preparation, "exclusive_lock", lambda _: contextlib.nullcontext())
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")
    client = MagicMock()
    backup = _backup(tmp_path, downloaded_at=datetime.now(UTC))
    client.instance.return_value.databases.backup.return_value = backup

    result = preparation.prepare_download(client, project)
    preparation.prepare_download(client, project)

    assert result.backup == backup
    assert result.source_git_branch == "develop"
    assert result.branch_origin is BackupBranchOrigin.CONFIGURED
    assert client.instance.call_count == 2
    client.instance.assert_called_with("https://example.test", master_password="remote-secret")
    assert client.instance.return_value.databases.backup.call_count == 2
    client.instance.return_value.databases.backup.assert_called_with(
        "remote_test", source_git_branch="develop"
    )


def test_missing_remote_secret_fails_before_client_or_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    monkeypatch.delenv("ODCLI_TEST_MASTER_PASSWORD", raising=False)
    client = MagicMock()
    lock = MagicMock()
    monkeypatch.setattr(preparation, "exclusive_lock", lock)
    with pytest.raises(MasterPasswordRequiredError):
        preparation.prepare_download(client, _project(tmp_path))
    client.instance.assert_not_called()
    lock.assert_not_called()


def test_restore_missing_remote_secret_fails_before_preparation_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    monkeypatch.delenv("ODCLI_TEST_MASTER_PASSWORD", raising=False)
    client = MagicMock()
    lock = MagicMock()
    cluster = MagicMock()
    monkeypatch.setattr(preparation, "exclusive_lock", lock)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project", cluster
    )

    with pytest.raises(MasterPasswordRequiredError):
        preparation.prepare_restore(client, _project(tmp_path))

    client.instance.assert_not_called()
    client.get_catalog.assert_not_called()
    lock.assert_not_called()
    cluster.assert_not_called()


def test_unpinned_download_preparation_fails_before_lock_or_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    monkeypatch.delenv("ODCLI_TEST_INSTANCE_ORIGIN_PINS", raising=False)
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")
    client = MagicMock()
    lock = MagicMock()
    monkeypatch.setattr(preparation, "exclusive_lock", lock)

    with pytest.raises(ConfigError, match="not approved outside the repository"):
        preparation.prepare_download(client, _project(tmp_path))

    client.instance.assert_not_called()
    client.get_catalog.assert_not_called()
    lock.assert_not_called()


def test_unpinned_restore_preflight_fails_before_lock_or_local_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    monkeypatch.delenv("ODCLI_TEST_INSTANCE_ORIGIN_PINS", raising=False)
    client = MagicMock()
    lock = MagicMock()
    cluster = MagicMock()
    monkeypatch.setattr(preparation, "exclusive_lock", lock)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project", cluster
    )

    with pytest.raises(ConfigError, match="not approved outside the repository"):
        preparation.preflight_restore(client, _project(tmp_path))

    client.instance.assert_not_called()
    client.get_catalog.assert_not_called()
    lock.assert_not_called()
    cluster.assert_not_called()


def test_project_runtime_executable_cannot_read_remote_master_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.server import run_command

    executable = tmp_path / "inspect-environment"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "print('present' if os.getenv('ODCLI_TEST_MASTER_PASSWORD') else 'absent')\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")

    result = run_command(str(executable), [])

    assert result.returncode == 0
    assert result.stdout.strip() == "absent"


def test_restore_preflight_orders_lock_cluster_manager_and_target_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\n"
        "http_interface = 127.0.0.1\n"
        "http_port = 8069\n"
        "db_name = source\n"
        "admin_passwd = local-secret\n"
    )
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    odoo = tmp_path / "odoo-bin"
    odoo.write_text("#!/bin/sh\n")
    odoo.chmod(0o755)
    project = ProjectConfig(
        repository_root=tmp_path,
        python=python,
        odoo_bin=odoo,
        source_config=source,
        test_instance=ConfigTestInstance(base_url="https://example.test", database="remote"),
    )
    events: list[str] = []

    @contextlib.contextmanager
    def lock(_: Path) -> Iterator[None]:
        events.append("preparation-lock")
        yield

    cluster = MagicMock()

    def ensure_cluster(**_: object) -> None:
        events.append("cluster")

    cluster.ensure_running.side_effect = ensure_cluster
    local = MagicMock()

    def list_names() -> tuple[str, ...]:
        events.append("names")
        return ("source",)

    def database_exists(_: str) -> bool:
        events.append("exists")
        return False

    local.databases.names.side_effect = list_names
    local.databases.exists.side_effect = database_exists
    client = MagicMock()
    client.instance.from_config.return_value = local
    monkeypatch.setattr(ProjectConfig, "load", MagicMock(return_value=project))
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setattr(preparation, "exclusive_lock", lock)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project",
        MagicMock(return_value=cluster),
    )
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")

    preflight = preparation.preflight_restore(client, project)

    assert preflight.target_database
    assert events == ["preparation-lock", "cluster", "names", "exists"]
    client.instance.assert_not_called()
    client.instance.from_config.assert_called_once()


@pytest.mark.parametrize("entrypoint", ["preflight_restore", "prepare_restore"])
def test_restore_entrypoints_reject_invalid_local_config_before_network_or_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\n"
        "http_interface = 0.0.0.0\n"
        "http_port = 8069\n"
        "db_name = source\n"
        "admin_passwd = local-secret\n"
    )
    project = ProjectConfig(
        repository_root=tmp_path,
        source_config=source,
        test_instance=ConfigTestInstance(base_url="https://example.test", database="remote"),
    )
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setattr(preparation, "exclusive_lock", lambda _: contextlib.nullcontext())
    monkeypatch.setattr(
        preparation, "exclusive_lock_until", lambda *_args, **_kwargs: contextlib.nullcontext()
    )
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")
    client = MagicMock()

    with pytest.raises(InstanceConfigurationError, match="local source config must bind"):
        getattr(preparation, entrypoint)(client, project)

    client.instance.assert_not_called()
    client.get_catalog.assert_not_called()


@pytest.mark.parametrize("entrypoint", ["preflight_restore", "prepare_restore"])
def test_restore_entrypoints_report_lock_contention_consistently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    @contextlib.contextmanager
    def fail_lock(*_args: object, **_kwargs: object) -> Iterator[None]:
        raise LockConflictError("database-preparation-repo.lock", mode="exclusive")
        yield

    monkeypatch.setattr(preparation, "exclusive_lock", fail_lock)
    monkeypatch.setattr(preparation, "exclusive_lock_until", fail_lock)
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")
    client = MagicMock()

    with pytest.raises(LockConflictError, match=r"database-preparation-repo\.lock"):
        getattr(preparation, entrypoint)(client, _project(tmp_path))

    client.instance.assert_not_called()
    client.get_catalog.assert_not_called()


def test_target_instance_is_target_only_secure_and_ephemeral(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.database_preparation import (
        ProjectRuntimeBinding,
        build_target_instance,
    )

    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\n"
        "http_interface = 127.0.0.1\n"
        "http_port = 8069\n"
        "db_name = source,other\n"
        "dbfilter = source|other\n"
        "admin_passwd = local-secret\n"
    )
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    odoo = tmp_path / "odoo-bin"
    odoo.write_text("#!/bin/sh\n")
    odoo.chmod(0o755)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_locks_dir", lambda: tmp_path / "locks"
    )
    runtime = ProjectRuntimeBinding(str(python), str(odoo), tmp_path)
    cluster = MagicMock()
    client = MagicMock()

    with build_target_instance(
        client,
        source_config=source,
        target_database="source_refresh_1",
        runtime=runtime,
        postgres_cluster=cluster,
        project_id="repo",
    ) as target:
        start_config = target.config.start_config
        assert start_config is not None
        generated = Path(start_config.config_path or "")
        assert generated.is_file()
        assert os.stat(generated).st_mode & 0o777 == 0o600
        assert target.config.configured_database_names == ("source_refresh_1",)
        assert start_config.db_name == "source_refresh_1"
        assert start_config.dbfilter == "source_refresh_1"
        assert target.config.command_prefix == (str(python), str(odoo))
        assert target.config.default_cwd == tmp_path
        assert target._postgres_cluster is cluster
        assert target._artifact_lock_path == (
            tmp_path / "locks" / "database-preparation-repo-source_refresh_1.lock"
        )
    assert not generated.exists()


def test_restore_coordinator_switches_default_only_after_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation
    from odoo_instance_sdk.internal.project_manifest import write_manifest

    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\n"
        "http_interface = 127.0.0.1\n"
        "http_port = 8069\n"
        "db_name = source\n"
        "admin_passwd = local-secret\n"
    )
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    odoo = tmp_path / "odoo-bin"
    odoo.write_text("#!/bin/sh\n")
    odoo.chmod(0o755)
    project = ProjectConfig(
        repository_root=tmp_path,
        python=python,
        odoo_bin=odoo,
        source_config=source,
        default_source_database="old",
        test_instance=ConfigTestInstance(base_url="https://example.test", database="remote"),
    )
    backup = _backup(tmp_path, downloaded_at=datetime.now(UTC))
    local = MagicMock()
    local.databases.names.return_value = ("source",)
    local.databases.exists.return_value = False
    remote = MagicMock()
    remote.databases.backup.return_value = backup
    cluster = MagicMock()
    client = MagicMock()
    client.instance.from_config.return_value = local
    client.instance.return_value = remote
    loader = MagicMock(return_value=project)
    monkeypatch.setattr(ProjectConfig, "load", loader)
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setattr(
        preparation, "exclusive_lock_until", lambda *_args, **_kwargs: contextlib.nullcontext()
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project",
        MagicMock(return_value=cluster),
    )
    monkeypatch.setattr(preparation, "write_manifest", write_manifest, raising=False)
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")

    result = preparation.prepare_restore(client, project)

    assert result.default_switched is True
    assert result.previous_default == "old"
    assert result.effective_default == result.restored_database
    remote.databases.backup.assert_called_once_with("remote", source_git_branch=None)
    local.databases.restore.assert_called_once_with(
        backup,
        result.restored_database,
        copy=True,
        neutralize_database=True,
    )
    assert loader.return_value.default_source_database == "old"
    assert loader.call_count >= 1


def test_restore_failure_retains_backup_and_does_not_write_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\nhttp_interface = 127.0.0.1\nhttp_port = 8069\n"
        "db_name = source\nadmin_passwd = local-secret\n"
    )
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    odoo = tmp_path / "odoo-bin"
    odoo.write_text("#!/bin/sh\n")
    odoo.chmod(0o755)
    project = ProjectConfig(
        repository_root=tmp_path,
        python=python,
        odoo_bin=odoo,
        source_config=source,
        default_source_database="old",
        test_instance=ConfigTestInstance(base_url="https://example.test", database="remote"),
    )
    backup = _backup(tmp_path, downloaded_at=datetime.now(UTC))
    local = MagicMock()
    local.databases.names.return_value = ("source",)
    local.databases.exists.return_value = False
    local.databases.restore.side_effect = RuntimeError("restore failed")
    remote = MagicMock()
    remote.databases.backup.return_value = backup
    cluster = MagicMock()
    client = MagicMock()
    client.instance.from_config.return_value = local
    client.instance.return_value = remote
    write = MagicMock()
    loader = MagicMock(return_value=project)
    monkeypatch.setattr(ProjectConfig, "load", loader)
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setattr(
        preparation, "exclusive_lock_until", lambda *_args, **_kwargs: contextlib.nullcontext()
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project",
        MagicMock(return_value=cluster),
    )
    monkeypatch.setattr(preparation, "write_manifest", write, raising=False)
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")

    with pytest.raises(RuntimeError, match="restore failed") as failure:
        preparation.prepare_restore(client, project)

    assert backup.path and Path(backup.path).is_file()
    assert "retained backup" in " ".join(failure.value.__notes__ or ())
    write.assert_not_called()


def test_restore_admin_reset_failure_retains_target_and_removes_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\nhttp_interface = 127.0.0.1\nhttp_port = 8069\n"
        "db_name = source\nadmin_passwd = local-secret\n"
    )
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    odoo = tmp_path / "odoo-bin"
    odoo.write_text("#!/bin/sh\n")
    odoo.chmod(0o755)
    project = ProjectConfig(
        repository_root=tmp_path,
        python=python,
        odoo_bin=odoo,
        source_config=source,
        default_source_database="old",
        test_instance=ConfigTestInstance(base_url="https://example.test", database="remote"),
    )
    backup = _backup(tmp_path, downloaded_at=datetime.now(UTC))
    local = MagicMock()
    local.databases.names.return_value = ("source",)
    local.databases.exists.return_value = False
    local.databases.restore.return_value = object()
    remote = MagicMock()
    remote.databases.backup.return_value = backup
    cluster = MagicMock()
    client = MagicMock()
    client.instance.from_config.return_value = local
    client.instance.return_value = remote
    monkeypatch.setattr(ProjectConfig, "load", MagicMock(return_value=project))
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setattr(
        preparation,
        "exclusive_lock_until",
        lambda *_args, **_kwargs: contextlib.nullcontext(),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project",
        MagicMock(return_value=cluster),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.OdooInstance._run_shell_script_exclusive",
        MagicMock(side_effect=RuntimeError("reset failed")),
    )
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")

    with pytest.raises(Exception, match="Administrator password reset failed") as failure:
        preparation.prepare_restore(
            client,
            project,
            options=DatabaseRefreshOptions(restore=True, reset_admin_password=True),
        )

    assert "retained database" in " ".join(failure.value.__notes__ or ())
    assert not list(tmp_path.glob(".odcli-refresh-*.conf"))
    assert project.default_source_database == "old"
    local.databases.restore.assert_called_once()


def test_catalogue_source_preflight_validates_exact_published_artifact(
    tmp_path: Path,
) -> None:
    from odoo_instance_sdk.internal.database_preparation import (
        _catalogue_backup_preflight,
        _CatalogueRestoreSource,
    )
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    archive = tmp_path / "registered.zip"
    archive.write_bytes(b"registered backup")
    backup_id = str(uuid.uuid4())
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    catalog.start_download(
        backup_id,
        "https://example.test",
        "remote_test",
        "zip",
        True,
        archive,
    )
    catalog.success_download(backup_id, archive.name, archive.stat().st_size, "")
    project = _project(tmp_path)

    assert _catalogue_backup_preflight(
        catalog, _CatalogueRestoreSource(uuid.UUID(backup_id)), project
    ).id == uuid.UUID(backup_id)
    catalog.close()


def test_catalogue_restore_uses_common_restore_stages_without_remote_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation
    from odoo_instance_sdk.internal.database_preparation import (
        ProjectRuntimeBinding,
        RestorePreflight,
        _CatalogueRestoreSource,
    )

    source_config = tmp_path / "odoo.conf"
    source_config.write_text(
        "[options]\nhttp_interface = 127.0.0.1\nhttp_port = 8069\n"
        "db_name = source\nadmin_passwd = local-secret\n"
    )
    project = ProjectConfig(
        repository_root=tmp_path,
        source_config=source_config,
        default_source_database="old",
        test_instance=ConfigTestInstance(base_url="https://example.test", database="remote_test"),
    )
    backup = _backup(tmp_path, downloaded_at=datetime.now(UTC))
    local = MagicMock()
    local.databases.names.return_value = ("source",)
    local.databases.exists.return_value = False
    cluster = MagicMock()
    preflight = RestorePreflight(
        project=project,
        project_id="project",
        source=None,
        source_config=source_config,
        local_instance=local,
        runtime=ProjectRuntimeBinding(
            python_executable="/usr/bin/python3",
            odoo_bin="/usr/bin/odoo-bin",
            runtime_cwd=tmp_path,
        ),
        postgres_cluster=cluster,
        target_database="restored_target",
        restore_source=_CatalogueRestoreSource(backup.id),
        catalogue_backup=backup,
    )

    @contextlib.contextmanager
    def fake_preflight(*_args: object, **_kwargs: object) -> Iterator[RestorePreflight]:
        yield preflight

    client = MagicMock()
    monkeypatch.setattr(preparation, "_restore_preflight", fake_preflight)
    monkeypatch.setattr(preparation, "_manifest_after_preparation", lambda *_args: project)
    monkeypatch.setattr(preparation, "write_manifest", MagicMock(), raising=False)

    result = preparation.prepare_restore(
        client,
        project,
        restore_source=_CatalogueRestoreSource(backup.id),
    )

    assert result.backup == backup
    assert result.source_git_branch == backup.source_git_branch
    assert result.default_switched is True
    client.instance.assert_not_called()
    local.databases.restore.assert_called_once_with(
        backup,
        "restored_target",
        copy=True,
        neutralize_database=True,
    )


def test_pinned_http_download_reaches_remote_database_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    project = ProjectConfig(
        repository_root=tmp_path,
        test_instance=ConfigTestInstance(
            base_url="http://example.test:8069",
            database="remote_test",
        ),
    )
    backup = _backup(tmp_path, downloaded_at=datetime.now(UTC))
    client = MagicMock()
    client.instance.return_value.databases.backup.return_value = backup
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")
    monkeypatch.setenv("ODCLI_TEST_INSTANCE_ORIGIN_PINS", "http://example.test:8069")
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setattr(preparation, "exclusive_lock", lambda _: contextlib.nullcontext())

    result = preparation.prepare_download(client, project)

    assert result.backup == backup
    client.instance.assert_called_once_with(
        "http://example.test:8069", master_password="remote-secret"
    )
    client.instance.return_value.databases.backup.assert_called_once_with(
        "remote_test", source_git_branch=None
    )


def test_checkout_coalesces_fresh_result_under_preparation_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\nhttp_interface = 127.0.0.1\nhttp_port = 8069\n"
        "db_host = localhost\ndb_name = old\nadmin_passwd = local-secret\n"
    )
    project = ProjectConfig(
        repository_root=tmp_path,
        source_config=source,
        default_source_database="old",
        refresh_after_hours=1,
        test_instance=ConfigTestInstance(
            base_url="https://example.test", database="remote", git_branch="develop"
        ),
    )
    backup = _backup(tmp_path, downloaded_at=datetime.now(UTC))
    backup = Backup(
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
        source_git_branch="develop",
    )
    catalog = MagicMock()
    catalog.latest_restore.return_value = backup
    client = MagicMock()
    client.get_catalog.return_value = catalog
    monkeypatch.setattr(
        preparation,
        "_wait_for_preparation_lock",
        lambda *_args, **_kwargs: contextlib.nullcontext(),
    )
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")

    result = preparation.prepare_restore(
        client,
        project,
        options=DatabaseRefreshOptions(restore=True),
        coalesce=True,
    )

    assert result.default_switched is False
    assert result.effective_default == "old"
    client.instance.assert_not_called()
