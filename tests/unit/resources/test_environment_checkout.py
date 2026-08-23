from __future__ import annotations

import subprocess
import textwrap
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from odoo_instance_sdk.exceptions import (
    ConfigError,
    EnvironmentConflictError,
    EnvironmentNotFoundError,
    InstanceConfigurationError,
)
from odoo_instance_sdk.models import Backup, BackupFormat, Database, NoBackup
from odoo_instance_sdk.resources.environment import (
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
    EnvironmentState,
)

if TYPE_CHECKING:
    from odoo_instance_sdk import OdooClient


@pytest.fixture(autouse=True)
def _restore_instance_factory() -> object:
    from odoo_instance_sdk.resources.instance import InstanceFactory

    original = InstanceFactory.from_config
    yield
    InstanceFactory.from_config = original  # type: ignore[method-assign]


def _copy_instance(env_client: OdooClient, *, target_exists: bool = False) -> MagicMock:
    """Return a deterministic local-Odoo boundary double for COPY checkout tests."""
    backup = Backup(
        id=uuid.uuid4(),
        source_base_url="http://127.0.0.1:8069",
        database_name="comerta",
        format=BackupFormat.ZIP,
        filestore_requested=True,
        path=str(Path("/tmp") / f"{uuid.uuid4()}.zip"),
        filename="comerta.zip",
        size_bytes=1,
        sha256="a" * 64,
        downloaded_at=datetime.now(UTC),
    )
    Path(backup.path).write_bytes(b"backup")
    instance = MagicMock()
    instance.config.db_host = "localhost"
    instance.config.db_port = 5432
    instance.config.db_user = "odoo"
    instance.databases.list.return_value = (Database(name="comerta", backup=NoBackup()),)
    instance.databases.names.return_value = ("comerta",)
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


class TestCheckoutPreflight:
    @pytest.mark.serial
    def test_auto_port_retries_after_os_reserved_default(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        import socket

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 8069))
        listener.listen()
        try:
            env = env_client.environments.checkout(
                project_manifest,
                "feat/next-port",
                options=EnvironmentCheckoutOptions(python=str(fake_python)),
            )
        finally:
            listener.close()
        assert env.http_port == 8070

    @pytest.mark.serial
    def test_second_branch_skips_generated_http_port(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python))
        first = env_client.environments.checkout(project_manifest, "feat/port-a", options=opts)
        generated = Path(first.generated_config_path)
        generated.write_text("[options]\nhttp_interface = 127.0.0.1\nhttp_port = 8077\n")
        second = env_client.environments.checkout(project_manifest, "feat/port-b", options=opts)
        assert second.http_port != 8077

    def test_missing_venv_interpreter_hints_create_venv(
        self, env_client: OdooClient, project_manifest: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python="/nonexistent/python")
        with pytest.raises((InstanceConfigurationError, ConfigError)):
            env_client.environments.checkout(project_manifest, "feat/x", options=opts)

    def test_active_environment_conflict(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python), db_mode=EnvironmentDatabaseMode.SHARED
        )
        env_client.environments.checkout(project_manifest, "feat/x", options=opts)
        with pytest.raises(EnvironmentConflictError):
            env_client.environments.checkout(project_manifest, "feat/x", options=opts)

    def test_multiple_db_names_requires_source_db(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path, git_repo: Path
    ) -> None:
        cfg = git_repo / "odoo.conf"
        cfg.write_text(
            "[options]\ndb_name = comerta,test\nhttp_interface = 127.0.0.1\nhttp_port = 8069\n"
        )
        manifest = project_manifest / ".odcli" / "project.toml"
        manifest.write_text(
            textwrap.dedent(f"""\
            [project]
            odoo_bin = "/usr/bin/odoo"
            python = "{fake_python}"
            source_config = "odoo.conf"
        """)
        )
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python), db_mode=EnvironmentDatabaseMode.SHARED, config_path=cfg
        )
        with pytest.raises(ConfigError):
            env_client.environments.checkout(project_manifest, "feat/multi", options=opts)

    def test_dirty_main_does_not_block(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path, git_repo: Path
    ) -> None:
        (git_repo / "dirty.txt").write_text("uncommitted")
        subprocess.run(["git", "add", "dirty.txt"], cwd=git_repo, check=True, capture_output=True)
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python), db_mode=EnvironmentDatabaseMode.SHARED
        )
        env = env_client.environments.checkout(project_manifest, "feat/dirty", options=opts)
        assert env.state == EnvironmentState.READY


class TestCheckoutShared:
    def test_shared_checkout_success(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path, source_config: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
            config_path=source_config,
        )
        env = env_client.environments.checkout(project_manifest, "feat/shared", options=opts)
        assert env.state == EnvironmentState.READY
        assert env.db_mode == EnvironmentDatabaseMode.SHARED
        assert env.source_db_name == "comerta"
        assert env.target_db_name is None
        assert env.backup_id is None
        assert Path(env.worktree_path).is_dir()
        assert Path(env.generated_config_path).is_file()

    def test_generated_config_has_correct_db_name(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
        )
        env = env_client.environments.checkout(project_manifest, "feat/cfg", options=opts)
        gen_cfg = Path(env.generated_config_path)
        content = gen_cfg.read_text()
        assert "comerta" in content
        assert f"http_port = {env.http_port}" in content

    def test_shared_remove_does_not_drop_source_db(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
        )
        env = env_client.environments.checkout(project_manifest, "feat/rm", options=opts)
        env_client.environments.remove(env)
        removed = env_client.environments.get(str(env.id))
        assert removed.state == EnvironmentState.REMOVED


class TestCheckoutCopy:
    def _checkout_copy(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        branch: str,
        instance: MagicMock,
    ) -> None:
        from odoo_instance_sdk.resources.instance import InstanceFactory

        InstanceFactory.from_config = MagicMock(return_value=instance)  # type: ignore[method-assign]
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.COPY,
            target_database="copy_target",
        )
        env_client.environments.checkout(project_manifest, branch, options=opts)

    def test_copy_success_records_restored_journal(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        instance = _copy_instance(env_client, target_exists=True)
        _record_backup(env_client, instance.databases.backup.return_value)

        self._checkout_copy(
            env_client, project_manifest, fake_python, "feat/copy-success", instance
        )

        env = env_client.environments.list(project=project_manifest)[0]
        journal = env_client.get_catalog().get_copy_journal(str(env.id))
        assert env.state is EnvironmentState.READY
        assert journal is not None and journal["stage"] == "restored"
        assert env.backup_id == instance.databases.backup.return_value.id

    def test_copy_restore_creates_exactly_one_restore_audit_record(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        instance = _copy_instance(env_client, target_exists=True)
        backup = instance.databases.backup.return_value
        _record_backup(env_client, backup)

        def restore_with_database_audit(*_args: object, **_kwargs: object) -> None:
            env_client.get_catalog().record_restore(
                "localhost", 5432, "copy_target", str(backup.id)
            )

        instance.databases.restore.side_effect = restore_with_database_audit
        self._checkout_copy(
            env_client, project_manifest, fake_python, "feat/copy-one-audit", instance
        )

        catalog = env_client.get_catalog()
        rows = catalog._conn.execute(
            "SELECT (SELECT COUNT(*) FROM restores WHERE database_name = ? AND backup_id = ?), "
            "(SELECT COUNT(*) FROM database_events WHERE database_name = ? AND backup_id = ? "
            "AND event_type = 'restored')",
            ("copy_target", str(backup.id), "copy_target", str(backup.id)),
        ).fetchone()
        assert rows is not None and tuple(rows) == (1, 1)

    def test_copy_existing_target_rolls_back_artifacts(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        instance = _copy_instance(env_client)
        instance.databases.list.return_value = (
            Database(name="comerta", backup=NoBackup()),
            Database(name="copy_target", backup=NoBackup()),
        )
        instance.databases.names.return_value = ("comerta", "copy_target")

        with pytest.raises(Exception, match="already exists"):
            self._checkout_copy(
                env_client, project_manifest, fake_python, "feat/copy-existing", instance
            )

        # Target ownership is rejected before a catalog row, worktree, config,
        # venv, or provisioning lock can be created.
        from odoo_instance_sdk.internal.paths import get_catalog_path

        assert not get_catalog_path().exists()
        assert env_client.environments.list(project=project_manifest) == []

    def test_copy_unavailable_source_rolls_back_artifacts(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        instance = _copy_instance(env_client)
        instance.databases.list.side_effect = OSError("offline")
        instance.databases.names.side_effect = OSError("offline")

        with pytest.raises(InstanceConfigurationError, match="unavailable"):
            self._checkout_copy(
                env_client, project_manifest, fake_python, "feat/copy-offline", instance
            )

        # Endpoint availability is also a COPY precondition, not a failed
        # partially-created environment.
        from odoo_instance_sdk.internal.paths import get_catalog_path

        assert not get_catalog_path().exists()
        assert env_client.environments.list(project=project_manifest) == []

    def test_copy_backup_failure_rolls_back_artifacts(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        instance = _copy_instance(env_client)
        instance.databases.backup.side_effect = OSError("backup failed")

        with pytest.raises(OSError, match="backup failed"):
            self._checkout_copy(
                env_client, project_manifest, fake_python, "feat/copy-backup-fail", instance
            )

        env = env_client.environments.list(project=project_manifest)[0]
        assert env.state is EnvironmentState.FAILED
        assert not Path(env.worktree_path).exists()
        assert not Path(env.generated_config_path).exists()

    def test_copy_restore_failure_deletes_backup_then_artifacts(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        instance = _copy_instance(env_client)
        _record_backup(env_client, instance.databases.backup.return_value)
        instance.databases.restore.side_effect = OSError("restore failed")
        instance.databases.exists.return_value = False

        with pytest.raises(OSError, match="restore failed"):
            self._checkout_copy(
                env_client, project_manifest, fake_python, "feat/copy-restore-fail", instance
            )

        env = env_client.environments.list(project=project_manifest)[0]
        assert env.state is EnvironmentState.FAILED
        assert not Path(env.worktree_path).exists()
        assert not Path(env.generated_config_path).exists()
        backup_row = env_client.get_catalog().get_by_id(
            str(instance.databases.backup.return_value.id)
        )
        assert backup_row is not None and backup_row["state"] == "deleted"

    def test_restore_side_effect_then_exception_drops_exact_target_before_cleanup(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        instance = _copy_instance(env_client)
        _record_backup(env_client, instance.databases.backup.return_value)
        exists = {"target": False}

        def restore_then_raise(*_args: object, **_kwargs: object) -> None:
            exists["target"] = True
            raise OSError("restore transport failed")

        instance.databases.restore.side_effect = restore_then_raise
        instance.databases.exists.side_effect = lambda _target: exists["target"]
        instance.databases.drop.side_effect = lambda _target: exists.__setitem__("target", False)

        with pytest.raises(OSError, match="restore transport failed"):
            self._checkout_copy(
                env_client, project_manifest, fake_python, "feat/copy-uncertain", instance
            )

        assert instance.databases.drop.call_args.args == ("copy_target",)
        assert not exists["target"]
        env = env_client.environments.list(project=project_manifest)[0]
        assert env.state is EnvironmentState.FAILED
        assert not Path(env.generated_config_path).exists()

    def test_restore_postcheck_is_owned_by_database_resource(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        instance = _copy_instance(env_client)
        _record_backup(env_client, instance.databases.backup.return_value)
        self._checkout_copy(
            env_client, project_manifest, fake_python, "feat/copy-postcheck-error", instance
        )
        # Environment checkout does not duplicate DatabaseResource.restore's
        # canonical postcondition/audit logic.
        instance.databases.exists.assert_not_called()

    def test_restore_record_failure_reconciles_target_before_cleanup(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        instance = _copy_instance(env_client, target_exists=True)
        _record_backup(env_client, instance.databases.backup.return_value)
        exists = {"target": True}
        instance.databases.exists.side_effect = lambda _target: exists["target"]
        instance.databases.drop.side_effect = lambda _target: exists.__setitem__("target", False)

        def restore_with_audit(*_args: object, **_kwargs: object) -> None:
            env_client.get_catalog().record_restore(
                "localhost", 5432, "copy_target", str(instance.databases.backup.return_value.id)
            )

        instance.databases.restore.side_effect = restore_with_audit
        monkeypatch.setattr(
            "odoo_instance_sdk.storage.backup_catalog.BackupCatalog.record_restore",
            lambda *_args: (_ for _ in ()).throw(OSError("catalog unavailable")),
        )

        with pytest.raises(OSError, match="catalog unavailable"):
            self._checkout_copy(
                env_client, project_manifest, fake_python, "feat/copy-record-error", instance
            )

        assert instance.databases.drop.call_args.args == ("copy_target",)
        assert not exists["target"]

    def test_copy_does_not_duplicate_database_restore_postcondition(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        instance = _copy_instance(env_client, target_exists=False)
        _record_backup(env_client, instance.databases.backup.return_value)
        instance.databases.exists.return_value = False

        self._checkout_copy(
            env_client, project_manifest, fake_python, "feat/copy-postcondition", instance
        )
        instance.databases.exists.assert_not_called()


class TestCheckoutDryRun:
    def test_dry_run_nothing_created(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
        )
        env = env_client.environments._plan_checkout(project_manifest, "feat/dry", options=opts)
        assert not env.worktree.exists()
        assert not env.generated_config.exists()


class TestWorktreeBranchRules:
    def test_existing_local_branch(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path, git_repo: Path
    ) -> None:
        subprocess.run(
            ["git", "branch", "feat/existing"], cwd=git_repo, check=True, capture_output=True
        )
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
        )
        env = env_client.environments.checkout(project_manifest, "feat/existing", options=opts)
        assert env.state == EnvironmentState.READY

    def test_absent_branch_created_from_base(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path, git_repo: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
        )
        env = env_client.environments.checkout(project_manifest, "feat/absent", options=opts)
        assert env.state == EnvironmentState.READY
        result = subprocess.run(
            ["git", "-C", str(git_repo), "branch", "--list", "feat/absent"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "feat/absent" in result.stdout


class TestGetAndList:
    def test_loaded_project_config_is_accepted_for_list(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        env_client.environments.checkout(
            project_manifest,
            "feat/config-project",
            options=EnvironmentCheckoutOptions(python=str(fake_python)),
        )
        from odoo_instance_sdk.project import ProjectConfig

        assert len(env_client.environments.list(project=ProjectConfig.load(project_manifest))) == 1

    def test_get_by_id(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python))
        env = env_client.environments.checkout(project_manifest, "feat/get", options=opts)
        fetched = env_client.environments.get(str(env.id))
        assert fetched.id == env.id

    def test_get_not_found(self, env_client: OdooClient) -> None:
        with pytest.raises(EnvironmentNotFoundError):
            env_client.environments.get("nonexistent-id")

    def test_list_hides_removed(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        opts = EnvironmentCheckoutOptions(python=str(fake_python))
        env = env_client.environments.checkout(project_manifest, "feat/list1", options=opts)
        env_client.environments.remove(env)
        envs = env_client.environments.list(project=project_manifest)
        assert all(e.state != EnvironmentState.REMOVED for e in envs)
        envs_all = env_client.environments.list(project=project_manifest, include_removed=True)
        assert any(e.state == EnvironmentState.REMOVED for e in envs_all)
