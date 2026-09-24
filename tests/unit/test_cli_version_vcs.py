from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.commands.cli_parts import registration as reg


class _FakeDist:
    def __init__(self, version: str, direct_url: str | None) -> None:
        self.version = version
        self._direct_url = direct_url

    def read_text(self, filename: str) -> str | None:
        if filename == "direct_url.json":
            return self._direct_url
        return None


def _patch_metadata(
    monkeypatch: pytest.MonkeyPatch,
    *,
    version: str = "0.1.0",
    direct_url: str | None,
) -> None:
    dist = _FakeDist(version=version, direct_url=direct_url)

    def _distribution(_name: str) -> _FakeDist:
        return dist

    monkeypatch.setattr(reg, "distribution", _distribution)


def _invoke_version() -> tuple[int, str, str]:
    runner = CliRunner()
    result = runner.invoke(reg.cli, ["--version"], prog_name="odcli")
    return result.exit_code, result.stdout, result.stderr


_VCS_DIRECT_URL = json.dumps(
    {
        "url": "git+https://github.com/maximchikAlexandr/odoo-instance-sdk.git@6a984c7deadbeef",
        "vcs_info": {
            "vcs": "git",
            "commit_id": "6a984c7deadbeefcafebabe1234567890abcdef",
            "requested_revision": "main",
            "resolved_revision": "6a984c7deadbeefcafebabe1234567890abcdef",
            "subdirectory": None,
        },
    }
)


@pytest.mark.parametrize(
    ("direct_url", "expected"),
    [
        (_VCS_DIRECT_URL, "odcli, version 0.1.0 (6a984c7)"),
        (
            json.dumps({"url": "https://example.com/odoo_instance_sdk-0.1.0-py3-none-any.whl"}),
            "odcli, version 0.1.0",
        ),
        (None, "odcli, version 0.1.0"),
    ],
    ids=["vcs-install", "wheel-install", "no-direct-url"],
)
def test_version_output_reflects_install_metadata(
    monkeypatch: pytest.MonkeyPatch, direct_url: str | None, expected: str
) -> None:
    _patch_metadata(monkeypatch, direct_url=direct_url)
    exit_code, stdout, stderr = _invoke_version()
    assert exit_code == 0, stderr
    assert stdout.strip() == expected


@pytest.mark.parametrize(
    ("direct_url", "expected"),
    [
        (json.dumps({"vcs_info": {"commit_id": "6a984c7"}}), "odcli, version 0.1.0 (6a984c7)"),
        (json.dumps({"vcs_info": {"commit_id": "ABCDEF0123456789"}}), "odcli, version 0.1.0"),
        ("{not json", "odcli, version 0.1.0"),
        (json.dumps({"url": "https://example.com"}), "odcli, version 0.1.0"),
        (json.dumps({"vcs_info": {}}), "odcli, version 0.1.0"),
        (json.dumps({"vcs_info": {"commit_id": "abcdef"}}), "odcli, version 0.1.0"),
        (json.dumps({"vcs_info": {"commit_id": "notahex!"}}), "odcli, version 0.1.0"),
        (json.dumps({"vcs_info": {"commit_id": 12345}}), "odcli, version 0.1.0"),
        ("[]", "odcli, version 0.1.0"),
        ("", "odcli, version 0.1.0"),
    ],
    ids=[
        "exact-7-hex",
        "uppercase-hex",
        "malformed-json",
        "missing-vcs_info",
        "empty-vcs_info",
        "short-commit",
        "non-hex-commit",
        "non-string-commit",
        "non-object-payload",
        "empty-text",
    ],
)
def test_version_handles_edge_case_commit_metadata(
    monkeypatch: pytest.MonkeyPatch, direct_url: str, expected: str
) -> None:
    _patch_metadata(monkeypatch, direct_url=direct_url or None)
    exit_code, stdout, stderr = _invoke_version()
    assert exit_code == 0, stderr
    assert stdout.strip() == expected


def test_version_falls_back_when_package_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from importlib.metadata import PackageNotFoundError

    def _raise(_name: str) -> Any:
        raise PackageNotFoundError("odoo-instance-sdk")

    monkeypatch.setattr(reg, "distribution", _raise)
    exit_code, stdout, stderr = _invoke_version()
    assert exit_code == 0, stderr
    assert stdout.strip() == "odcli, version unknown"
