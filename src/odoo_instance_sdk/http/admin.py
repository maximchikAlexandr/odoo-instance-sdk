"""Optional authenticated administrator surface for the local monitor.

The module is imported only by non-headless monitor composition.  Catalogue
views consume the existing safe projections directly; Starlette Admin's
generic mutation, import, and export actions are disabled for those views.
"""

from __future__ import annotations

import os
import re
import stat
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, fields as dataclass_fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette_admin import (
    BaseAdmin,
    BaseModelView,
    ComputedField,
    DateTimeField,
    IntegerField,
    PasswordField,
    StringField,
)
from starlette_admin.auth import AdminUser
from starlette_admin.auth.password import AuthProvider
from starlette_admin.exceptions import FormValidationError, LoginFailed

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.paths import get_admin_config_path, get_catalog_path
from odoo_instance_sdk.internal.resource_inventory import open_catalog_read_only
from odoo_instance_sdk.internal.secret_store import (
    GitLabMemberSecret,
    RemoteOdooSecret,
    SecretStore,
)
from odoo_instance_sdk.storage.catalog.helpers import (
    BackupSummary,
    EnvironmentHistoryEvent,
    IncompleteOperation,
)

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette_admin.filters.base import FilterGroup

_SESSION_KEY_MINIMUM = 32
_ADMIN_USER = "admin"
_PRIVATE_BITS = stat.S_IRWXG | stat.S_IRWXO
_ARGON2_HASH = re.compile(r"\$argon2(?:id|i|d)\$[A-Za-z0-9=,.$+/:-]+\Z")


@dataclass(frozen=True, slots=True, repr=False)
class AdminSettings:
    password_hash: str
    session_key: str

    def __repr__(self) -> str:
        return "AdminSettings(password_hash=<redacted>, session_key=<redacted>)"

    @classmethod
    def load(cls, path: Path | None = None) -> AdminSettings:
        selected = get_admin_config_path(ensure_exists=False)
        if path is not None and path.resolve(strict=False) != selected.resolve(strict=False):
            raise ConfigError("administrator configuration must use the canonical user config path")
        try:
            descriptor = os.open(
                selected,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            )
        except OSError as exc:
            raise ConfigError("administrator configuration is unavailable") from exc
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & _PRIVATE_BITS
            ):
                raise ConfigError("administrator configuration must be a private regular file")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                try:
                    raw = tomllib.load(handle)
                except (OSError, tomllib.TOMLDecodeError) as exc:
                    raise ConfigError("administrator configuration is invalid") from exc
        finally:
            if descriptor != -1:
                os.close(descriptor)
        data = raw.get("admin", raw)
        if not isinstance(data, dict) or set(data) != {"password_hash", "session_key"}:
            raise ConfigError(
                "administrator configuration must contain password_hash and session_key"
            )
        password_hash = data.get("password_hash")
        session_key = data.get("session_key")
        if not isinstance(password_hash, str) or not _ARGON2_HASH.fullmatch(password_hash):
            raise ConfigError("administrator password hash is unsupported")
        if (
            not isinstance(session_key, str)
            or len(session_key) < _SESSION_KEY_MINIMUM
            or not session_key.strip()
            or "\x00" in session_key
        ):
            raise ConfigError("administrator session key is invalid")
        return cls(password_hash=password_hash, session_key=session_key)


class _AdminAuth(AuthProvider):
    def __init__(self, settings: AdminSettings) -> None:
        super().__init__()
        self._settings = settings

    async def login(
        self, username: str, password: str, remember_me: bool, request: Request
    ) -> None:
        del remember_me
        if username != _ADMIN_USER:
            raise LoginFailed("Invalid username or password")
        try:
            from pwdlib import PasswordHash

            valid = PasswordHash.recommended().verify(password, self._settings.password_hash)
        except Exception as exc:
            raise LoginFailed("Invalid username or password") from exc
        if not valid:
            raise LoginFailed("Invalid username or password")
        request.session["admin_user"] = _ADMIN_USER

    async def logout(self, request: Request) -> None:
        request.session.clear()

    async def authenticate(self, request: Request) -> AdminUser | None:
        if request.session.get("admin_user") == _ADMIN_USER:
            return AdminUser(username=_ADMIN_USER)
        return None


def _page(items: Sequence[Any], *, skip: int, limit: int, q: str | None) -> tuple[Any, ...]:
    values = tuple(items)
    if q:
        needle = q.casefold()
        values = tuple(
            item
            for item in values
            if needle
            in " ".join(
                str(getattr(item, field.name)) for field in dataclass_fields(item)
            ).casefold()
        )
    if limit < 0:
        return values[skip:]
    return values[skip : skip + min(limit, 1000)]


class _ReadOnlyCatalogView(BaseModelView):
    exporters = ()
    importers = ()
    actions = ()
    row_actions = ()
    page_size = 25
    page_size_options = (25, 50, 100)

    def can_create(self, request: Request) -> bool:
        return False

    def can_edit(self, request: Request) -> bool:
        return False

    def can_delete(self, request: Request) -> bool:
        return False

    def can_export(self, request: Request) -> bool:
        return False

    def can_import(self, request: Request) -> bool:
        return False

    async def create(self, request: Request, data: dict[str, Any]) -> Any:
        raise PermissionError("catalogue resources are read-only")

    async def edit(self, request: Request, pk: Any, data: dict[str, Any]) -> Any:
        raise PermissionError("catalogue resources are read-only")

    async def delete(self, request: Request, pks: list[Any]) -> int:
        raise PermissionError("catalogue resources are read-only")


async def _backup_events(request: Request, obj: BackupSummary) -> str:
    del request
    catalog = open_catalog_read_only(get_catalog_path(ensure_exists=False))
    if catalog is None:
        return ""
    try:
        page = catalog.list_backup_events(str(obj.backup_id), limit=100)
        return "; ".join(
            f"{event.event_type.value} at {event.occurred_at.isoformat()}"
            + (f": {event.message}" if event.message else "")
            for event in page.items
        )
    finally:
        catalog.close()


class BackupView(_ReadOnlyCatalogView):
    key = "backups"
    display_name = "Backups"
    pk_attr = "backup_id"
    fields = (
        StringField("backup_id", read_only=True),
        StringField("project_id", read_only=True),
        StringField("project_label", read_only=True),
        StringField("source_label", read_only=True),
        StringField("database_name", read_only=True),
        StringField("format", read_only=True),
        IntegerField("size_bytes", read_only=True),
        StringField("state", read_only=True),
        DateTimeField("downloaded_at", read_only=True),
        ComputedField("events", getter=_backup_events, read_only=True),
    )

    async def find_all(
        self,
        request: Request,
        skip: int = 0,
        limit: int = 100,
        q: str | None = None,
        sorts: Sequence[tuple[str, str]] | None = None,
        filters: FilterGroup | None = None,
    ) -> Sequence[BackupSummary]:
        del request, sorts, filters
        catalog = open_catalog_read_only(get_catalog_path(ensure_exists=False))
        if catalog is None:
            return ()
        try:
            page = catalog.list_backup_summaries(limit=1000)
            return _page(page.items, skip=skip, limit=limit, q=q)
        finally:
            catalog.close()

    async def count(
        self, request: Request, q: str | None = None, filters: FilterGroup | None = None
    ) -> int:
        return len(await self.find_all(request, q=q, limit=-1, filters=filters))

    async def find_by_pk(self, request: Request, pk: Any) -> BackupSummary | None:
        del request
        catalog = open_catalog_read_only(get_catalog_path(ensure_exists=False))
        if catalog is None:
            return None
        try:
            page = catalog.list_backup_summaries(limit=1000)
            return next((item for item in page.items if str(item.backup_id) == str(pk)), None)
        finally:
            catalog.close()

    async def find_by_pks(self, request: Request, pks: list[Any]) -> Sequence[BackupSummary]:
        result: list[BackupSummary] = []
        for pk in pks:
            item = await self.find_by_pk(request, pk)
            if item is not None:
                result.append(item)
        return tuple(result)


class IncompleteOperationView(_ReadOnlyCatalogView):
    key = "incomplete-operations"
    display_name = "Incomplete operations"
    pk_attr = "sequence"
    fields = (
        IntegerField("sequence", read_only=True),
        StringField("object_type", read_only=True),
        StringField("object_id", read_only=True),
        StringField("state", read_only=True),
        DateTimeField("occurred_at", read_only=True),
        StringField("error", read_only=True),
    )

    async def find_all(
        self,
        request: Request,
        skip: int = 0,
        limit: int = 100,
        q: str | None = None,
        sorts: Sequence[tuple[str, str]] | None = None,
        filters: FilterGroup | None = None,
    ) -> Sequence[IncompleteOperation]:
        del request, sorts, filters
        catalog = open_catalog_read_only(get_catalog_path(ensure_exists=False))
        if catalog is None:
            return ()
        try:
            return _page(
                catalog.list_incomplete_operations(limit=1000).items, skip=skip, limit=limit, q=q
            )
        finally:
            catalog.close()

    async def count(
        self, request: Request, q: str | None = None, filters: FilterGroup | None = None
    ) -> int:
        return len(await self.find_all(request, q=q, limit=-1, filters=filters))

    async def find_by_pk(self, request: Request, pk: Any) -> IncompleteOperation | None:
        return next(
            (
                item
                for item in await self.find_all(request, limit=-1)
                if str(item.sequence) == str(pk)
            ),
            None,
        )

    async def find_by_pks(self, request: Request, pks: list[Any]) -> Sequence[IncompleteOperation]:
        result: list[IncompleteOperation] = []
        for pk in pks:
            item = await self.find_by_pk(request, pk)
            if item is not None:
                result.append(item)
        return tuple(result)


class EnvironmentHistoryView(_ReadOnlyCatalogView):
    key = "environment-history"
    display_name = "Environment history"
    pk_attr = "sequence"
    fields = (
        IntegerField("sequence", read_only=True),
        StringField("environment_id", read_only=True),
        StringField("environment_name", read_only=True),
        StringField("project_id", read_only=True),
        DateTimeField("occurred_at", read_only=True),
        StringField("operation", read_only=True),
        StringField("outcome", read_only=True),
        StringField("state", read_only=True),
        StringField("message", read_only=True),
    )

    async def find_all(
        self,
        request: Request,
        skip: int = 0,
        limit: int = 100,
        q: str | None = None,
        sorts: Sequence[tuple[str, str]] | None = None,
        filters: FilterGroup | None = None,
    ) -> Sequence[EnvironmentHistoryEvent]:
        del request, sorts, filters
        catalog = open_catalog_read_only(get_catalog_path(ensure_exists=False))
        if catalog is None:
            return ()
        try:
            return _page(
                catalog.list_environment_history(limit=1000).items, skip=skip, limit=limit, q=q
            )
        finally:
            catalog.close()

    async def count(
        self, request: Request, q: str | None = None, filters: FilterGroup | None = None
    ) -> int:
        return len(await self.find_all(request, q=q, limit=-1, filters=filters))

    async def find_by_pk(self, request: Request, pk: Any) -> EnvironmentHistoryEvent | None:
        return next(
            (
                item
                for item in await self.find_all(request, limit=-1)
                if str(item.sequence) == str(pk)
            ),
            None,
        )

    async def find_by_pks(
        self, request: Request, pks: list[Any]
    ) -> Sequence[EnvironmentHistoryEvent]:
        result: list[EnvironmentHistoryEvent] = []
        for pk in pks:
            item = await self.find_by_pk(request, pk)
            if item is not None:
                result.append(item)
        return tuple(result)


class _SecretView(BaseModelView):
    exporters = ()
    importers = ()
    actions = ()
    row_actions = None
    secret_field = "secret"

    def can_export(self, request: Request) -> bool:
        return False

    def can_import(self, request: Request) -> bool:
        return False

    async def validate(self, request: Request, data: dict[str, Any]) -> None:
        from starlette_admin.types import RequestAction

        errors: dict[str | int, Any] = {}
        if (
            request.state.action == RequestAction.CREATE
            and not str(data.get(self.secret_field) or "").strip()
        ):
            errors[self.secret_field] = "A nonblank secret is required."
        if self.key == "gitlab-member-secrets" and not str(data.get("login") or "").strip():
            errors["login"] = "A nonblank login is required."
        if errors:
            raise FormValidationError(errors)


class RemoteOdooSecretView(_SecretView):
    key = "remote-odoo-secrets"
    display_name = "Remote Odoo secrets"
    pk_attr = "base_url"
    show_pk_in_forms = True
    secret_field = "master_password"
    fields = (
        StringField("base_url", exclude_from_edit=True),
        IntegerField(
            "revision",
            read_only=False,
            exclude_from_list=True,
            exclude_from_detail=True,
            exclude_from_create=True,
        ),
        StringField("created_at", read_only=True, exclude_from_create=True, exclude_from_edit=True),
        StringField("updated_at", read_only=True, exclude_from_create=True, exclude_from_edit=True),
        PasswordField(
            "master_password",
            label="Master password",
            exclude_from_list=True,
            exclude_from_detail=True,
            exclude_from_export=True,
            exclude_from_import=True,
        ),
    )

    @staticmethod
    def _store() -> SecretStore:
        return SecretStore()

    async def find_all(
        self,
        request: Request,
        skip: int = 0,
        limit: int = 100,
        q: str | None = None,
        sorts: Sequence[tuple[str, str]] | None = None,
        filters: FilterGroup | None = None,
    ) -> Sequence[RemoteOdooSecret]:
        del request, sorts, filters
        return _page(self._store().list_remote_odoo(), skip=skip, limit=limit, q=q)

    async def count(
        self, request: Request, q: str | None = None, filters: FilterGroup | None = None
    ) -> int:
        return len(await self.find_all(request, q=q, limit=-1, filters=filters))

    async def find_by_pk(self, request: Request, pk: Any) -> RemoteOdooSecret | None:
        del request
        return self._store().get_remote_odoo(str(pk))

    async def find_by_pks(self, request: Request, pks: list[Any]) -> Sequence[RemoteOdooSecret]:
        result: list[RemoteOdooSecret] = []
        for pk in pks:
            item = await self.find_by_pk(request, pk)
            if item is not None:
                result.append(item)
        return tuple(result)

    async def create(self, request: Request, data: dict[str, Any]) -> RemoteOdooSecret:
        return self._store().create_remote_odoo(
            str(data["base_url"]), str(data.get("master_password") or "")
        )

    async def edit(self, request: Request, pk: Any, data: dict[str, Any]) -> RemoteOdooSecret:
        return self._store().update_remote_odoo(
            str(pk),
            revision=int(data["revision"]),
            master_password=str(data.get("master_password") or ""),
        )

    async def delete(self, request: Request, pks: list[Any]) -> int:
        for pk in pks:
            record = self._store().get_remote_odoo(str(pk))
            if record is not None:
                self._store().delete_remote_odoo(record.base_url, revision=record.revision)
        return len(pks)


class GitLabMemberSecretView(_SecretView):
    key = "gitlab-member-secrets"
    display_name = "GitLab member secrets"
    pk_attr = "member_id"
    show_pk_in_forms = True
    secret_field = "token"
    fields = (
        StringField("member_id", exclude_from_edit=True),
        StringField("host", exclude_from_edit=True),
        StringField("login"),
        IntegerField(
            "revision",
            read_only=False,
            exclude_from_list=True,
            exclude_from_detail=True,
            exclude_from_create=True,
        ),
        StringField("created_at", read_only=True, exclude_from_create=True, exclude_from_edit=True),
        StringField("updated_at", read_only=True, exclude_from_create=True, exclude_from_edit=True),
        PasswordField(
            "token",
            label="Token",
            exclude_from_list=True,
            exclude_from_detail=True,
            exclude_from_export=True,
            exclude_from_import=True,
        ),
    )

    @staticmethod
    def _store() -> SecretStore:
        return SecretStore()

    async def get_pk_value(self, request: Request, obj: GitLabMemberSecret) -> str:
        del request
        return f"{obj.member_id}|{obj.host}"

    async def find_all(
        self,
        request: Request,
        skip: int = 0,
        limit: int = 100,
        q: str | None = None,
        sorts: Sequence[tuple[str, str]] | None = None,
        filters: FilterGroup | None = None,
    ) -> Sequence[GitLabMemberSecret]:
        del request, sorts, filters
        return _page(self._store().list_gitlab_members(), skip=skip, limit=limit, q=q)

    async def count(
        self, request: Request, q: str | None = None, filters: FilterGroup | None = None
    ) -> int:
        return len(await self.find_all(request, q=q, limit=-1, filters=filters))

    async def find_by_pk(self, request: Request, pk: Any) -> GitLabMemberSecret | None:
        del request
        try:
            member_id, host = str(pk).split("|", 1)
        except ValueError:
            return None
        return self._store().get_gitlab_member(member_id, host)

    async def find_by_pks(self, request: Request, pks: list[Any]) -> Sequence[GitLabMemberSecret]:
        result: list[GitLabMemberSecret] = []
        for pk in pks:
            item = await self.find_by_pk(request, pk)
            if item is not None:
                result.append(item)
        return tuple(result)

    async def create(self, request: Request, data: dict[str, Any]) -> GitLabMemberSecret:
        return self._store().create_gitlab_member(
            str(data["member_id"]),
            str(data["host"]),
            str(data["login"]),
            str(data.get("token") or ""),
        )

    async def edit(self, request: Request, pk: Any, data: dict[str, Any]) -> GitLabMemberSecret:
        member_id, host = str(pk).split("|", 1)
        return self._store().update_gitlab_member(
            member_id,
            host,
            revision=int(data["revision"]),
            login=str(data["login"]),
            token=str(data.get("token") or ""),
        )

    async def delete(self, request: Request, pks: list[Any]) -> int:
        for pk in pks:
            record = await self.find_by_pk(request, pk)
            if record is not None:
                self._store().delete_gitlab_member(
                    record.member_id, record.host, revision=record.revision
                )
        return len(pks)


def mount_admin(app: Any, *, secure_cookie: bool = False) -> BaseAdmin:
    """Mount authenticated Admin and return it for tests/introspection."""
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
    admin.add_view(BackupView())
    admin.add_view(IncompleteOperationView())
    admin.add_view(EnvironmentHistoryView())
    admin.add_view(RemoteOdooSecretView())
    admin.add_view(GitLabMemberSecretView())
    admin.mount_to(app)
    return admin


__all__ = [
    "AdminSettings",
    "BackupView",
    "EnvironmentHistoryView",
    "GitLabMemberSecretView",
    "IncompleteOperationView",
    "RemoteOdooSecretView",
    "mount_admin",
]
