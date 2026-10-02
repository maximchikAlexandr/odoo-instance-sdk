from __future__ import annotations

from dataclasses import dataclass

import pytest

from odoo_instance_sdk.internal.multica_compat import (
    MULTICA_CLI_MINIMUM,
    MULTICA_PY_REVISION,
    MULTICA_PY_VERSION,
    MulticaCompatibilityError,
    checkout,
    checkout_command,
    daemon_status,
    daemon_status_command,
    require_contract,
)


@dataclass(frozen=True)
class RepositoryCheckoutResult:
    path: str


@dataclass(frozen=True)
class DaemonStatus:
    status: str


class CommandTimeoutError(RuntimeError):
    pass


class CommandCancelledError(RuntimeError):
    pass


class _Repositories:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, bool, object | None]] = []

    def checkout_command(
        self,
        url: str,
        *,
        ref: str | None = None,
        fresh: bool = False,
        options: object | None = None,
    ) -> str:
        self.calls.append((url, ref, fresh, options))
        return "checkout-command"

    def checkout(
        self,
        url: str,
        *,
        ref: str | None = None,
        fresh: bool = False,
        options: object | None = None,
    ) -> RepositoryCheckoutResult:
        self.calls.append((url, ref, fresh, options))
        return RepositoryCheckoutResult("/caller-owned/checkout")


class _Daemon:
    def __init__(self) -> None:
        self.calls: list[object | None] = []

    def status_command(self, *, options: object | None = None) -> str:
        self.calls.append(options)
        return "status-command"

    def status(self, *, options: object | None = None) -> DaemonStatus:
        self.calls.append(options)
        return DaemonStatus("ready")


@dataclass
class _Client:
    repositories: _Repositories
    daemon: _Daemon


def test_typed_public_operations_preserve_results_and_options() -> None:
    client = _Client(_Repositories(), _Daemon())
    options = {"timeout": 3.0, "environment": {"TOKEN": "secret"}}

    assert checkout_command(client, "https://example.test/repo", ref="main", options=options) == (
        "checkout-command"
    )
    result = checkout(client, "https://example.test/repo", ref="main", options=options)
    assert result.path == "/caller-owned/checkout"
    assert "secret" not in repr(result)
    assert daemon_status_command(client, options=options) == "status-command"
    assert daemon_status(client, options=options) == DaemonStatus("ready")
    assert client.repositories.calls == [
        ("https://example.test/repo", "main", False, options),
        ("https://example.test/repo", "main", False, options),
    ]
    assert client.daemon.calls == [options, options]
    assert MULTICA_CLI_MINIMUM == "0.5.3"


@pytest.mark.parametrize(
    ("version", "revision"),
    [
        (None, None),
        (MULTICA_PY_VERSION, None),
        ("0.1.0", "unrelated-revision"),
        ("0.1.1", MULTICA_PY_REVISION),
    ],
    ids=["unavailable", "missing-revision", "ambiguous-revision", "ambiguous-version"],
)
def test_compatibility_rejects_unavailable_or_ambiguous_identity(
    version: str | None, revision: str | None
) -> None:
    with pytest.raises(MulticaCompatibilityError):
        require_contract(version=version, revision=revision)


def test_compatibility_accepts_exact_identity() -> None:
    require_contract(version=MULTICA_PY_VERSION, revision=MULTICA_PY_REVISION)


@pytest.mark.parametrize(
    "error", [CommandTimeoutError("timed out"), CommandCancelledError("cancelled")]
)
def test_public_sdk_timeout_and_cancellation_errors_are_not_decoded(
    error: BaseException,
) -> None:
    class FailingRepositories(_Repositories):
        def checkout(self, *args: object, **kwargs: object) -> RepositoryCheckoutResult:
            raise error

    client = _Client(FailingRepositories(), _Daemon())
    with pytest.raises(type(error), match=str(error)):
        checkout(client, "https://example.test/repo")
