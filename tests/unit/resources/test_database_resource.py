from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import (
    DatabaseManagerUnavailableError,
    MasterPasswordRequiredError,
    NonLocalInstanceError,
    RestoreFailedError,
)
from odoo_instance_sdk.models import Backup, BackupFormat, Database, NoBackup


def _mock_http(json_data: object) -> MagicMock:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status.return_value = None
    mock_http = MagicMock(spec=httpx.Client)
    mock_http.post.return_value = mock_resp
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_http
    return mock_cm


def _make_backup(**kw: object) -> Backup:
    return Backup(
        id=uuid.uuid4(),
        source_base_url=kw.get("source_base_url", "http://localhost:8069"),
        database_name=kw.get("database_name", "testdb"),
        format=kw.get("format", BackupFormat.ZIP),
        filestore_requested=kw.get("filestore_requested", True),
        path=kw.get("path", "/tmp/test.zip"),
        filename=kw.get("filename", "test.zip"),
        size_bytes=kw.get("size_bytes", 100),
        sha256=kw.get("sha256", "abc"),
        downloaded_at=kw.get("downloaded_at", datetime.now(UTC)),
    )


def _make_instance_with_cluster_key(client, db_host: str = "localhost", db_port: int = 5432, db_user: str | None = None, configured_names: tuple[str, ...] = ()):
    inst = client.instance("http://localhost:8069", master_password="admin")
    cfg = InstanceConfig(
        base_url="http://localhost:8069",
        master_password="admin",
        configured_database_names=configured_names,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
    )
    object.__setattr__(inst, "config", cfg)
    return inst


class TestList:
    def test_returns_database_tuple(self, instance):
        mock_cm = _mock_http({"result": ["db1", "db2", "db3"]})
        with patch("httpx.Client", return_value=mock_cm):
            dbs = instance.databases.list()
        assert isinstance(dbs, tuple)
        assert all(isinstance(db, Database) for db in dbs)
        assert tuple(db.name for db in dbs) == ("db1", "db2", "db3")
        assert all(isinstance(db.backup, NoBackup) for db in dbs)

    def test_returns_ordered(self, instance):
        mock_cm = _mock_http({"result": ["db1", "db2", "db3"]})
        with patch("httpx.Client", return_value=mock_cm):
            dbs = instance.databases.list()
        assert [db.name for db in dbs] == ["db1", "db2", "db3"]

    def test_with_cluster_key_populates_backup(self, client):
        mock_cm = _mock_http({"result": ["prod", "staging"]})
        inst = _make_instance_with_cluster_key(client)

        backup = _make_backup(database_name="prod")
        mock_catalog = MagicMock()
        mock_catalog.latest_restore.side_effect = lambda h, p, n: backup if n == "prod" else None
        mock_catalog.distinct_restored_database_names.return_value = ()

        with (
            patch("httpx.Client", return_value=mock_cm),
            patch.object(inst, "_client") as mock_client,
        ):
            mock_client.get_catalog.return_value = mock_catalog
            dbs = inst.databases.list()
        assert len(dbs) == 2
        assert dbs[0].name == "prod"
        assert dbs[0].backup == backup
        assert dbs[1].name == "staging"
        assert isinstance(dbs[1].backup, NoBackup)

    def test_reconciliation_records_dropped(self, client):
        mock_cm = _mock_http({"result": []})
        inst = _make_instance_with_cluster_key(client)

        mock_catalog = MagicMock()
        mock_catalog.distinct_restored_database_names.return_value = ("staging", "test")
        mock_catalog.latest_restore.return_value = None

        with (
            patch("httpx.Client", return_value=mock_cm),
            patch.object(inst, "_client") as mock_client,
        ):
            mock_client.get_catalog.return_value = mock_catalog
            inst.databases.list()

        assert mock_catalog.record_database_dropped.call_count == 2
        mock_catalog.record_database_dropped.assert_any_call("localhost", 5432, "staging")
        mock_catalog.record_database_dropped.assert_any_call("localhost", 5432, "test")


class TestExists:
    def test_true(self, instance):
        mock_cm = _mock_http({"result": ["mydb", "other"]})
        with patch("httpx.Client", return_value=mock_cm):
            result = instance.databases.exists("mydb")
        assert result is True

    def test_false(self, instance):
        mock_cm = _mock_http({"result": ["other"]})
        with patch("httpx.Client", return_value=mock_cm):
            result = instance.databases.exists("mydb")
        assert result is False

    def test_odoo_down_psql_confirms(self, client):
        inst = _make_instance_with_cluster_key(client, db_user="odoo")
        with (
            patch("odoo_instance_sdk.resources.database.DatabaseResource.list", side_effect=DatabaseManagerUnavailableError("down")),
            patch("odoo_instance_sdk.resources.database._verify_database_via_psql", return_value=True),
        ):
            assert inst.databases.exists("mydb") is True

    def test_odoo_down_psql_absent(self, client):
        inst = _make_instance_with_cluster_key(client, db_user="odoo")
        mock_catalog = MagicMock()
        with (
            patch("odoo_instance_sdk.resources.database.DatabaseResource.list", side_effect=DatabaseManagerUnavailableError("down")),
            patch("odoo_instance_sdk.resources.database._verify_database_via_psql", return_value=False),
            patch.object(inst, "_client") as mock_client,
        ):
            mock_client.get_catalog.return_value = mock_catalog
            assert inst.databases.exists("mydb") is False
        mock_catalog.record_database_dropped.assert_called_once_with("localhost", 5432, "mydb")

    def test_odoo_down_psql_inconclusive(self, client):
        inst = _make_instance_with_cluster_key(client, db_user="odoo")
        with (
            patch("odoo_instance_sdk.resources.database.DatabaseResource.list", side_effect=DatabaseManagerUnavailableError("down")),
            patch("odoo_instance_sdk.resources.database._verify_database_via_psql", return_value=None),
            pytest.raises(DatabaseManagerUnavailableError),
        ):
            inst.databases.exists("mydb")

    def test_odoo_down_no_cluster_key(self, instance):
        with (
            patch("odoo_instance_sdk.resources.database.DatabaseResource.list", side_effect=DatabaseManagerUnavailableError("down")),
            pytest.raises(DatabaseManagerUnavailableError),
        ):
            instance.databases.exists("mydb")

    def test_odoo_down_no_db_user(self, client):
        inst = _make_instance_with_cluster_key(client)
        with (
            patch("odoo_instance_sdk.resources.database.DatabaseResource.list", side_effect=DatabaseManagerUnavailableError("down")),
            pytest.raises(DatabaseManagerUnavailableError),
        ):
            inst.databases.exists("mydb")


class TestGetItem:
    def test_index(self, instance):
        mock_cm = _mock_http({"result": ["prod", "staging"]})
        with patch("httpx.Client", return_value=mock_cm):
            db = instance.databases[0]
        assert isinstance(db, Database)
        assert db.name == "prod"

    def test_negative_index(self, instance):
        mock_cm = _mock_http({"result": ["prod", "staging"]})
        with patch("httpx.Client", return_value=mock_cm):
            db = instance.databases[-1]
        assert db.name == "staging"

    def test_out_of_range(self, instance):
        mock_cm = _mock_http({"result": ["prod"]})
        with patch("httpx.Client", return_value=mock_cm), pytest.raises(IndexError):
            instance.databases[5]

    def test_slice_raises_type_error(self, instance):
        with pytest.raises(TypeError):
            instance.databases[0:1]

    def test_string_index_raises_type_error(self, instance):
        with pytest.raises(TypeError):
            instance.databases["prod"]  # type: ignore[index]


class TestCurrent:
    def test_no_configured_names_returns_empty(self, client):
        inst = client.instance("http://localhost:8069")
        db = inst.databases.current()
        assert db.name == ""
        assert isinstance(db.backup, NoBackup)

    def test_empty_tuple_returns_empty(self, client):
        inst = client.instance("http://localhost:8069")
        object.__setattr__(inst.config, "configured_database_names", ())
        db = inst.databases.current()
        assert db.name == ""
        assert isinstance(db.backup, NoBackup)

    def test_with_configured_names(self, client):
        mock_cm = _mock_http({"result": ["prod"]})
        inst = client.instance("http://localhost:8069")
        object.__setattr__(inst.config, "configured_database_names", ("prod",))

        with patch("httpx.Client", return_value=mock_cm):
            db = inst.databases.current()
        assert db.name == "prod"
        assert isinstance(db.backup, NoBackup)

    def test_database_missing_records_dropped(self, client):
        mock_cm = _mock_http({"result": ["other"]})
        inst = _make_instance_with_cluster_key(client, configured_names=("prod",))

        mock_catalog = MagicMock()

        with (
            patch("httpx.Client", return_value=mock_cm),
            patch.object(inst, "_client") as mock_client,
        ):
            mock_client.get_catalog.return_value = mock_catalog
            db = inst.databases.current()

        assert db.name == "prod"
        assert isinstance(db.backup, NoBackup)
        mock_catalog.record_database_dropped.assert_called_once_with("localhost", 5432, "prod")

    def test_odoo_down_no_cluster_key_propagates(self, client):
        inst = client.instance("http://localhost:8069")
        object.__setattr__(inst.config, "configured_database_names", ("prod",))
        with patch("httpx.Client", side_effect=httpx.HTTPError("down")), pytest.raises(DatabaseManagerUnavailableError):
            inst.databases.current()

    def test_odoo_down_with_psql_confirms(self, client, monkeypatch):
        inst = _make_instance_with_cluster_key(client, db_user="odoo", configured_names=("prod",))
        mock_catalog = MagicMock()
        mock_catalog.latest_restore.return_value = None

        def mock_psql(*args: object, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = "1\n"
            proc.stderr = ""
            return proc

        monkeypatch.setattr("subprocess.run", mock_psql)
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/psql")

        with (
            patch("httpx.Client", side_effect=httpx.HTTPError("down")),
            patch.object(inst, "_client") as mock_client,
        ):
            mock_client.get_catalog.return_value = mock_catalog
            db = inst.databases.current()

        assert db.name == "prod"
        assert isinstance(db.backup, NoBackup)

    def test_odoo_down_with_psql_absent(self, client, monkeypatch):
        inst = _make_instance_with_cluster_key(client, db_user="odoo", configured_names=("prod",))
        mock_catalog = MagicMock()

        def mock_psql(*args: object, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = ""
            proc.stderr = ""
            return proc

        monkeypatch.setattr("subprocess.run", mock_psql)
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/psql")

        with (
            patch("httpx.Client", side_effect=httpx.HTTPError("down")),
            patch.object(inst, "_client") as mock_client,
        ):
            mock_client.get_catalog.return_value = mock_catalog
            db = inst.databases.current()

        assert db.name == "prod"
        assert isinstance(db.backup, NoBackup)
        mock_catalog.record_database_dropped.assert_called_once()

    def test_odoo_down_with_psql_error(self, client, monkeypatch):
        inst = _make_instance_with_cluster_key(client, db_user="odoo", configured_names=("prod",))
        mock_catalog = MagicMock()

        def mock_psql(*args: object, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            proc.returncode = 1
            proc.stdout = ""
            proc.stderr = "could not connect"
            return proc

        monkeypatch.setattr("subprocess.run", mock_psql)
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/psql")

        with (
            patch("httpx.Client", side_effect=httpx.HTTPError("down")),
            patch.object(inst, "_client") as mock_client,
        ):
            mock_client.get_catalog.return_value = mock_catalog
            db = inst.databases.current()

        assert db.name == "prod"
        assert isinstance(db.backup, NoBackup)
        mock_catalog.record_database_dropped.assert_not_called()

    def test_odoo_down_with_psql_timeout(self, client, monkeypatch):
        inst = _make_instance_with_cluster_key(client, db_user="odoo", configured_names=("prod",))
        mock_catalog = MagicMock()

        import subprocess

        def mock_psql(*args: object, **kwargs: object) -> MagicMock:
            raise subprocess.TimeoutExpired(cmd="psql", timeout=30)

        monkeypatch.setattr("subprocess.run", mock_psql)
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/psql")

        with (
            patch("httpx.Client", side_effect=httpx.HTTPError("down")),
            patch.object(inst, "_client") as mock_client,
        ):
            mock_client.get_catalog.return_value = mock_catalog
            db = inst.databases.current()

        assert db.name == "prod"
        assert isinstance(db.backup, NoBackup)
        mock_catalog.record_database_dropped.assert_not_called()

    def test_odoo_down_without_cluster_key_propagates(self, client):
        inst = client.instance("http://localhost:8069")
        object.__setattr__(inst.config, "configured_database_names", ("prod",))
        with patch("httpx.Client", side_effect=httpx.HTTPError("down")), pytest.raises(DatabaseManagerUnavailableError):
            inst.databases.current()


class TestVerifyPsql:
    """Direct tests for the _verify_database_via_psql helper."""

    def test_db_user_none_returns_none(self, monkeypatch):
        from odoo_instance_sdk.resources.database import _verify_database_via_psql

        # Even if psql is callable, db_user=None short-circuits.
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/psql")
        assert _verify_database_via_psql("localhost", 5432, None, None, "mydb") is None

    def test_psql_not_in_path_returns_none(self, monkeypatch):
        from odoo_instance_sdk.resources.database import _verify_database_via_psql

        monkeypatch.setattr("shutil.which", lambda _: None)
        called = False

        def fail(*args: object, **kwargs: object) -> MagicMock:
            nonlocal called
            called = True
            raise AssertionError("subprocess.run should not be called when psql is absent")

        monkeypatch.setattr("subprocess.run", fail)
        assert _verify_database_via_psql("localhost", 5432, "odoo", None, "mydb") is None
        assert not called

    def test_db_password_none_omits_pgpassword_from_env(self, monkeypatch):
        from odoo_instance_sdk.resources.database import _verify_database_via_psql

        captured: dict[str, object] = {}

        def fake_run(cmd: list[str], *, env: dict[str, str], **kwargs: object) -> MagicMock:
            captured["cmd"] = cmd
            captured["env"] = env
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = "1\n"
            proc.stderr = ""
            return proc

        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/psql")
        monkeypatch.setattr("subprocess.run", fake_run)
        result = _verify_database_via_psql("localhost", 5432, "odoo", None, "mydb")
        assert result is True
        assert "PGPASSWORD" not in captured["env"]

    def test_db_password_set_populates_pgpassword(self, monkeypatch):
        from odoo_instance_sdk.resources.database import _verify_database_via_psql

        captured: dict[str, object] = {}

        def fake_run(cmd: list[str], *, env: dict[str, str], **kwargs: object) -> MagicMock:
            captured["env"] = env
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = "1\n"
            proc.stderr = ""
            return proc

        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/psql")
        monkeypatch.setattr("subprocess.run", fake_run)
        _verify_database_via_psql("localhost", 5432, "odoo", "p4ss", "mydb")
        assert captured["env"]["PGPASSWORD"] == "p4ss"

    def test_psql_nonzero_exit_returns_none(self, monkeypatch):
        from odoo_instance_sdk.resources.database import _verify_database_via_psql

        def fake_run(*args: object, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            proc.returncode = 1
            proc.stdout = ""
            proc.stderr = "boom"
            return proc

        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/psql")
        monkeypatch.setattr("subprocess.run", fake_run)
        assert _verify_database_via_psql("localhost", 5432, "odoo", None, "mydb") is None

    def test_psql_timeout_returns_none(self, monkeypatch):
        import subprocess

        from odoo_instance_sdk.resources.database import _verify_database_via_psql

        def fake_run(*args: object, **kwargs: object) -> MagicMock:
            raise subprocess.TimeoutExpired(cmd="psql", timeout=30)

        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/psql")
        monkeypatch.setattr("subprocess.run", fake_run)
        assert _verify_database_via_psql("localhost", 5432, "odoo", None, "mydb") is None

    def test_psql_empty_stdout_returns_false(self, monkeypatch):
        from odoo_instance_sdk.resources.database import _verify_database_via_psql

        def fake_run(*args: object, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = ""
            proc.stderr = ""
            return proc

        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/psql")
        monkeypatch.setattr("subprocess.run", fake_run)
        assert _verify_database_via_psql("localhost", 5432, "odoo", None, "mydb") is False

    def test_socket_no_h_flag(self, monkeypatch):
        """db_host=None (socket) MUST NOT emit a ``-h`` flag in psql argv."""
        from odoo_instance_sdk.resources.database import _verify_database_via_psql

        captured: dict[str, object] = {}

        def fake_run(cmd: list[str], *, env: dict[str, str], **kwargs: object) -> MagicMock:
            captured["cmd"] = cmd
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = "1\n"
            proc.stderr = ""
            return proc

        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/psql")
        monkeypatch.setattr("subprocess.run", fake_run)
        result = _verify_database_via_psql(None, 5432, "odoo", None, "mydb")
        assert result is True
        assert "-h" not in captured["cmd"]


def test_missing_password_raises(instance_no_pwd):
    dr = instance_no_pwd.databases
    with pytest.raises(MasterPasswordRequiredError):
        dr._require_password()


def test_require_password_returns(instance):
    dr = instance.databases
    assert dr._require_password() == "admin"


def test_instance_url_isolation(client):
    inst1 = client.instance("http://localhost:8069", master_password="admin")
    inst2 = client.instance("http://localhost:8070", master_password="admin")
    assert inst1.databases.base_url == "http://localhost:8069"
    assert inst2.databases.base_url == "http://localhost:8070"
    assert inst1.databases is not inst2.databases


def test_no_basic_auth(instance):
    mock_cm = _mock_http({"result": ["db1"]})
    with patch("httpx.Client", return_value=mock_cm) as mock_cls:
        instance.databases.list()
    call_kwargs = mock_cls.call_args.kwargs
    assert "auth" not in call_kwargs


def test_database_resource_repr(instance):
    dr = instance.databases
    r = repr(dr)
    assert "base_url" in r


def test_remote_restore_rejected(instance_remote, tmp_path):
    backup = Backup(
        id=uuid.uuid4(),
        source_base_url="http://example.com:8069",
        database_name="testdb",
        format=BackupFormat.ZIP,
        filestore_requested=True,
        path=str(tmp_path / "x.zip"),
        filename="x.zip",
        size_bytes=0,
        sha256="",
        downloaded_at=datetime.now(),
    )
    with pytest.raises(NonLocalInstanceError):
        instance_remote.databases.restore(backup, "testdb")


def test_remote_drop_rejected(instance_remote):
    with pytest.raises(NonLocalInstanceError):
        instance_remote.databases.drop("testdb")


class TestRestore:
    def test_with_cluster_key_records_restore(self, client, tmp_path):
        backup_path = tmp_path / "test.zip"
        backup_path.write_text("fake content")
        backup = _make_backup(path=str(backup_path))
        inst = _make_instance_with_cluster_key(client)

        mock_cm = _mock_http({"result": True})
        mock_catalog = MagicMock()

        with (
            patch("httpx.Client", return_value=mock_cm),
            patch.object(inst, "_client") as mock_client,
            patch("odoo_instance_sdk.resources.database.DatabaseResource.exists") as mock_exists,
        ):
            mock_client.get_catalog.return_value = mock_catalog
            mock_exists.side_effect = [False, True]
            result = inst.databases.restore(backup, "newdb")

        assert result.new_db == "newdb"
        assert result.source == backup
        mock_catalog.record_restore.assert_called_once_with("localhost", 5432, "newdb", str(backup.id))

    def test_without_cluster_key_does_not_record_restore(self, instance, tmp_path):
        backup_path = tmp_path / "test.zip"
        backup_path.write_text("fake content")
        backup = _make_backup(path=str(backup_path))

        mock_cm = _mock_http({"result": True})

        with (
            patch("httpx.Client", return_value=mock_cm),
            patch.object(instance, "_client") as mock_client,
            patch("odoo_instance_sdk.resources.database.DatabaseResource.exists") as mock_exists,
        ):
            mock_catalog = MagicMock()
            mock_client.get_catalog.return_value = mock_catalog
            mock_exists.side_effect = [False, True]
            result = instance.databases.restore(backup, "newdb")

        assert result.new_db == "newdb"
        mock_catalog.record_restore.assert_not_called()

    def test_postcondition_fail_does_not_record_restore(self, client, tmp_path):
        backup_path = tmp_path / "test.zip"
        backup_path.write_text("fake content")
        backup = _make_backup(path=str(backup_path))
        inst = _make_instance_with_cluster_key(client)

        mock_cm = _mock_http({"result": True})
        mock_catalog = MagicMock()

        with (
            patch("httpx.Client", return_value=mock_cm) as mock_client_cls,
            patch.object(inst, "_client") as mock_client,
            patch("odoo_instance_sdk.resources.database.DatabaseResource.exists") as mock_exists,
            pytest.raises(RestoreFailedError),
        ):
            mock_client.get_catalog.return_value = mock_catalog
            mock_exists.side_effect = [False, False]
            inst.databases.restore(backup, "newdb")

        assert mock_client_cls.call_count == 1
        assert mock_client_cls.return_value.__enter__.return_value.post.call_count == 1
        assert mock_exists.call_count == 2
        mock_catalog.record_restore.assert_not_called()
        mock_catalog.record_database_dropped.assert_not_called()


class TestDrop:
    def test_with_cluster_key_records_dropped(self, client):
        inst = _make_instance_with_cluster_key(client)
        mock_cm = _mock_http({"result": True})
        mock_catalog = MagicMock()

        with (
            patch("httpx.Client", return_value=mock_cm),
            patch.object(inst, "_client") as mock_client,
            patch("odoo_instance_sdk.resources.database.DatabaseResource.exists", return_value=False),
        ):
            mock_client.get_catalog.return_value = mock_catalog
            result = inst.databases.drop("mydb")

        assert result.db == "mydb"
        mock_catalog.record_database_dropped.assert_called_once_with("localhost", 5432, "mydb")

    def test_without_cluster_key_does_not_record_dropped(self, instance):
        mock_cm = _mock_http({"result": True})

        with (
            patch("httpx.Client", return_value=mock_cm),
            patch.object(instance, "_client") as mock_client,
            patch("odoo_instance_sdk.resources.database.DatabaseResource.exists", return_value=False),
        ):
            mock_catalog = MagicMock()
            mock_client.get_catalog.return_value = mock_catalog
            result = instance.databases.drop("mydb")

        assert result.db == "mydb"
        mock_catalog.record_database_dropped.assert_not_called()
