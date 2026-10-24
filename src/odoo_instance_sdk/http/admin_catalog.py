"""Read-only authenticated Admin catalogue views."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Never, TypeVar, cast

from odoo_instance_sdk.internal.paths import get_catalog_path
from odoo_instance_sdk.internal.resource_inventory import open_catalog_read_only
from odoo_instance_sdk.storage.catalog.helpers import (
    BackupSummary,
    EnvironmentHistoryEvent,
    IncompleteOperation,
)

from .admin_support import (
    ComputedField,
    DateTimeField,
    FilterDataType,
    FilterValidationError,
    HTTPException,
    IntegerField,
    StringField,
    _CatalogFetcher,
    _DashboardBaseFilter,
    _DashboardBaseModelView,
    _dataclass_text,
    _FilterContext,
    _FilterValue,
    _FormValue,
    _PrimaryKey,
)

if TYPE_CHECKING:
    from .admin_support import FilterGroup, Request

_T = TypeVar("_T")


class _ExactFilter(_DashboardBaseFilter):
    name = "eq"
    label = "Equals"
    data_type = FilterDataType.STRING

    def apply(self, ctx: _FilterContext) -> str:
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

    def apply(self, ctx: _FilterContext) -> str:
        return ctx.query


def _filter_values(  # noqa: C901 -- reject malformed trees before typed catalogue APIs
    filters: FilterGroup | None,
    *,
    exact_fields: frozenset[str],
    date_fields: Mapping[str, tuple[str, str]],
) -> dict[str, _FilterValue]:
    """Convert the Admin filter tree into the existing typed catalog inputs."""
    if filters is None or filters.is_empty():
        return {}
    if filters.logic != "and":
        raise HTTPException(400, "Only conjunctive catalogue filters are supported")
    result: dict[str, _FilterValue] = {}
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


def _validate_choice(
    values: Mapping[str, _FilterValue], field: str, choices: frozenset[str]
) -> None:
    value = values.get(field)
    if value is not None and value not in choices:
        raise HTTPException(400, f"Unsupported catalogue value for {field}")


def _validate_backup_filters(values: Mapping[str, _FilterValue]) -> None:
    _validate_choice(values, "format", frozenset({"zip", "dump"}))
    _validate_choice(
        values,
        "state",
        frozenset({"downloading", "available", "failed", "deleted"}),
    )


def _validate_incomplete_filters(values: Mapping[str, _FilterValue]) -> None:
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


def _validate_environment_filters(values: Mapping[str, _FilterValue]) -> None:
    _validate_choice(
        values,
        "operation",
        frozenset({"checkout", "sync", "use", "shell", "remove"}),
    )
    _validate_choice(values, "outcome", frozenset({"started", "succeeded", "failed"}))


def _validate_sort_query(request: Request) -> None:
    if request.query_params.getlist("sort"):
        raise HTTPException(400, "Catalogue sorting is unsupported; use canonical order")


def _query_match(item: _T, q: str | None) -> bool:
    if not q:
        return True
    needle = q.casefold()
    return needle in _dataclass_text(item).casefold()


def _read_catalog_pages(
    fetch: _CatalogFetcher[_T],
    *,
    skip: int,
    limit: int,
    q: str | None,
    **kwargs: _FilterValue,
) -> tuple[_T, ...]:
    """Read only the canonical keyset rows needed for one Admin page."""
    if limit <= 0:
        return ()
    needed = skip + min(limit, 1000)
    cursor: str | None = None
    result: list[_T] = []
    while True:
        page_limit = min(1000, max(1, needed - len(result)))
        page = fetch(**kwargs, limit=page_limit, cursor=cursor)
        result.extend(item for item in page.items if _query_match(item, q))
        if len(result) >= needed:
            return tuple(result[skip:needed])
        cursor = page.next_cursor
        if cursor is None:
            return tuple(result[skip:])


def _count_catalog_pages(
    fetch: _CatalogFetcher[_T], *, q: str | None, **kwargs: _FilterValue
) -> int:
    """Count pages without materializing their projections in memory."""
    cursor: str | None = None
    total = 0
    while True:
        page = fetch(**kwargs, limit=1000, cursor=cursor)
        total += sum(_query_match(item, q) for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            return total


def _find_catalog_item(
    fetch: _CatalogFetcher[_T],
    predicate: Callable[[_T], bool],
    **kwargs: _FilterValue,
) -> _T | None:
    cursor: str | None = None
    while True:
        page = fetch(**kwargs, limit=1000, cursor=cursor)
        for item in page.items:
            if predicate(item):
                return item
        cursor = page.next_cursor
        if cursor is None:
            return None


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

    async def create(self, request: Request, data: dict[str, _FormValue]) -> Never:
        raise PermissionError("catalogue resources are read-only")

    async def edit(self, request: Request, pk: _PrimaryKey, data: dict[str, _FormValue]) -> Never:
        raise PermissionError("catalogue resources are read-only")

    async def delete(self, request: Request, pks: list[_PrimaryKey]) -> Never:
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
    menu_label = "Backups"
    icon = "fa fa-database"
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
                cast("_CatalogFetcher[BackupSummary]", catalog.list_backup_summaries),
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
            return _count_catalog_pages(
                cast("_CatalogFetcher[BackupSummary]", catalog.list_backup_summaries),
                q=q,
                **filter_values,
            )
        finally:
            catalog.close()

    async def find_by_pk(self, request: Request, pk: _PrimaryKey) -> BackupSummary | None:
        del request
        catalog = open_catalog_read_only(get_catalog_path(ensure_exists=False))
        if catalog is None:
            return None
        try:
            return _find_catalog_item(
                cast("_CatalogFetcher[BackupSummary]", catalog.list_backup_summaries),
                lambda item: str(item.backup_id) == str(pk),
            )
        finally:
            catalog.close()

    async def find_by_pks(
        self, request: Request, pks: list[_PrimaryKey]
    ) -> Sequence[BackupSummary]:
        result: list[BackupSummary] = []
        for pk in pks:
            item = await self.find_by_pk(request, pk)
            if item is not None:
                result.append(item)
        return tuple(result)


class IncompleteOperationView(_ReadOnlyCatalogView):
    key = "incomplete-operations"
    display_name = "Incomplete operations"
    menu_label = "Incomplete operations"
    icon = "fa fa-exclamation-triangle"
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
                cast("_CatalogFetcher[IncompleteOperation]", catalog.list_incomplete_operations),
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
            return _count_catalog_pages(
                cast("_CatalogFetcher[IncompleteOperation]", catalog.list_incomplete_operations),
                q=q,
                **filter_values,
            )
        finally:
            catalog.close()

    async def find_by_pk(self, request: Request, pk: _PrimaryKey) -> IncompleteOperation | None:
        del request
        catalog = open_catalog_read_only(get_catalog_path(ensure_exists=False))
        if catalog is None:
            return None
        try:
            return _find_catalog_item(
                cast("_CatalogFetcher[IncompleteOperation]", catalog.list_incomplete_operations),
                lambda item: str(item.sequence) == str(pk),
            )
        finally:
            catalog.close()

    async def find_by_pks(
        self, request: Request, pks: list[_PrimaryKey]
    ) -> Sequence[IncompleteOperation]:
        result: list[IncompleteOperation] = []
        for pk in pks:
            item = await self.find_by_pk(request, pk)
            if item is not None:
                result.append(item)
        return tuple(result)


class EnvironmentHistoryView(_ReadOnlyCatalogView):
    key = "environment-history"
    display_name = "Environment history"
    menu_label = "Environment history"
    icon = "fa fa-history"
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
                cast("_CatalogFetcher[EnvironmentHistoryEvent]", catalog.list_environment_history),
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
            return _count_catalog_pages(
                cast("_CatalogFetcher[EnvironmentHistoryEvent]", catalog.list_environment_history),
                q=q,
                **filter_values,
            )
        finally:
            catalog.close()

    async def find_by_pk(self, request: Request, pk: _PrimaryKey) -> EnvironmentHistoryEvent | None:
        del request
        catalog = open_catalog_read_only(get_catalog_path(ensure_exists=False))
        if catalog is None:
            return None
        try:
            return _find_catalog_item(
                cast("_CatalogFetcher[EnvironmentHistoryEvent]", catalog.list_environment_history),
                lambda item: str(item.sequence) == str(pk),
            )
        finally:
            catalog.close()

    async def find_by_pks(
        self, request: Request, pks: list[_PrimaryKey]
    ) -> Sequence[EnvironmentHistoryEvent]:
        result: list[EnvironmentHistoryEvent] = []
        for pk in pks:
            item = await self.find_by_pk(request, pk)
            if item is not None:
                result.append(item)
        return tuple(result)
