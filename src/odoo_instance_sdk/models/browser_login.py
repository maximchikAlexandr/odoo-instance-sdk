from __future__ import annotations

from typing import Annotated, Literal

import msgspec


class BrowserLoginUser(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    id: int
    login: str
    name: str


class BrowserLoginUsersResponse(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    users: tuple[BrowserLoginUser, ...]


class BrowserLoginUsersRequest(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    owner_kind: Literal["project", "environment"]
    owner_id: Annotated[str, msgspec.Meta(min_length=1, max_length=128)]
    query: Annotated[str, msgspec.Meta(max_length=80)] = ""


class BrowserLoginTicketRequest(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    owner_kind: Literal["project", "environment"]
    owner_id: Annotated[str, msgspec.Meta(min_length=1, max_length=128)]
    user_id: Annotated[int, msgspec.Meta(gt=0)]


class BrowserLoginTicket(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    action_url: str
    method: str
    handoff: str
    expires_at: str

    def __repr__(self) -> str:
        return "BrowserLoginTicket(action_url=<redacted>, method='POST', handoff=<redacted>, expires_at=<redacted>)"
