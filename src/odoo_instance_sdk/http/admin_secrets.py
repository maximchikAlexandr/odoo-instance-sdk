"""Write-only administrator views for encrypted credential records."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.exceptions import (
    GitLabCredentialError,
    InvalidBaseUrlError,
    SecretStoreConflictError,
    SecretStoreIdentityConflictError,
    SecretStoreRecordError,
)
from odoo_instance_sdk.internal.gitlab_credentials import normalize_gitlab_host, normalize_member_id
from odoo_instance_sdk.internal.secret_store import (
    GitLabMemberSecret,
    RemoteOdooSecret,
    SecretStore,
    decode_admin_id,
)
from odoo_instance_sdk.internal.urls import normalize_base_url

from .admin_support import (
    FormValidationError,
    PasswordField,
    StringField,
    _conflict,
    _dashboard_symbol,
    _DashboardBaseModelView,
    _FormValue,
    _page,
    _PrimaryKey,
    _RequestAction,
)

if TYPE_CHECKING:
    from .admin_support import FilterGroup, Request


def _request_revision(request: Request) -> int | None:
    raw = request.query_params.get("revision")
    if raw is None:
        return None
    try:
        revision = int(raw)
    except (TypeError, ValueError):
        return None
    return revision if revision > 0 else None


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

    async def validate(self, request: Request, data: dict[str, _FormValue]) -> None:
        request_action = cast(
            "_RequestAction", _dashboard_symbol("starlette_admin.types", "RequestAction")
        )

        errors: dict[str | int, str] = {}
        if (
            request.state.action == request_action.CREATE
            and not str(data.get(self.secret_field) or "").strip()
        ):
            errors[self.secret_field] = "A nonblank secret is required."
        if self.key == "gitlab-member-secrets" and not str(data.get("login") or "").strip():
            errors["login"] = "A nonblank login is required."
        if self.key == "remote-odoo-secrets":
            try:
                normalize_base_url(str(data.get("base_url") or ""))
            except (InvalidBaseUrlError, TypeError, ValueError):
                errors["base_url"] = "Enter a valid HTTP or HTTPS base URL."
        elif self.key == "gitlab-member-secrets":
            try:
                normalize_member_id(str(data.get("member_id") or ""))
            except (GitLabCredentialError, TypeError, ValueError):
                errors["member_id"] = "Enter a valid Multica member user ID."
            try:
                normalize_gitlab_host(str(data.get("host") or ""))
            except (GitLabCredentialError, TypeError, ValueError):
                errors["host"] = "Enter a valid HTTPS GitLab host."
        if errors:
            raise FormValidationError(errors)


class RemoteOdooSecretView(_SecretView):
    key = "remote-odoo-secrets"
    display_name = "Remote Odoo secrets"
    menu_label = "Remote Odoo secrets"
    icon = "fa fa-cloud"
    pk_attr = "admin_id"
    show_pk_in_forms = False
    secret_field = "master_password"
    fields = (
        StringField("base_url"),
        StringField("created_at", read_only=True, exclude_from_create=True, exclude_from_edit=True),
        StringField("updated_at", read_only=True, exclude_from_create=True, exclude_from_edit=True),
        StringField(
            "_continue_editing",
            input_type="hidden",
            exclude_from_list=True,
            exclude_from_detail=True,
            exclude_from_create=True,
            exclude_from_export=True,
            exclude_from_import=True,
        ),
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

    async def find_by_pk(self, request: Request, pk: _PrimaryKey) -> RemoteOdooSecret | None:
        del request
        identity = decode_admin_id(str(pk))
        if identity is None or identity.kind != "remote-odoo":
            return None
        record = self._store().get_remote_odoo(identity.components[0])
        return record if record is not None and record.revision == identity.revision else None

    async def find_by_pks(
        self, request: Request, pks: list[_PrimaryKey]
    ) -> Sequence[RemoteOdooSecret]:
        result: list[RemoteOdooSecret] = []
        for pk in pks:
            item = await self.find_by_pk(request, pk)
            if item is not None:
                result.append(item)
        return tuple(result)

    async def create(self, request: Request, data: dict[str, _FormValue]) -> RemoteOdooSecret:
        return self._store().create_remote_odoo(
            str(data["base_url"]), str(data.get("master_password") or "")
        )

    async def get_pk_value(self, request: Request, obj: RemoteOdooSecret) -> str:
        del request
        return obj.admin_id

    async def edit(
        self, request: Request, pk: _PrimaryKey, data: dict[str, _FormValue]
    ) -> RemoteOdooSecret:
        identity = decode_admin_id(str(pk))
        if identity is None or identity.kind != "remote-odoo":
            raise _conflict()
        try:
            return self._store().update_remote_odoo(
                identity.components[0],
                revision=identity.revision,
                new_base_url=str(data["base_url"]),
                master_password=str(data.get("master_password") or ""),
            )
        except SecretStoreConflictError as exc:
            raise FormValidationError({"base_url": _conflict().msg}) from exc
        except SecretStoreIdentityConflictError as exc:
            raise FormValidationError(
                {"base_url": "A secret already exists for this URL."}
            ) from exc
        except SecretStoreRecordError as exc:
            raise FormValidationError({"base_url": _conflict().msg}) from exc
        except InvalidBaseUrlError as exc:
            raise FormValidationError(
                {"base_url": "Enter a valid HTTP or HTTPS base URL."}
            ) from exc

    async def delete(self, request: Request, pks: list[_PrimaryKey]) -> int:
        for pk in pks:
            identity = decode_admin_id(str(pk))
            if identity is None or identity.kind != "remote-odoo":
                raise _conflict()
            requested_revision = _request_revision(request)
            if requested_revision is not None and requested_revision != identity.revision:
                raise _conflict()
            try:
                self._store().delete_remote_odoo(identity.components[0], revision=identity.revision)
            except SecretStoreConflictError as exc:
                raise _conflict() from exc
        return len(pks)


class GitLabMemberSecretView(_SecretView):
    key = "gitlab-member-secrets"
    display_name = "GitLab member secrets"
    menu_label = "GitLab member secrets"
    icon = "fa fa-gitlab"
    pk_attr = "admin_id"
    show_pk_in_forms = False
    secret_field = "token"
    fields = (
        StringField("member_id"),
        StringField("host"),
        StringField("login"),
        StringField("created_at", read_only=True, exclude_from_create=True, exclude_from_edit=True),
        StringField("updated_at", read_only=True, exclude_from_create=True, exclude_from_edit=True),
        StringField(
            "_continue_editing",
            input_type="hidden",
            exclude_from_list=True,
            exclude_from_detail=True,
            exclude_from_create=True,
            exclude_from_export=True,
            exclude_from_import=True,
        ),
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
        return obj.admin_id

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

    async def find_by_pk(self, request: Request, pk: _PrimaryKey) -> GitLabMemberSecret | None:
        del request
        identity = decode_admin_id(str(pk))
        if identity is None or identity.kind != "gitlab-member":
            return None
        record = self._store().get_gitlab_member(*identity.components)
        return record if record is not None and record.revision == identity.revision else None

    async def find_by_pks(
        self, request: Request, pks: list[_PrimaryKey]
    ) -> Sequence[GitLabMemberSecret]:
        result: list[GitLabMemberSecret] = []
        for pk in pks:
            item = await self.find_by_pk(request, pk)
            if item is not None:
                result.append(item)
        return tuple(result)

    async def create(self, request: Request, data: dict[str, _FormValue]) -> GitLabMemberSecret:
        return self._store().create_gitlab_member(
            str(data["member_id"]),
            str(data["host"]),
            str(data["login"]),
            str(data.get("token") or ""),
        )

    async def edit(
        self, request: Request, pk: _PrimaryKey, data: dict[str, _FormValue]
    ) -> GitLabMemberSecret:
        identity = decode_admin_id(str(pk))
        if identity is None or identity.kind != "gitlab-member":
            raise _conflict()
        try:
            return self._store().update_gitlab_member(
                *identity.components,
                revision=identity.revision,
                new_member_id=str(data["member_id"]),
                new_host=str(data["host"]),
                login=str(data["login"]),
                token=str(data.get("token") or ""),
            )
        except SecretStoreConflictError as exc:
            raise FormValidationError({"member_id": _conflict().msg}) from exc
        except SecretStoreIdentityConflictError as exc:
            field = "member_id" if str(data["member_id"]) != identity.components[0] else "host"
            raise FormValidationError(
                {field: "A secret already exists for this member and host."}
            ) from exc
        except SecretStoreRecordError as exc:
            raise FormValidationError({"member_id": _conflict().msg}) from exc
        except GitLabCredentialError as exc:
            field = "member_id" if str(data["member_id"]) != identity.components[0] else "host"
            raise FormValidationError(
                {field: "Enter a valid member ID and HTTPS GitLab host."}
            ) from exc

    async def delete(self, request: Request, pks: list[_PrimaryKey]) -> int:
        for pk in pks:
            identity = decode_admin_id(str(pk))
            if identity is None or identity.kind != "gitlab-member":
                raise _conflict()
            requested_revision = _request_revision(request)
            if requested_revision is not None and requested_revision != identity.revision:
                raise _conflict()
            try:
                self._store().delete_gitlab_member(*identity.components, revision=identity.revision)
            except SecretStoreConflictError as exc:
                raise _conflict() from exc
        return len(pks)
