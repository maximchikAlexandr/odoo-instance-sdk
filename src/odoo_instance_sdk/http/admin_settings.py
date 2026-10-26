"""Authenticated Admin CRUD for public catalogue settings."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.exceptions import BackupCatalogError
from odoo_instance_sdk.internal.paths import get_catalog_path
from odoo_instance_sdk.internal.resource_inventory import open_catalog_read_only
from odoo_instance_sdk.storage.catalog import BackupCatalog, CatalogSetting

from .admin_support import (
    DateTimeField,
    FormValidationError,
    IntegerField,
    StringField,
    _DashboardBaseModelView,
    _FormValue,
    _PrimaryKey,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue

    from .admin_support import FilterGroup, Request


def _route_identity(pk: _PrimaryKey) -> tuple[str, int]:
    try:
        encoded = str(pk)
        import base64

        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        value = json.loads(raw)
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not isinstance(value[0], str)
            or type(value[1]) is not int
            or value[1] < 1
        ):
            return _invalid_route_identity()
        return value[0], value[1]
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise FormValidationError(
            {"revision": "Setting route is invalid; reload and retry."}
        ) from exc


def _json_form(value: _FormValue) -> object:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 64 * 1024:
        raise ValueError
    try:
        return json.loads(
            value,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (ValueError, TypeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError from exc


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise ValueError


def _invalid_route_identity() -> tuple[str, int]:
    raise ValueError


class CatalogSettingView(_DashboardBaseModelView):
    key = "settings"
    display_name = "Settings"
    menu_label = "Settings"
    icon = "fa fa-sliders"
    pk_attr = "route_id"
    fields = (
        StringField("key"),
        StringField("value_json"),
        IntegerField("revision", read_only=True),
        DateTimeField("created_at", read_only=True),
        DateTimeField("updated_at", read_only=True),
    )
    exporters = ()
    importers = ()
    actions = ()
    row_actions = ()
    page_size = 25
    page_size_options = (25, 50, 100)

    def can_export(self, request: Request) -> bool:
        return False

    def can_import(self, request: Request) -> bool:
        return False

    async def find_all(
        self,
        request: Request,
        skip: int = 0,
        limit: int = 100,
        q: str | None = None,
        sorts: Sequence[tuple[str, str]] | None = None,
        filters: FilterGroup | None = None,
    ) -> Sequence[CatalogSetting]:
        del request, sorts, filters
        catalog = open_catalog_read_only(get_catalog_path(ensure_exists=False))
        if catalog is None:
            return ()
        try:
            rows = catalog.list_settings()
            if q:
                rows = tuple(row for row in rows if q.casefold() in row.key.casefold())
            return rows[skip : skip + min(max(limit, 0), 1000)]
        finally:
            catalog.close()

    async def count(
        self, request: Request, q: str | None = None, filters: FilterGroup | None = None
    ) -> int:
        return len(await self.find_all(request, skip=0, limit=1000, q=q, filters=filters))

    async def find_by_pk(self, request: Request, pk: _PrimaryKey) -> CatalogSetting | None:
        del request
        key, revision = _route_identity(pk)
        catalog = open_catalog_read_only(get_catalog_path(ensure_exists=False))
        if catalog is None:
            return None
        try:
            setting = catalog.get_setting(key)
            return setting if setting is not None and setting.revision == revision else None
        finally:
            catalog.close()

    async def find_by_pks(
        self, request: Request, pks: list[_PrimaryKey]
    ) -> Sequence[CatalogSetting]:
        rows: list[CatalogSetting] = []
        for pk in pks:
            row = await self.find_by_pk(request, pk)
            if row is not None:
                rows.append(row)
        return tuple(rows)

    async def validate(self, request: Request, data: dict[str, _FormValue]) -> None:
        del request
        errors: dict[str | int, str] = {}
        key = data.get("key")
        value = data.get("value_json")
        if not isinstance(key, str) or not key.strip() or len(key.encode("utf-8")) > 512:
            errors["key"] = "Enter a non-empty key within 512 bytes."
        else:
            try:
                from odoo_instance_sdk.storage.catalog.settings import _validate_key

                _validate_key(key)
            except BackupCatalogError:
                errors["key"] = "This key is invalid or classified as secret-bearing."
        try:
            parsed = _json_form(value)
            if isinstance(key, str) and "key" not in errors:
                from odoo_instance_sdk.storage.catalog.settings import _canonical_value

                _canonical_value(key, cast("JsonValue", parsed))
        except (ValueError, BackupCatalogError):
            errors["value_json"] = "Enter valid canonical JSON for this setting."
        if errors:
            raise FormValidationError(errors)

    async def create(self, request: Request, data: dict[str, _FormValue]) -> CatalogSetting:
        del request
        try:
            value = _json_form(data.get("value_json"))
            catalog = BackupCatalog(db_path=get_catalog_path(ensure_exists=False))
            try:
                return catalog.create_setting(str(data["key"]), cast("JsonValue", value))
            finally:
                catalog.close()
        except (KeyError, ValueError, BackupCatalogError) as exc:
            raise FormValidationError(
                {"value_json": "Setting could not be saved; check its key and JSON."}
            ) from exc

    async def edit(
        self, request: Request, pk: _PrimaryKey, data: dict[str, _FormValue]
    ) -> CatalogSetting:
        del request
        old_key, revision = _route_identity(pk)
        new_key = data.get("key")
        try:
            value = _json_form(data.get("value_json"))
            catalog = BackupCatalog(db_path=get_catalog_path(ensure_exists=False))
            try:
                if not isinstance(new_key, str):
                    raise BackupCatalogError("setting key is invalid")
                return catalog.update_setting(
                    old_key,
                    cast("JsonValue", value),
                    expected_revision=revision,
                    new_key=new_key,
                )
            finally:
                catalog.close()
        except (ValueError, BackupCatalogError) as exc:
            raise FormValidationError(
                {"revision": "Setting changed or is invalid; reload and retry."}
            ) from exc

    async def delete(self, request: Request, pks: list[_PrimaryKey]) -> int:
        del request
        catalog = BackupCatalog(db_path=get_catalog_path(ensure_exists=False))
        try:
            for pk in pks:
                key, revision = _route_identity(pk)
                try:
                    catalog.delete_setting(key, expected_revision=revision)
                except BackupCatalogError as exc:
                    raise FormValidationError(
                        {"revision": "Setting changed; reload and retry."}
                    ) from exc
            return len(pks)
        finally:
            catalog.close()
