"""Structural checks for the pinned public multica-py operation surface.

This module deliberately has no multica-py import: the core distribution must
remain installable without Multica.  The optional integration can pass its
typed client through these public-operation protocols.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

MULTICA_PY_VERSION = "0.1.0"
MULTICA_PY_REVISION = "c1842ae2dfcd0cc5e739b7785d3209d5e72d01ed"
MULTICA_CLI_MINIMUM = "0.5.3"

CheckoutResultT_co = TypeVar("CheckoutResultT_co", covariant=True)
CheckoutCommandT_co = TypeVar("CheckoutCommandT_co", covariant=True)
DaemonStatusT_co = TypeVar("DaemonStatusT_co", covariant=True)
DaemonCommandT_co = TypeVar("DaemonCommandT_co", covariant=True)


class MulticaCompatibilityError(RuntimeError):
    """The installed optional client cannot provide the pinned public API."""


class RepositoryOperations(Protocol[CheckoutCommandT_co, CheckoutResultT_co]):
    def checkout_command(
        self,
        url: str,
        *,
        ref: str | None = None,
        fresh: bool = False,
        options: object | None = None,
    ) -> CheckoutCommandT_co: ...

    def checkout(
        self,
        url: str,
        *,
        ref: str | None = None,
        fresh: bool = False,
        options: object | None = None,
    ) -> CheckoutResultT_co: ...


class DaemonOperations(Protocol[DaemonCommandT_co, DaemonStatusT_co]):
    def status_command(self, *, options: object | None = None) -> DaemonCommandT_co: ...

    def status(self, *, options: object | None = None) -> DaemonStatusT_co: ...


class MulticaClient(
    Protocol[CheckoutCommandT_co, CheckoutResultT_co, DaemonCommandT_co, DaemonStatusT_co]
):
    @property
    def repositories(self) -> RepositoryOperations[CheckoutCommandT_co, CheckoutResultT_co]: ...

    @property
    def daemon(self) -> DaemonOperations[DaemonCommandT_co, DaemonStatusT_co]: ...


def require_contract(*, version: str | None, revision: str | None) -> None:
    """Reject unavailable or ambiguous package identity before use."""
    if version is None or revision is None:
        raise MulticaCompatibilityError("multica-py compatibility metadata is unavailable")
    if version != MULTICA_PY_VERSION or revision != MULTICA_PY_REVISION:
        raise MulticaCompatibilityError("multica-py compatibility metadata is ambiguous")


def checkout_command(
    client: MulticaClient[
        CheckoutCommandT_co, CheckoutResultT_co, DaemonCommandT_co, DaemonStatusT_co
    ],
    url: str,
    *,
    ref: str | None = None,
    options: object | None = None,
) -> CheckoutCommandT_co:
    """Consume the typed repository command without a CLI or wire decoder."""
    return client.repositories.checkout_command(url, ref=ref, fresh=False, options=options)


def checkout(
    client: MulticaClient[
        CheckoutCommandT_co, CheckoutResultT_co, DaemonCommandT_co, DaemonStatusT_co
    ],
    url: str,
    *,
    ref: str | None = None,
    options: object | None = None,
) -> CheckoutResultT_co:
    """Consume the typed repository result and preserve SDK errors unchanged."""
    return client.repositories.checkout(url, ref=ref, fresh=False, options=options)


def daemon_status_command(
    client: MulticaClient[
        CheckoutCommandT_co, CheckoutResultT_co, DaemonCommandT_co, DaemonStatusT_co
    ],
    *,
    options: object | None = None,
) -> DaemonCommandT_co:
    """Consume the complete typed daemon-status command."""
    return client.daemon.status_command(options=options)


def daemon_status(
    client: MulticaClient[
        CheckoutCommandT_co, CheckoutResultT_co, DaemonCommandT_co, DaemonStatusT_co
    ],
    *,
    options: object | None = None,
) -> DaemonStatusT_co:
    """Consume the typed daemon status without locally decoding output."""
    return client.daemon.status(options=options)
