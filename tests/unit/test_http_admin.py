from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import Mock

import pytest

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
