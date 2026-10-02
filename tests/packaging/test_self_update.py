"""Packaging E2E for ``odcli update`` on a uv-tool install."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from odoo_instance_sdk.storage.catalog_migrate import CATALOG_REVISION, catalog_revision

pytestmark = [pytest.mark.packaging, pytest.mark.timeout(180)]

_REPO = Path(__file__).resolve().parents[2]
_V16_CATALOG = _REPO / "tests" / "fixtures" / "catalog_v16.sql"
_GIT_COMMAND_TIMEOUT_SECONDS = 10
_INSTALL_CHILD_TIMEOUT_SECONDS = 120
_FULL_UPDATE_TEST_TIMEOUT_SECONDS = 600
_FULL_UPDATE_CHILD_TIMEOUT_SECONDS = 240
_FULL_DRY_RUN_CHILD_TIMEOUT_SECONDS = 120
_FULL_VERSION_CHILD_TIMEOUT_SECONDS = 30
_FULL_DOCTOR_CHILD_TIMEOUT_SECONDS = 30
_CURRENT_UPDATE_TEST_TIMEOUT_SECONDS = 180
_CURRENT_UPDATE_CHILD_TIMEOUT_SECONDS = 120
_CURRENT_VERSION_CHILD_TIMEOUT_SECONDS = 30
_CHECK_TEST_TIMEOUT_SECONDS = 300
_CHECK_CHILD_TIMEOUT_SECONDS = 90


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
    # Production deliberately installs from the allow-listed GitHub origin.
    # Rewrite that origin to this checkout so packaging E2E never waits on a
    # network fetch after the local fixture install has already succeeded.
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = f"url.file://{_REPO.resolve()}/.insteadOf"
    env["GIT_CONFIG_VALUE_0"] = "https://github.com/maximchikAlexandr/odoo-instance-sdk.git"
    # Reuse the package/source cache between the old install and target
    # install. The virtual environments remain isolated, while the expensive
    # local VCS build is performed once per packaging test session.
    env["UV_CACHE_DIR"] = str(tmp_path.parent / "uv-cache")
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
        timeout=_INSTALL_CHILD_TIMEOUT_SECONDS,
    )


def _trace_odcli_launcher(
    executable: Path,
    launcher: Path,
    trace_path: Path,
) -> None:
    """Create an exec-preserving launcher that records external argv safely."""
    body = (
        "import json\n"
        "import os\n"
        "import sys\n"
        f"REAL = {json.dumps(str(executable))}\n"
        f"TRACE = {json.dumps(str(trace_path))}\n"
        "with open(TRACE, 'a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "os.execv(REAL, [REAL, *sys.argv[1:]])\n"
    )
    launcher.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    launcher.chmod(0o755)


@pytest.mark.timeout(_CURRENT_UPDATE_TEST_TIMEOUT_SECONDS)
def test_uv_tool_update_already_current_from_local_install(tmp_path: Path) -> None:
    """Install the checkout and verify the no-op update within a cold-start budget."""
    _require_uv()
    env = _isolated_home(tmp_path)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO,
        text=True,
        timeout=_GIT_COMMAND_TIMEOUT_SECONDS,
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
        timeout=_CURRENT_UPDATE_CHILD_TIMEOUT_SECONDS,
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
        timeout=_CURRENT_VERSION_CHILD_TIMEOUT_SECONDS,
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


@pytest.mark.timeout(_FULL_UPDATE_TEST_TIMEOUT_SECONDS)
def test_uv_tool_full_update_from_old_revision(tmp_path: Path) -> None:
    """Install an older checkout, run ``odcli update``, migrate, and verify.

    The 600-second budget covers the bounded 120-second fixture install, the
    120-second public dry-run, the 240-second update child, two 30-second
    post-update probes, git/setup overhead, and assertions. The Git URL rewrite
    keeps the target install local while retaining the production allow-listed
    origin.
    """
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
        timeout=_GIT_COMMAND_TIMEOUT_SECONDS,
    ).strip()
    try:
        old_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD~1"],
            cwd=_REPO,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=_GIT_COMMAND_TIMEOUT_SECONDS,
        ).strip()
    except subprocess.CalledProcessError:
        pytest.skip("no parent revision available for update E2E")
    if old_sha == head:
        pytest.skip("no parent revision available for update E2E")
    install_old = _install_uv_tool_vcs(env, _REPO, old_sha)
    if install_old.returncode != 0:
        pytest.skip(f"uv tool install unavailable for old revision: {install_old.stderr}")
    odcli = Path(env["HOME"]) / ".local" / "bin" / "odcli"
    trace_path = tmp_path / "odcli-launches.jsonl"
    traced_odcli = tmp_path / "odcli-traced"
    _trace_odcli_launcher(odcli, traced_odcli, trace_path)
    env["ODCLI_UV_TOOL_EXECUTABLE"] = str(traced_odcli)
    env["ODCLI_TRACE_FILE"] = str(trace_path)
    preview = subprocess.run(
        [str(odcli), "update", "--ref", head, "--yes", "--dry-run", "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=_FULL_DRY_RUN_CHILD_TIMEOUT_SECONDS,
    )
    assert preview.returncode == 0, preview.stderr or preview.stdout
    preview_payload = json.loads(preview.stdout)
    planned_steps = preview_payload["result"]["steps"]
    planned_verify_steps = [
        step for step in planned_steps if step["step_id"] == "update.verify.version"
    ]
    assert len(planned_verify_steps) == 1
    assert planned_verify_steps[0]["argv"][0] == str(traced_odcli)
    assert planned_verify_steps[0]["argv"][-1] == "--version"
    update_argv = [str(odcli), "update", "--ref", head, "--yes", "--format", "json"]
    if int(head, 16) < int(old_sha, 16):
        update_argv.append("--allow-downgrade")
    update = subprocess.run(
        update_argv,
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=_FULL_UPDATE_CHILD_TIMEOUT_SECONDS,
    )
    if update.returncode != 0:
        error = json.loads(update.stdout).get("error", {})
        message = str(error.get("message", ""))
        if "uv tool install failed" in message:
            pytest.skip("target revision is not installable from GitHub in this environment")
        if "target history is unavailable" in message:
            pytest.skip("Git ancestry source is unavailable for the local packaging fixture")
        if "Unplanned execution step" in message:
            pytest.fail(message)
        assert False, update.stderr or update.stdout
    payload = json.loads(update.stdout)
    assert payload["result"]["outcome"] == "updated"
    assert payload["result"]["target_sha"] == head
    assert payload["result"]["final_sha"] == head
    assert payload["result"]["snapshot_state"] == "cleared"
    assert payload["result"]["journal_state"] == "cleared"
    assert not (user_root / "update" / "journal.json").exists()
    assert not (user_root / "update" / "snapshot").exists()
    with sqlite3.connect(str(catalog_path)) as conn:
        assert catalog_revision(conn) == CATALOG_REVISION
    post_update_env = {
        key: value
        for key, value in env.items()
        if key not in {"ODCLI_UV_TOOL_EXECUTABLE", "ODCLI_TRACE_FILE"}
    }
    version = subprocess.run(
        [str(odcli), "--version"],
        check=False,
        capture_output=True,
        text=True,
        env=post_update_env,
        timeout=_FULL_VERSION_CHILD_TIMEOUT_SECONDS,
    )
    assert version.returncode == 0, version.stderr
    assert "odcli, version" in version.stdout
    doctor = subprocess.run(
        [str(odcli), "doctor", "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
        env=post_update_env,
        cwd=tmp_path,
        timeout=_FULL_DOCTOR_CHILD_TIMEOUT_SECONDS,
    )
    assert doctor.returncode in {0, 1}, doctor.stderr or doctor.stdout
    launches = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert [argv for argv in launches if argv == ["--version"]].count(["--version"]) == 1


@pytest.mark.timeout(_CHECK_TEST_TIMEOUT_SECONDS)
def test_uv_tool_update_check_reports_interrupted_state_without_resume(tmp_path: Path) -> None:
    """Public check reports frozen recovery evidence without mutating it.

    The 300-second budget covers the bounded 120-second fixture install, the
    90-second public check (the isolated macOS cold-start is about 21 seconds),
    local fixture setup, and pytest overhead; the check child cannot consume
    the remaining scenario budget indefinitely.
    """
    _require_uv()
    env = _isolated_home(tmp_path)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO,
        text=True,
        timeout=_GIT_COMMAND_TIMEOUT_SECONDS,
    ).strip()
    install = _install_uv_tool_vcs(env, _REPO, head)
    if install.returncode != 0:
        pytest.skip(f"uv tool install unavailable: {install.stderr}")
    odcli = Path(env["HOME"]) / ".local" / "bin" / "odcli"
    env["ODCLI_UV_TOOL_EXECUTABLE"] = str(odcli)
    update_root = Path(env["HOME"]) / ".odcli" / "update"
    snapshot = update_root / "snapshot"
    snapshot.mkdir(parents=True)
    (snapshot / "metadata.json").write_text(
        json.dumps(
            {
                "version": 1,
                "previous_version": "0.1.0",
                "package_revision": "0.1.0",
                "previous_sha": head,
                "install_requirement": _vcs_install_requirement(_REPO, head),
                "source_repo": _REPO.resolve().as_uri(),
                "target_ref": head,
                "snapshot_sha": head,
            }
        ),
        encoding="utf-8",
    )
    journal = update_root / "journal.json"
    journal.write_text(
        json.dumps(
            {
                "version": 1,
                "phase": "migrate",
                "target_ref": head,
                "snapshot_sha": head,
                "maintenance_pid": 2_000_000,
            }
        ),
        encoding="utf-8",
    )
    journal_before = journal.read_bytes()
    snapshot_before = {
        path.relative_to(snapshot): path.read_bytes()
        for path in snapshot.rglob("*")
        if path.is_file()
    }

    result = subprocess.run(
        [str(odcli), "update", "--check", "--ref", head, "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=_CHECK_CHILD_TIMEOUT_SECONDS,
    )

    assert result.returncode != 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    details = payload["error"]["details"]
    assert payload["error"]["code"] == "update_incomplete"
    assert journal.read_bytes() == journal_before
    assert {
        path.relative_to(snapshot): path.read_bytes()
        for path in snapshot.rglob("*")
        if path.is_file()
    } == snapshot_before
    assert details["outcome"] == "update_incomplete"
    assert details["journal_state"] == "present"
    assert details["snapshot_state"] == "present"
    assert details["recovery_argv"] == [str(odcli), "update", "--ref", head, "--yes"]
    assert journal.is_file()
    assert snapshot.is_dir()
    assert journal.read_bytes() == journal_before
    assert {
        path.relative_to(snapshot): path.read_bytes()
        for path in snapshot.rglob("*")
        if path.is_file()
    } == snapshot_before
