"""Focused tests for real-Odoo log, cache, and workflow contracts."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from scripts import real_odoo_bootstrap as bootstrap
from scripts import real_odoo_ci as ci
from tests.integration.real_odoo.cleanup import ResourceLedger

if TYPE_CHECKING:
    import pytest


def test_service_log_capture_selects_generated_compose_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = type(
        "Runtime",
        (),
        {
            "scope": "target",
            "topology": type("Topology", (), {"project_name": "owned-project"})(),
            "compose_file": tmp_path / "compose.yaml",
            "root": tmp_path / "runtime",
        },
    )()
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "bounded tail", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(ci, "_evidence_root", lambda: tmp_path / "evidence")
    ci._capture_service_logs(runtime)
    flattened = [argument for command in calls for argument in command]
    assert "target_init" in flattened
    assert "target_postgres" in flattened
    assert "source_odoo" not in flattened
    assert "target_odoo" not in flattened
    assert "source_postgres" not in flattened
    assert (tmp_path / "evidence" / "odoo.log").is_file()
    assert (tmp_path / "evidence" / "postgres.log").is_file()


def test_target_log_capture_reads_host_managed_odoo_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = type(
        "Runtime",
        (),
        {
            "scope": "target",
            "topology": type("Topology", (), {"project_name": "owned-project"})(),
            "compose_file": tmp_path / "compose.yaml",
            "root": tmp_path / "runtime",
        },
    )()
    (runtime.root / "xdg-data" / "environment").mkdir(parents=True)
    (runtime.root / "xdg-data" / "environment" / "odoo.log").write_text(
        "host-managed target process\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "compose tail", ""),
    )
    monkeypatch.setattr(ci, "_evidence_root", lambda: tmp_path / "evidence")
    ci._capture_service_logs(runtime)
    assert "host-managed target process" in (tmp_path / "evidence" / "target-odoo.log").read_text(
        encoding="utf-8"
    )


def test_command_matrix_projection_is_staged_in_evidence_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ci, "_evidence_root", lambda: tmp_path / "evidence")
    ci._write_command_matrix()
    projection = tmp_path / "evidence" / "command-matrix.md"
    assert projection.is_file()
    assert "# Public CLI traceability matrix" in projection.read_text(encoding="utf-8")


def test_source_cache_consumption_requires_actual_shared_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "odoo.git"
    alternates = tmp_path / "runtime" / "project" / ".git" / "objects" / "info"
    alternates.mkdir(parents=True)
    cache_objects = cache / "objects"
    cache_objects.mkdir(parents=True)
    (alternates / "alternates").write_text(str(cache_objects) + "\n", encoding="utf-8")
    runtime = type("Runtime", (), {"root": tmp_path / "runtime"})()
    monkeypatch.setenv("ODCLI_E2E_ODOO_SOURCE_CACHE", str(cache))

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 0, "cd992ceebbaf343c03e1941d39cfe423d35ba6c6\n", ""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert ci._runtime_consumed_source_cache(runtime)
    ci._SOURCE_CACHE_CONSUMED[:] = [True]
    assert ci._source_cache_consumed()


def test_source_cache_consumption_survives_in_test_unwind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "odoo.git"
    alternates = tmp_path / "odcli-e2e-run-a" / "project" / ".git" / "objects" / "info"
    alternates.mkdir(parents=True)
    objects = cache / "objects"
    objects.mkdir(parents=True)
    (alternates / "alternates").write_text(str(objects) + "\n", encoding="utf-8")
    runtime_root = tmp_path / "odcli-e2e-run-a"
    monkeypatch.setenv("ODCLI_E2E_ODOO_SOURCE_CACHE", str(cache))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, "cd992ceebbaf343c03e1941d39cfe423d35ba6c6\n", ""
        ),
    )
    ci._SOURCE_CACHE_CONSUMED.clear()
    ledger = ResourceLedger("run-a")
    ledger.record("runtime-root", "run-a-root", lambda: shutil.rmtree(runtime_root))
    original_unwind = ci._ORIGINAL_UNWIND
    ci._ORIGINAL_UNWIND = ResourceLedger.unwind
    try:
        ci._instrumented_unwind(ledger)
    finally:
        ci._ORIGINAL_UNWIND = original_unwind
    assert not runtime_root.exists()
    assert ci._source_cache_consumed()


def test_source_cache_consumption_ignores_another_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "odoo.git"
    alternates = tmp_path / "odcli-e2e-run-b" / "project" / ".git" / "objects" / "info"
    alternates.mkdir(parents=True)
    objects = cache / "objects"
    objects.mkdir(parents=True)
    (alternates / "alternates").write_text(str(objects) + "\n", encoding="utf-8")
    monkeypatch.setenv("ODCLI_E2E_ODOO_SOURCE_CACHE", str(cache))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, "cd992ceebbaf343c03e1941d39cfe423d35ba6c6\n", ""
        ),
    )
    ci._SOURCE_CACHE_CONSUMED.clear()
    ci._remember_source_cache_consumption("run-a")
    assert not ci._source_cache_consumed()


def test_full_bootstrap_has_no_synthetic_checkout_prerequisite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.real_odoo_bootstrap.shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(
        bootstrap,
        "_run",
        lambda _command, **_kwargs: subprocess.CompletedProcess([], 0, "", ""),
    )
    monkeypatch.setattr(bootstrap, "_version_is_exact", lambda _command, _expected: True)
    monkeypatch.setattr(bootstrap, "_image_manifest_is_pinned", lambda _image, _digest: True)
    monkeypatch.setattr(bootstrap, "python_resolution_lock_is_valid", lambda: True)
    monkeypatch.setattr(bootstrap, "python_resolution_audit_is_valid", lambda: True)
    monkeypatch.setattr(bootstrap, "_source_revision_is_available", lambda: True)
    checks = bootstrap.prerequisite_checks("full", "linux/amd64")
    assert checks["odoo_source_revision"] is True
    assert "odoo_source_checkout" not in checks


def test_full_bootstrap_rejects_audit_before_source_cache_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.real_odoo_bootstrap.shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(
        bootstrap,
        "_run",
        lambda _command, **_kwargs: subprocess.CompletedProcess([], 0, "", ""),
    )
    monkeypatch.setattr(bootstrap, "_version_is_exact", lambda _command, _expected: True)
    monkeypatch.setattr(bootstrap, "_image_manifest_is_pinned", lambda _image, _digest: True)
    monkeypatch.setattr(bootstrap, "python_resolution_lock_is_valid", lambda: True)
    monkeypatch.setattr(bootstrap, "python_resolution_audit_is_valid", lambda: False)
    source_probe_calls: list[bool] = []

    def source_revision_probe() -> bool:
        source_probe_calls.append(True)
        return True

    monkeypatch.setattr(bootstrap, "_source_revision_is_available", source_revision_probe)

    checks = bootstrap.prerequisite_checks("full", "linux/amd64")

    assert checks["python_resolution_audit"] is False
    assert checks["odoo_source_revision"] is False
    assert source_probe_calls == []


def test_full_bootstrap_hashes_requirements_after_source_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "_cache_os_arch", lambda: ("Linux", "X64"))
    requirements = tmp_path / ".cache" / "odoo-requirements.txt"
    (tmp_path / "uv.lock").write_bytes(b"repository lock bytes")

    def fake_prerequisites(_tier: str, _platform: str) -> dict[str, bool]:
        requirements.parent.mkdir()
        requirements.write_bytes(b"pinned requirement bytes\r\n")
        return {"odoo_source_revision": True}

    monkeypatch.setattr(bootstrap, "prerequisite_checks", fake_prerequisites)
    manifest = bootstrap.bootstrap("full", tmp_path / "bootstrap.json", platform_name="linux/amd64")
    cache = manifest["cache"]
    assert isinstance(cache, dict)
    assert cache["uv_key"] == bootstrap.uv_cache_key(
        "Linux",
        "X64",
        requirements.read_bytes(),
        (tmp_path / "uv.lock").read_bytes(),
        bootstrap.PYTHON_RESOLUTION_LOCK.read_bytes(),
    )


def test_real_odoo_workflows_are_immutable_and_select_their_tier() -> None:
    root = Path(__file__).resolve().parents[2]
    smoke = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    full = (root / ".github/workflows/real-odoo-full.yml").read_text(encoding="utf-8")
    docs = (root / "docs/real-odoo-e2e.md").read_text(encoding="utf-8")
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    smoke_scenario = (root / "tests/integration/real_odoo/test_smoke.py").read_text(
        encoding="utf-8"
    )
    smoke_job = smoke[smoke.index("  real-odoo-smoke:") : smoke.index("\n  lint:")]
    for workflow in (smoke_job, full):
        assert "@v" not in workflow
        assert "retention-days: 7" in workflow
        assert "--output .artifacts/real-odoo-e2e/bootstrap.json" in workflow
    assert "timeout-minutes: 10" in smoke_job
    assert "real_odoo and e2e_smoke" in smoke
    assert "real_odoo and e2e_full" in full
    assert "pytest.mark.e2e_smoke" in smoke_scenario
    for scenario in ("E2E-SM-01", "E2E-SM-02", "E2E-SM-03", "E2E-SM-04", "E2E-SM-05"):
        assert scenario in smoke_scenario
    assert "runtime = target_runtime" in smoke_scenario
    assert "_align_target_master_password(runtime, source_server)" in smoke_scenario
    assert "source_server.topology.source_odoo_port" in smoke_scenario
    assert "runtime.topology.target_postgres_port" in smoke_scenario
    assert "target-data" in smoke_scenario
    warm_job = smoke[smoke.index("  real-odoo-smoke-warm:") : smoke.index("\n  lint:")]
    cold_job = smoke[smoke.index("  real-odoo-smoke:") : smoke.index("\n  real-odoo-smoke-warm:")]
    assert "needs: real-odoo-smoke" in warm_job
    assert "Save cold smoke uv cache" in cold_job
    assert "actions/cache/restore@" not in cold_job
    assert "actions/cache/restore@6849a6489940f00c2f30c0fb92c6274307ccb58a" in warm_job
    assert cold_job.count("uv_cache_key(") == 1
    assert warm_job.count("uv_cache_key(") == 1
    assert 'b"", Path("uv.lock").read_bytes()' in cold_job
    assert 'b"", Path("uv.lock").read_bytes()' in warm_job
    assert "key: ${{ steps.uv-key.outputs.key }}" in cold_job
    assert "key: ${{ steps.uv-key.outputs.key }}" in warm_job
    assert "restore-keys:" not in warm_job
    assert "matrix.cache_class" not in smoke_job
    assert "real-odoo-smoke-evidence-cold" in cold_job
    assert "real-odoo-smoke-evidence-warm" in warm_job
    assert "enable-cache: false" in smoke_job
    assert "ODCLI_E2E_SOURCE_CACHE_HIT" in smoke_job
    assert "ODCLI_E2E_UV_CACHE_HIT" in smoke_job
    assert "ODCLI_E2E_SOURCE_CACHE_HIT: ${{ matrix.cache_class" not in smoke_job
    assert "ODCLI_E2E_UV_CACHE_HIT: ${{ matrix.cache_class" not in smoke_job
    assert "steps.uv-cache.outputs.cache-hit" in smoke_job
    assert "actions/cache/restore@6849a6489940f00c2f30c0fb92c6274307ccb58a" in smoke_job
    assert "Force cold smoke cache miss" in smoke_job
    assert "Require warm smoke cache hit" in smoke_job
    assert ".cache.source_hit" in smoke_job
    assert ".stat().st_size > 0" in smoke_scenario
    assert "Assert smoke cache classification" in smoke_job
    assert "Assert smoke evidence budget" in smoke_job
    assert ".cache/odoo-source" in full
    assert ".cache/uv" in full
    assert "backups" not in full.lower()
    smoke_command = "uv run pytest -o addopts='' -o junit_family=xunit1 --junitxml=.artifacts/real-odoo-e2e/junit.xml -p scripts.real_odoo_timing -p scripts.real_odoo_ci -m 'real_odoo and e2e_smoke' tests/integration/real_odoo"
    full_command = "uv run pytest -o addopts='' -o junit_family=xunit1 --junitxml=.artifacts/real-odoo-e2e/junit.xml -p scripts.real_odoo_timing -p scripts.real_odoo_ci -m 'real_odoo and e2e_full' tests/integration/real_odoo"
    assert smoke_command in smoke_job
    assert smoke_command in docs
    assert smoke_command in makefile
    assert full_command in full
    assert full_command in docs
    assert full_command in makefile
    assert (
        "uv run python scripts/real_odoo_bootstrap.py --tier smoke --output .artifacts/real-odoo-e2e/bootstrap.json"
        in docs
    )
    assert (
        "uv run python scripts/real_odoo_bootstrap.py --tier full --output .artifacts/real-odoo-e2e/bootstrap.json"
        in docs
    )
    assert "--junitxml=.artifacts/real-odoo-e2e/junit.xml" in smoke_job
    assert "--junitxml=.artifacts/real-odoo-e2e/junit.xml" in full
    for workflow in (smoke_job, full):
        assert "--junitxml=.artifacts/real-odoo-e2e/junit.xml" in workflow
        assert "-p scripts.real_odoo_timing" in workflow
        assert "ODCLI_E2E_TIMING_FILE" in workflow
        assert "phase setup" in workflow and "phase test" in workflow
        assert "phase cleanup" not in workflow
        assert "-p scripts.real_odoo_ci" in workflow
        assert "if: steps.package.outcome == 'success'" in workflow
        assert "if: steps.package.outcome != 'success'" in workflow
        assert "packaging-error.json" in workflow
    for workflow, failure_name in (
        (cold_job, "real-odoo-smoke-packaging-failure-cold"),
        (warm_job, "real-odoo-smoke-packaging-failure-warm"),
        (full, "real-odoo-full-packaging-failure"),
    ):
        failure_upload = workflow[workflow.index(failure_name) :]
        assert ".artifacts/real-odoo-e2e/junit.xml" in failure_upload
        assert ".artifacts/real-odoo-e2e/packaging-error.json" in failure_upload
        assert ".artifacts/real-odoo-e2e/bootstrap.json" not in failure_upload
    assert "uv run --no-project python scripts/real_odoo_timing.py start" in smoke_job
    assert "uv run --no-project python scripts/real_odoo_timing.py start" in full
    assert "hashFiles(" not in full
    assert full.count("steps.source-key.outputs.key") >= 4
    assert full.count("steps.uv-key.outputs.key") >= 4
    assert "steps.source-prep.outputs.verified_source_cache_hit" in full
    assert "Materialize verified source-backed checkout" not in full
    assert "git -C .cache/odoo-source update-ref refs/heads/odoo19-pinned FETCH_HEAD" in full
    assert "ODCLI_E2E_SOURCE_CHECKOUT" not in full
    assert ".artifacts/real-odoo-e2e/odoo-source" not in full
    assert "from scripts.real_odoo_bootstrap import source_cache_key" in full
    assert "from scripts.real_odoo_bootstrap import uv_cache_key" in full
    assert "ODCLI_E2E_ODOO_SOURCE_CACHE: .cache/odoo-source" in full
    assert "ODCLI_E2E_SOURCE_CACHE: .cache/odoo-source" not in full
    action_refs = re.findall(r"uses:\s+[^@\s]+@([0-9a-f]{40})", smoke_job + full)
    assert action_refs and all(len(reference) == 40 for reference in action_refs)
