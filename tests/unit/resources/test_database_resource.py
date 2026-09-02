from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, patch

import httpx
import pytest

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import (
    BackupDownloadError,
    ConfigError,
    DatabaseError,
    DatabaseManagerUnavailableError,
    InstanceConfigurationError,
    MasterPasswordRequiredError,
    NonLocalInstanceError,
    RestoreFailedError,
)
from odoo_instance_sdk.internal.proc import ProcessResult, RecordingExecutor
from odoo_instance_sdk.models import (
    AdminPasswordResetResult,
    Backup,
    BackupFormat,
    CommandResult,
    Database,
    NoBackup,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.resources.instance import OdooInstance


def _mock_http(json_data: object) -> MagicMock:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status.return_value = None
    mock_http = MagicMock(spec=httpx.Client)
    mock_http.post.return_value = mock_resp
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_http
    return mock_cm


def _make_backup(**kw: Any) -> Backup:
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
        source_git_branch=kw.get("source_git_branch"),
    )


def _exception_graph_text(error: BaseException) -> str:
    """Inspect exception links and HTTP request/response objects recursively."""
    pending: list[object] = [error]
    seen: set[int] = set()
    parts: list[str] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, bytes):
            parts.append(repr(current))
            continue
        if isinstance(current, str):
            parts.append(current)
            continue
        if isinstance(current, BaseException):
            parts.extend((str(current), repr(current), repr(current.args)))
            pending.extend(getattr(current, "__notes__", None) or ())
            pending.extend(vars(current).values())
            pending.extend(
                linked for linked in (current.__cause__, current.__context__) if linked is not None
            )
            continue
        if isinstance(current, Mapping):
            pending.extend(current.keys())
            pending.extend(current.values())
            continue
        if isinstance(current, (list, tuple, set, frozenset)):
            pending.extend(current)
            continue
        try:
            pending.extend(vars(current).values())
        except TypeError:
            parts.append(repr(current))
    return "\n".join(parts)


def _make_instance_with_cluster_key(
    client: OdooClient,
    db_host: str = "localhost",
    db_port: int = 5432,
    db_user: str | None = None,
    configured_names: tuple[str, ...] = (),
) -> OdooInstance:
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
    def test_uses_jsonrpc_request(self, instance: OdooInstance) -> None:
        mock_cm = _mock_http({"result": []})
        with patch("httpx.Client", return_value=mock_cm):
            instance.databases.list()

        mock_cm.__enter__.return_value.post.assert_called_once_with(
            "http://localhost:8069/web/database/list",
            json={"jsonrpc": "2.0", "method": "call", "params": {}},
        )

    def test_returns_database_tuple(self, instance: OdooInstance) -> None:
        mock_cm = _mock_http({"result": ["db1", "db2", "db3"]})
        with patch("httpx.Client", return_value=mock_cm):
            dbs = instance.databases.list()
        assert isinstance(dbs, tuple)
        assert all(isinstance(db, Database) for db in dbs)
        assert tuple(db.name for db in dbs) == ("db1", "db2", "db3")
        assert all(isinstance(db.backup, NoBackup) for db in dbs)

    def test_returns_ordered(self, instance: OdooInstance) -> None:
        mock_cm = _mock_http({"result": ["db1", "db2", "db3"]})
        with patch("httpx.Client", return_value=mock_cm):
            dbs = instance.databases.list()
        assert [db.name for db in dbs] == ["db1", "db2", "db3"]

    def test_with_cluster_key_populates_backup(self, client: OdooClient) -> None:
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

    def test_reconciliation_records_dropped(self, client: OdooClient) -> None:
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
    def test_pgadmin_fallback_consumes_captured_probe_after_ambient_change(
        self, client: OdooClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.proc import PreparedAction, RunContext

        inst = _make_instance_with_cluster_key(client, db_user="odoo")
        with patch(
            "odoo_instance_sdk.internal.pg.builder.shutil.which",
            return_value="/usr/bin/psql",
        ):
            probe = inst.databases._psql_probe_for("mydb", "pgadmin.database.exists.psql")
        assert probe is not None
        result = ProcessResult(
            argv=probe.argv,
            returncode=0,
            stdout="1\n",
            stderr="",
            duration=0.0,
            cwd=probe.cwd,
            environment=probe.environment,
        )
        executor = RecordingExecutor(results={probe.step_id: result})
        action = PreparedAction(step_id="pgadmin.database.fallback", mutating=False)

        def callback(context: RunContext[bool]) -> bool:
            context.action(action.step_id)
            with patch.object(
                inst.databases.__class__,
                "list",
                side_effect=DatabaseManagerUnavailableError("down"),
            ):
                return inst.databases.exists("mydb")

        command = Command.create(
            ExecutionPlan(steps=(action.public_projection(), probe.public_projection())),
            callback,
            (action, probe),
            executor=executor,
        )
        monkeypatch.setenv("PGHOST", "ambient-substitution")
        with (
            patch(
                "odoo_instance_sdk.internal.pg.builder.shutil.which",
                return_value="/usr/bin/psql",
            ),
        ):
            assert command.run() is True
        assert executor.executed == [probe]

    def test_command_records_psql_fallback_step(self, client: OdooClient) -> None:
        inst = _make_instance_with_cluster_key(client, db_user="odoo")
        result = ProcessResult(
            argv=(),
            returncode=0,
            stdout="1\n",
            stderr="",
            duration=0.0,
            cwd=None,
            environment=(),
        )
        executor = RecordingExecutor(results={"database.exists.psql": result})
        with (
            patch(
                "odoo_instance_sdk.resources.database.DatabaseResource.list",
                side_effect=DatabaseManagerUnavailableError("down"),
            ),
            patch(
                "odoo_instance_sdk.internal.pg.builder.shutil.which",
                return_value="/usr/bin/psql",
            ),
        ):
            command = inst.databases.exists_command("mydb", executor=executor)
            assert command.run() is True

        assert tuple(step.step_id for step in command.plan.process_steps) == (
            "database.exists.psql",
        )
        assert tuple(step.step_id for step in executor.executed) == ("database.exists.psql",)

    def test_true(self, instance: OdooInstance) -> None:
        mock_cm = _mock_http({"result": ["mydb", "other"]})
        with patch("httpx.Client", return_value=mock_cm):
            result = instance.databases.exists("mydb")
        assert result is True

    def test_successful_list_accounts_reserved_psql_probe(
        self, client: OdooClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inst = _make_instance_with_cluster_key(client, db_user="odoo")
        executor = RecordingExecutor()
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
        )
        with patch("httpx.Client", return_value=_mock_http({"result": ["mydb"]})):
            command = inst.databases.exists_command("mydb", executor=executor)
            assert command.run() is True

        assert tuple(step.step_id for step in command.plan.process_steps) == (
            "database.exists.psql",
        )
        assert executor.executed == []

    def test_false(self, instance: OdooInstance) -> None:
        mock_cm = _mock_http({"result": ["other"]})
        with patch("httpx.Client", return_value=mock_cm):
            result = instance.databases.exists("mydb")
        assert result is False

    def test_odoo_down_psql_confirms(self, client: OdooClient) -> None:
        inst = _make_instance_with_cluster_key(client, db_user="odoo")
        with (
            patch(
                "odoo_instance_sdk.resources.database.DatabaseResource.list",
                side_effect=DatabaseManagerUnavailableError("down"),
            ),
            patch(
                "odoo_instance_sdk.resources.database._verify_database_via_psql", return_value=True
            ),
        ):
            assert inst.databases.exists("mydb") is True

    def test_odoo_down_psql_absent(self, client: OdooClient) -> None:
        inst = _make_instance_with_cluster_key(client, db_user="odoo")
        mock_catalog = MagicMock()
        with (
            patch(
                "odoo_instance_sdk.resources.database.DatabaseResource.list",
                side_effect=DatabaseManagerUnavailableError("down"),
            ),
            patch(
                "odoo_instance_sdk.resources.database._verify_database_via_psql", return_value=False
            ),
            patch.object(inst, "_client") as mock_client,
        ):
            mock_client.get_catalog.return_value = mock_catalog
            assert inst.databases.exists("mydb") is False
        mock_catalog.record_database_dropped.assert_called_once_with("localhost", 5432, "mydb")

    def test_odoo_down_psql_inconclusive(self, client: OdooClient) -> None:
        inst = _make_instance_with_cluster_key(client, db_user="odoo")
        with (
            patch(
                "odoo_instance_sdk.resources.database.DatabaseResource.list",
                side_effect=DatabaseManagerUnavailableError("down"),
            ),
            patch(
                "odoo_instance_sdk.resources.database._verify_database_via_psql", return_value=None
            ),
            pytest.raises(DatabaseManagerUnavailableError),
        ):
            inst.databases.exists("mydb")

    def test_odoo_down_no_cluster_key(self, instance: OdooInstance) -> None:
        with (
            patch(
                "odoo_instance_sdk.resources.database.DatabaseResource.list",
                side_effect=DatabaseManagerUnavailableError("down"),
            ),
            pytest.raises(DatabaseManagerUnavailableError),
        ):
            instance.databases.exists("mydb")

    def test_odoo_down_no_db_user(self, client: OdooClient) -> None:
        inst = _make_instance_with_cluster_key(client)
        with (
            patch(
                "odoo_instance_sdk.resources.database.DatabaseResource.list",
                side_effect=DatabaseManagerUnavailableError("down"),
            ),
            pytest.raises(DatabaseManagerUnavailableError),
        ):
            inst.databases.exists("mydb")


class TestGetItem:
    def test_index(self, instance: OdooInstance) -> None:
        mock_cm = _mock_http({"result": ["prod", "staging"]})
        with patch("httpx.Client", return_value=mock_cm):
            db = instance.databases[0]
        assert isinstance(db, Database)
        assert db.name == "prod"

    def test_negative_index(self, instance: OdooInstance) -> None:
        mock_cm = _mock_http({"result": ["prod", "staging"]})
        with patch("httpx.Client", return_value=mock_cm):
            db = instance.databases[-1]
        assert db.name == "staging"

    def test_out_of_range(self, instance: OdooInstance) -> None:
        mock_cm = _mock_http({"result": ["prod"]})
        with patch("httpx.Client", return_value=mock_cm), pytest.raises(IndexError):
            instance.databases[5]

    def test_slice_raises_type_error(self, instance: OdooInstance) -> None:
        with pytest.raises(TypeError):
            instance.databases[0:1]  # type: ignore[index]

    def test_string_index_raises_type_error(self, instance: OdooInstance) -> None:
        with pytest.raises(TypeError):
            instance.databases["prod"]  # type: ignore[index]


class TestCurrent:
    def test_no_configured_names_returns_empty(self, client: OdooClient) -> None:
        inst = client.instance("http://localhost:8069")
        db = inst.databases.current()
        assert db.name == ""
        assert isinstance(db.backup, NoBackup)

    def test_empty_tuple_returns_empty(self, client: OdooClient) -> None:
        inst = client.instance("http://localhost:8069")
        object.__setattr__(inst.config, "configured_database_names", ())
        db = inst.databases.current()
        assert db.name == ""
        assert isinstance(db.backup, NoBackup)

    def test_with_configured_names(self, client: OdooClient) -> None:
        mock_cm = _mock_http({"result": ["prod"]})
        inst = client.instance("http://localhost:8069")
        object.__setattr__(inst.config, "configured_database_names", ("prod",))

        with patch("httpx.Client", return_value=mock_cm):
            db = inst.databases.current()
        assert db.name == "prod"
        assert isinstance(db.backup, NoBackup)

    def test_database_missing_records_dropped(self, client: OdooClient) -> None:
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

    def test_odoo_down_no_cluster_key_propagates(self, client: OdooClient) -> None:
        inst = client.instance("http://localhost:8069")
        object.__setattr__(inst.config, "configured_database_names", ("prod",))
        with (
            patch("httpx.Client", side_effect=httpx.HTTPError("down")),
            pytest.raises(DatabaseManagerUnavailableError),
        ):
            inst.databases.current()

    def test_odoo_down_with_psql_confirms(
        self, client: OdooClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    def test_odoo_down_with_psql_absent(
        self, client: OdooClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    def test_odoo_down_with_psql_error(
        self, client: OdooClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    def test_odoo_down_with_psql_timeout(
        self, client: OdooClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    def test_odoo_down_without_cluster_key_propagates(self, client: OdooClient) -> None:
        inst = client.instance("http://localhost:8069")
        object.__setattr__(inst.config, "configured_database_names", ("prod",))
        with (
            patch("httpx.Client", side_effect=httpx.HTTPError("down")),
            pytest.raises(DatabaseManagerUnavailableError),
        ):
            inst.databases.current()


class TestVerifyPsql:
    """Direct tests for the _verify_database_via_psql helper."""

    def test_db_user_none_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from odoo_instance_sdk.resources.database import _verify_database_via_psql

        # Even if psql is callable, db_user=None short-circuits.
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/psql")
        assert _verify_database_via_psql("localhost", 5432, None, None, "mydb") is None

    def test_psql_not_in_path_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def test_db_password_none_omits_pgpassword_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
        assert "PGPASSWORD" not in cast("dict[str, str]", captured["env"])

    def test_db_password_set_populates_pgpassword(self, monkeypatch: pytest.MonkeyPatch) -> None:
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
        assert cast("dict[str, str]", captured["env"])["PGPASSWORD"] == "p4ss"

    def test_psql_nonzero_exit_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def test_psql_timeout_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        from odoo_instance_sdk.resources.database import _verify_database_via_psql

        def fake_run(*args: object, **kwargs: object) -> MagicMock:
            raise subprocess.TimeoutExpired(cmd="psql", timeout=30)

        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/psql")
        monkeypatch.setattr("subprocess.run", fake_run)
        assert _verify_database_via_psql("localhost", 5432, "odoo", None, "mydb") is None

    def test_psql_empty_stdout_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def test_missing_host_preserves_unix_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Restore tracking intentionally lets libpq select its Unix socket."""
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
        assert "-h" not in cast("list[str]", captured["cmd"])


class TestBackupProvenance:
    def test_direct_https_backup_does_not_require_repository_origin_pin(
        self, client: OdooClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ODCLI_TEST_INSTANCE_ORIGIN_PINS", raising=False)
        instance = client.instance("https://example.com", master_password="remote-secret")
        captured: dict[str, Path] = {}
        catalog = MagicMock()
        catalog.start_download.side_effect = lambda **kwargs: captured.update(
            path=Path(kwargs["path"])
        )
        response = MagicMock(spec=httpx.Response)
        response.headers = {}

        def chunks(**_: object) -> Any:
            part = captured["path"]
            assert part.is_file()
            assert part.stat().st_mode & 0o777 == 0o600
            yield b"backup"

        response.iter_bytes.side_effect = chunks
        http_cm = _mock_http({})
        http_cm.__enter__.return_value.post.return_value = response
        with (
            patch("odoo_instance_sdk.client.OdooClient.get_catalog", return_value=catalog),
            patch("httpx.Client", return_value=http_cm),
        ):
            backup = instance.databases.backup("testdb", destination=tmp_path)

        assert tmp_path.stat().st_mode & 0o777 == 0o700
        assert backup.path
        assert Path(backup.path).stat().st_mode & 0o777 == 0o600

    def test_backup_http_failure_does_not_retain_request_graph(
        self, client: OdooClient, tmp_path: Path
    ) -> None:
        remote_password = "remote-backup-password-sentinel"
        backup_body = b"backup-body-sentinel"
        request = httpx.Request(
            "POST",
            "https://example.com/web/database/backup",
            content=f"master_pwd={remote_password}".encode() + backup_body,
        )
        response = httpx.Response(502, request=request, content=backup_body)
        failure = httpx.HTTPStatusError("server failure", request=request, response=response)
        http_cm = _mock_http({})
        http_cm.__enter__.return_value.post.side_effect = failure
        catalog = MagicMock()

        with (
            patch("odoo_instance_sdk.client.OdooClient.get_catalog", return_value=catalog),
            patch("httpx.Client", return_value=http_cm),
            pytest.raises(BackupDownloadError) as raised,
        ):
            client.instance(
                "https://example.com", master_password=remote_password
            ).databases.backup("testdb", destination=tmp_path)

        graph = _exception_graph_text(raised.value)
        assert remote_password not in graph
        assert backup_body.decode() not in graph
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None

    def test_branch_is_normalized_and_audited_before_http(
        self, instance: OdooInstance, tmp_path: Path
    ) -> None:
        events: list[str] = []
        catalog = MagicMock()
        catalog.start_download.side_effect = lambda **_: events.append("catalog")
        response = MagicMock(spec=httpx.Response)
        response.headers = {}
        response.iter_bytes.return_value = [b"backup"]
        response.raise_for_status.side_effect = lambda: events.append("http")
        http_cm = _mock_http({})
        http_cm.__enter__.return_value.post.return_value = response

        with (
            patch("odoo_instance_sdk.client.OdooClient.get_catalog", return_value=catalog),
            patch("httpx.Client", return_value=http_cm),
        ):
            backup = instance.databases.backup(
                "testdb", destination=tmp_path, source_git_branch="  release/19  "
            )

        assert events == ["catalog", "http"]
        assert backup.source_git_branch == "release/19"
        assert catalog.start_download.call_args.kwargs["source_git_branch"] == "release/19"

    def test_omitted_branch_preserves_none(self, instance: OdooInstance, tmp_path: Path) -> None:
        catalog = MagicMock()
        response = MagicMock(spec=httpx.Response)
        response.headers = {}
        response.iter_bytes.return_value = [b"backup"]
        http_cm = _mock_http({})
        http_cm.__enter__.return_value.post.return_value = response
        with (
            patch("odoo_instance_sdk.client.OdooClient.get_catalog", return_value=catalog),
            patch("httpx.Client", return_value=http_cm),
        ):
            backup = instance.databases.backup("testdb", destination=tmp_path)
        assert backup.source_git_branch is None
        assert catalog.start_download.call_args.kwargs["source_git_branch"] is None

    @pytest.mark.parametrize("branch", ["", "   ", "release\n19", "release\x0019", "release\x8519"])
    def test_invalid_branch_has_no_catalog_or_http_side_effect(
        self, instance: OdooInstance, tmp_path: Path, branch: str
    ) -> None:
        destination = tmp_path / "backups"
        with (
            patch("odoo_instance_sdk.client.OdooClient.get_catalog") as get_catalog,
            patch("httpx.Client") as http_client,
            pytest.raises(ConfigError),
        ):
            instance.databases.backup("testdb", destination=destination, source_git_branch=branch)
        get_catalog.assert_not_called()
        http_client.assert_not_called()
        assert not destination.exists()

    def test_download_failure_keeps_branch_audit(
        self, instance: OdooInstance, tmp_path: Path
    ) -> None:
        catalog = MagicMock()
        response = MagicMock(spec=httpx.Response)
        response.headers = {}
        response.raise_for_status.side_effect = httpx.HTTPError("download failed")
        http_cm = _mock_http({})
        http_cm.__enter__.return_value.post.return_value = response
        with (
            patch("odoo_instance_sdk.client.OdooClient.get_catalog", return_value=catalog),
            patch("httpx.Client", return_value=http_cm),
            pytest.raises(BackupDownloadError),
        ):
            instance.databases.backup(
                "testdb", destination=tmp_path, source_git_branch="release/19"
            )
        assert catalog.start_download.call_args.kwargs["source_git_branch"] == "release/19"
        catalog.fail_download.assert_called_once()


class TestAdminPasswordReset:
    def test_uses_one_bound_database_and_committed_orm_script(self, client: OdooClient) -> None:
        instance = _make_instance_with_cluster_key(client, configured_names=("prod",))
        command = CommandResult(
            args=["odoo", "shell"],
            returncode=0,
            stdout="password sentinel must not escape",
            stderr="",
            duration=0.1,
        )
        with patch(
            "odoo_instance_sdk.resources.instance.OdooInstance._run_shell_script_exclusive",
            return_value=command,
        ) as run:
            result = instance.databases.reset_admin_password()

        assert isinstance(result, AdminPasswordResetResult)
        assert result.database == "prod"
        assert result.completed is True
        assert result.xml_id == "base.user_admin"
        source = run.call_args.args[0]
        assert "env.ref('base.user_admin'" in source
        assert "ensure_one()" in source
        assert "write({'password': 'admin'})" in source
        assert run.call_args.kwargs == {"commit": True}
        assert "sentinel" not in repr(result)

    @pytest.mark.parametrize("configured", [(), ("one", "two")])
    def test_requires_exactly_one_configured_database(
        self, client: OdooClient, configured: tuple[str, ...]
    ) -> None:
        instance = _make_instance_with_cluster_key(client, configured_names=configured)
        with (
            patch(
                "odoo_instance_sdk.resources.instance.OdooInstance._run_shell_script_exclusive"
            ) as run,
            pytest.raises(InstanceConfigurationError),
        ):
            instance.databases.reset_admin_password()
        run.assert_not_called()

    def test_remote_instance_rejected_before_shell(self, instance_remote: OdooInstance) -> None:
        object.__setattr__(instance_remote.config, "configured_database_names", ("prod",))
        with (
            patch(
                "odoo_instance_sdk.resources.instance.OdooInstance._run_shell_script_exclusive"
            ) as run,
            pytest.raises(NonLocalInstanceError),
        ):
            instance_remote.databases.reset_admin_password()
        run.assert_not_called()

    @pytest.mark.parametrize(
        "failure", ["External ID not found", "Expected singleton", "shell unavailable"]
    )
    def test_shell_failure_is_sanitized(self, client: OdooClient, failure: str) -> None:
        instance = _make_instance_with_cluster_key(client, configured_names=("prod",))
        sentinel = "reset-password-sentinel"
        with (
            patch(
                "odoo_instance_sdk.resources.instance.OdooInstance._run_shell_script_exclusive",
                side_effect=RuntimeError(f"{failure}: password={sentinel}"),
            ),
            pytest.raises(DatabaseManagerUnavailableError) as raised,
        ):
            instance.databases.reset_admin_password()
        assert sentinel not in str(raised.value)


def test_missing_password_raises(instance_no_pwd: OdooInstance) -> None:
    dr = instance_no_pwd.databases
    with pytest.raises(MasterPasswordRequiredError):
        dr._require_password()


def test_require_password_returns(instance: OdooInstance) -> None:
    dr = instance.databases
    assert dr._require_password() == "admin"


def test_instance_url_isolation(client: OdooClient) -> None:
    inst1 = client.instance("http://localhost:8069", master_password="admin")
    inst2 = client.instance("http://localhost:8070", master_password="admin")
    assert inst1.databases.base_url == "http://localhost:8069"
    assert inst2.databases.base_url == "http://localhost:8070"
    assert inst1.databases is not inst2.databases


def test_no_basic_auth(instance: OdooInstance) -> None:
    mock_cm = _mock_http({"result": ["db1"]})
    with patch("httpx.Client", return_value=mock_cm) as mock_cls:
        instance.databases.list()
    call_kwargs = mock_cls.call_args.kwargs
    assert "auth" not in call_kwargs


def test_database_resource_repr(instance: OdooInstance) -> None:
    dr = instance.databases
    r = repr(dr)
    assert "base_url" in r


def test_remote_restore_rejected(instance_remote: OdooInstance, tmp_path: Path) -> None:
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


def test_remote_drop_rejected(instance_remote: OdooInstance) -> None:
    with pytest.raises(NonLocalInstanceError):
        instance_remote.databases.drop("testdb")


class TestRestore:
    def test_http_failure_does_not_retain_request_or_backup_graph(
        self, client: OdooClient, tmp_path: Path
    ) -> None:
        local_password = "local-restore-password-sentinel"
        backup_body = b"restore-backup-body-sentinel"
        backup_path = tmp_path / "test.zip"
        backup_path.write_bytes(backup_body)
        backup = _make_backup(path=str(backup_path))
        instance = _make_instance_with_cluster_key(client)
        request = httpx.Request(
            "POST",
            "http://127.0.0.1:8069/web/database/restore",
            content=f"master_pwd={local_password}".encode() + backup_body,
        )
        response = httpx.Response(500, request=request, content=backup_body)
        failure = httpx.HTTPStatusError("restore failure", request=request, response=response)
        http_cm = _mock_http({})
        http_cm.__enter__.return_value.post.side_effect = failure
        catalog = MagicMock()

        with (
            patch.object(instance, "_client") as mock_client,
            patch("httpx.Client", return_value=http_cm),
            patch(
                "odoo_instance_sdk.resources.database.DatabaseResource.exists", return_value=False
            ),
            pytest.raises(DatabaseError) as raised,
        ):
            mock_client.get_catalog.return_value = catalog
            instance.databases.restore(backup, "newdb")

        graph = _exception_graph_text(raised.value)
        assert local_password not in graph
        assert backup_body.decode() not in graph
        assert raised.value.body == b""
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None

    def test_with_cluster_key_records_restore(self, client: OdooClient, tmp_path: Path) -> None:
        backup_path = tmp_path / "test.zip"
        backup_path.write_text("fake content")
        backup = _make_backup(path=str(backup_path), source_git_branch="release/19")
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
        mock_catalog.verify_identity.assert_called_once_with(backup)
        assert mock_cm.__enter__.return_value.post.call_args.kwargs["data"]["name"] == "newdb"
        mock_catalog.record_restore.assert_called_once_with(
            "localhost", 5432, "newdb", str(backup.id)
        )

    def test_without_cluster_key_does_not_record_restore(
        self, instance: OdooInstance, tmp_path: Path
    ) -> None:
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

    def test_postcondition_fail_does_not_record_restore(
        self, client: OdooClient, tmp_path: Path
    ) -> None:
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
    def test_with_cluster_key_records_dropped(self, client: OdooClient) -> None:
        inst = _make_instance_with_cluster_key(client)
        mock_cm = _mock_http({"result": True})
        mock_catalog = MagicMock()

        with (
            patch("httpx.Client", return_value=mock_cm),
            patch.object(inst, "_client") as mock_client,
            patch(
                "odoo_instance_sdk.resources.database.DatabaseResource.exists", return_value=False
            ),
        ):
            mock_client.get_catalog.return_value = mock_catalog
            result = inst.databases.drop("mydb")

        assert result.db == "mydb"
        mock_catalog.record_database_dropped.assert_called_once_with("localhost", 5432, "mydb")

    def test_without_cluster_key_does_not_record_dropped(self, instance: OdooInstance) -> None:
        mock_cm = _mock_http({"result": True})

        with (
            patch("httpx.Client", return_value=mock_cm),
            patch.object(instance, "_client") as mock_client,
            patch(
                "odoo_instance_sdk.resources.database.DatabaseResource.exists", return_value=False
            ),
        ):
            mock_catalog = MagicMock()
            mock_client.get_catalog.return_value = mock_catalog
            result = instance.databases.drop("mydb")

        assert result.db == "mydb"
        mock_catalog.record_database_dropped.assert_not_called()
