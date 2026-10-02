from __future__ import annotations

import inspect
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest

from odoo_instance_sdk.internal.multica_compat import (
    MULTICA_CLI_MINIMUM,
    MULTICA_PY_REVISION,
    MULTICA_PY_VERSION,
    MulticaCompatibility,
    MulticaCompatibilityError,
    checkout,
    checkout_command,
    daemon_status,
    daemon_status_command,
    require_contract,
)

COMPATIBILITY = MulticaCompatibility(
    package_version=MULTICA_PY_VERSION,
    package_revision=MULTICA_PY_REVISION,
    native_cli_version=MULTICA_CLI_MINIMUM,
)


class _Repositories:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, bool, object | None]] = []
        self.command_result = object()
        self.checkout_result = object()

    def checkout_command(
        self,
        url: str,
        *,
        ref: str | None = None,
        fresh: bool = False,
        options: object | None = None,
    ) -> object:
        self.calls.append((url, ref, fresh, options))
        return self.command_result

    def checkout(
        self,
        url: str,
        *,
        ref: str | None = None,
        fresh: bool = False,
        options: object | None = None,
    ) -> object:
        self.calls.append((url, ref, fresh, options))
        return self.checkout_result


class _Daemon:
    def __init__(self) -> None:
        self.calls: list[object | None] = []
        self.command_result = object()
        self.status_result = object()

    def status_command(self, *, options: object | None = None) -> object:
        self.calls.append(options)
        return self.command_result

    def status(self, *, options: object | None = None) -> object:
        self.calls.append(options)
        return self.status_result


@dataclass
class _Client:
    repositories: _Repositories
    daemon: _Daemon


def test_public_operations_enforce_identity_and_preserve_results() -> None:
    client = _Client(_Repositories(), _Daemon())
    options = {"timeout": 3.0, "environment": {"TOKEN": "secret"}}

    assert (
        checkout_command(
            client,
            "https://example.test/repo",
            ref="main",
            options=options,
            compatibility=COMPATIBILITY,
        )
        is client.repositories.command_result
    )
    assert (
        checkout(
            client,
            "https://example.test/repo",
            ref="main",
            options=options,
            compatibility=COMPATIBILITY,
        )
        is client.repositories.checkout_result
    )
    assert (
        daemon_status_command(client, options=options, compatibility=COMPATIBILITY)
        is client.daemon.command_result
    )
    assert (
        daemon_status(client, options=options, compatibility=COMPATIBILITY)
        is client.daemon.status_result
    )
    assert client.repositories.calls == [
        ("https://example.test/repo", "main", False, options),
        ("https://example.test/repo", "main", False, options),
    ]
    assert client.daemon.calls == [options, options]
    assert "secret" not in repr(client.repositories.checkout_result)


@pytest.mark.parametrize(
    "compatibility",
    [
        None,
        MulticaCompatibility("0.1.1", MULTICA_PY_REVISION, MULTICA_CLI_MINIMUM),
        MulticaCompatibility(MULTICA_PY_VERSION, "unrelated-revision", MULTICA_CLI_MINIMUM),
        MulticaCompatibility(MULTICA_PY_VERSION, MULTICA_PY_REVISION, "0.5.2"),
        MulticaCompatibility(MULTICA_PY_VERSION, MULTICA_PY_REVISION, "unknown"),
    ],
    ids=["unavailable", "ambiguous-version", "ambiguous-revision", "old-cli", "ambiguous-cli"],
)
def test_compatibility_rejects_unavailable_or_ambiguous_identity(
    compatibility: MulticaCompatibility | None,
) -> None:
    with pytest.raises(MulticaCompatibilityError):
        require_contract(compatibility)


def test_compatibility_accepts_exact_identity() -> None:
    require_contract(COMPATIBILITY)


def test_metadata_file_keeps_the_pinned_public_dependency_contract() -> None:
    root = Path(__file__).parents[3]
    with (root / "pyproject.toml").open("rb") as stream:
        metadata = tomllib.load(stream)["tool"]["odoo-instance-sdk"]["multica"]
    assert metadata == {
        "distribution": "multica-py",
        "version": MULTICA_PY_VERSION,
        "revision": MULTICA_PY_REVISION,
        "native_cli_minimum": MULTICA_CLI_MINIMUM,
    }
    requirement = (root / "tests/compatibility/multica-py-requirements.txt").read_text()
    assert "@c1842ae2dfcd0cc5e739b7785d3209d5e72d01ed" in requirement


def test_pinned_public_types_operations_and_redaction_when_sdk_is_installed(
    tmp_path: Path,
) -> None:
    multica_py = pytest.importorskip("multica_py")
    repositories = pytest.importorskip("multica_py.resources.repositories")
    daemon = pytest.importorskip("multica_py.resources.daemon")
    models = pytest.importorskip("multica_py.models.system")

    assert hasattr(multica_py, "MulticaClient")
    assert hasattr(models, "RepositoryCheckoutResult")
    assert hasattr(models, "DaemonStatus")
    result = models.RepositoryCheckoutResult(path="/caller-owned/checkout")
    assert result.path == "/caller-owned/checkout"
    checkout_signature = inspect.signature(repositories.RepositoryResource.checkout_command)
    checkout_parameters = checkout_signature.parameters
    assert checkout_parameters["url"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert checkout_parameters["fresh"].default is False
    assert "options" in checkout_parameters
    assert "status_command" in dir(daemon.DaemonResource)

    executable = tmp_path / "multica"
    executable.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *'repo checkout https://example.test/repo'*) printf '/caller-owned/checkout\\n' ;;\n"
        "  *'daemon status'*) printf '{\"status\":\"ready\"}\\n' ;;\n"
        "  *) printf 'token=secret\\n' >&2; exit 7 ;;\n"
        "esac\n"
    )
    os.chmod(executable, 0o755)
    client = multica_py.MulticaClient(multica_py.ClientConfig(executable=str(executable)))
    options = multica_py.OperationOptions(environment={"TOKEN": "secret"})
    checkout_command_result = checkout_command(
        client,
        "https://example.test/repo",
        ref="main",
        options=options,
        compatibility=COMPATIBILITY,
    )
    assert checkout_command_result.run().path == "/caller-owned/checkout"
    assert (
        checkout(
            client,
            "https://example.test/repo",
            ref="main",
            options=options,
            compatibility=COMPATIBILITY,
        ).path
        == "/caller-owned/checkout"
    )
    assert (
        daemon_status_command(client, options=options, compatibility=COMPATIBILITY).run().status
        == "ready"
    )
    assert daemon_status(client, options=options, compatibility=COMPATIBILITY).status == "ready"
    with pytest.raises(multica_py.CommandExecutionError) as raised:
        checkout(
            client, "https://example.test/failure", options=options, compatibility=COMPATIBILITY
        )
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize("error_name", ["CommandTimeoutError", "CommandCancelledError"])
def test_pinned_public_errors_propagate_without_local_decoding(error_name: str) -> None:
    multica_py = pytest.importorskip("multica_py")
    error_type = getattr(multica_py, error_name)

    class FailingRepositories(_Repositories):
        def checkout(self, *args: object, **kwargs: object) -> object:
            raise error_type("public SDK failure with token=secret")

    client = _Client(FailingRepositories(), _Daemon())
    with pytest.raises(error_type, match="public SDK failure"):
        checkout(client, "https://example.test/repo", compatibility=COMPATIBILITY)
