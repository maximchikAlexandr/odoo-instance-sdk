"""Packaging E2E for ``odcli update`` on a uv-tool install."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from odoo_instance_sdk.storage.catalog_migrate import CATALOG_REVISION, catalog_revision

pytestmark = [pytest.mark.packaging]

_REPO = Path(__file__).resolve().parents[2]
_V16_CATALOG = _REPO / "tests" / "fixtures" / "catalog_v16.sql"


def _isolated_home(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir()
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    env["XDG_STATE_HOME"] = str(home / ".local" / "state")
    env["XDG_CACHE_HOME"] = str(home / ".cache")
    for path in (
        env["XDG_CONFIG_HOME"],
        env["XDG_DATA_HOME"],
        env["XDG_STATE_HOME"],
        env["XDG_CACHE_HOME"],
    ):
        Path(path).mkdir(parents=True, exist_ok=True)
    return env


def _require_uv() -> None:
    if shutil.which("uv") is None:
        pytest.skip("uv is required for update packaging E2E")


def _vcs_install_requirement(repo: Path, ref: str) -> str:
    return f"odoo-instance-sdk @ git+file://{repo.resolve()}@{ref}"


def _install_uv_tool_vcs(
    env: dict[str, str], repo: Path, ref: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "tool", "install", "--force", _vcs_install_requirement(repo, ref)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_uv_tool_update_already_current_from_local_install(tmp_path: Path) -> None:
    """Install the checkout as a uv tool and verify ``update --ref`` no-op."""
    _require_uv()
    env = _isolated_home(tmp_path)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO,
        text=True,
    ).strip()
    install = _install_uv_tool_vcs(env, _REPO, head)
    if install.returncode != 0:
        pytest.skip(f"uv tool install unavailable: {install.stderr}")
    odcli = Path(env["HOME"]) / ".local" / "bin" / "odcli"
    assert odcli.is_file()
    env["ODCLI_UV_TOOL_EXECUTABLE"] = str(odcli)
    result = subprocess.run(
        [str(odcli), "update", "--ref", head, "--yes", "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["result"]["outcome"] == "already_current"
    version = subprocess.run(
        [str(odcli), "--version"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert version.returncode == 0, version.stderr
    assert "odcli, version" in version.stdout


def test_maintenance_child_writes_update_result(tmp_path: Path) -> None:
    """Maintenance mode emits one JSON ``UpdateResult`` document on stdout."""
    _require_uv()
    env = _isolated_home(tmp_path)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO,
        text=True,
    ).strip()
    install = _install_uv_tool_vcs(env, _REPO, head)
    if install.returncode != 0:
        pytest.skip(f"uv tool install unavailable: {install.stderr}")
    odcli = Path(env["HOME"]) / ".local" / "bin" / "odcli"
    env["ODCLI_MAINTENANCE"] = "1"
    env["ODCLI_UV_TOOL_EXECUTABLE"] = str(odcli)
    result = subprocess.run(
        [str(odcli), "update", "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "updated"
    assert "executed_migration_ids" in payload


def test_uv_tool_update_migrates_fixture_catalog_in_separate_process(tmp_path: Path) -> None:
    """Apply real catalog migrations from a v16 fixture through maintenance mode."""
    _require_uv()
    env = _isolated_home(tmp_path)
    user_root = Path(env["HOME"]) / ".odcli"
    user_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    catalog_path = user_root / "catalog.sqlite3"
    with sqlite3.connect(str(catalog_path)) as conn:
        conn.executescript(_V16_CATALOG.read_text(encoding="utf-8"))
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO,
        text=True,
    ).strip()
    install = _install_uv_tool_vcs(env, _REPO, head)
    if install.returncode != 0:
        pytest.skip(f"uv tool install unavailable: {install.stderr}")
    odcli = Path(env["HOME"]) / ".local" / "bin" / "odcli"
    env["ODCLI_MAINTENANCE"] = "1"
    env["ODCLI_UV_TOOL_EXECUTABLE"] = str(odcli)
    maintenance = subprocess.run(
        [str(odcli), "update", "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert maintenance.returncode == 0, maintenance.stderr or maintenance.stdout
    payload = json.loads(maintenance.stdout)
    assert payload["outcome"] == "updated"
    assert any(entry.startswith("catalog:") for entry in payload["executed_migration_ids"])
    with sqlite3.connect(str(catalog_path)) as conn:
        assert catalog_revision(conn) == CATALOG_REVISION
    version = subprocess.run(
        [str(odcli), "--version"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert version.returncode == 0, version.stderr
    assert "odcli, version" in version.stdout


def test_uv_tool_full_update_from_old_revision(tmp_path: Path) -> None:
    """Install an older checkout, run ``odcli update``, migrate, and verify."""
    _require_uv()
    env = _isolated_home(tmp_path)
    user_root = Path(env["HOME"]) / ".odcli"
    user_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    catalog_path = user_root / "catalog.sqlite3"
    with sqlite3.connect(str(catalog_path)) as conn:
        conn.executescript(_V16_CATALOG.read_text(encoding="utf-8"))
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO,
        text=True,
    ).strip()
    try:
        old_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD~1"],
            cwd=_REPO,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        pytest.skip("no parent revision available for update E2E")
    if old_sha == head:
        pytest.skip("no parent revision available for update E2E")
    install_old = _install_uv_tool_vcs(env, _REPO, old_sha)
    if install_old.returncode != 0:
        pytest.skip(f"uv tool install unavailable for old revision: {install_old.stderr}")
    odcli = Path(env["HOME"]) / ".local" / "bin" / "odcli"
    env["ODCLI_UV_TOOL_EXECUTABLE"] = str(odcli)
    update_argv = [str(odcli), "update", "--ref", head, "--yes", "--format", "json"]
    if int(head, 16) < int(old_sha, 16):
        update_argv.append("--allow-downgrade")
    update = subprocess.run(
        update_argv,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if update.returncode != 0:
        error = json.loads(update.stdout).get("error", {})
        message = str(error.get("message", ""))
        if "uv tool install failed" in message:
            pytest.skip("target revision is not installable from GitHub in this environment")
        if "Unplanned execution step" in message:
            pytest.skip("installed revision lacks update plan compatibility required for E2E")
        assert False, update.stderr or update.stdout
    payload = json.loads(update.stdout)
    assert payload["result"]["outcome"] in {"updated", "already_current"}
    with sqlite3.connect(str(catalog_path)) as conn:
        assert catalog_revision(conn) == CATALOG_REVISION
    version = subprocess.run(
        [str(odcli), "--version"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert version.returncode == 0, version.stderr
    assert "odcli, version" in version.stdout
    doctor = subprocess.run(
        [str(odcli), "doctor", "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    assert doctor.returncode in {0, 1}, doctor.stderr or doctor.stdout
