from __future__ import annotations

import contextlib
import hashlib
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from odoo_instance_sdk.models import BackupFormat
from tests.fixtures import make_backup

if TYPE_CHECKING:
    from pytest_httpx import HTTPXMock

    from odoo_instance_sdk.resources.instance import OdooInstance


class TestRestore:
    def test_public_restore_starts_owned_manager_before_pipeline_request(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from click.testing import CliRunner

        from odoo_instance_sdk.cli import cli
        from odoo_instance_sdk.config import InstanceConfig
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.proc import ProcessHandle, RecordingExecutor
        from odoo_instance_sdk.internal.project_manifest import write_manifest
        from odoo_instance_sdk.models import (
            DatabasePreparationAction,
            DatabasePreparationResult,
            StartConfig,
        )
        from odoo_instance_sdk.project import ProjectConfig
        from odoo_instance_sdk.resources.database import DatabaseResource
        from odoo_instance_sdk.resources.instance import (
            OdooInstance,
            active_auxiliary_restore_session,
            auxiliary_restore_session,
        )

        source = tmp_path / "odoo.conf"
        source.write_text(
            "[options]\nhttp_interface = 127.0.0.1\nhttp_port = 8069\nadmin_passwd = private\n"
        )
        python = tmp_path / "python"
        python.write_text("#!/bin/sh\n")
        python.chmod(0o755)
        odoo_bin = tmp_path / "odoo-bin"
        odoo_bin.write_text("#!/bin/sh\n")
        odoo_bin.chmod(0o755)
        project = ProjectConfig(
            repository_root=tmp_path,
            python=python,
            odoo_bin=odoo_bin,
            source_config=source,
            default_source_database="restored",
        )
        write_manifest(tmp_path, project)

        client = MagicMock()
        auxiliary = OdooInstance(
            config=InstanceConfig(
                base_url="http://127.0.0.1:8069",
                start_config=StartConfig(config_path=str(source), http_port=8069),
                command_prefix=(str(python), str(odoo_bin)),
                default_cwd=tmp_path,
            ),
            _client=client,
        )
        session = auxiliary_restore_session(auxiliary)
        handle = ProcessHandle(
            process=MagicMock(),
            argv=session.start_step.argv,
            process_group_id=123,
            session_id=123,
            inherited_stdio=False,
        )
        executor = RecordingExecutor(handles={session.start_step.step_id: handle})
        client.instance.from_project.return_value = auxiliary
        client.unregister_process.return_value = (None, None)
        monkeypatch.setattr(
            "odoo_instance_sdk.resources.instance._assert_http_port_free", lambda _config: None
        )
        monkeypatch.setattr(
            OdooInstance, "wait_ready", lambda _self, _proc, *, timeout: MagicMock(ok=True)
        )
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"result": ["restored"]}
        http = MagicMock()
        http.post.return_value = response

        @contextlib.contextmanager
        def fake_http(_self: DatabaseResource, timeout: float | None = None) -> Any:
            del timeout
            yield http

        monkeypatch.setattr(DatabaseResource, "_http", fake_http)

        def callback(context: Any) -> DatabasePreparationResult:
            active = active_auxiliary_restore_session()
            assert active is not None
            assert active.instance is auxiliary
            assert auxiliary.databases.names() == ("restored",)
            return DatabasePreparationResult(
                mode=DatabasePreparationAction.RESTORE,
                restored_database="restored",
                default_switched=True,
                effective_default="restored",
            )

        client.environments.refresh_database_command.return_value = Command.create(
            ExecutionPlan(), callback, executor=executor
        )
        monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path
        )

        result = CliRunner().invoke(
            cli,
            ["db", "restore", str(uuid.uuid4()), "--yes", "--format", "json"],
        )

        assert result.exit_code == 0, result.output
        assert result.stdout and '"default_switched": true' in result.stdout
        assert executor.spawned == [session.start_step]
        assert http.post.call_count == 1
        client.unregister_process.assert_called_once()

    def test_remote_backup_to_local_restore(
        self,
        instance: OdooInstance,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        httpx_mock: HTTPXMock,
    ) -> None:
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.paths.get_cache_root", lambda **_kwargs: tmp_path
        )

        from tests.fixtures.odoo_database_server import BACKUP_ZIP_CONTENT

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_file = backup_dir / "test_backup.zip"
        backup_file.write_bytes(BACKUP_ZIP_CONTENT)

        backup = make_backup(
            source_base_url="http://localhost:8069",
            database_name="testdb",
            path=str(backup_file),
            filename=backup_file.name,
            size_bytes=len(BACKUP_ZIP_CONTENT),
        )

        catalog = instance._client.get_catalog()
        catalog.start_download(
            str(backup.id),
            backup.source_base_url,
            backup.database_name,
            backup.format.value,
            backup.filestore_requested,
            Path(backup.path),
        )
        catalog.success_download(str(backup.id), backup.filename, backup.size_bytes, backup.sha256)

        httpx_mock.add_response(
            url="http://localhost:8069/web/database/list",
            method="POST",
            json={"result": []},
        )

        httpx_mock.add_response(
            url="http://localhost:8069/web/database/restore",
            method="POST",
            status_code=303,
            headers={"location": "/web/database/manager"},
        )

        httpx_mock.add_response(
            url="http://localhost:8069/web/database/list",
            method="POST",
            json={"result": ["testdb"]},
        )

        result = instance.databases.restore(backup, "testdb")
        assert result.new_db == "testdb"
        assert result.source.format is BackupFormat.ZIP
        assert result.source.filestore_requested is True
        assert result.source.database_name == "testdb"

    @pytest.mark.parametrize("failure", [None, "spawn", "foreign", "preflight"])
    def test_public_stopped_restore_runs_real_coordinator_and_preserves_zip_contract(  # noqa: C901
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
    ) -> None:
        """The public stopped-project path must exercise coordinator postconditions."""
        from click.testing import CliRunner

        from odoo_instance_sdk.cli import cli
        from odoo_instance_sdk.config import InstanceConfig
        from odoo_instance_sdk.internal.database_preparation import (
            DatabasePreparationCoordinator,
        )
        from odoo_instance_sdk.internal.proc import (
            PreparedStep,
            ProcessHandle,
            ProcessResult,
            RecordingExecutor,
        )
        from odoo_instance_sdk.internal.project_manifest import write_manifest
        from odoo_instance_sdk.models import StartConfig
        from odoo_instance_sdk.project import ProjectConfig, TestInstanceProjectConfig
        from odoo_instance_sdk.resources.database import DatabaseResource
        from odoo_instance_sdk.resources.instance import OdooInstance, auxiliary_restore_session

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
        odoo_bin = tmp_path / "odoo-bin"
        odoo_bin.write_text("#!/bin/sh\n")
        odoo_bin.chmod(0o755)
        project = ProjectConfig(
            repository_root=tmp_path,
            python=python,
            odoo_bin=odoo_bin,
            source_config=source,
            default_source_database="old",
            test_instance=TestInstanceProjectConfig(
                base_url="https://example.test", database="remote_test"
            ),
        )
        write_manifest(tmp_path, project)
        backup_file = tmp_path / "remote.zip"
        backup_file.write_bytes(b"zip-backup")
        backup = make_backup(
            source_base_url="https://example.test",
            database_name="remote_test",
            path=str(backup_file),
            filename=backup_file.name,
            size_bytes=backup_file.stat().st_size,
            sha256=hashlib.sha256(backup_file.read_bytes()).hexdigest(),
            format=BackupFormat.ZIP,
            filestore_requested=True,
            source_git_branch="develop",
        )

        client = MagicMock()
        auxiliary = OdooInstance(
            config=InstanceConfig(
                base_url="http://127.0.0.1:8069",
                start_config=StartConfig(config_path=str(source), http_port=8069),
                command_prefix=(str(python), str(odoo_bin)),
                default_cwd=tmp_path,
            ),
            _client=client,
        )
        local = OdooInstance(
            config=InstanceConfig(
                base_url="http://127.0.0.1:8069",
                start_config=StartConfig(config_path=str(source), http_port=8069),
            ),
            _client=client,
        )
        remote = MagicMock()
        remote.databases.backup.return_value = backup
        client.instance.from_project.return_value = auxiliary
        client.instance.from_config.return_value = local
        client.instance.return_value = remote
        client.unregister_process.return_value = (None, None)

        handle = ProcessHandle(
            process=MagicMock(),
            argv=(),
            process_group_id=123,
            session_id=123,
            inherited_stdio=False,
        )
        session = auxiliary_restore_session(auxiliary)
        executor = RecordingExecutor(
            handles={} if failure == "spawn" else {session.start_step.step_id: handle}
        )
        cluster = MagicMock()
        cluster.mode = "external"
        cluster._ensure_running_steps.return_value = ()
        monkeypatch.setattr(
            "odoo_instance_sdk.resources.postgres.PostgresCluster._from_config",
            MagicMock(return_value=cluster),
        )
        if failure == "foreign":
            from odoo_instance_sdk.exceptions import InstanceConfigurationError

            monkeypatch.setattr(
                "odoo_instance_sdk.resources.instance._assert_http_port_free",
                MagicMock(
                    side_effect=InstanceConfigurationError("port-conflict: ownership unknown")
                ),
            )
        else:
            monkeypatch.setattr(
                "odoo_instance_sdk.resources.instance._assert_http_port_free", lambda _config: None
            )
        monkeypatch.setattr(
            OdooInstance, "wait_ready", lambda _self, _proc, *, timeout: MagicMock(ok=True)
        )
        restore = MagicMock()
        monkeypatch.setattr(DatabaseResource, "restore", restore)
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"result": [] if failure == "preflight" else ["source"]}

        @contextlib.contextmanager
        def fake_http(_self: DatabaseResource, timeout: float | None = None) -> Any:
            del timeout
            yield MagicMock(post=MagicMock(return_value=response))

        monkeypatch.setattr(DatabaseResource, "_http", fake_http)

        def command_factory(project_path: Path, *, options: Any) -> Any:
            command = DatabasePreparationCoordinator(client).refresh_database_command(
                project_path,
                options=options,
                executor=executor,
            )
            for step in command._prepared().steps:
                if (
                    isinstance(step, PreparedStep)
                    and step.step_id == "database.prepare.git.toplevel"
                ):
                    executor.results[step.step_id] = ProcessResult(
                        argv=step.argv,
                        returncode=0,
                        stdout=str(tmp_path),
                        stderr="",
                        duration=0.0,
                        cwd=step.cwd,
                        environment=step.environment,
                    )
                elif (
                    isinstance(step, PreparedStep)
                    and step.step_id == "database.prepare.git.common-dir"
                ):
                    executor.results[step.step_id] = ProcessResult(
                        argv=step.argv,
                        returncode=0,
                        stdout=str(tmp_path / ".git"),
                        stderr="",
                        duration=0.0,
                        cwd=step.cwd,
                        environment=step.environment,
                    )
            return command

        client.environments.refresh_database_command.side_effect = command_factory
        monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path
        )
        monkeypatch.setenv("ODCLI_TEST_INSTANCE_ORIGIN_PINS", "https://example.test:443")
        monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")

        result = CliRunner().invoke(cli, ["db", "refresh", "--restore", "--format", "json"])

        if failure is not None:
            assert result.exit_code == 1, result.output
            if failure == "spawn":
                assert "odcli run" in result.stdout
            elif failure == "foreign":
                assert "port-conflict" in result.stdout
            else:
                assert "local database manager returned no databases" in result.stdout
            restore.assert_not_called()
            if failure == "preflight":
                client.unregister_process.assert_called_once()
            else:
                client.unregister_process.assert_not_called()
            return

        assert result.exit_code == 0, result.output
        assert '"default_switched": true' in result.stdout
        assert backup.format is BackupFormat.ZIP
        assert backup.filestore_requested is True
        restore.assert_called_once()
        assert restore.call_args is not None
        assert restore.call_args.args[0] is backup
        assert restore.call_args.kwargs == {
            "copy": True,
            "neutralize_database": True,
        }
        assert [step.step_id for step in executor.spawned] == [session.start_step.step_id]
        client.unregister_process.assert_called_once()

    def test_forged_backup_rejected(
        self, instance: OdooInstance, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.paths.get_cache_root", lambda **_kwargs: tmp_path
        )

        backup_file = tmp_path / "forged.zip"
        backup_file.write_bytes(b"forged content")
        backup = make_backup(
            source_base_url="http://localhost:8069",
            database_name="testdb",
            path=str(backup_file),
            filename=backup_file.name,
        )

        from odoo_instance_sdk.exceptions import BackupNotFoundError

        with pytest.raises(BackupNotFoundError):
            instance.databases.restore(backup, "testdb")

    def test_existing_target_rejected(
        self,
        instance: OdooInstance,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        httpx_mock: HTTPXMock,
    ) -> None:
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.paths.get_cache_root", lambda **_kwargs: tmp_path
        )

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_file = backup_dir / "backup.zip"
        backup_file.write_bytes(b"fake-content")

        backup = make_backup(
            source_base_url="http://localhost:8069",
            database_name="existing_db",
            path=str(backup_file),
            filename=backup_file.name,
            size_bytes=12,
        )

        catalog = instance._client.get_catalog()
        catalog.start_download(
            str(backup.id),
            backup.source_base_url,
            backup.database_name,
            backup.format.value,
            backup.filestore_requested,
            Path(backup.path),
        )
        catalog.success_download(str(backup.id), backup.filename, backup.size_bytes, backup.sha256)

        httpx_mock.add_response(
            url="http://localhost:8069/web/database/list",
            method="POST",
            json={"result": ["existing_db"]},
        )

        from odoo_instance_sdk.exceptions import DatabaseAlreadyExistsError

        with pytest.raises(DatabaseAlreadyExistsError):
            instance.databases.restore(backup, "existing_db")

    def test_remote_restore_rejected(self, instance_remote: OdooInstance, tmp_path: Path) -> None:
        backup_file = tmp_path / "test.zip"
        backup = make_backup(
            source_base_url="http://example.com:8069",
            database_name="testdb",
            path=str(backup_file),
            filename=backup_file.name,
        )

        from odoo_instance_sdk.exceptions import NonLocalInstanceError

        with pytest.raises(NonLocalInstanceError):
            instance_remote.databases.restore(backup, "testdb")


class TestDrop:
    def test_drop_success(self, instance: OdooInstance, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url="http://localhost:8069/web/database/drop",
            method="POST",
            status_code=303,
            headers={"location": "/web/database/manager"},
        )

        httpx_mock.add_response(
            url="http://localhost:8069/web/database/list",
            method="POST",
            json={"result": []},
        )

        result = instance.databases.drop("testdb")
        assert result.db == "testdb"

    def test_drop_nonexistent(self, instance: OdooInstance, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url="http://localhost:8069/web/database/drop",
            method="POST",
            status_code=303,
            headers={"location": "/web/database/manager"},
        )
        httpx_mock.add_response(
            url="http://localhost:8069/web/database/list",
            method="POST",
            json={"result": []},
        )

        result = instance.databases.drop("nonexistent")
        assert result.db == "nonexistent"

    def test_remote_drop_rejected(self, instance_remote: OdooInstance) -> None:
        from odoo_instance_sdk.exceptions import NonLocalInstanceError

        with pytest.raises(NonLocalInstanceError):
            instance_remote.databases.drop("testdb")


class TestValidationUnavailable:
    def test_validation_unavailable_records_event(
        self,
        instance: OdooInstance,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        httpx_mock: HTTPXMock,
    ) -> None:
        from odoo_instance_sdk.models import BackupState

        monkeypatch.setattr(
            "odoo_instance_sdk.internal.paths.get_cache_root", lambda **_kwargs: tmp_path
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.backup_validation.shutil.which",
            lambda *a, **k: None,
        )

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_file = backup_dir / "test.zip"
        backup_file.write_bytes(b"not a real dump")

        backup = make_backup(
            source_base_url="http://localhost:8069",
            database_name="testdb",
            path=str(backup_file),
            filename=backup_file.name,
            format=BackupFormat.DUMP,
            sha256=hashlib.sha256(b"not a real dump").hexdigest(),
        )

        catalog = instance._client.get_catalog()
        catalog.start_download(
            str(backup.id),
            backup.source_base_url,
            backup.database_name,
            backup.format.value,
            backup.filestore_requested,
            Path(backup.path),
        )
        catalog.success_download(str(backup.id), backup.filename, backup.size_bytes, backup.sha256)

        from odoo_instance_sdk.resources.backup import BackupResource

        res = BackupResource(_client=instance._client)

        from odoo_instance_sdk.exceptions import BackupValidationUnavailableError

        with pytest.raises(BackupValidationUnavailableError):
            res.validate(backup, raise_if_unavailable=True)

        row = catalog.get_by_id(str(backup.id))
        assert row is not None
        assert row["state"] == BackupState.AVAILABLE.value

        events = catalog.get_backup_history(backup_id=str(backup.id))
        kinds = [e.event_type.value for e in events]
        assert "validation_unavailable" in kinds
