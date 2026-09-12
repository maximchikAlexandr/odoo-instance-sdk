from __future__ import annotations

import hashlib
import subprocess
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, PropertyMock, patch

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
from odoo_instance_sdk.internal.proc import ProcessResult, ProcessTimeoutError, RecordingExecutor
from odoo_instance_sdk.models import (
    AdminPasswordResetResult,
    Backup,
    BackupFormat,
    CommandResult,
    Database,
    NoBackup,
    RestoreResult,
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

    def stream(*args: object, **kwargs: object) -> MagicMock:
        response = mock_http.post(*args, **kwargs)
        stream_cm = MagicMock()
        stream_cm.__enter__.return_value = response
        return stream_cm

    mock_http.stream.side_effect = stream
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_http
    return mock_cm


def _stream_http(response: MagicMock) -> tuple[MagicMock, MagicMock]:
    http_cm = _mock_http({})
    http = http_cm.__enter__.return_value
    stream_cm = MagicMock()
    stream_cm.__enter__.return_value = response
    http.stream.side_effect = None
    http.stream.return_value = stream_cm
    return http_cm, http


def _patch_captured_process(monkeypatch: pytest.MonkeyPatch, fake_run: Any) -> None:
    from odoo_instance_sdk.internal.proc.executor import _environment

    def fake_pump(step: Any, *, timeout: float | None, **_: Any) -> tuple[int, bytes, bytes, float]:
        try:
            completed = fake_run(
                list(step.argv),
                env=_environment(
                    step.environment,
                    policy=step.environment_policy,
                    snapshot=step.environment_snapshot,
                ),
                timeout=timeout,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired as error:
            raise ProcessTimeoutError(
                step.argv,
                timeout or 0.0,
                duration=timeout or 0.0,
            ) from error
        stdout = completed.stdout
        stderr = completed.stderr
        return (
            completed.returncode,
            stdout.encode() if isinstance(stdout, str) else stdout,
            stderr.encode() if isinstance(stderr, str) else stderr,
            0.0,
        )

    monkeypatch.setattr("odoo_instance_sdk.internal.proc.executor._run_pump", fake_pump)


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

    def test_probe_consumed(self, client: OdooClient, monkeypatch: pytest.MonkeyPatch) -> None:
        inst = _make_instance_with_cluster_key(client, db_user="odoo")
        result = MagicMock(returncode=0, stdout="1\n")
        executor = RecordingExecutor(results={"database.exists.psql": result})
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
        )
        with patch("httpx.Client", return_value=_mock_http({"result": ["mydb"]})) as http:
            command = inst.databases.exists_command("mydb", executor=executor)
            assert command.run() is True
        assert tuple(step.step_id for step in command.plan.process_steps) == (
            "database.exists.psql",
        )
        assert tuple(step.step_id for step in executor.executed) == ("database.exists.psql",)

        http.assert_not_called()

    def test_true(self, instance: OdooInstance) -> None:
        mock_cm = _mock_http({"result": ["mydb", "other"]})
        with patch("httpx.Client", return_value=mock_cm):
            result = instance.databases.exists("mydb")
        assert result is True

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

        _patch_captured_process(monkeypatch, mock_psql)
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

        _patch_captured_process(monkeypatch, mock_psql)
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

        _patch_captured_process(monkeypatch, mock_psql)
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

        _patch_captured_process(monkeypatch, mock_psql)
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
        _patch_captured_process(monkeypatch, fake_run)
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
        _patch_captured_process(monkeypatch, fake_run)
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
        _patch_captured_process(monkeypatch, fake_run)
        assert _verify_database_via_psql("localhost", 5432, "odoo", None, "mydb") is None

    def test_psql_timeout_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        from odoo_instance_sdk.resources.database import _verify_database_via_psql

        def fake_run(*args: object, **kwargs: object) -> MagicMock:
            raise subprocess.TimeoutExpired(cmd="psql", timeout=30)

        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/psql")
        _patch_captured_process(monkeypatch, fake_run)
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
        _patch_captured_process(monkeypatch, fake_run)
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
        _patch_captured_process(monkeypatch, fake_run)
        result = _verify_database_via_psql(None, 5432, "odoo", None, "mydb")
        assert result is True
        assert "-h" not in cast("list[str]", captured["cmd"])


class TestBackupProvenance:
    @pytest.mark.parametrize(
        ("base_url", "path_label"),
        [("http://localhost:8069", "local"), ("https://example.test", "remote")],
        ids=["local-project", "remote-project"],
    )
    def test_project_download_records_canonical_owner_for_local_and_remote_entries(
        self,
        client: OdooClient,
        tmp_path: Path,
        base_url: str,
        path_label: str,
    ) -> None:
        from odoo_instance_sdk.internal.repo_key import repo_key
        from odoo_instance_sdk.resources.instance import _RuntimeBinding

        root = tmp_path / path_label
        common = root / ".git"
        project_id = f"project_{repo_key(root, common)}"
        instance = client.instance(base_url, master_password="admin")
        object.__setattr__(
            instance,
            "_runtime_binding",
            _RuntimeBinding(
                owner_kind="project",
                owner_id=project_id,
                project_id=project_id,
                repository_root=root,
                git_common_dir=common,
            ),
        )
        catalog = MagicMock()
        response = MagicMock(spec=httpx.Response)
        response.headers = {}
        response.iter_bytes.return_value = [b"backup"]
        response.raise_for_status.return_value = None
        http_cm = _mock_http({})
        http_cm.__enter__.return_value.post.return_value = response

        with (
            patch("odoo_instance_sdk.client.OdooClient.get_catalog", return_value=catalog),
            patch("httpx.Client", return_value=http_cm),
        ):
            instance.databases.backup("testdb", destination=tmp_path / path_label)

        assert catalog.start_download.call_args.kwargs["project_id"] == project_id

    @pytest.mark.parametrize(
        ("headers", "expected_total"),
        [
            ({"content-length": "6"}, 6),
            ({}, None),
            ({"content-length": "6", "content-encoding": "gzip"}, None),
            ({"content-length": "invalid"}, None),
        ],
        ids=["reliable", "absent", "encoded", "invalid"],
    )
    def test_backup_streams_and_reports_only_trustworthy_lengths(
        self,
        instance: OdooInstance,
        tmp_path: Path,
        headers: dict[str, str],
        expected_total: int | None,
    ) -> None:
        from odoo_instance_sdk.internal.proc import StepEvent

        response = MagicMock(spec=httpx.Response)
        response.headers = {"content-disposition": 'attachment; filename="demo.zip"', **headers}
        response.iter_bytes.return_value = [b"back", b"up"]
        response.raise_for_status.return_value = None
        content = PropertyMock(side_effect=AssertionError("response content must not be buffered"))
        type(response).content = content
        http_cm, http = _stream_http(response)
        catalog = MagicMock()
        events: list[StepEvent] = []

        with (
            patch("odoo_instance_sdk.client.OdooClient.get_catalog", return_value=catalog),
            patch("httpx.Client", return_value=http_cm),
        ):
            backup = instance.databases.backup_command("testdb", destination=tmp_path).run(
                observer=events.append
            )

        assert http.stream.call_count == 1
        http.post.assert_not_called()
        assert backup.size_bytes == 6
        assert backup.sha256 == hashlib.sha256(b"backup").hexdigest()
        assert response.raise_for_status.call_count == 1
        assert response.iter_bytes.call_count == 1
        content.assert_not_called()
        transfer_progress = [
            event
            for event in events
            if event.step_id == "database.backup.transfer" and event.kind == "progress"
        ]
        assert transfer_progress
        assert transfer_progress[-1].completed_units == 6
        assert transfer_progress[-1].total_units == expected_total
        wait_completed = next(
            index
            for index, event in enumerate(events)
            if event.step_id == "database.backup.wait" and event.kind == "completed"
        )
        transfer_started = next(
            index
            for index, event in enumerate(events)
            if event.step_id == "database.backup.transfer" and event.kind == "started"
        )
        assert wait_completed < transfer_started

    def test_backup_rejects_trustworthy_length_mismatch_without_publishing(
        self, instance: OdooInstance, tmp_path: Path
    ) -> None:
        response = MagicMock(spec=httpx.Response)
        response.headers = {
            "content-disposition": 'attachment; filename="demo.zip"',
            "content-length": "7",
        }
        response.iter_bytes.return_value = [b"backup"]
        response.raise_for_status.return_value = None
        http_cm, _http = _stream_http(response)
        catalog = MagicMock()

        with (
            patch("odoo_instance_sdk.client.OdooClient.get_catalog", return_value=catalog),
            patch("httpx.Client", return_value=http_cm),
            pytest.raises(BackupDownloadError) as raised,
        ):
            instance.databases.backup("testdb", destination=tmp_path)

        context = getattr(raised.value, "failure_context")
        assert context.state.value == "failed"
        assert context.published is False
        assert uuid.UUID(str(context.backup_id))
        catalog.fail_download.assert_called_once()
        assert list(tmp_path.glob("*.part")) == []
        assert list(tmp_path.glob("*.zip")) == []

    def test_backup_stream_break_is_safe_and_cleans_partial_file(
        self, instance: OdooInstance, tmp_path: Path
    ) -> None:
        response = MagicMock(spec=httpx.Response)
        response.headers = {"content-disposition": 'attachment; filename="demo.zip"'}
        response.raise_for_status.return_value = None

        def chunks(**_: object) -> Any:
            yield b"partial"
            raise httpx.ReadError("stream broke")

        response.iter_bytes.side_effect = chunks
        http_cm, _http = _stream_http(response)
        catalog = MagicMock()

        with (
            patch("odoo_instance_sdk.client.OdooClient.get_catalog", return_value=catalog),
            patch("httpx.Client", return_value=http_cm),
            pytest.raises(BackupDownloadError, match="Backup request failed"),
        ):
            instance.databases.backup("testdb", destination=tmp_path)

        assert list(tmp_path.glob("*.part")) == []
        assert list(tmp_path.glob("*.zip")) == []
        catalog.fail_download.assert_called_once()

    def test_backup_limit_stops_iteration_before_requesting_another_chunk(
        self, tmp_path: Path
    ) -> None:
        from odoo_instance_sdk.resources.database import _stream_response_to_file

        class Chunks:
            calls = 0

            def __iter__(self) -> Any:
                self.calls += 1
                yield b"1234"
                self.calls += 1
                yield b"56"
                self.calls += 1
                raise AssertionError("iterator advanced after the over-limit chunk")

        response = MagicMock(spec=httpx.Response)
        chunks = Chunks()
        response.iter_bytes.return_value = chunks

        with pytest.raises(BackupDownloadError, match="exceeded"):
            _stream_response_to_file(response, tmp_path / "backup.part", max_bytes=5)

        assert chunks.calls == 2
        assert (tmp_path / "backup.part").read_bytes() == b"1234"

    def test_backup_interrupt_closes_stream_and_retains_known_failed_context(
        self, instance: OdooInstance, tmp_path: Path
    ) -> None:
        response = MagicMock(spec=httpx.Response)
        response.headers = {"content-disposition": 'attachment; filename="demo.zip"'}

        def chunks(**_: object) -> Any:
            yield b"partial"
            raise KeyboardInterrupt

        response.iter_bytes.side_effect = chunks
        response.raise_for_status.return_value = None
        http_cm, _http = _stream_http(response)
        stream_cm = http_cm.__enter__.return_value.stream.return_value
        catalog = MagicMock()

        with (
            patch("odoo_instance_sdk.client.OdooClient.get_catalog", return_value=catalog),
            patch("httpx.Client", return_value=http_cm),
            pytest.raises(KeyboardInterrupt) as raised,
        ):
            instance.databases.backup("testdb", destination=tmp_path)

        context = getattr(raised.value, "failure_context")
        assert context.state.value == "failed"
        assert context.published is False
        assert stream_cm.__exit__.call_count == 1
        catalog.fail_download.assert_called_once()
        assert list(tmp_path.glob("*.part")) == []

    def test_backup_interrupt_before_publication_closes_response_and_cleans(
        self, instance: OdooInstance, tmp_path: Path
    ) -> None:
        response = MagicMock(spec=httpx.Response)
        response.headers = {"content-disposition": 'attachment; filename="demo.zip"'}
        response.raise_for_status.side_effect = KeyboardInterrupt
        response.iter_bytes.return_value = []
        http_cm, _http = _stream_http(response)
        stream_cm = http_cm.__enter__.return_value.stream.return_value
        catalog = MagicMock()

        with (
            patch("odoo_instance_sdk.client.OdooClient.get_catalog", return_value=catalog),
            patch("httpx.Client", return_value=http_cm),
            pytest.raises(KeyboardInterrupt) as raised,
        ):
            instance.databases.backup("testdb", destination=tmp_path)

        context = getattr(raised.value, "failure_context")
        assert context.published is False
        assert context.state.value == "failed"
        assert stream_cm.__exit__.call_count == 1
        catalog.fail_download.assert_called_once()
        assert list(tmp_path.glob("*.part")) == []

    def test_backup_interrupt_after_publication_retains_backup_and_available_state(
        self, instance: OdooInstance, tmp_path: Path
    ) -> None:
        response = MagicMock(spec=httpx.Response)
        response.headers = {"content-disposition": 'attachment; filename="demo.zip"'}
        response.raise_for_status.return_value = None
        response.iter_bytes.return_value = [b"backup"]
        http_cm, _http = _stream_http(response)
        catalog = MagicMock()

        with (
            patch("odoo_instance_sdk.client.OdooClient.get_catalog", return_value=catalog),
            patch("httpx.Client", return_value=http_cm),
            patch(
                "odoo_instance_sdk.resources.database.Backup",
                side_effect=KeyboardInterrupt,
            ),
            pytest.raises(KeyboardInterrupt) as raised,
        ):
            instance.databases.backup("testdb", destination=tmp_path)

        context = getattr(raised.value, "failure_context")
        assert context.published is True
        assert context.state.value == "available"
        catalog.success_download.assert_called_once()
        catalog.fail_download.assert_not_called()
        assert len(list(tmp_path.glob("*.zip"))) == 1
        assert list(tmp_path.glob("*.part")) == []

    @pytest.mark.parametrize(("timeout", "expected"), [(None, 600.0), (12.5, 12.5)])
    def test_backup_uses_long_default_timeout_and_honors_override(
        self,
        instance: OdooInstance,
        tmp_path: Path,
        timeout: float | None,
        expected: float,
    ) -> None:
        catalog = MagicMock()
        response = MagicMock(spec=httpx.Response)
        response.headers = {}
        response.iter_bytes.return_value = [b"backup"]
        http_cm = _mock_http({})
        http_cm.__enter__.return_value.post.return_value = response

        with (
            patch("odoo_instance_sdk.client.OdooClient.get_catalog", return_value=catalog),
            patch("httpx.Client", return_value=http_cm) as http_client,
        ):
            instance.databases.backup("testdb", destination=tmp_path, timeout=timeout)

        configured_timeout = http_client.call_args.kwargs["timeout"]
        assert configured_timeout.connect == expected
        assert configured_timeout.read == expected
        assert configured_timeout.write == expected
        assert configured_timeout.pool == expected

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


@pytest.mark.parametrize("value", [1, "", "  ", "bad\nbranch"])
def test_source_git_branch_validation_is_fail_closed(value: object) -> None:
    from odoo_instance_sdk.resources.database import _normalize_source_git_branch

    with pytest.raises(ConfigError):
        _normalize_source_git_branch(value)  # type: ignore[arg-type]


def test_stream_response_rejects_oversized_declared_content_before_open(
    tmp_path: Path,
) -> None:
    from odoo_instance_sdk.resources.database import _stream_response_to_file

    response = MagicMock(spec=httpx.Response)
    with pytest.raises(BackupDownloadError, match="exceeded"):
        _stream_response_to_file(
            response,
            tmp_path / "backup.part",
            max_bytes=5,
            expected_bytes=6,
        )
    response.iter_bytes.assert_not_called()


def test_database_probe_rejects_backslash_name_without_spawning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.resources.database import _verify_database_via_psql

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.transport.run_psql", lambda **_: pytest.fail()
    )
    assert _verify_database_via_psql("localhost", 5432, "odoo", None, "unsafe\\name") is None


def test_database_resource_rejects_non_integer_index(instance: OdooInstance) -> None:
    with pytest.raises(TypeError, match="indices must be integers"):
        instance.databases["first"]  # type: ignore[index]


def test_execute_sql_rejects_non_string_before_preparation(instance: OdooInstance) -> None:
    with pytest.raises(TypeError, match="sql must be a string"):
        instance.databases.execute_sql_command(1)  # type: ignore[arg-type]


def test_catalog_rejects_non_string_backup_id(tmp_path: Path) -> None:
    from odoo_instance_sdk.exceptions import BackupNotFoundError
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    with pytest.raises(BackupNotFoundError, match="complete UUID"):
        catalog._canonical_backup_id(1)  # type: ignore[arg-type]
    catalog.close()


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
    def test_verified_restore_does_not_probe_when_database_manager_is_unavailable(
        self, client: OdooClient, tmp_path: Path
    ) -> None:
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.proc import PreparedAction, RunContext

        backup_path = tmp_path / "test.zip"
        backup_path.write_text("fake content")
        backup = _make_backup(path=str(backup_path))
        inst = _make_instance_with_cluster_key(client, db_user="odoo")
        with patch(
            "odoo_instance_sdk.internal.pg.builder.shutil.which", return_value="/usr/bin/psql"
        ):
            before = inst.databases._psql_probe_for("newdb", "database.restore.exists-before")
            after = inst.databases._psql_probe_for("newdb", "database.restore.exists-after")
        assert before is not None
        assert after is not None

        executor = RecordingExecutor(
            results={
                before.step_id: ProcessResult(
                    argv=before.argv,
                    returncode=0,
                    stdout="",
                    stderr="",
                    duration=0.0,
                    cwd=before.cwd,
                    environment=before.environment,
                ),
                after.step_id: ProcessResult(
                    argv=after.argv,
                    returncode=0,
                    stdout="1\n",
                    stderr="",
                    duration=0.0,
                    cwd=after.cwd,
                    environment=after.environment,
                ),
            }
        )
        action = PreparedAction("database.restore")

        def callback(context: RunContext[RestoreResult]) -> RestoreResult:
            context.action(action.step_id)
            context.process(before.step_id)
            result = inst.databases._restore_after_verified_absence(backup, "newdb")
            context.process(after.step_id)
            return result

        command = Command.create(
            ExecutionPlan(
                steps=(
                    action.public_projection(),
                    before.public_projection(),
                    after.public_projection(),
                )
            ),
            callback,
            (action, before, after),
            executor=executor,
        )
        mock_catalog = MagicMock()
        with (
            patch.object(inst, "_client") as mock_client,
            patch("httpx.Client", return_value=_mock_http({"result": True})),
            patch.object(
                inst.databases.__class__,
                "list",
                side_effect=DatabaseManagerUnavailableError("database manager unavailable"),
            ) as list_method,
        ):
            mock_client.config = client.config
            mock_client.get_catalog.return_value = mock_catalog
            result = command.run()

        assert result.new_db == "newdb"
        assert [step.step_id for step in executor.executed] == [
            before.step_id,
            after.step_id,
        ]
        list_method.assert_not_called()
        mock_catalog.record_restore.assert_called_once_with(
            "localhost", 5432, "newdb", str(backup.id)
        )

    def test_default_timeout_uses_long_running_backup_budget(
        self, client: OdooClient, tmp_path: Path
    ) -> None:
        backup_path = tmp_path / "test.zip"
        backup_path.write_text("fake content")
        backup = _make_backup(path=str(backup_path))
        instance = _make_instance_with_cluster_key(client)
        http_cm = _mock_http({"result": True})

        with (
            patch("httpx.Client", return_value=http_cm) as mock_client_cls,
            patch.object(instance, "_client") as mock_client,
            patch(
                "odoo_instance_sdk.resources.database.DatabaseResource.exists",
                side_effect=[False, True],
            ),
        ):
            mock_client.config = client.config
            mock_client.get_catalog.return_value = MagicMock()
            instance.databases.restore(backup, "newdb")

        timeout = mock_client_cls.call_args.kwargs["timeout"]
        assert timeout.connect == client.config.backup_timeout_seconds
        assert timeout.read == client.config.backup_timeout_seconds

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


@pytest.mark.parametrize("payload", [[], {"result": "disabled"}])
def test_rejects_malformed_names_response(
    instance: OdooInstance, payload: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_cm = _mock_http(payload)
    monkeypatch.setattr("httpx.Client", lambda **_: mock_cm)
    with pytest.raises(DatabaseManagerUnavailableError):
        instance.databases.names()


class TestPlannedExistsProbe:
    def test_is_authoritative_over_filtered_odoo_list(
        self, client: OdooClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
        )
        with patch("httpx.Client", return_value=_mock_http({"result": ["previous-db"]})) as http:
            command = inst.databases.exists_command("mydb", executor=executor)
            assert command.run() is True

        assert tuple(step.step_id for step in command.plan.process_steps) == (
            "database.exists.psql",
        )
        assert tuple(step.step_id for step in executor.executed) == ("database.exists.psql",)
        http.assert_not_called()

    @pytest.mark.parametrize(("probe_result", "expected"), [(True, True), (False, False)])
    def test_unplanned_psql_fallback_when_odoo_is_unavailable(
        self, client: OdooClient, probe_result: bool, expected: bool
    ) -> None:
        inst = _make_instance_with_cluster_key(client, db_user="odoo")
        catalog = MagicMock()
        with (
            patch.object(
                inst.databases.__class__, "list", side_effect=DatabaseManagerUnavailableError
            ),
            patch(
                "odoo_instance_sdk.resources.database._verify_database_via_psql",
                return_value=probe_result,
            ),
            patch.object(inst, "_client") as mock_client,
        ):
            mock_client.get_catalog.return_value = catalog
            assert inst.databases._exists_impl("mydb") is expected

        if probe_result:
            catalog.record_database_dropped.assert_not_called()
        else:
            catalog.record_database_dropped.assert_called_once_with("localhost", 5432, "mydb")

    def test_confirmed_absence_is_authoritative(
        self, client: OdooClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inst = _make_instance_with_cluster_key(client, db_user="odoo")
        result = ProcessResult(
            argv=(),
            returncode=0,
            stdout="",
            stderr="",
            duration=0.0,
            cwd=None,
            environment=(),
        )
        executor = RecordingExecutor(results={"database.exists.psql": result})
        catalog = MagicMock()
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
        )
        with (
            patch.object(inst, "_client") as mock_client,
            patch("httpx.Client", return_value=_mock_http({"result": ["mydb"]})) as http,
        ):
            mock_client.get_catalog.return_value = catalog
            catalog.has_tracked_database.return_value = True
            assert inst.databases.exists_command("mydb", executor=executor).run() is False

        catalog.record_database_dropped.assert_called_once_with("localhost", 5432, "mydb")
        http.assert_not_called()

    def test_without_cluster_or_user_has_no_direct_probe(
        self, instance: OdooInstance, client: OdooClient
    ) -> None:
        assert instance.databases._planned_exists_result("mydb", "probe") is None
        inst = _make_instance_with_cluster_key(client)
        assert inst.databases._planned_exists_result("mydb", "probe") is None

    def test_inconclusive_result_does_not_fall_back_to_odoo_list(
        self, client: OdooClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inst = _make_instance_with_cluster_key(client, db_user="odoo")
        result = ProcessResult(
            argv=(),
            returncode=1,
            stdout="",
            stderr="connection failed",
            duration=0.0,
            cwd=None,
            environment=(),
        )
        executor = RecordingExecutor(results={"database.exists.psql": result})
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _: "/usr/bin/psql"
        )
        with (
            patch("httpx.Client", return_value=_mock_http({"result": ["mydb"]})) as http,
            pytest.raises(DatabaseManagerUnavailableError, match="existence probe failed"),
        ):
            inst.databases.exists_command("mydb", executor=executor).run()

        http.assert_not_called()


def test_remote_http_backup_warns_without_exposing_password(
    client: OdooClient, tmp_path: Path
) -> None:
    from odoo_instance_sdk.internal import urls

    urls._cleartext_warned = [False]
    password = "cleartext-password-sentinel"
    catalog = MagicMock()
    response = MagicMock(spec=httpx.Response)
    response.headers = {}
    response.iter_bytes.return_value = [b"backup"]
    http_cm = _mock_http({})
    http_cm.__enter__.return_value.post.return_value = response
    instance = client.instance("http://example.test:8069", master_password=password)

    with (
        patch("odoo_instance_sdk.client.OdooClient.get_catalog", return_value=catalog),
        patch("httpx.Client", return_value=http_cm),
        pytest.warns(UserWarning, match="cleartext") as warnings,
    ):
        instance.databases.backup("testdb", destination=tmp_path)

    request_data = http_cm.__enter__.return_value.post.call_args.kwargs["data"]
    assert request_data["master_pwd"] == password
    assert password not in str(warnings[0].message)
