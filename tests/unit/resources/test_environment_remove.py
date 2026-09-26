from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from odoo_instance_sdk.exceptions import EnvironmentConflictError
from odoo_instance_sdk.internal.paths import get_backups_dir
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    ProcessExecutor,
    ProcessHandle,
    ProcessResultLike,
    RecordingExecutor,
    StepObserver,
    SubprocessExecutor,
    prepared_command,
)
from odoo_instance_sdk.models import Backup, BackupFormat, Database, NoBackup
from odoo_instance_sdk.resources.environment import (
    DevelopmentEnvironment,
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
    EnvironmentState,
)
from odoo_instance_sdk.storage.backup_catalog import CopyJournalStage

if TYPE_CHECKING:
    from odoo_instance_sdk import OdooClient
    from odoo_instance_sdk.resources.postgres import PostgresCluster


@pytest.fixture(autouse=True)
def _restore_instance_factory() -> object:
    from odoo_instance_sdk.resources.instance import InstanceFactory

    original = InstanceFactory.from_config
    yield
    InstanceFactory.from_config = original  # type: ignore[method-assign]


def _copy_instance(*, target_exists: bool = True) -> MagicMock:
    backup_path = get_backups_dir() / f"{uuid.uuid4()}.zip"
    backup = Backup(
        id=uuid.uuid4(),
        source_base_url="http://127.0.0.1:8069",
        database_name="comerta",
        format=BackupFormat.ZIP,
        filestore_requested=True,
        path=str(backup_path),
        filename="comerta.zip",
        size_bytes=1,
        sha256="a" * 64,
        downloaded_at=datetime.now(UTC),
    )
    backup_path.write_bytes(b"backup")
    instance = MagicMock()
    instance.config.db_host = "localhost"
    instance.config.db_port = 5432
    instance.config.db_user = "odoo"
    instance.databases.list.return_value = (Database(name="comerta", backup=NoBackup()),)
    instance.databases.backup.return_value = backup
    instance.databases.exists.return_value = target_exists
    return instance


def _record_backup(env_client: OdooClient, backup: Backup) -> None:
    catalog = env_client.get_catalog()
    catalog.start_download(
        str(backup.id),
        backup.source_base_url,
        backup.database_name,
        backup.format.value,
        backup.filestore_requested,
        Path(backup.path),
    )
    catalog.success_download(str(backup.id), backup.filename, backup.size_bytes, backup.sha256)


def _checkout_copy(
    env_client: OdooClient,
    project_manifest: Path,
    fake_python: Path,
    branch: str,
    instance: MagicMock,
    http_port: int | None = None,
) -> DevelopmentEnvironment:
    from odoo_instance_sdk.resources.instance import InstanceFactory

    backup = instance.databases.backup.return_value
    _record_backup(env_client, backup)
    if instance.databases._restore_after_verified_absence.side_effect is None:

        def record_restore(_backup: Backup, target: str, **_kwargs: object) -> None:
            env_client.get_catalog().record_restore("localhost", 5432, target, str(_backup.id))

        instance.databases._restore_after_verified_absence.side_effect = record_restore
    InstanceFactory.from_config = MagicMock(return_value=instance)  # type: ignore[method-assign]
    return env_client.environments.checkout(
        project_manifest,
        branch,
        options=EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.COPY,
            target_database="copy_target",
            source_database="comerta",
            http_port=http_port,
        ),
    )


class TestEnvRemove:
    def test_dirty_worktree_blocks(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path, git_repo: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python), source_database="comerta")
        env = env_client.environments.checkout(project_manifest, "feat/rm-dirty", options=opts)
        worktree = Path(env.worktree_path)
        (worktree / "dirty.txt").write_text("uncommitted")
        subprocess.run(["git", "add", "dirty.txt"], cwd=worktree, check=True, capture_output=True)
        with pytest.raises(EnvironmentConflictError):
            env_client.environments.remove(env)
        updated = env_client.environments.get(str(env.id))
        assert updated.state == EnvironmentState.READY

    def test_shared_source_db_never_dropped(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
            source_database="comerta",
        )
        env = env_client.environments.checkout(project_manifest, "feat/rm-shared", options=opts)
        env_client.environments.remove(env)
        removed = env_client.environments.get(str(env.id))
        assert removed.state == EnvironmentState.REMOVED

    def test_idempotent_missing_artifact(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python), source_database="comerta")
        env = env_client.environments.checkout(project_manifest, "feat/rm-idem", options=opts)
        import shutil

        shutil.rmtree(Path(env.worktree_path), ignore_errors=True)
        env_client.environments.remove(env)
        removed = env_client.environments.get(str(env.id))
        assert removed.state == EnvironmentState.REMOVED

    def test_remove_deletes_generated_config(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python), source_database="comerta")
        env = env_client.environments.checkout(project_manifest, "feat/rm-cfg", options=opts)
        cfg_path = Path(env.generated_config_path)
        assert cfg_path.is_file()
        env_client.environments.remove(env)
        assert not cfg_path.exists()

    @pytest.mark.serial
    def test_occupied_reserved_port_causes_zero_mutations(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        free_port = int(probe.getsockname()[1])
        probe.close()
        env = env_client.environments.checkout(
            project_manifest,
            "feat/rm-port",
            options=EnvironmentCheckoutOptions(
                python=str(fake_python), source_database="comerta", http_port=free_port
            ),
        )
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind((env.http_interface, env.http_port))
        listener.listen()
        try:
            with pytest.raises(EnvironmentConflictError, match="reserved port"):
                env_client.environments.remove(env)
        finally:
            listener.close()
        assert env_client.environments.get(str(env.id)).state == EnvironmentState.READY
        assert Path(env.worktree_path).is_dir()
        assert Path(env.generated_config_path).is_file()

    def test_removed_environment_is_idempotent_when_port_is_reused(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        free_port = int(probe.getsockname()[1])
        probe.close()
        env = env_client.environments.checkout(
            project_manifest,
            "feat/rm-port-reused",
            options=EnvironmentCheckoutOptions(
                python=str(fake_python), source_database="comerta", http_port=free_port
            ),
        )
        env_client.environments.remove(env)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind((env.http_interface, env.http_port))
        listener.listen()
        try:
            env_client.environments.remove(env_client.environments.get(str(env.id)))
        finally:
            listener.close()
        assert env_client.environments.get(str(env.id)).state is EnvironmentState.REMOVED

    def test_audit_rows_kept(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python), source_database="comerta")
        env = env_client.environments.checkout(project_manifest, "feat/rm-audit", options=opts)
        env_client.environments.remove(env)
        catalog = env_client.get_catalog()
        row = catalog.get_environment(str(env.id))
        assert row is not None
        assert row["state"] == EnvironmentState.REMOVED.value


class TestCopyRemoveRecovery:
    @pytest.fixture(autouse=True)
    def _fake_direct_drop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from odoo_instance_sdk.resources.environment import EnvironmentResource

        def capture_direct_drop(
            resource: EnvironmentResource,
            env: DevelopmentEnvironment,
            *,
            executor: ProcessExecutor | None,
            force_connections: bool = False,
        ) -> Any:
            if env.db_mode is not EnvironmentDatabaseMode.COPY:
                return None
            instance = resource._client.instance.from_config(
                env.generated_config_path, master_password="admin"
            )
            step = PreparedAction(
                step_id="test.copy.database.drop",
                action="drop-owned-copy-database",
                description="test direct drop",
                mutating=True,
            )

            def run(context: Any) -> Any:
                context.action(step.step_id)
                target = env.target_db_name
                assert target is not None
                if instance.databases.exists(target):
                    instance.databases.drop(target)
                context.complete_action(step.step_id)
                return object()

            return prepared_command(
                run,
                (step,),
                executor=executor or SubprocessExecutor(),
            )

        monkeypatch.setattr(
            EnvironmentResource, "_remove_copy_database_command", capture_direct_drop
        )

    @pytest.fixture(autouse=True)
    def _fake_psql(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        executable = tmp_path / "psql"
        marker = tmp_path / "psql-called"
        executable.write_text(
            "#!/bin/sh\n"
            f'if [ -f "{marker}" ]; then\n'
            "  printf '1\\n'\n"
            "else\n"
            f'  : > "{marker}"\n'
            "fi\n"
        )
        executable.chmod(0o755)
        monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _name: str(executable)
        )

    def test_socket_cluster_copy_is_removable_without_restore_audit(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        instance = _copy_instance(target_exists=False)
        instance.config.db_host = None
        env = _checkout_copy(env_client, project_manifest, fake_python, "feat/rm-socket", instance)

        env_client.environments.remove(env)

        assert env_client.environments.get(str(env.id)).state is EnvironmentState.REMOVED

    @pytest.mark.parametrize(
        ("target_present", "rollback_present", "expect_rollback"),
        [(True, False, False), (False, True, True), (True, True, True)],
    )
    def test_preflight_replays_each_retained_replacement_topology(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        target_present: bool,
        rollback_present: bool,
        expect_rollback: bool,
    ) -> None:
        env = _checkout_copy(
            env_client, project_manifest, fake_python, "feat/rm-retained-topology", _copy_instance()
        )
        from odoo_instance_sdk.internal.odoo_config import parse_odoo_config

        config = parse_odoo_config(Path(env.generated_config_path))
        data_dir = Path(config["data_dir"])
        rollback = f"copy_target_odcli_rb_{env.id.hex[:20]}"
        rollback_path = data_dir / "filestore" / rollback
        if not target_present:
            import shutil

            shutil.rmtree(data_dir / "filestore" / "copy_target", ignore_errors=True)
        if rollback_present:
            rollback_path.mkdir(parents=True)
        retained = {
            "backup_id": str(env.backup_id),
            "previous_backup_id": str(env.backup_id),
            "target_database": "copy_target",
            "rollback_database": rollback,
            "target_present": target_present,
            "rollback_present": rollback_present,
            "rollback_filestore_present": rollback_present,
            "published": False,
        }
        env_client.get_catalog().update_environment_state(
            str(env.id),
            EnvironmentState.CLEANUP_FAILED.value,
            last_error=f"copy replacement cleanup_failed; retained={json.dumps(retained)}; stale",
        )
        selected = env_client.environments.get(str(env.id))
        plan = env_client.environments._preflight_remove(env_client.get_catalog(), selected)

        assert plan is not None
        assert (plan.rollback_database is not None) is expect_rollback

    @pytest.mark.serial
    def test_copy_remove_ignores_unrelated_http_port_occupant(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        free_port = int(probe.getsockname()[1])
        probe.close()
        env = _checkout_copy(
            env_client,
            project_manifest,
            fake_python,
            "feat/rm-copy-port-occupant",
            _copy_instance(target_exists=False),
            http_port=free_port,
        )
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind((env.http_interface, env.http_port))
        listener.listen()
        try:
            env_client.environments.remove(env)
        finally:
            listener.close()

        assert env_client.environments.get(str(env.id)).state is EnvironmentState.REMOVED

    def test_live_owned_runtime_fails_closed_before_cleanup(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        env = _checkout_copy(
            env_client,
            project_manifest,
            fake_python,
            "feat/rm-live-runtime",
            _copy_instance(),
        )
        env_client.get_catalog().upsert_environment_runtime(
            str(env.id),
            root_pid=1234,
            create_time=1.0,
            started_at="2026-01-01T00:00:00",
            checkout_branch=env.branch,
            commit_sha="abc",
            http_url=f"http://{env.http_interface}:{env.http_port}",
            http_port=env.http_port,
            database_name="copy_target",
        )

        with pytest.raises(EnvironmentConflictError, match="active owned runtime"):
            env_client.environments.remove(env)

        assert env_client.environments.get(str(env.id)).state is EnvironmentState.READY
        assert Path(env.generated_config_path).is_file()

    def test_cluster_mismatch_fails_closed_without_destructive_calls(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        instance = _copy_instance()
        env = _checkout_copy(
            env_client, project_manifest, fake_python, "feat/rm-cluster-mismatch", instance
        )
        catalog = env_client.get_catalog()
        catalog.upsert_copy_journal(
            str(env.id),
            target_database="copy_target",
            db_host="other-cluster",
            db_port=5432,
            db_user="odoo",
            backup_id=str(env.backup_id),
            stage=CopyJournalStage.RESTORED,
        )

        with pytest.raises(EnvironmentConflictError, match="disagree"):
            env_client.environments.remove(env)

        instance.databases.drop.assert_not_called()
        assert Path(env.generated_config_path).is_file()
        assert catalog.get_copy_journal(str(env.id))["db_host"] == "other-cluster"  # type: ignore[index]

    def test_restore_pending_requires_manual_reconciliation_after_catalog_reopen(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        instance = _copy_instance()
        env = _checkout_copy(
            env_client, project_manifest, fake_python, "feat/rm-pending-reopen", instance
        )
        catalog = env_client.get_catalog()
        catalog.upsert_copy_journal(
            str(env.id),
            target_database="copy_target",
            db_host="localhost",
            db_port=5432,
            db_user="odoo",
            backup_id=str(env.backup_id),
            stage=CopyJournalStage.RESTORE_PENDING,
        )
        # Simulate a fresh CLI process: recovery must derive ownership from
        # SQLite journal state rather than process-local checkout state.
        catalog.close()
        env_client._catalog = None
        with pytest.raises(EnvironmentConflictError, match="manual reconciliation"):
            env_client.environments.remove(str(env.id))

        assert env_client.environments.get(str(env.id)).state is EnvironmentState.CLEANUP_FAILED
        assert Path(env.generated_config_path).is_file()
        assert instance.databases.drop.call_count == 0
        backup_row = env_client.get_catalog().get_by_id(str(env.backup_id))
        assert backup_row is not None and backup_row["state"] == "available"

    def test_backed_up_recovers_after_catalog_reopen_without_database_drop(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        instance = _copy_instance()
        env = _checkout_copy(
            env_client, project_manifest, fake_python, "feat/rm-backed-up-reopen", instance
        )
        catalog = env_client.get_catalog()
        catalog.upsert_copy_journal(
            str(env.id),
            target_database="copy_target",
            db_host="localhost",
            db_port=5432,
            db_user="odoo",
            backup_id=str(env.backup_id),
            stage=CopyJournalStage.BACKED_UP,
        )
        catalog.close()
        env_client._catalog = None

        env_client.environments.remove(str(env.id))

        instance.databases.drop.assert_not_called()
        assert env_client.environments.get(str(env.id)).state is EnvironmentState.REMOVED

    def test_restored_missing_config_fails_closed_with_evidence(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        instance = _copy_instance()
        env = _checkout_copy(
            env_client, project_manifest, fake_python, "feat/rm-restored-missing", instance
        )
        Path(env.generated_config_path).unlink()

        with pytest.raises(EnvironmentConflictError, match="copy environment config is missing"):
            env_client.environments.remove(env)

        journal = env_client.get_catalog().get_copy_journal(str(env.id))
        assert journal is not None and journal["stage"] == "restored"
        assert env_client.get_catalog().get_by_id(str(env.backup_id)) is not None
        assert Path(env.worktree_path).is_dir()

    def test_dropped_missing_config_deletes_backup_then_files(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        instance = _copy_instance()
        env = _checkout_copy(env_client, project_manifest, fake_python, "feat/rm-dropped", instance)
        catalog = env_client.get_catalog()
        catalog.upsert_copy_journal(
            str(env.id),
            target_database="copy_target",
            db_host="localhost",
            db_port=5432,
            db_user="odoo",
            backup_id=str(env.backup_id),
            stage=CopyJournalStage.DROPPED,
            backup_ownership="owned",
        )
        Path(env.generated_config_path).unlink()
        delete = MagicMock()
        from odoo_instance_sdk.resources.backup import BackupResource

        monkeypatch.setattr(BackupResource, "delete_owned", delete)

        env_client.environments.remove(env)

        assert delete.call_count == 1
        assert not Path(env.worktree_path).exists()
        assert env_client.environments.get(str(env.id)).state is EnvironmentState.REMOVED

    @pytest.mark.parametrize("ownership", ["borrowed", "unknown"])
    def test_copy_remove_preserves_non_owned_backup_and_journal_stage(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        ownership: str,
    ) -> None:
        instance = _copy_instance()
        env = _checkout_copy(
            env_client, project_manifest, fake_python, f"feat/rm-{ownership}", instance
        )
        catalog = env_client.get_catalog()
        catalog.upsert_copy_journal(
            str(env.id),
            target_database="copy_target",
            db_host="localhost",
            db_port=5432,
            db_user="odoo",
            backup_id=str(env.backup_id),
            stage=CopyJournalStage.DROPPED,
            backup_ownership=ownership,
        )
        Path(env.generated_config_path).unlink()
        backup_path = Path(instance.databases.backup.return_value.path)

        env_client.environments.remove(env)

        row = catalog.get_by_id(str(env.backup_id))
        journal = catalog.get_copy_journal(str(env.id))
        assert backup_path.is_file()
        assert row is not None and row["state"] == "available"
        assert journal is not None and journal["stage"] == CopyJournalStage.DROPPED.value

    def test_owned_copy_remove_deletes_backup_and_marks_terminal_stage(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        instance = _copy_instance()
        env = _checkout_copy(env_client, project_manifest, fake_python, "feat/rm-owned", instance)
        catalog = env_client.get_catalog()
        catalog.upsert_copy_journal(
            str(env.id),
            target_database="copy_target",
            db_host="localhost",
            db_port=5432,
            db_user="odoo",
            backup_id=str(env.backup_id),
            stage=CopyJournalStage.DROPPED,
            backup_ownership="owned",
        )
        Path(env.generated_config_path).unlink()
        backup_path = Path(instance.databases.backup.return_value.path)

        env_client.environments.remove(env)

        row = catalog.get_by_id(str(env.backup_id))
        journal = catalog.get_copy_journal(str(env.id))
        assert not backup_path.exists()
        assert row is not None and row["state"] == "deleted"
        assert journal is not None and journal["stage"] == CopyJournalStage.BACKUP_DELETED.value

    def test_backup_deleted_missing_config_removes_files_only(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        instance = _copy_instance()
        env = _checkout_copy(
            env_client, project_manifest, fake_python, "feat/rm-backup-deleted", instance
        )
        catalog = env_client.get_catalog()
        catalog.upsert_copy_journal(
            str(env.id),
            target_database="copy_target",
            db_host="localhost",
            db_port=5432,
            db_user="odoo",
            backup_id=str(env.backup_id),
            stage=CopyJournalStage.BACKUP_DELETED,
        )
        Path(env.generated_config_path).unlink()
        delete = MagicMock()
        from odoo_instance_sdk.resources.backup import BackupResource

        monkeypatch.setattr(BackupResource, "delete_owned", delete)

        env_client.environments.remove(env)

        delete.assert_not_called()
        assert not Path(env.worktree_path).exists()
        assert env_client.environments.get(str(env.id)).state is EnvironmentState.REMOVED

    def test_drop_failure_preserves_config_and_backup(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        instance = _copy_instance()
        env = _checkout_copy(
            env_client, project_manifest, fake_python, "feat/rm-drop-fails", instance
        )
        instance.databases.drop.side_effect = OSError("drop refused")

        with pytest.raises(EnvironmentConflictError, match="drop refused"):
            env_client.environments.remove(env)

        assert Path(env.generated_config_path).is_file()
        assert Path(env.worktree_path).is_dir()
        backup_row = env_client.get_catalog().get_by_id(str(env.backup_id))
        assert backup_row is not None and backup_row["state"] == "available"
        assert env_client.environments.get(str(env.id)).state is EnvironmentState.CLEANUP_FAILED

    def test_cleanup_failed_retry_reuses_retained_owned_artifacts(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        instance = _copy_instance()
        env = _checkout_copy(env_client, project_manifest, fake_python, "feat/rm-retry", instance)
        instance.databases.drop.side_effect = OSError("temporary drop refusal")

        with pytest.raises(EnvironmentConflictError, match="temporary drop refusal"):
            env_client.environments.remove(env)
        assert env_client.environments.get(str(env.id)).state is EnvironmentState.CLEANUP_FAILED

        instance.databases.drop.side_effect = None
        env_client.environments.remove(env)

        assert env_client.environments.get(str(env.id)).state is EnvironmentState.REMOVED
        assert not Path(env.generated_config_path).exists()

    def test_successful_copy_remove_orders_database_backup_then_files(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        executable = tmp_path / "psql"
        marker = tmp_path / "psql-called"
        executable.write_text(
            f'#!/bin/sh\nif [ -f "{marker}" ]; then printf \'1\\n\'; else : > "{marker}"; fi\n'
        )
        executable.chmod(0o755)
        monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
        instance = _copy_instance(target_exists=True)
        env = _checkout_copy(env_client, project_manifest, fake_python, "feat/rm-order", instance)
        events: list[str] = []
        instance.databases.drop.side_effect = lambda _target: events.append("drop")

        def target_is_absent(_target: str) -> bool:
            events.append("exists")
            return False

        instance.databases.exists.side_effect = target_is_absent
        delete = MagicMock(side_effect=lambda _backup: events.append("backup"))
        from odoo_instance_sdk.resources.backup import BackupResource

        monkeypatch.setattr(BackupResource, "delete_owned", delete)

        env_client.environments.remove(env)

        assert events[:2] == ["exists", "backup"]
        assert not Path(env.generated_config_path).exists()
        assert not Path(env.worktree_path).exists()


class TestRetainedReplacementRemove:
    @pytest.fixture(autouse=True)
    def _fake_psql(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        executable = tmp_path / "psql"
        marker = tmp_path / "psql-called"
        executable.write_text(
            "#!/bin/sh\n"
            f'if [ -f "{marker}" ]; then\n'
            "  printf '1\\n'\n"
            "else\n"
            f'  : > "{marker}"\n'
            "fi\n"
        )
        executable.chmod(0o755)
        monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _name: str(executable)
        )

    @pytest.mark.parametrize(
        ("target_present", "rollback_present", "tamper"),
        [
            (True, False, False),
            (False, True, False),
            (True, True, False),
            (True, False, True),
            (False, True, True),
            (True, True, True),
        ],
    )
    def test_environment_remove_replays_each_retained_topology(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
        target_present: bool,
        rollback_present: bool,
        tamper: bool,
    ) -> None:
        instance = _copy_instance(target_exists=True)
        env = _checkout_copy(env_client, project_manifest, fake_python, "feat/rm-both", instance)
        from odoo_instance_sdk.internal.odoo_config import parse_odoo_config

        data_dir = Path(parse_odoo_config(Path(env.generated_config_path))["data_dir"])
        rollback = f"copy_target_odcli_rb_{env.id.hex[:20]}"
        if not target_present:
            shutil.rmtree(data_dir / "filestore" / "copy_target", ignore_errors=True)
        if rollback_present:
            (data_dir / "filestore" / rollback).mkdir()
        retained = {
            "backup_id": str(env.backup_id),
            "previous_backup_id": str(env.backup_id),
            "target_database": "copy_target",
            "rollback_database": rollback,
            "target_present": target_present,
            "rollback_present": rollback_present,
            "rollback_filestore_present": rollback_present,
            "published": False,
        }
        env_client.get_catalog().update_environment_state(
            str(env.id),
            EnvironmentState.CLEANUP_FAILED.value,
            last_error=f"copy replacement cleanup_failed; retained={json.dumps(retained)}; stale",
        )
        dropped: list[str] = []
        built: list[str] = []

        class Built:
            def __init__(self, database: str) -> None:
                built.append(database)
                step = PreparedAction(
                    step_id=f"test.drop.{database}",
                    action="drop",
                    description="test guarded drop",
                    mutating=True,
                )

                def run(context: Any) -> object:
                    context.action(step.step_id)
                    dropped.append(database)
                    context.complete_action(step.step_id)
                    return object()

                self._command = prepared_command(run, (step,))

            def _prepared(self) -> Any:
                return self._command

        monkeypatch.setattr(
            "odoo_instance_sdk.internal.pg.drop.build_database_drop_command",
            lambda _instance, _root, database, **_kwargs: Built(database),
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project",
            lambda _root: MagicMock(),
        )

        selected = env_client.environments.get(str(env.id))
        assert selected.last_error is not None
        assert (
            env_client.environments._remove_copy_database_command(selected, executor=None)
            is not None
        )
        expected = [rollback] if not target_present else ["copy_target"]
        if target_present and rollback_present:
            expected = ["copy_target", rollback]
        assert built == expected
        assert (
            env_client.environments._preflight_remove(env_client.get_catalog(), selected)
            is not None
        )
        built.clear()
        executor = RecordingExecutor()
        command = env_client.environments.remove_command(selected, executor=executor)
        if tamper:
            changed = dict(retained)
            changed["target_present"] = not target_present
            env_client.get_catalog().update_environment_state(
                str(env.id),
                EnvironmentState.CLEANUP_FAILED.value,
                last_error=f"copy replacement cleanup_failed; retained={json.dumps(changed)}; stale",
            )
            with pytest.raises(EnvironmentConflictError, match="retained replacement evidence"):
                command.run()
            assert dropped == []
            assert env_client.environments.get(str(env.id)).state is EnvironmentState.CLEANUP_FAILED
            return

        command.run()

        assert built == expected
        assert dropped == expected
        assert env_client.environments.get(str(env.id)).state is EnvironmentState.REMOVED
        assert not (data_dir / "filestore" / rollback).exists()


def _activate_managed_cluster(
    env_client: OdooClient, project_manifest: Path
) -> tuple[str, PostgresCluster]:
    from odoo_instance_sdk.internal.postgres_compose import compose_volume_name
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    manifest = project_manifest / ".odcli" / "project.toml"
    manifest.write_text(
        manifest.read_text()
        + '\n[postgres]\nmode = "compose"\nimage = "postgres:16"\nport = 5432\n'
    )
    cluster = PostgresCluster.from_project(project_manifest)
    catalog = env_client.get_catalog()
    project_id = cluster._project_id
    claim = catalog._ensure_postgres_cluster_pending(
        project_id,
        cluster.compose_project_name,
        compose_volume_name(project_id),
    )
    active = catalog._activate_postgres_cluster(
        claim.cluster_id,
        project_id,
        cluster.compose_project_name,
        compose_volume_name(project_id),
    )
    return str(active.cluster_id), cluster


class TestCopyRestoreClusterIdentity:
    @pytest.fixture(autouse=True)
    def _fake_psql(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        executable = tmp_path / "psql"
        marker = tmp_path / "psql-called"
        executable.write_text(
            "#!/bin/sh\n"
            'if [ "${ODCLI_TEST_PSQL_FAILURE:-}" = "1" ]; then\n'
            "  printf 'probe failed\\n' >&2\n"
            "  exit 1\n"
            "fi\n"
            'if [ "${ODCLI_TEST_PSQL_EXISTS_BEFORE:-}" = "1" ]; then\n'
            "  printf '1\\n'\n"
            "  exit 0\n"
            "fi\n"
            f'if [ -f "{marker}" ]; then\n'
            "  printf '1\\n'\n"
            "else\n"
            f'  : > "{marker}"\n'
            "fi\n"
        )
        executable.chmod(0o755)
        monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    def test_copy_restore_binds_cluster_id_and_removes(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from odoo_instance_sdk.config import InstanceConfig
        from odoo_instance_sdk.resources.instance import InstanceFactory

        cluster_id, cluster = _activate_managed_cluster(env_client, project_manifest)
        catalog = env_client.get_catalog()
        db_host = cluster.endpoint_host
        db_port = cluster.endpoint_port

        backup = Backup(
            id=uuid.uuid4(),
            source_base_url="http://127.0.0.1:8069",
            database_name="comerta",
            format=BackupFormat.ZIP,
            filestore_requested=True,
            path=str(get_backups_dir() / f"{uuid.uuid4()}.zip"),
            filename="comerta.zip",
            size_bytes=1,
            sha256="a" * 64,
            downloaded_at=datetime.now(UTC),
        )
        Path(backup.path).write_bytes(b"backup")
        _record_backup(env_client, backup)

        instance = env_client.instance("http://127.0.0.1:8069", master_password="admin")
        object.__setattr__(
            instance,
            "config",
            InstanceConfig(
                base_url="http://127.0.0.1:8069",
                master_password="admin",
                db_host=db_host,
                db_port=db_port,
                db_user="odoo",
                db_password="secret",
                configured_database_names=("comerta",),
            ),
        )

        captured_cluster_ids: list[str | None] = []
        original_record_restore = catalog.record_restore

        def capture_restore(
            db_host: str | None,
            db_port: int,
            database_name: str,
            backup_id: str,
            *,
            cluster_id: str | None = None,
            data_directory: str | Path | None = None,
        ) -> None:
            captured_cluster_ids.append(cluster_id)
            original_record_restore(
                db_host,
                db_port,
                database_name,
                backup_id,
                cluster_id=cluster_id,
                data_directory=data_directory,
            )

        catalog.record_restore = capture_restore  # type: ignore[assignment]

        from tests.unit.resources.test_database_resource import _mock_http

        with (
            patch.object(
                instance.databases,
                "list",
                return_value=(Database(name="comerta", backup=NoBackup()),),
            ),
            patch.object(instance.databases, "names", return_value=("comerta",)),
            patch.object(instance.databases, "backup", return_value=backup),
            patch(
                "odoo_instance_sdk.internal.transport.factory.open_odoo_http_client",
                return_value=_mock_http({"result": True}),
            ),
        ):
            InstanceFactory.from_config = MagicMock(return_value=instance)  # type: ignore[method-assign]
            env = env_client.environments.checkout(
                project_manifest,
                "feat/copy-cluster-id",
                options=EnvironmentCheckoutOptions(
                    python=str(fake_python),
                    db_mode=EnvironmentDatabaseMode.COPY,
                    target_database="copy_target",
                    source_database="comerta",
                ),
            )

        assert env.state is EnvironmentState.READY
        assert captured_cluster_ids == [cluster_id]

        restore_row = catalog._conn.execute(
            "SELECT cluster_id FROM restores WHERE database_name=? AND backup_id=?",
            ("copy_target", str(backup.id)),
        ).fetchone()
        assert restore_row is not None
        assert str(restore_row["cluster_id"]) == cluster_id

        from odoo_instance_sdk.resources.postgres import PostgresCluster
        from tests.unit.internal.test_postgres_drop import _executor

        monkeypatch.setattr("odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/psql")
        monkeypatch.setattr(PostgresCluster, "_inspect_cluster_volume", lambda *_a, **_k: True)

        selected = env_client.environments.get(str(env.id))
        drop_executor = _executor(exists=False)
        subprocess_executor = SubprocessExecutor()

        class _HybridRemoveExecutor:
            def execute(
                self,
                step: PreparedStep,
                *,
                observer: StepObserver | None = None,
                observe_output: bool = False,
            ) -> ProcessResultLike:
                if step.step_id.startswith("database.drop"):
                    return drop_executor.execute(
                        step, observer=observer, observe_output=observe_output
                    )
                return subprocess_executor.execute(
                    step, observer=observer, observe_output=observe_output
                )

            def spawn(
                self,
                step: PreparedStep,
                *,
                observer: StepObserver | None = None,
                observe_output: bool = False,
            ) -> ProcessHandle:
                if step.step_id.startswith("database.drop"):
                    return drop_executor.spawn(
                        step, observer=observer, observe_output=observe_output
                    )
                return subprocess_executor.spawn(
                    step, observer=observer, observe_output=observe_output
                )

        command = env_client.environments.remove_command(selected, executor=_HybridRemoveExecutor())

        step_ids = [step.step_id for step in command.plan.steps]
        assert "database.drop" in step_ids
        assert any(step_id.startswith("database.drop.") for step_id in step_ids)

        command.run()
        assert env_client.environments.get(str(env.id)).state is EnvironmentState.REMOVED
        assert not Path(env.generated_config_path).exists()
        assert not Path(env.worktree_path).exists()
        backup_row = catalog.get_by_id(str(env.backup_id))
        assert backup_row is None or backup_row["state"] != "available"

    def test_drop_plan_failure_reports_sanitized_reason(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from odoo_instance_sdk.config import InstanceConfig
        from odoo_instance_sdk.resources.instance import InstanceFactory

        cluster_id, cluster = _activate_managed_cluster(env_client, project_manifest)
        catalog = env_client.get_catalog()
        db_host = cluster.endpoint_host
        db_port = cluster.endpoint_port

        backup = Backup(
            id=uuid.uuid4(),
            source_base_url="http://127.0.0.1:8069",
            database_name="comerta",
            format=BackupFormat.ZIP,
            filestore_requested=True,
            path=str(get_backups_dir() / f"{uuid.uuid4()}.zip"),
            filename="comerta.zip",
            size_bytes=1,
            sha256="a" * 64,
            downloaded_at=datetime.now(UTC),
        )
        Path(backup.path).write_bytes(b"backup")
        _record_backup(env_client, backup)

        instance = env_client.instance("http://127.0.0.1:8069", master_password="admin")
        object.__setattr__(
            instance,
            "config",
            InstanceConfig(
                base_url="http://127.0.0.1:8069",
                master_password="admin",
                db_host=db_host,
                db_port=db_port,
                db_user="odoo",
                db_password="secret",
                configured_database_names=("comerta",),
            ),
        )

        captured_cluster_ids: list[str | None] = []
        original_record_restore = catalog.record_restore

        def capture_restore(
            db_host: str | None,
            db_port: int,
            database_name: str,
            backup_id: str,
            *,
            cluster_id: str | None = None,
            data_directory: str | Path | None = None,
        ) -> None:
            captured_cluster_ids.append(cluster_id)
            original_record_restore(
                db_host,
                db_port,
                database_name,
                backup_id,
                cluster_id=cluster_id,
                data_directory=data_directory,
            )

        catalog.record_restore = capture_restore  # type: ignore[assignment]

        from tests.unit.resources.test_database_resource import _mock_http

        with (
            patch.object(
                instance.databases,
                "list",
                return_value=(Database(name="comerta", backup=NoBackup()),),
            ),
            patch.object(instance.databases, "names", return_value=("comerta",)),
            patch.object(instance.databases, "backup", return_value=backup),
            patch(
                "odoo_instance_sdk.internal.transport.factory.open_odoo_http_client",
                return_value=_mock_http({"result": True}),
            ),
        ):
            InstanceFactory.from_config = MagicMock(return_value=instance)  # type: ignore[method-assign]
            env = env_client.environments.checkout(
                project_manifest,
                "feat/copy-drop-fail-reason",
                options=EnvironmentCheckoutOptions(
                    python=str(fake_python),
                    db_mode=EnvironmentDatabaseMode.COPY,
                    target_database="copy_target",
                    source_database="comerta",
                ),
            )

        assert captured_cluster_ids == [cluster_id]

        def fail_build(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("password=admin_passwd leaked in psql output")

        monkeypatch.setattr(
            "odoo_instance_sdk.internal.pg.drop.build_database_drop_command", fail_build
        )

        selected = env_client.environments.get(str(env.id))
        with pytest.raises(EnvironmentConflictError, match="guarded COPY database drop plan"):
            env_client.environments.remove_command(selected, executor=RecordingExecutor())

        assert env_client.environments.get(str(env.id)).state is EnvironmentState.READY


class TestEnvRemoveForceConnections:
    """Item 3: `env rm --force-connections` routes into the COPY-only drop."""

    @pytest.fixture(autouse=True)
    def _fake_psql(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        executable = tmp_path / "psql"
        marker = tmp_path / "psql-called"
        executable.write_text(
            "#!/bin/sh\n"
            f'if [ -f "{marker}" ]; then\n'
            "  printf '1\\n'\n"
            "else\n"
            f'  : > "{marker}"\n'
            "fi\n"
        )
        executable.chmod(0o755)
        monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _name: str(executable)
        )

    def _checkout_copy_env(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        instance: MagicMock,
    ) -> DevelopmentEnvironment:
        return _checkout_copy(
            env_client, project_manifest, fake_python, "feat/rm-force-conn", instance
        )

    def test_remove_command_threads_force_connections_and_env_remove_origin(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from odoo_instance_sdk.internal.pg.drop import (
            DatabaseDropSafetyError,
            DatabaseDropSession,
        )

        instance = _copy_instance(target_exists=True)
        env = self._checkout_copy_env(env_client, project_manifest, fake_python, instance)

        captured: dict[str, object] = {}

        session = DatabaseDropSession(pid=42, user="odoo")
        safety_error = DatabaseDropSafetyError(
            "database 'copy_target' has 1 active session(s); "
            "require --force-connections (or stop the environment with `odcli stop`) "
            "to terminate only target sessions",
            (session,),
        )

        def stub_build(
            _instance: object,
            _root: object,
            database: str,
            *,
            force_connections: bool,
            command_origin: str | None,
            **_kwargs: object,
        ) -> Any:
            captured["database"] = database
            captured["force_connections"] = force_connections
            captured["command_origin"] = command_origin
            from odoo_instance_sdk.execution import ExecutionPlan
            from odoo_instance_sdk.internal.proc import PreparedAction

            step = PreparedAction(
                step_id="test.copy.database.drop",
                action="drop-owned-copy-database",
                description="test direct drop",
                mutating=True,
            )

            def run(context: Any) -> object:
                context.action(step.step_id)
                context.complete_action(step.step_id)
                raise safety_error

            class StubCommand:
                def __init__(self) -> None:
                    self._prepared_cmd = prepared_command(run, (step,))
                    self.plan = ExecutionPlan(
                        steps=tuple(step.public_projection() for step in (step,))
                    )

                def _prepared(self) -> Any:
                    return self._prepared_cmd

                def run(self) -> Any:
                    return self._prepared_cmd.callback.__wrapped__()  # type: ignore[attr-defined]

            return StubCommand()

        monkeypatch.setattr(
            "odoo_instance_sdk.internal.pg.drop.build_database_drop_command", stub_build
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project",
            lambda _root: MagicMock(),
        )

        selected = env_client.environments.get(str(env.id))
        command = env_client.environments.remove_command(
            selected, force_connections=True, executor=RecordingExecutor()
        )
        with pytest.raises(EnvironmentConflictError):
            command.run()

        assert captured == {
            "database": "copy_target",
            "force_connections": True,
            "command_origin": "env-remove",
        }

    def test_remove_without_force_connections_names_flag_and_stop(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from odoo_instance_sdk.internal.pg.drop import (
            DatabaseDropSafetyError,
            DatabaseDropSession,
        )

        instance = _copy_instance(target_exists=True)
        env = self._checkout_copy_env(env_client, project_manifest, fake_python, instance)

        session = DatabaseDropSession(pid=42, user="odoo")
        safety_error = DatabaseDropSafetyError(
            "database 'copy_target' has 1 active session(s); "
            "require --force-connections (or stop the environment with `odcli stop`) "
            "to terminate only target sessions",
            (session,),
        )

        def stub_build(
            _instance: object,
            _root: object,
            database: str,
            *,
            force_connections: bool,
            command_origin: str | None,
            **_kwargs: object,
        ) -> Any:
            assert command_origin == "env-remove"
            assert force_connections is False
            from odoo_instance_sdk.execution import ExecutionPlan
            from odoo_instance_sdk.internal.proc import PreparedAction

            step = PreparedAction(
                step_id="test.copy.database.drop",
                action="drop-owned-copy-database",
                description="test direct drop",
                mutating=True,
            )

            def run(context: Any) -> object:
                context.action(step.step_id)
                context.complete_action(step.step_id)
                raise safety_error

            class StubCommand:
                def __init__(self) -> None:
                    self._prepared_cmd = prepared_command(run, (step,))
                    self.plan = ExecutionPlan(
                        steps=tuple(step.public_projection() for step in (step,))
                    )

                def _prepared(self) -> Any:
                    return self._prepared_cmd

            return StubCommand()

        monkeypatch.setattr(
            "odoo_instance_sdk.internal.pg.drop.build_database_drop_command", stub_build
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project",
            lambda _root: MagicMock(),
        )

        selected = env_client.environments.get(str(env.id))
        command = env_client.environments.remove_command(selected, executor=RecordingExecutor())
        with pytest.raises(EnvironmentConflictError) as caught:
            command.run()

        message = str(caught.value)
        assert "--force-connections" in message
        assert "odcli stop" in message
        assert "missing option" not in message
        assert env_client.environments.get(str(env.id)).state is not EnvironmentState.REMOVED

    def test_force_connections_terminate_scope_limited_to_exact_copy_database(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from odoo_instance_sdk.internal.pg.drop import _terminate_sql

        instance = _copy_instance(target_exists=True)
        env = self._checkout_copy_env(env_client, project_manifest, fake_python, instance)

        captured: dict[str, object] = {}

        def stub_build(
            _instance: object,
            _root: object,
            database: str,
            *,
            force_connections: bool,
            command_origin: str | None,
            **_kwargs: object,
        ) -> Any:
            captured["database"] = database
            captured["force_connections"] = force_connections
            captured["command_origin"] = command_origin
            from odoo_instance_sdk.execution import ExecutionPlan
            from odoo_instance_sdk.internal.proc import PreparedAction

            step = PreparedAction(
                step_id="test.copy.database.drop",
                action="drop-owned-copy-database",
                description="test direct drop",
                mutating=True,
            )

            def run(context: Any) -> object:
                context.action(step.step_id)
                context.complete_action(step.step_id)
                return None

            class StubCommand:
                def __init__(self) -> None:
                    self._prepared_cmd = prepared_command(run, (step,))
                    self.plan = ExecutionPlan(
                        steps=tuple(step.public_projection() for step in (step,))
                    )

                def _prepared(self) -> Any:
                    return self._prepared_cmd

            return StubCommand()

        monkeypatch.setattr(
            "odoo_instance_sdk.internal.pg.drop.build_database_drop_command", stub_build
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project",
            lambda _root: MagicMock(),
        )

        selected = env_client.environments.get(str(env.id))
        command = env_client.environments.remove_command(
            selected, force_connections=True, executor=RecordingExecutor()
        )
        command.run()

        assert captured == {
            "database": "copy_target",
            "force_connections": True,
            "command_origin": "env-remove",
        }
        terminate_sql = _terminate_sql("copy_target")
        assert "datname='copy_target'" in terminate_sql
        assert "pg_terminate_backend" in terminate_sql
        for protected in ("postgres", "template0", "template1", "comerta"):
            assert f"datname='{protected}'" not in terminate_sql
        assert env_client.environments.get(str(env.id)).state is EnvironmentState.REMOVED
