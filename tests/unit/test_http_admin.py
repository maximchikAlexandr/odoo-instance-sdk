from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette_admin.exceptions import ActionFailed, FormValidationError

from odoo_instance_sdk.exceptions import ConfigError

pytestmark = pytest.mark.dashboard


def _admin_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    del tmp_path
    from odoo_instance_sdk.internal.paths import get_data_root, get_user_root

    root = get_user_root(ensure_exists=True)
    os.chmod(root, 0o700)
    data_root = get_data_root(ensure_exists=True)
    os.chmod(data_root, 0o700)
    monkeypatch.delenv("ODCLI_USER_ROOT", raising=False)
    return root


def _write_admin_config(root: Path, *, password: str = "admin-password") -> None:
    from pwdlib import PasswordHash

    config = root / "config"
    config.mkdir(mode=0o700)
    os.chmod(config, 0o700)
    path = config / "admin.toml"
    path.write_text(
        "[admin]\n"
        f'password_hash = "{PasswordHash.recommended().hash(password)}"\n'
        f'session_key = "{"s" * 40}"\n',
        encoding="utf-8",
    )
    path.chmod(0o600)


def _csrf(client: object) -> str:
    response = client.get("/admin/login")  # type: ignore[attr-defined]
    match = re.search(r'name="csrftoken"[^>]*value="([^"]+)', response.text)
    assert match is not None
    return match.group(1)


def _authenticated_client(root: Path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.http.admin import mount_admin

    app = FastAPI()
    admin = mount_admin(app)
    client = TestClient(app)
    token = _csrf(client)
    response = client.post(
        "/admin/login",
        data={"username": "admin", "password": "admin-password", "csrftoken": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return admin, client, token


def _request(query: str = "") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin",
            "query_string": query.encode(),
            "headers": [],
        }
    )


def test_admin_config_rejects_missing_malformed_and_unsafe_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from odoo_instance_sdk.http.admin import AdminSettings

    root = _admin_root(tmp_path, monkeypatch)
    with pytest.raises(ConfigError, match="unavailable"):
        AdminSettings.load()

    _write_admin_config(root)
    path = root / "config" / "admin.toml"
    path.chmod(0o644)
    with pytest.raises(ConfigError, match="private"):
        AdminSettings.load()

    path.chmod(0o600)
    path.write_text('[admin]\npassword_hash = "not-a-hash"\nsession_key = "short"\n')
    with pytest.raises(ConfigError, match="unsupported"):
        AdminSettings.load()


def test_non_headless_startup_rejects_missing_admin_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from odoo_instance_sdk.http.app import create_app

    _admin_root(tmp_path, monkeypatch)
    with pytest.raises(ConfigError, match="unavailable"):
        create_app(
            headless=False,
            static_assets=False,
            monitor=Mock(),
            pgadmin_opener=Mock(),
            runtime_controller=Mock(),
        )


def test_admin_login_logout_and_headless_exclusion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.http.admin import mount_admin
    from odoo_instance_sdk.http.app import create_app

    root = _admin_root(tmp_path, monkeypatch)
    _write_admin_config(root)

    app = FastAPI()
    mount_admin(app)
    client = TestClient(app)
    response = client.get("/admin/", follow_redirects=False)
    assert response.status_code == 303
    assert "/admin/login" in response.headers["location"]

    token = _csrf(client)
    response = client.post(
        "/admin/login",
        data={"username": "admin", "password": "wrong", "csrftoken": token},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "Invalid username or password" in response.text

    response = client.post(
        "/admin/login",
        data={"username": "admin", "password": "admin-password", "csrftoken": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.get("/admin/").status_code == 200
    response = client.post(
        "/admin/logout", data={"csrftoken": _csrf(client)}, follow_redirects=False
    )
    assert response.status_code == 303
    assert client.get("/admin/", follow_redirects=False).status_code == 303

    headless = create_app(headless=True, static_assets=False, monitor=Mock())
    assert (
        TestClient(headless, base_url="http://localhost")
        .get("/admin/", follow_redirects=False)
        .status_code
        == 404
    )


def test_admin_keeps_the_trusted_proxy_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.http.app import ExternalProxyConfig, create_app

    root = _admin_root(tmp_path, monkeypatch)
    _write_admin_config(root)
    app = create_app(
        headless=False,
        static_assets=False,
        monitor=Mock(),
        pgadmin_opener=lambda _environment_id: Mock(),
        runtime_controller=Mock(),
        external_proxy=ExternalProxyConfig(
            allowed_hosts=("panel.example.test",),
            trusted_proxy_addresses=("testclient",),
        ),
    )
    client = TestClient(app, base_url="https://panel.example.test")
    forwarded = {"X-Forwarded-Host": "panel.example.test", "X-Forwarded-Proto": "https"}
    assert client.get("/admin/", headers=forwarded, follow_redirects=False).status_code == 303
    assert (
        client.get(
            "/admin/",
            headers={**forwarded, "X-Forwarded-Host": "attacker.example.test"},
            follow_redirects=False,
        ).status_code
        == 400
    )


def test_catalogue_resources_are_exactly_three_read_only_views(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _admin_root(tmp_path, monkeypatch)
    _write_admin_config(root)
    admin, client, token = _authenticated_client(root)

    from odoo_instance_sdk.http.admin import (
        BackupView,
        EnvironmentHistoryView,
        IncompleteOperationView,
    )

    catalogue_types = {type(view) for view in admin._model_views}
    assert catalogue_types & {BackupView, IncompleteOperationView, EnvironmentHistoryView} == {
        BackupView,
        IncompleteOperationView,
        EnvironmentHistoryView,
    }
    assert (
        len(
            [
                view
                for view in admin._model_views
                if isinstance(view, (BackupView, IncompleteOperationView, EnvironmentHistoryView))
            ]
        )
        == 3
    )

    for key in ("backups", "incomplete-operations", "environment-history"):
        assert client.get(f"/admin/{key}/list").status_code == 200
        assert (
            client.post(
                f"/admin/{key}/create", data={"csrftoken": token}, follow_redirects=False
            ).status_code
            == 403
        )
        assert client.get(f"/admin/{key}/edit?pk=1").status_code == 403
        assert (
            client.post(f"/admin/_api/{key}/import", files={"file": ("rows.csv", b"x")}).status_code
            == 403
        )
        for action in ("delete", "export", "restore"):
            response = client.post(
                f"/admin/_api/{key}/action?name={action}&pks=1",
                headers={"x-csrftoken": token},
            )
            assert response.status_code in {400, 403}

    assert (
        client.get(
            "/admin/backups/list",
            params={"filter": "state__eq=available"},
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/admin/backups/list",
            params={"filter": "state__contains=x"},
        ).status_code
        == 400
    )
    assert client.get("/admin/backups/list", params={"sort": "unknown__asc"}).status_code == 400


def test_catalogue_filters_reject_adversarial_inputs_and_follow_all_pages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _admin_root(tmp_path, monkeypatch)
    _write_admin_config(root)

    from starlette_admin.filters.base import FilterGroup, FilterRule

    from odoo_instance_sdk.http import admin as admin_module
    from odoo_instance_sdk.http.admin import BackupView
    from odoo_instance_sdk.models.backup import BackupFormat, BackupState
    from odoo_instance_sdk.storage.catalog.helpers import BackupSummary, BackupSummaryPage

    now = datetime.now(UTC)
    first = tuple(
        BackupSummary(
            uuid.UUID(int=index + 1),
            "project",
            "label",
            "source",
            "db",
            BackupFormat.DUMP,
            1,
            BackupState.AVAILABLE,
            now,
        )
        for index in range(1000)
    )
    final = BackupSummary(
        uuid.UUID(int=1001),
        "project",
        "label",
        "source",
        "db",
        BackupFormat.DUMP,
        1,
        BackupState.AVAILABLE,
        now,
    )

    class Catalog:
        def __init__(self) -> None:
            self.cursors: list[str | None] = []

        def list_backup_summaries(self, **kwargs: object) -> BackupSummaryPage:
            cursor = kwargs["cursor"]
            assert kwargs["limit"] == 1000
            self.cursors.append(cursor if isinstance(cursor, str) else None)
            return (
                BackupSummaryPage(first, "page-2")
                if cursor is None
                else BackupSummaryPage((final,), None)
            )

        def close(self) -> None:
            pass

    catalog = Catalog()
    monkeypatch.setattr(admin_module, "get_catalog_path", lambda **_: Path("catalog"))
    monkeypatch.setattr(admin_module, "open_catalog_read_only", lambda _path: catalog)
    filters = FilterGroup(rules=[FilterRule("state", "eq", "available")])
    rows = asyncio.run(
        BackupView().find_all(_request(), skip=1000, limit=25, filters=filters, sorts=[])
    )
    assert len(rows) == 1
    assert rows[0].backup_id == final.backup_id
    assert catalog.cursors == [None, "page-2"]

    with pytest.raises(HTTPException, match="Unsupported catalogue filter"):
        asyncio.run(
            BackupView().find_all(
                _request(), filters=FilterGroup(rules=[FilterRule("state", "contains", "x")])
            )
        )
    with pytest.raises(HTTPException, match="Unsupported catalogue sort"):
        asyncio.run(BackupView().find_all(_request("sort=secret__asc")))


def test_secret_form_is_write_only_and_creates_direct_store_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _admin_root(tmp_path, monkeypatch)
    _write_admin_config(root)
    _, client, _ = _authenticated_client(root)

    token = _csrf(client)
    response = client.post(
        "/admin/remote-odoo-secrets/create",
        data={
            "csrftoken": token,
            "base_url": "https://odoo.example/",
            "master_password": "persisted-password",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    listing = client.get("/admin/remote-odoo-secrets/list")
    assert listing.status_code == 200
    assert "odoo.example" in listing.text
    assert "persisted-password" not in listing.text


def test_secret_edit_is_cas_safe_and_delete_uses_submitted_revision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _admin_root(tmp_path, monkeypatch)
    _write_admin_config(root)

    from odoo_instance_sdk.http.admin import RemoteOdooSecretView
    from odoo_instance_sdk.internal.secret_store import SecretStore

    store = SecretStore()
    original = store.create_remote_odoo("https://cas.example", "original-secret")
    view = RemoteOdooSecretView()
    empty_edit = asyncio.run(
        view.edit(
            _request(), original.base_url, {"revision": original.revision, "master_password": ""}
        )
    )
    assert empty_edit.revision == 2
    assert store.lookup_remote_odoo(original.base_url).master_password == "original-secret"

    replaced = asyncio.run(
        view.edit(
            _request(),
            original.base_url,
            {"revision": empty_edit.revision, "master_password": "replacement-secret"},
        )
    )
    assert store.lookup_remote_odoo(original.base_url).master_password == "replacement-secret"
    with pytest.raises(FormValidationError, match="Record changed"):
        asyncio.run(
            view.edit(
                _request(),
                original.base_url,
                {"revision": original.revision, "master_password": "stale"},
            )
        )

    stale_token = f"{original.base_url}|{original.revision}"
    with pytest.raises(ActionFailed, match="reload and retry"):
        asyncio.run(view.delete(_request(), [stale_token]))
    assert store.get_remote_odoo(original.base_url) is not None
    assert (
        asyncio.run(view.delete(_request(f"revision={replaced.revision}"), [original.base_url]))
        == 1
    )
    assert store.get_remote_odoo(original.base_url) is None
