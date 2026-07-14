from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest

from odoo_instance_sdk.exceptions import MasterPasswordRequiredError, NonLocalInstanceError
from odoo_instance_sdk.models import Backup, BackupFormat


def _mock_http(json_data: object) -> MagicMock:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status.return_value = None
    mock_http = MagicMock(spec=httpx.Client)
    mock_http.post.return_value = mock_resp
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_http
    return mock_cm


def test_list_returns_ordered(instance):
    mock_cm = _mock_http({"result": ["db1", "db2", "db3"]})
    with patch("httpx.Client", return_value=mock_cm):
        dbs = instance.databases.list()
    assert dbs == ("db1", "db2", "db3")


def test_exists_true(instance):
    mock_cm = _mock_http({"result": ["mydb", "other"]})
    with patch("httpx.Client", return_value=mock_cm):
        result = instance.databases.exists("mydb")
    assert result is True


def test_exists_false(instance):
    mock_cm = _mock_http({"result": ["other"]})
    with patch("httpx.Client", return_value=mock_cm):
        result = instance.databases.exists("mydb")
    assert result is False


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
