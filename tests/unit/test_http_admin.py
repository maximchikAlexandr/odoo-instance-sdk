from __future__ import annotations

import asyncio
import os
import re
import secrets
import uuid
from collections.abc import Callable, MutableMapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import Mock
from urllib.parse import parse_qs

import pytest

from odoo_instance_sdk.exceptions import ConfigError

pytestmark = pytest.mark.dashboard

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.http.admin_auth import _LoginAttemptBudget
    from odoo_instance_sdk.http.admin_support import (
        _DashboardAdmin,
        _FilterRule as _FilterRuleProtocol,
        _RequestQueryParams,
        _RequestState as _RequestStateProtocol,
    )


@dataclass(frozen=True, slots=True)
class _LoginResponse:
    status_code: int
    text: str


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
        f'session_key = "{secrets.token_urlsafe(32)}"\n',
        encoding="utf-8",
    )
    path.chmod(0o600)


@dataclass(slots=True)
class _AdminLoginCase:
    client: TestClient
    headers: dict[str, str]
    budget: _LoginAttemptBudget
    verification_calls: list[tuple[str, str]]


@pytest.fixture(params=[False, True], ids=["local", "external-proxy"])
def admin_login_case(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> _AdminLoginCase:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.http import admin_auth
    from odoo_instance_sdk.http.app import ExternalProxyConfig, create_app

    root = _admin_root(tmp_path, monkeypatch)
    _write_admin_config(root)
    budget = admin_auth._LoginAttemptBudget()
    monkeypatch.setattr(admin_auth, "_LOGIN_ATTEMPTS", budget)

    from pwdlib import PasswordHash

    original_verify: Callable[[PasswordHash, str, str], bool] = PasswordHash.verify
    verification_calls: list[tuple[str, str]] = []

    def recording_verify(self: PasswordHash, password: str, encoded: str) -> bool:
        verification_calls.append((password, encoded))
        return original_verify(self, password, encoded)

    monkeypatch.setattr(PasswordHash, "verify", recording_verify)

    external = bool(request.param)
    if external:
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
        headers = {
            "X-Forwarded-Host": "panel.example.test",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-For": "203.0.113.88",
        }
    else:
        app = FastAPI()
        from odoo_instance_sdk.http.admin import mount_admin

        mount_admin(app)
        client = TestClient(app)
        headers = {}
    return _AdminLoginCase(client, headers, budget, verification_calls)


def _admin_login(case: _AdminLoginCase, username: str, password: str, token: str) -> _LoginResponse:
    response = case.client.post(
        "/admin/login",
        data={"username": username, "password": password, "csrftoken": token},
        headers=case.headers,
        follow_redirects=False,
    )
    return _LoginResponse(response.status_code, response.text)


def test_admin_bootstrap_generates_owner_only_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from odoo_instance_sdk.http.admin import AdminSettings
    from scripts import bootstrap_admin_config

    monkeypatch.setenv("ODCLI_USER_ROOT", str(tmp_path / "user"))
    token_sizes: list[int] = []

    def token_urlsafe(size: int) -> str:
        token_sizes.append(size)
        return "generated-session-key-with-more-than-thirty-two-characters"

    monkeypatch.setattr(secrets, "token_urlsafe", token_urlsafe)
    path = bootstrap_admin_config.write_admin_config("test-password")
    settings = AdminSettings.load()

    assert token_sizes == [32]
    assert settings.password_hash.startswith("$argon2")
    assert settings.session_key == "generated-session-key-with-more-than-thirty-two-characters"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    with pytest.raises(FileExistsError):
        bootstrap_admin_config.write_admin_config("replacement-password")


def _csrf(client: object, *, headers: dict[str, str] | None = None) -> str:
    response = client.get("/admin/login", headers=headers)  # type: ignore[attr-defined]
    match = re.search(r'name="csrftoken"[^>]*value="([^"]+)', response.text)
    assert match is not None
    return match.group(1)


def _authenticated_client(root: Path) -> tuple[_DashboardAdmin, TestClient, str]:
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


@dataclass(slots=True)
class _CatalogFilterRule:
    field: str
    filter: str
    value: str | datetime | None
    value2: str | datetime | None = None


@dataclass(slots=True)
class _CatalogFilterGroup:
    logic: str
    rules: Sequence[_FilterRuleProtocol]

    def is_empty(self) -> bool:
        return not self.rules


class _QueryParams:
    def __init__(self, query: str) -> None:
        self._values = parse_qs(query)

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._values.get(key, [default])[0]

    def getlist(self, key: str) -> list[str]:
        return self._values.get(key, [])


class _RequestState:
    action = ""


class _AdminRequest:
    query_params: _RequestQueryParams
    session: MutableMapping[str, str]
    state: _RequestStateProtocol

    def __init__(self, query: str) -> None:
        self.query_params = _QueryParams(query)
        self.session: dict[str, str] = {}
        self.state = _RequestState()


def _request(query: str = "") -> _AdminRequest:
    return _AdminRequest(query)


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


def test_admin_login_failure_responses_are_indistinguishable(
    admin_login_case: _AdminLoginCase,
) -> None:
    token = _csrf(admin_login_case.client, headers=admin_login_case.headers)
    unknown = _admin_login(admin_login_case, "other", "wrong", token)
    wrong_password = _admin_login(admin_login_case, "admin", "wrong", token)
    assert unknown.status_code == wrong_password.status_code == 400
    assert unknown.text == wrong_password.text
    assert "Invalid username or password" in unknown.text
    assert "Invalid username or password" in wrong_password.text


def test_admin_login_budget_blocks_argon2_after_max_failures(
    admin_login_case: _AdminLoginCase,
) -> None:
    token = _csrf(admin_login_case.client, headers=admin_login_case.headers)
    for username, password in [
        ("other", "wrong"),
        ("admin", "wrong"),
        ("admin", "wrong"),
        ("admin", "wrong"),
        ("admin", "wrong"),
    ]:
        response = _admin_login(admin_login_case, username, password, token)
        assert response.status_code == 400
        assert "Invalid username or password" in response.text
    assert len(admin_login_case.verification_calls) == admin_login_case.budget._MAX_FAILURES

    limited = _admin_login(admin_login_case, "admin", "admin-password", token)
    assert limited.status_code == 400
    assert "Invalid username or password" in limited.text
    assert len(admin_login_case.verification_calls) == admin_login_case.budget._MAX_FAILURES


def test_admin_login_budget_resets_after_window(
    admin_login_case: _AdminLoginCase,
) -> None:
    token = _csrf(admin_login_case.client, headers=admin_login_case.headers)
    for _ in range(admin_login_case.budget._MAX_FAILURES):
        response = _admin_login(admin_login_case, "admin", "wrong", token)
        assert response.status_code == 400
    assert len(admin_login_case.verification_calls) == admin_login_case.budget._MAX_FAILURES

    admin_login_case.budget._window_started -= admin_login_case.budget._WINDOW_SECONDS + 1
    reset = _admin_login(admin_login_case, "admin", "admin-password", token)
    assert reset.status_code == 303
    assert len(admin_login_case.verification_calls) == admin_login_case.budget._MAX_FAILURES + 1


def test_admin_login_budget_reserves_verification_slots() -> None:
    from odoo_instance_sdk.http.admin_auth import _LoginAttemptBudget

    budget = _LoginAttemptBudget()
    assert all(budget.allow() for _ in range(budget._MAX_FAILURES))
    assert not budget.allow()
    for _ in range(budget._MAX_FAILURES):
        budget.failed()
    assert not budget.allow()
    budget._window_started -= budget._WINDOW_SECONDS + 1
    assert budget.allow()


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
    assert (
        client.get(
            "/admin/",
            headers={**forwarded, "X-Forwarded-Proto": "http"},
            follow_redirects=False,
        ).status_code
        == 400
    )

    # Caddy connects to the monitor over HTTP. Admin must still generate HTTPS
    # asset URLs for the external browser after the proxy boundary is checked.
    upstream_client = TestClient(app, base_url="http://panel.example.test")
    login_page = upstream_client.get("/admin/login", headers=forwarded)
    assert login_page.status_code == 200
    assert "https://panel.example.test/admin/static/css/vendor/tabler.min.css" in login_page.text
    assert "http://panel.example.test/admin/static/css/" not in login_page.text
    css = upstream_client.get("/admin/static/css/vendor/tabler.min.css", headers=forwarded)
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]


def test_catalogue_resources_are_exactly_three_read_only_views(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _admin_root(tmp_path, monkeypatch)
    _write_admin_config(root)
    admin, client, _token = _authenticated_client(root)

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


def test_catalogue_mutations_are_denied_for_all_resources_and_actions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _admin_root(tmp_path, monkeypatch)
    _write_admin_config(root)
    _admin, client, token = _authenticated_client(root)

    for key in ("backups", "incomplete-operations", "environment-history"):
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


def test_catalogue_filters_follow_supported_exact_and_date_ranges(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _admin_root(tmp_path, monkeypatch)
    _write_admin_config(root)
    _admin, client, _token = _authenticated_client(root)

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
    for key, field in (
        ("backups", "downloaded_at"),
        ("incomplete-operations", "occurred_at"),
        ("environment-history", "occurred_at"),
    ):
        assert (
            client.get(
                f"/admin/{key}/list",
                params={"filter": f"{field}__between=2025-01-01T00:00:00Z..2025-01-02T00:00:00Z"},
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/admin/{key}/list",
                params={"filter": f"{field}__between=not-a-date..2025-01-02T00:00:00Z"},
            ).status_code
            == 400
        )
        assert (
            client.get(
                f"/admin/{key}/list",
                params={"filter": f"{field}__between=2025-01-02T00:00:00Z..2025-01-01T00:00:00Z"},
            ).status_code
            == 400
        )


def test_catalogue_filters_follow_all_pages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _admin_root(tmp_path, monkeypatch)
    _write_admin_config(root)

    from odoo_instance_sdk.http import admin_catalog as admin_module
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
            limit = kwargs["limit"]
            assert isinstance(limit, int) and 1 <= limit <= 1000
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
    filters = _CatalogFilterGroup(
        logic="and",
        rules=(_CatalogFilterRule("state", "eq", "available"),),
    )
    rows = asyncio.run(
        BackupView().find_all(_request(), skip=1000, limit=25, filters=filters, sorts=[])
    )
    assert len(rows) == 1
    assert rows[0].backup_id == final.backup_id
    assert catalog.cursors == [None, "page-2"]


def test_catalogue_filters_reject_adversarial_inputs() -> None:
    from starlette.exceptions import HTTPException

    from odoo_instance_sdk.http.admin import BackupView

    with pytest.raises(HTTPException, match="Unsupported catalogue filter"):
        asyncio.run(
            BackupView().find_all(
                _request(),
                filters=_CatalogFilterGroup(
                    logic="and",
                    rules=(_CatalogFilterRule("state", "contains", "x"),),
                ),
            )
        )
    with pytest.raises(HTTPException, match="Catalogue sorting is unsupported"):
        asyncio.run(BackupView().find_all(_request("sort=secret__asc")))


def test_catalogue_http_projection_redacts_unsafe_persisted_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _admin_root(tmp_path, monkeypatch)
    _write_admin_config(root)

    from odoo_instance_sdk.internal.applied_settings import LEGACY_UNKNOWN_APPLIED_SETTINGS_JSON
    from odoo_instance_sdk.internal.paths import get_catalog_path
    from odoo_instance_sdk.storage.catalog import BackupCatalog

    raw_path = "/private/raw/odoo/admin.toml"
    raw_json = '{"admin_passwd":"raw-secret","config":"/private/raw/config.json"}'
    raw_command = "odoo --config /private/raw/odoo.conf admin_passwd=raw-secret"
    now = datetime.now(UTC)

    backup_id = str(uuid.uuid4())
    catalog = BackupCatalog(db_path=get_catalog_path(ensure_exists=True))
    catalog.start_download(
        backup_id,
        raw_path,
        raw_json,
        "dump",
        False,
        Path(raw_path),
        source_name=raw_path,
    )
    catalog.fail_download(backup_id, "raw-error", raw_command)
    catalog.create_environment(
        {
            "id": "env-raw",
            "name": raw_path,
            "repository_root": raw_path,
            "git_common_dir": raw_path,
            "branch": "main",
            "base_ref": "main",
            "worktree_path": raw_path,
            "generated_config_path": raw_path,
            "python_environment_path": raw_path,
            "python_environment_owned": False,
            "dependency_lock_path": raw_path,
            "db_mode": "copy",
            "source_db_name": raw_json,
            "target_db_name": raw_json,
            "runtime_json": raw_json,
            "state": "failed",
            "created_at": now.isoformat(),
            "last_used_at": now.isoformat(),
            "last_error": raw_command,
            "applied_settings_json": LEGACY_UNKNOWN_APPLIED_SETTINGS_JSON,
            "recovery_json": raw_json,
            "checkout_repository_root": raw_path,
            "checkout_git_common_dir": raw_path,
            "checkout_commit_sha": raw_json,
            "code_ownership": "sdk_owned",
            "artifact_root": raw_path,
            "adoption_input_fingerprint": raw_json,
        }
    )
    catalog.add_environment_event("env-raw", "sync", "failed", message=raw_command)
    catalog.close()

    _, client, _ = _authenticated_client(root)
    for key in ("backups", "incomplete-operations", "environment-history"):
        response = client.get(f"/admin/{key}/list")
        assert response.status_code == 200
        assert raw_path not in response.text
        assert raw_json not in response.text
        assert raw_command not in response.text
        assert "raw-secret" not in response.text


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
    from starlette_admin.exceptions import ActionFailed, FormValidationError

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
    stored = store.lookup_remote_odoo(original.base_url)
    assert stored is not None
    assert stored.master_password == "original-secret"

    replaced = asyncio.run(
        view.edit(
            _request(),
            original.base_url,
            {"revision": empty_edit.revision, "master_password": "replacement-secret"},
        )
    )
    stored = store.lookup_remote_odoo(original.base_url)
    assert stored is not None
    assert stored.master_password == "replacement-secret"
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
