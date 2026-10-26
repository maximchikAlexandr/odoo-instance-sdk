from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

pytestmark = pytest.mark.dashboard

if TYPE_CHECKING:
    from odoo_instance_sdk.http.admin_support import Request


def test_authenticated_settings_admin_crud_and_stale_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette_admin.exceptions import FormValidationError

    from odoo_instance_sdk.http.admin import mount_admin
    from odoo_instance_sdk.http.admin_settings import CatalogSettingView
    from odoo_instance_sdk.internal.paths import get_catalog_path
    from odoo_instance_sdk.storage.catalog import BackupCatalog
    from scripts.bootstrap_admin_config import write_admin_config

    user_root = tmp_path / "user"
    user_root.mkdir(mode=0o700)
    monkeypatch.setenv("ODCLI_USER_ROOT", str(user_root))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    os.chmod(tmp_path / "data", 0o700) if (tmp_path / "data").exists() else None
    write_admin_config("admin-password")
    catalog = BackupCatalog(db_path=get_catalog_path())
    catalog.close()

    app = FastAPI()
    admin = mount_admin(app)
    client = TestClient(app)
    csrf_page = client.get("/admin/login")
    import re

    token = re.search(r'name="csrftoken"[^>]*value="([^"]+)', csrf_page.text)
    assert token is not None
    response = client.post(
        "/admin/login",
        data={"username": "admin", "password": "admin-password", "csrftoken": token.group(1)},
        follow_redirects=False,
    )
    assert response.status_code == 303

    token_value = token.group(1)
    create = client.post(
        "/admin/settings/create",
        data={
            "csrftoken": token_value,
            "key": "gitlab.mr.default_assignee:https://example.test/group/project",
            "value_json": '"octocat"',
        },
        follow_redirects=False,
    )
    assert create.status_code == 303
    listing = client.get("/admin/settings/list")
    assert listing.status_code == 200
    assert "octocat" in listing.text
    assert {getattr(view, "menu_label") for view in admin._model_views} >= {"Settings"}

    view = CatalogSettingView()
    read_catalog = BackupCatalog(db_path=get_catalog_path())
    try:
        row = read_catalog.get_setting(
            "gitlab.mr.default_assignee:https://example.test/group/project"
        )
    finally:
        read_catalog.close()
    assert row is not None
    request = cast("Request", _request())
    updated = asyncio.run(view.edit(request, row.route_id, {"key": row.key, "value_json": "42"}))
    with pytest.raises(FormValidationError):
        asyncio.run(view.delete(request, [row.route_id]))
    assert updated.revision == 2

    with pytest.raises(FormValidationError) as error:
        asyncio.run(
            view.validate(
                request,
                {"key": row.key, "value_json": "true"},
            )
        )
    assert error.value.has("value_json")


class _Request:
    query_params: object
    session: dict[str, str]
    state: object

    def __init__(self) -> None:
        self.session = {}


def _request() -> _Request:
    from types import SimpleNamespace

    request = _Request()
    request.query_params = SimpleNamespace(get=lambda *_args: None, getlist=lambda *_args: [])
    request.state = SimpleNamespace(action="edit")
    return request
