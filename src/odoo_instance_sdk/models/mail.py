"""Typed results for the shared GreenMail test service."""

from __future__ import annotations

from typing import Literal

import msgspec

MailState = Literal["running", "stopped", "not_owned", "exposed", "unavailable"]
MailPlacementKind = Literal["host", "docker"]


class MailEndpoint(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One loopback or container endpoint with no credentials."""

    host: str
    port: int


class MailStatus(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Secret-free state of the user-level GreenMail service."""

    state: MailState
    reused: bool
    container_name: str
    owner: str
    bind_host: str
    smtp: MailEndpoint | None
    imap: MailEndpoint | None
    api: MailEndpoint | None
    docker_hostname: str
    docker_network: str
    image: str
    limitation: str


class MailConfigureResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Database mail setup or undo, without the mailbox password."""

    database: str
    mailbox: str
    credentials_path: str
    smtp_host: str
    smtp_port: int
    imap_host: str
    imap_port: int
    placement: MailPlacementKind
    undone: bool
    limitation: str


__all__ = [
    "MailConfigureResult",
    "MailEndpoint",
    "MailPlacementKind",
    "MailState",
    "MailStatus",
]
