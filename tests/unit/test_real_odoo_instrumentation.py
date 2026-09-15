"""instrumentation tests for the real-Odoo CI contract."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import real_odoo_bootstrap as bootstrap
from scripts import real_odoo_ci as ci
from scripts import real_odoo_timing as timing


def test_real_pytest_plugins_load_through_collection(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        path for path in (str(root), environment.get("PYTHONPATH", "")) if path
    )
    environment["ODCLI_E2E_EVIDENCE_ROOT"] = str(tmp_path / "evidence")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
            "-p",
            "scripts.real_odoo_timing",
            "-p",
            "scripts.real_odoo_ci",
            str(root / "tests/integration/real_odoo"),
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_container_smoke_public_path" in result.stdout


def test_timing_plugin_measures_fixture_cleanup_after_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "timing.json"
    timing.start(path, "test", now=10.0)
    monkeypatch.setenv("ODCLI_E2E_TIMING_FILE", str(path))
    clock = iter((12.0, 20.0, 23.0))
    monkeypatch.setattr("scripts.real_odoo_timing.time.monotonic", lambda: next(clock))
    hook = timing.pytest_sessionfinish(None, 0)
    next(hook)
    value = json.loads(path.read_text())
    assert "duration_seconds" not in value["phases"]["test"]
    assert "cleanup" not in value["phases"]
    with pytest.raises(StopIteration):
        next(hook)
    value = json.loads(path.read_text())
    assert value["phases"]["test"]["duration_seconds"] == 2.0


def test_declared_plugins_reach_pytest_collection_and_execution(tmp_path: Path) -> None:
    test_file = tmp_path / "test_declared_plugin.py"
    test_file.write_text(
        "def test_declared_plugin_executes():\n    assert True\n",
        encoding="utf-8",
    )
    timing_file = tmp_path / "timing.json"
    timing.start(timing_file, "test")
    environment = {
        **os.environ,
        "ODCLI_E2E_TIMING_FILE": str(timing_file),
        "ODCLI_E2E_EVIDENCE_ROOT": str(tmp_path / "evidence"),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-o",
            "addopts=",
            "-p",
            "scripts.real_odoo_timing",
            "-p",
            "scripts.real_odoo_ci",
            str(test_file),
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    manifest = json.loads(timing_file.read_text(encoding="utf-8"))
    assert manifest["phases"]["test"]["duration_seconds"] >= 0.0
    assert (tmp_path / "evidence" / "command-matrix.md").is_file()


def test_test_timing_excludes_recorded_cleanup_segments(tmp_path: Path) -> None:
    path = tmp_path / "timing.json"
    timing.start(path, "test", now=0.0)
    timing.record(path, "cleanup", 10.0, 20.0)
    assert timing.finish_test_excluding_cleanup(path, now=50.0) == 40.0
    value = json.loads(path.read_text())
    assert value["phases"]["test"]["duration_seconds"] == 40.0
    assert value["phases"]["cleanup"]["duration_seconds"] == 10.0


def test_bootstrap_rejects_injected_cache_key_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        bootstrap,
        "prerequisite_checks",
        lambda _tier, _platform: {"docker": True},
    )
    monkeypatch.setenv("ODCLI_E2E_SOURCE_CACHE_KEY", "injected-wrong-key")
    output = tmp_path / "bootstrap.json"
    manifest = bootstrap.bootstrap("smoke", output, platform_name="linux/amd64")
    assert manifest["ok"] is False
    assert "does not match computed cache key" in str(manifest["error"])


def test_cleanup_instrumentation_wraps_finalize_and_writes_post_audit_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Record:
        def __init__(self) -> None:
            self.kind = "container"
            self.name = "owned-run-container"
            self.metadata = {"owner": "owned-run"}

    class Ledger:
        records = (Record(),)

    runtime = type(
        "Runtime",
        (),
        {
            "run_id": "owned-run",
            "scope": "source",
            "ledger": Ledger(),
            "topology": type(
                "Topology",
                (),
                {
                    "project_name": "owned-run-project",
                    "source_postgres_port": 1,
                    "target_postgres_port": 2,
                    "source_odoo_port": 3,
                },
            )(),
            "reservations": (None, None, None, type("Reservation", (), {"port": 4})()),
            "compose_file": tmp_path / "compose.yaml",
            "root": tmp_path / "runtime",
        },
    )()
    timing_path = tmp_path / "timing.json"
    timing.start(timing_path, "test", now=0.0)
    monkeypatch.setenv("ODCLI_E2E_TIMING_FILE", str(timing_path))
    monkeypatch.setattr(ci, "_evidence_root", lambda: tmp_path / "evidence")
    monkeypatch.setattr(ci, "_capture_service_logs", lambda _runtime: None)
    monkeypatch.setattr(
        ci,
        "_audit",
        lambda _runtime: {"run_id": "owned-run", "state": "clean", "leaks": {}},
    )
    monkeypatch.setattr("scripts.real_odoo_ci.time.monotonic", iter((10.0, 15.0)).__next__)
    finalized: list[str] = []
    monkeypatch.setattr(ci, "_ENABLED", True)
    ci._RUNTIMES.clear()
    ci._RESOURCE_SNAPSHOTS.clear()
    ci._SOURCE_CACHE_CONSUMED.clear()
    with ci.capture_cleanup(runtime):
        finalized.append("done")
    assert finalized == ["done"]
    timing_value = json.loads(timing_path.read_text())
    assert timing_value["phases"]["cleanup"]["duration_seconds"] == 5.0
    resource = json.loads((tmp_path / "evidence" / "resource-manifest.json").read_text())
    assert resource["resources"][0]["name"] == "owned-run-container"
    assert resource["audit"]["state"] == "clean"
