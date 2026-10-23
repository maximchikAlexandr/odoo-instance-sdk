"""Compose the optional authenticated administrator surface."""

from __future__ import annotations

from typing import cast

from .admin_auth import AdminSettings, _AdminAuth
from .admin_catalog import BackupView, EnvironmentHistoryView, IncompleteOperationView
from .admin_secrets import GitLabMemberSecretView, RemoteOdooSecretView
from .admin_support import (
    BaseAdmin,
    Middleware,
    SessionMiddleware,
    _DashboardAdmin,
    _DashboardApp,
)


def mount_admin(app: _DashboardApp, *, secure_cookie: bool = False) -> _DashboardAdmin:
    """Mount the existing auth, catalogue, and secret views onto the monitor."""
    settings = AdminSettings.load()
    admin = BaseAdmin(
        title="OdCLI Admin",
        base_url="/admin",
        auth_provider=_AdminAuth(settings),
        secret_key=settings.session_key,
        middlewares=[
            Middleware(
                SessionMiddleware,
                secret_key=settings.session_key,
                session_cookie="odcli_admin_session",
                same_site="strict",
                https_only=secure_cookie,
            )
        ],
    )
    for view in (
        BackupView(),
        IncompleteOperationView(),
        EnvironmentHistoryView(),
        RemoteOdooSecretView(),
        GitLabMemberSecretView(),
    ):
        admin.add_view(view)
    admin.mount_to(app)
    return cast("_DashboardAdmin", admin)


__all__ = [
    "AdminSettings",
    "BackupView",
    "EnvironmentHistoryView",
    "GitLabMemberSecretView",
    "IncompleteOperationView",
    "RemoteOdooSecretView",
    "mount_admin",
]
