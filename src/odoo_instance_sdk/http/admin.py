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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields as dataclass_fields
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

from odoo_instance_sdk.exceptions import ConfigError, SecretStoreConflictError
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


def _dashboard_symbol(module: str, name: str) -> Any:
    return getattr(import_module(module), name)


HTTPException = _dashboard_symbol("starlette.exceptions", "HTTPException")
Middleware = _dashboard_symbol("starlette.middleware", "Middleware")
SessionMiddleware = _dashboard_symbol("starlette.middleware.sessions", "SessionMiddleware")
BaseAdmin = _dashboard_symbol("starlette_admin", "BaseAdmin")
ComputedField = _dashboard_symbol("starlette_admin", "ComputedField")
DateTimeField = _dashboard_symbol("starlette_admin", "DateTimeField")
IntegerField = _dashboard_symbol("starlette_admin", "IntegerField")
PasswordField = _dashboard_symbol("starlette_admin", "PasswordField")
StringField = _dashboard_symbol("starlette_admin", "StringField")
AdminUser = _dashboard_symbol("starlette_admin.auth", "AdminUser")
ActionFailed = _dashboard_symbol("starlette_admin.exceptions", "ActionFailed")
FormValidationError = _dashboard_symbol("starlette_admin.exceptions", "FormValidationError")
LoginFailed = _dashboard_symbol("starlette_admin.exceptions", "LoginFailed")
FilterDataType = _dashboard_symbol("starlette_admin.filters.base", "FilterDataType")
FilterValidationError = _dashboard_symbol("starlette_admin.filters.base", "FilterValidationError")
FilterApplyContext = Any

if TYPE_CHECKING:

    class _DashboardBaseFilter:
        name: str
        label: str
        data_type: object
        has_value2: bool

    class _DashboardAuthProvider:
        def __init__(self) -> None: ...

    class _DashboardBaseModelView:
        pass

    Request = Any
    FilterGroup = Any
else:
    _DashboardBaseFilter = _dashboard_symbol("starlette_admin.filters.base", "BaseFilter")
    _DashboardAuthProvider = _dashboard_symbol("starlette_admin.auth.password", "AuthProvider")
    _DashboardBaseModelView = _dashboard_symbol("starlette_admin", "BaseModelView")

_SESSION_KEY_MINIMUM = 32
_ADMIN_USER = "admin"
_PRIVATE_BITS = stat.S_IRWXG | stat.S_IRWXO
_ARGON2_HASH = re.compile(r"\$argon2(?:id|i|d)\$[A-Za-z0-9=,.$+/:-]+\Z")


class _ExactFilter(_DashboardBaseFilter):
    name = "eq"
    label = "Equals"
    data_type = FilterDataType.STRING

    def apply(self, ctx: FilterApplyContext) -> Any:
        return ctx.query


class _DateRangeFilter(_DashboardBaseFilter):
    name = "between"
    label = "Between"
    data_type = FilterDataType.DATETIME
    has_value2 = True

    def parse_value(self, raw: str) -> datetime:
        try:
            value = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise FilterValidationError("invalid datetime") from exc
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def apply(self, ctx: FilterApplyContext) -> Any:
        return ctx.query


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


class _AdminAuth(_DashboardAuthProvider):
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
            PasswordHash = _dashboard_symbol("pwdlib", "PasswordHash")
            valid = PasswordHash.recommended().verify(password, self._settings.password_hash)
        except Exception as exc:
            raise LoginFailed("Invalid username or password") from exc
        if not valid:
            raise LoginFailed("Invalid username or password")
        request.session["admin_user"] = _ADMIN_USER

    async def logout(self, request: Request) -> None:
        request.session.clear()

    async def authenticate(self, request: Request) -> Any:
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


def _filter_values(  # noqa: C901 -- reject malformed trees before typed catalogue APIs
    filters: FilterGroup | None,
    *,
    exact_fields: frozenset[str],
    date_fields: Mapping[str, tuple[str, str]],
) -> dict[str, Any]:
    """Convert the Admin filter tree into the existing typed catalog inputs."""
    if filters is None or filters.is_empty():
        return {}
    if filters.logic != "and":
        raise HTTPException(400, "Only conjunctive catalogue filters are supported")
    result: dict[str, Any] = {}
    for rule in filters.rules:
        if hasattr(rule, "rules"):
            raise HTTPException(400, "Nested catalogue filters are unsupported")
        field = rule.field
        if field in exact_fields and rule.filter == "eq":
            if field in result:
                raise HTTPException(400, "Duplicate catalogue filter")
            result[field] = str(rule.value)
            continue
        if field in date_fields and rule.filter == "between":
            if not isinstance(rule.value, datetime) or not isinstance(rule.value2, datetime):
                raise HTTPException(400, "Catalogue date range is invalid")
            if rule.value > rule.value2:
                raise HTTPException(400, "Catalogue date range is reversed")
            after_key, before_key = date_fields[field]
            if after_key in result or before_key in result:
                raise HTTPException(400, "Duplicate catalogue filter")
            result[after_key] = rule.value
            result[before_key] = rule.value2
            continue
        raise HTTPException(400, "Unsupported catalogue filter")
    return result


def _validate_choice(values: dict[str, Any], field: str, choices: frozenset[str]) -> None:
    value = values.get(field)
    if value is not None and value not in choices:
        raise HTTPException(400, f"Unsupported catalogue value for {field}")


def _validate_backup_filters(values: dict[str, Any]) -> None:
    _validate_choice(values, "format", frozenset({"zip", "dump"}))
    _validate_choice(
        values,
        "state",
        frozenset({"downloading", "available", "failed", "deleted"}),
    )


def _validate_incomplete_filters(values: dict[str, Any]) -> None:
    _validate_choice(values, "object_type", frozenset({"backup", "restore", "environment"}))
    _validate_choice(
        values,
        "state",
        frozenset(
            {
                "downloading",
                "failed",
                "incomplete",
                "creating",
                "removing",
                "cleanup_failed",
            }
        ),
    )


def _validate_environment_filters(values: dict[str, Any]) -> None:
    _validate_choice(
        values,
        "operation",
        frozenset({"checkout", "sync", "use", "shell", "remove"}),
    )
    _validate_choice(values, "outcome", frozenset({"started", "succeeded", "failed"}))


def _validate_sort_query(request: Request) -> None:
    if request.query_params.getlist("sort"):
        raise HTTPException(400, "Catalogue sorting is unsupported; use canonical order")


def _query_match(item: Any, q: str | None) -> bool:
    if not q:
        return True
    needle = q.casefold()
    return (
        needle
        in " ".join(str(getattr(item, field.name)) for field in dataclass_fields(item)).casefold()
    )


def _read_catalog_pages(
    fetch: Any,
    *,
    skip: int,
    limit: int,
    q: str | None,
    **kwargs: Any,
) -> tuple[Any, ...]:
    """Read only the canonical keyset rows needed for one Admin page."""
    if limit <= 0:
        return ()
    needed = skip + min(limit, 1000)
    cursor: str | None = None
    result: list[Any] = []
    while True:
        page_limit = min(1000, max(1, needed - len(result)))
        page = fetch(**kwargs, limit=page_limit, cursor=cursor)
        result.extend(item for item in page.items if _query_match(item, q))
        if len(result) >= needed:
            return tuple(result[skip:needed])
        cursor = page.next_cursor
        if cursor is None:
            return tuple(result[skip:])


def _count_catalog_pages(fetch: Any, *, q: str | None, **kwargs: Any) -> int:
    """Count pages without materializing their projections in memory."""
    cursor: str | None = None
    total = 0
    while True:
        page = fetch(**kwargs, limit=1000, cursor=cursor)
        total += sum(_query_match(item, q) for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            return total


def _find_catalog_item(fetch: Any, predicate: Any, **kwargs: Any) -> Any | None:
    cursor: str | None = None
    while True:
        page = fetch(**kwargs, limit=1000, cursor=cursor)
        for item in page.items:
            if predicate(item):
                return item
        cursor = page.next_cursor
        if cursor is None:
            return None


def _revision_token(value: Any) -> tuple[str, int | None]:
    identity, separator, raw_revision = str(value).rpartition("|")
    if separator and raw_revision.isdigit() and int(raw_revision) > 0:
        return identity, int(raw_revision)
    return str(value), None


def _request_revision(request: Request) -> int | None:
    raw = request.query_params.get("revision")
    if raw is None:
        return None
    try:
        revision = int(raw)
    except (TypeError, ValueError):
        return None
    return revision if revision > 0 else None


def _conflict() -> Any:
    return ActionFailed("Record changed; reload and retry.")


class _ReadOnlyCatalogView(_DashboardBaseModelView):
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
        StringField("project_id", read_only=True, filters=[_ExactFilter]),
        StringField("project_label", read_only=True),
        StringField("source_label", read_only=True, filters=[_ExactFilter]),
        StringField("database_name", read_only=True, filters=[_ExactFilter]),
        StringField("format", read_only=True, filters=[_ExactFilter]),
        IntegerField("size_bytes", read_only=True),
        StringField("state", read_only=True, filters=[_ExactFilter]),
        DateTimeField("downloaded_at", read_only=True, filters=[_DateRangeFilter]),
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
        filter_values = _filter_values(
            filters,
            exact_fields=frozenset(
                {"project_id", "source_label", "database_name", "format", "state"}
            ),
            date_fields={"downloaded_at": ("downloaded_after", "downloaded_before")},
        )
        _validate_backup_filters(filter_values)
        _validate_sort_query(request)
        catalog = open_catalog_read_only(get_catalog_path(ensure_exists=False))
        if catalog is None:
            return ()
        try:
            return _read_catalog_pages(
                catalog.list_backup_summaries,
                skip=skip,
                limit=limit,
                q=q,
                **filter_values,
            )
        finally:
            catalog.close()

    async def count(
        self, request: Request, q: str | None = None, filters: FilterGroup | None = None
    ) -> int:
        filter_values = _filter_values(
            filters,
            exact_fields=frozenset(
                {"project_id", "source_label", "database_name", "format", "state"}
            ),
            date_fields={"downloaded_at": ("downloaded_after", "downloaded_before")},
        )
        _validate_backup_filters(filter_values)
        catalog = open_catalog_read_only(get_catalog_path(ensure_exists=False))
        if catalog is None:
            return 0
        try:
            return _count_catalog_pages(catalog.list_backup_summaries, q=q, **filter_values)
        finally:
            catalog.close()

    async def find_by_pk(self, request: Request, pk: Any) -> BackupSummary | None:
        del request
        catalog = open_catalog_read_only(get_catalog_path(ensure_exists=False))
        if catalog is None:
            return None
        try:
            return _find_catalog_item(
                catalog.list_backup_summaries,
                lambda item: str(item.backup_id) == str(pk),
            )
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
        StringField("object_type", read_only=True, filters=[_ExactFilter]),
        StringField("object_id", read_only=True),
        StringField("state", read_only=True, filters=[_ExactFilter]),
        DateTimeField("occurred_at", read_only=True, filters=[_DateRangeFilter]),
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
        filter_values = _filter_values(
            filters,
            exact_fields=frozenset({"object_type", "state"}),
            date_fields={"occurred_at": ("occurred_after", "occurred_before")},
        )
        _validate_incomplete_filters(filter_values)
        _validate_sort_query(request)
        catalog = open_catalog_read_only(get_catalog_path(ensure_exists=False))
        if catalog is None:
            return ()
        try:
            return _read_catalog_pages(
                catalog.list_incomplete_operations,
                skip=skip,
                limit=limit,
                q=q,
                **filter_values,
            )
        finally:
            catalog.close()

    async def count(
        self, request: Request, q: str | None = None, filters: FilterGroup | None = None
    ) -> int:
        filter_values = _filter_values(
            filters,
            exact_fields=frozenset({"object_type", "state"}),
            date_fields={"occurred_at": ("occurred_after", "occurred_before")},
        )
        _validate_incomplete_filters(filter_values)
        catalog = open_catalog_read_only(get_catalog_path(ensure_exists=False))
        if catalog is None:
            return 0
        try:
            return _count_catalog_pages(catalog.list_incomplete_operations, q=q, **filter_values)
        finally:
            catalog.close()

    async def find_by_pk(self, request: Request, pk: Any) -> IncompleteOperation | None:
        del request
        catalog = open_catalog_read_only(get_catalog_path(ensure_exists=False))
        if catalog is None:
            return None
        try:
            return _find_catalog_item(
                catalog.list_incomplete_operations,
                lambda item: str(item.sequence) == str(pk),
            )
        finally:
            catalog.close()

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
        StringField("environment_id", read_only=True, filters=[_ExactFilter]),
        StringField("environment_name", read_only=True),
        StringField("project_id", read_only=True, filters=[_ExactFilter]),
        DateTimeField("occurred_at", read_only=True, filters=[_DateRangeFilter]),
        StringField("operation", read_only=True, filters=[_ExactFilter]),
        StringField("outcome", read_only=True, filters=[_ExactFilter]),
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
        filter_values = _filter_values(
            filters,
            exact_fields=frozenset({"environment_id", "project_id", "operation", "outcome"}),
            date_fields={"occurred_at": ("occurred_after", "occurred_before")},
        )
        _validate_environment_filters(filter_values)
        _validate_sort_query(request)
        catalog = open_catalog_read_only(get_catalog_path(ensure_exists=False))
        if catalog is None:
            return ()
        try:
            return _read_catalog_pages(
                catalog.list_environment_history,
                skip=skip,
                limit=limit,
                q=q,
                **filter_values,
            )
        finally:
            catalog.close()

    async def count(
        self, request: Request, q: str | None = None, filters: FilterGroup | None = None
    ) -> int:
        filter_values = _filter_values(
            filters,
            exact_fields=frozenset({"environment_id", "project_id", "operation", "outcome"}),
            date_fields={"occurred_at": ("occurred_after", "occurred_before")},
        )
        _validate_environment_filters(filter_values)
        catalog = open_catalog_read_only(get_catalog_path(ensure_exists=False))
        if catalog is None:
            return 0
        try:
            return _count_catalog_pages(catalog.list_environment_history, q=q, **filter_values)
        finally:
            catalog.close()

    async def find_by_pk(self, request: Request, pk: Any) -> EnvironmentHistoryEvent | None:
        del request
        catalog = open_catalog_read_only(get_catalog_path(ensure_exists=False))
        if catalog is None:
            return None
        try:
            return _find_catalog_item(
                catalog.list_environment_history,
                lambda item: str(item.sequence) == str(pk),
            )
        finally:
            catalog.close()

    async def find_by_pks(
        self, request: Request, pks: list[Any]
    ) -> Sequence[EnvironmentHistoryEvent]:
        result: list[EnvironmentHistoryEvent] = []
        for pk in pks:
            item = await self.find_by_pk(request, pk)
            if item is not None:
                result.append(item)
        return tuple(result)


class _SecretView(_DashboardBaseModelView):
    key: str

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
        request_action = _dashboard_symbol("starlette_admin.types", "RequestAction")

        errors: dict[str | int, Any] = {}
        if (
            request.state.action == request_action.CREATE
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
        identity, _revision = _revision_token(pk)
        return self._store().get_remote_odoo(identity)

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

    async def get_pk_value(self, request: Request, obj: RemoteOdooSecret) -> str:
        del request
        return f"{obj.base_url}|{obj.revision}"

    async def edit(self, request: Request, pk: Any, data: dict[str, Any]) -> RemoteOdooSecret:
        identity, _revision = _revision_token(pk)
        try:
            return self._store().update_remote_odoo(
                identity,
                revision=int(data["revision"]),
                master_password=str(data.get("master_password") or ""),
            )
        except SecretStoreConflictError as exc:
            raise FormValidationError({"revision": _conflict().msg}) from exc

    async def delete(self, request: Request, pks: list[Any]) -> int:
        for pk in pks:
            identity, token_revision = _revision_token(pk)
            revision = _request_revision(request) or token_revision
            if revision is None:
                raise _conflict()
            try:
                self._store().delete_remote_odoo(identity, revision=revision)
            except SecretStoreConflictError as exc:
                raise _conflict() from exc
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
        return f"{obj.member_id}|{obj.host}|{obj.revision}"

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
            identity, _revision = _revision_token(pk)
            member_id, host = identity.split("|", 1)
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
        identity, _revision = _revision_token(pk)
        member_id, host = identity.split("|", 1)
        try:
            return self._store().update_gitlab_member(
                member_id,
                host,
                revision=int(data["revision"]),
                login=str(data["login"]),
                token=str(data.get("token") or ""),
            )
        except SecretStoreConflictError as exc:
            raise FormValidationError({"revision": _conflict().msg}) from exc

    async def delete(self, request: Request, pks: list[Any]) -> int:
        for pk in pks:
            identity, token_revision = _revision_token(pk)
            revision = _request_revision(request) or token_revision
            if revision is None:
                raise _conflict()
            try:
                member_id, host = identity.split("|", 1)
                self._store().delete_gitlab_member(member_id, host, revision=revision)
            except SecretStoreConflictError as exc:
                raise _conflict() from exc
        return len(pks)


def mount_admin(app: Any, *, secure_cookie: bool = False) -> Any:
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
