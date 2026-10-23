"""Shared Starlette Admin adapters and narrow typing protocols."""

from __future__ import annotations

from collections.abc import MutableMapping, Sequence
from dataclasses import fields as dataclass_fields
from datetime import datetime
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, TypeVar, cast


def _dashboard_symbol(module: str, name: str) -> type:
    return cast("type", getattr(import_module(module), name))


class _AdminException(Exception):
    msg: str


class _FilterDataType(Protocol):
    STRING: str
    DATETIME: str


class _PasswordVerifier(Protocol):
    def verify(self, password: str, password_hash: str) -> bool: ...

    def hash(self, password: str) -> str: ...


class _PasswordHashFactory(Protocol):
    def recommended(self) -> _PasswordVerifier: ...


class _RequestAction(Protocol):
    CREATE: str


class _AdminUser:
    username: str

    def __init__(self, username: str) -> None: ...


if TYPE_CHECKING:
    from _typeshed import DataclassInstance

HTTPException = cast("type[Exception]", _dashboard_symbol("starlette.exceptions", "HTTPException"))
Middleware = _dashboard_symbol("starlette.middleware", "Middleware")
SessionMiddleware = _dashboard_symbol("starlette.middleware.sessions", "SessionMiddleware")
BaseAdmin = _dashboard_symbol("starlette_admin", "BaseAdmin")
ComputedField = _dashboard_symbol("starlette_admin", "ComputedField")
DateTimeField = _dashboard_symbol("starlette_admin", "DateTimeField")
IntegerField = _dashboard_symbol("starlette_admin", "IntegerField")
PasswordField = _dashboard_symbol("starlette_admin", "PasswordField")
StringField = _dashboard_symbol("starlette_admin", "StringField")
AdminUser = cast("type[_AdminUser]", _dashboard_symbol("starlette_admin.auth", "AdminUser"))
ActionFailed = cast(
    "type[_AdminException]", _dashboard_symbol("starlette_admin.exceptions", "ActionFailed")
)
FormValidationError = cast(
    "type[_AdminException]",
    _dashboard_symbol("starlette_admin.exceptions", "FormValidationError"),
)
LoginFailed = cast(
    "type[_AdminException]", _dashboard_symbol("starlette_admin.exceptions", "LoginFailed")
)
FilterDataType = cast(
    "_FilterDataType", _dashboard_symbol("starlette_admin.filters.base", "FilterDataType")
)
FilterValidationError = cast(
    "type[Exception]",
    _dashboard_symbol("starlette_admin.filters.base", "FilterValidationError"),
)


class _FilterContext(Protocol):
    query: str


class _FilterRule(Protocol):
    field: str
    filter: str
    value: str | datetime | None
    value2: str | datetime | None


class _FilterGroup(Protocol):
    logic: str
    rules: Sequence[_FilterRule]

    def is_empty(self) -> bool: ...


class _RequestQueryParams(Protocol):
    def get(self, key: str, default: str | None = None) -> str | None: ...

    def getlist(self, key: str) -> list[str]: ...


class _RequestState(Protocol):
    action: str


class _Request(Protocol):
    query_params: _RequestQueryParams
    session: MutableMapping[str, str]
    state: _RequestState


class _DashboardApp(Protocol):
    __class__: type


class _DashboardAdmin(Protocol):
    _model_views: Sequence[_DashboardBaseModelView]

    def add_view(self, view: _DashboardBaseModelView) -> None: ...

    def mount_to(self, app: _DashboardApp) -> None: ...


_T_co = TypeVar("_T_co", covariant=True)


class _CatalogPage(Protocol[_T_co]):
    @property
    def items(self) -> Sequence[_T_co]: ...

    @property
    def next_cursor(self) -> str | None: ...


class _CatalogFetcher(Protocol[_T_co]):
    def __call__(
        self, *, limit: int, cursor: str | None, **kwargs: _FilterValue
    ) -> _CatalogPage[_T_co]: ...


_FilterValue = str | int | bool | datetime | None
_FormValue = str | int | None
_PrimaryKey = str | int


_T = TypeVar("_T")


def _dataclass_text(item: _T) -> str:
    return " ".join(
        str(getattr(item, field.name))
        for field in dataclass_fields(cast("DataclassInstance", item))
    )


def _page(items: Sequence[_T], *, skip: int, limit: int, q: str | None) -> tuple[_T, ...]:
    values = tuple(items)
    if q:
        needle = q.casefold()
        values = tuple(item for item in values if needle in _dataclass_text(item).casefold())
    if limit < 0:
        return values[skip:]
    return values[skip : skip + min(limit, 1000)]


if TYPE_CHECKING:

    class _DashboardBaseFilter:
        name: str
        label: str
        data_type: str
        has_value2: bool

    class _DashboardAuthProvider:
        def __init__(self) -> None: ...

    class _DashboardBaseModelView:
        pass

    Request = _Request
    FilterGroup = _FilterGroup
else:
    _DashboardBaseFilter = _dashboard_symbol("starlette_admin.filters.base", "BaseFilter")
    _DashboardAuthProvider = _dashboard_symbol("starlette_admin.auth.password", "AuthProvider")
    _DashboardBaseModelView = _dashboard_symbol("starlette_admin", "BaseModelView")
    RequestAction = _dashboard_symbol("starlette_admin.types", "RequestAction")

_AdminAuthBase = _DashboardAuthProvider


def _conflict() -> _AdminException:
    return ActionFailed("Record changed; reload and retry.")
