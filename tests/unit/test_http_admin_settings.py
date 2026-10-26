from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.dashboard


def test_authenticated_settings_admin_create_edit_delete_and_stale_rejection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.http.admin import mount_admin
    from odoo_instance_sdk.internal.paths import get_catalog_path
    from odoo_instance_sdk.storage.catalog import BackupCatalog
    from scripts.bootstrap_admin_config import write_admin_config

    user_root = tmp_path / "user"
    user_root.mkdir(mode=0o700)
    monkeypatch.setenv("ODCLI_USER_ROOT", str(user_root))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    write_admin_config("admin-password")
    catalog = BackupCatalog(db_path=get_catalog_path())
    catalog.close()

    app = FastAPI()
    admin = mount_admin(app)
    client = TestClient(app)
    csrf_page = client.get("/admin/login")
    token = re.search(r'name="csrftoken"[^>]*value="([^"]+)', csrf_page.text)
    assert token is not None
    response = client.post(
        "/admin/login",
        data={"username": "admin", "password": "admin-password", "csrftoken": token.group(1)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    csrf = token.group(1)

    mr_key = "gitlab.mr.default_assignee:https://example.test/group/project"
    invalid_mr = client.post(
        "/admin/settings/create",
        data={"csrftoken": csrf, "key": mr_key, "value_json": "true"},
        follow_redirects=False,
    )
    assert invalid_mr.status_code == 422
    assert "value_json" in invalid_mr.text
    assert "Setting changed; reload and retry." not in invalid_mr.text

    create = client.post(
        "/admin/settings/create",
        data={"csrftoken": csrf, "key": "feature.name", "value_json": "true"},
        follow_redirects=False,
    )
    assert create.status_code == 303
    second_create = client.post(
        "/admin/settings/create",
        data={"csrftoken": csrf, "key": "feature.other", "value_json": '"existing"'},
        follow_redirects=False,
    )
    assert second_create.status_code == 303
    listing = client.get("/admin/settings/list")
    assert listing.status_code == 200
    assert {getattr(view, "menu_label") for view in admin._model_views} >= {"Settings"}

    catalog = BackupCatalog(db_path=get_catalog_path())
    try:
        original = catalog.get_setting("feature.name")
    finally:
        catalog.close()
    assert original is not None

    invalid_rename = client.post(
        f"/admin/settings/edit?pk={original.route_id}",
        data={"csrftoken": csrf, "key": mr_key, "value_json": "true"},
        follow_redirects=False,
    )
    assert invalid_rename.status_code == 422
    assert "value_json" in invalid_rename.text

    duplicate_rename = client.post(
        f"/admin/settings/edit?pk={original.route_id}",
        data={"csrftoken": csrf, "key": "feature.other", "value_json": '"conflict"'},
        follow_redirects=False,
    )
    assert duplicate_rename.status_code == 422
    assert "key" in duplicate_rename.text
    assert "Setting changed; reload and retry." not in duplicate_rename.text
    catalog = BackupCatalog(db_path=get_catalog_path())
    try:
        assert catalog.get_setting("feature.name") == original
        assert catalog.get_setting("feature.other") is not None
    finally:
        catalog.close()

    edit = client.post(
        f"/admin/settings/edit?pk={original.route_id}",
        data={"csrftoken": csrf, "key": "feature.name", "value_json": '"saved-first"'},
        follow_redirects=False,
    )
    assert edit.status_code == 303
    catalog = BackupCatalog(db_path=get_catalog_path())
    try:
        saved = catalog.get_setting("feature.name")
    finally:
        catalog.close()
    assert saved is not None
    assert (saved.value, saved.revision) == ("saved-first", 2)

    stale_get = client.get(f"/admin/settings/edit?pk={original.route_id}")
    assert stale_get.status_code == 404
    stale_edit = client.post(
        f"/admin/settings/edit?pk={original.route_id}",
        data={"csrftoken": csrf, "key": "feature.name", "value_json": '"stale-edit"'},
        follow_redirects=False,
    )
    assert stale_edit.status_code == 409
    assert "Setting changed; reload and retry." in stale_edit.text
    assert "stale-edit" not in stale_edit.text
    stale_delete = client.post(
        f"/admin/_api/settings/row-action?name=delete&pk={original.route_id}",
        data={"csrftoken": csrf},
    )
    assert stale_delete.status_code == 400
    assert "Setting changed; reload and retry." in stale_delete.text
    catalog = BackupCatalog(db_path=get_catalog_path())
    try:
        assert catalog.get_setting("feature.name") == saved
    finally:
        catalog.close()

    delete = client.post(
        f"/admin/_api/settings/row-action?name=delete&pk={saved.route_id}",
        data={"csrftoken": csrf},
    )
    assert delete.status_code == 200
    catalog = BackupCatalog(db_path=get_catalog_path())
    try:
        assert catalog.get_setting("feature.name") is None
    finally:
        catalog.close()
