"""Focused tests for real-Odoo log, cache, and workflow contracts."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from scripts import real_odoo_bootstrap as bootstrap
from scripts import real_odoo_ci as ci
from tests.integration.real_odoo import cleanup as real_odoo_cleanup
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


def test_source_cache_consumption_resolves_relative_alternates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "odoo.git"
    alternates = tmp_path / "runtime" / "project" / ".git" / "objects" / "info"
    alternates.mkdir(parents=True)
    (cache / "objects").mkdir(parents=True)
    (alternates / "alternates").write_text("../../../../../odoo.git/objects\n", encoding="utf-8")
    runtime = type("Runtime", (), {"root": tmp_path / "runtime"})()
    monkeypatch.setenv("ODCLI_E2E_ODOO_SOURCE_CACHE", str(cache))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, "cd992ceebbaf343c03e1941d39cfe423d35ba6c6\n", ""
        ),
    )
    assert ci._runtime_consumed_source_cache(runtime)


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


def test_finalize_writes_cache_consumption_after_target_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The final target teardown must publish the source-cache audit."""
    runtime_root = tmp_path / "odcli-e2e-run-a"
    runtime_root.mkdir()
    evidence = tmp_path / "evidence"
    monkeypatch.setattr(ci, "_evidence_root", lambda: evidence)
    monkeypatch.setattr(ci, "_capture_service_logs", lambda _runtime: None)
    monkeypatch.setattr(ci, "_remember_source_cache_consumption", lambda *_args: None)
    monkeypatch.setattr(ci, "_write_resource_manifest", lambda: evidence.mkdir(exist_ok=True))
    original = ci._ORIGINAL_FINALIZE
    try:
        ci._ORIGINAL_FINALIZE = lambda _runtime, _failure: None
        runtime = type(
            "Runtime",
            (),
            {
                "run_id": "run-a",
                "root": runtime_root,
                "scope": "target",
                "ledger": type("Ledger", (), {"records": ()})(),
            },
        )()
        ci._instrumented_finalize(runtime)
    finally:
        ci._ORIGINAL_FINALIZE = original
    assert evidence.is_dir()


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


def test_bootstrap_imports_before_project_environment_sync() -> None:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-S", "-c", "import scripts.real_odoo_bootstrap"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


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
    assert "  pull_request:" in full
    assert "ref: ${{ github.event.pull_request.head.sha || github.sha }}" in full
    assert "Seed minimal pre-scenario failure evidence" in full
    for early_failure_file in (
        "command-matrix.md",
        "compose.log",
        "odoo.log",
        "postgres.log",
        "resource-manifest.json",
        "target-odoo.log",
    ):
        assert early_failure_file in full
    assert "pytest.mark.e2e_smoke" in smoke_scenario
    assert "@pytest.mark.timeout(180, func_only=True)" in smoke_scenario
    for scenario in ("E2E-SM-01", "E2E-SM-02", "E2E-SM-03", "E2E-SM-04", "E2E-SM-05"):
        assert scenario in smoke_scenario
    assert "runtime = target_runtime" in smoke_scenario
    assert "_align_target_master_password(runtime, source_server)" in smoke_scenario
    assert "source_server.topology.source_odoo_port" in smoke_scenario
    assert "runtime.topology.target_postgres_port" in smoke_scenario
    assert "target-data" in smoke_scenario
    warm_job = smoke[smoke.index("  real-odoo-smoke-warm:") : smoke.index("\n  lint:")]
    cold_job = smoke[smoke.index("  real-odoo-smoke:") : smoke.index("\n  real-odoo-smoke-warm:")]
    for workflow in (cold_job, warm_job):
        assert "name: Pin workflow uv cache path" in workflow
        assert 'echo "UV_CACHE_DIR=${{ github.workspace }}/.cache/uv" >> "$GITHUB_ENV"' in workflow
    for workflow in (smoke_job, full):
        assert f"actions/cache/restore@{bootstrap.ACTIONS_CACHE}" in workflow
        assert f"actions/cache/save@{bootstrap.ACTIONS_CACHE}" in workflow
        assert "actions/cache/restore@6849a6489940f00c2f30c0fb92c6274307ccb58a" not in workflow
        assert "actions/cache/save@6849a6489940f00c2f30c0fb92c6274307ccb58a" not in workflow
    sync_step = full.index("      - name: Sync frozen test environment")
    bootstrap_step = full.index("      - name: Bootstrap required full prerequisites")
    assert sync_step < bootstrap_step
    assert "from scripts.real_odoo_bootstrap import uv_cache_key" in full
    assert "needs: real-odoo-smoke" in warm_job
    assert "Save cold smoke uv cache" in cold_job
    assert "actions/cache/restore@" not in cold_job
    assert "actions/cache/restore@0400d5f644dc74513175e3cd8d07132dd4860809" in warm_job
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
    assert "actions/cache/restore@0400d5f644dc74513175e3cd8d07132dd4860809" in smoke_job
    assert "Force cold smoke cache miss" in smoke_job
    assert "Require warm smoke cache hit" in smoke_job
    assert ".cache.source_hit" in smoke_job
    assert ".stat().st_size > 0" in smoke_scenario
    assert "Assert smoke cache classification" in smoke_job
    assert "Assert smoke evidence budget" in smoke_job
    assert ".cache/odoo-source" in full
    assert ".cache/uv" in full
    assert "if: always() && steps.package.outcome == 'success'" in full
    assert "if: always() && steps.package.outcome == 'success'" in cold_job
    assert "if: always() && steps.package.outcome == 'success'" in warm_job

    assert "ODCLI_E2E_ODOO_SOURCE_CACHE: ${{ github.workspace }}/.cache/odoo-source" in full
    assert "ODCLI_E2E_ODOO_SOURCE_REPO: ${{ github.workspace }}/.cache/odoo-source" in full
    source_cache_verify = full.index("Verify pinned source cache before full pytest")
    full_pytest = full.index("Run source-backed full E2E")
    assert source_cache_verify < full_pytest
    assert 'test "$(git -C "$cache" rev-parse --is-bare-repository)" = true' in full
    assert 'git -C "$cache" fsck --full --no-progress' in full
    assert 'cat-file -e "${ODOO_SOURCE_COMMIT}^{commit}"' in full
    assert 'cat-file -e "${ODOO_SOURCE_COMMIT}:requirements.txt"' in full
    assert 'find "$cache" -mindepth 1 -print -quit' in full
    assert "restored source cache is not a bare repository" in full
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
        assert "if: always() && steps.package.outcome == 'success'" in workflow
        assert "if: always() && steps.package.outcome != 'success'" in workflow
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
    assert 'git -C "$cache" update-ref refs/heads/odoo19-pinned FETCH_HEAD' in full
    assert "ODCLI_E2E_SOURCE_CHECKOUT" not in full
    assert ".artifacts/real-odoo-e2e/odoo-source" not in full
    assert "from scripts.real_odoo_bootstrap import source_cache_key" in full
    assert "from scripts.real_odoo_bootstrap import uv_cache_key" in full
    assert "ODCLI_E2E_ODOO_SOURCE_CACHE: .cache/odoo-source" not in full
    assert "ODCLI_E2E_SOURCE_CACHE: .cache/odoo-source" not in full
    action_refs = re.findall(r"uses:\s+[^@\s]+@([0-9a-f]{40})", smoke_job + full)
    assert action_refs and all(len(reference) == 40 for reference in action_refs)


def test_runtime_fixture_normalizes_nested_container_data_permissions() -> None:
    root = Path(__file__).resolve().parents[2]
    conftest = (root / "tests/integration/real_odoo/conftest.py").read_text(encoding="utf-8")
    assert "chmod -R a+rwX -- {data_dir}" in conftest
    assert "_make_container_data_host_removable(" in conftest
    assert '"HOME": str(home)' in conftest
    assert '"ODCLI_E2E_CATALOG": str(home / ".odcli" / "catalog.sqlite3")' in conftest


def test_focused_leaves_snapshot_catalog_and_wait_for_project_ports() -> None:
    root = Path(__file__).resolve().parents[2]
    focused = (root / "tests/integration/real_odoo/test_focused_failures.py").read_text(
        encoding="utf-8"
    )
    assert "focused-catalog-baseline.sqlite3" in focused
    assert "_isolated_catalog(focused_catalog, tmp_path)" in focused
    assert '"HOME": str(catalog_path.parent.parent)' in focused
    assert '"postgres",\n            "approve-image"' in focused
    assert "ports=(project_postgres_port,)" in focused
    assert "_ensure_isolated_environment(" in focused
    assert "_get_postgres_cluster(cluster._project_id)" in focused
    assert '"postgres",\n            "up"' in focused
    assert "public_http_port" in focused


def test_smoke_rewrites_container_config_for_uid_100() -> None:
    root = Path(__file__).resolve().parents[2]
    smoke = (root / "tests/integration/real_odoo/test_smoke.py").read_text(encoding="utf-8")
    assert "mode=0o644" in smoke
    assert "auxiliary_http_port" in smoke
    assert "target_runtime.reservations[3].port" in smoke


def test_real_odoo_fixture_preserves_shared_runtime_until_finalizer() -> None:
    root = Path(__file__).resolve().parents[2]
    critical = (root / "tests/integration/real_odoo/test_critical_path.py").read_text(
        encoding="utf-8"
    )
    assert "runtime.ledger.unwind()" not in critical
    assert "ports=(cluster.endpoint_port,)" in critical


def test_full_critical_path_persists_registered_python_manifest() -> None:
    root = Path(__file__).resolve().parents[2]
    critical = (root / "tests/integration/real_odoo/test_critical_path.py").read_text(
        encoding="utf-8"
    )
    assert "python=python" in critical
    assert "ProjectConfig.load(project)" in critical


def test_port_reservation_release_is_idempotent() -> None:
    from tests.integration.real_odoo.compose import reserve_loopback_port

    reservation = reserve_loopback_port()
    reservation.release()
    reservation.release()
    assert reservation.socket.fileno() == -1


def test_full_workflow_installs_only_approved_ldap_build_prerequisites() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/real-odoo-full.yml").read_text(encoding="utf-8")
    assert "Install approved Linux LDAP build prerequisites" in workflow
    assert (
        "sudo apt-get install --yes --no-install-recommends libldap2-dev libsasl2-dev" in workflow
    )
    deps = workflow.split("Install approved Linux LDAP build prerequisites", 1)[1].split(
        "Bootstrap required full prerequisites", 1
    )[0]
    assert "python-ldap" not in deps


def test_smoke_readiness_keeps_budget_for_diagnostic_service_logs() -> None:
    root = Path(__file__).resolve().parents[2]
    smoke = (root / "tests/integration/real_odoo/test_smoke.py").read_text(encoding="utf-8")
    assert "timeout=90.0" in smoke and "timeout=15.0" in smoke
    assert '"logs"' in smoke and '"--no-color"' in smoke and '"target_init"' in smoke
    assert "target Odoo readiness failed" in smoke


def test_owned_process_cleanup_targets_the_whole_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int, float]] = []
    monkeypatch.setattr(
        real_odoo_cleanup,
        "terminate_pid",
        lambda pid, *, process_group_id, timeout: calls.append((pid, process_group_id, timeout)),
    )
    real_odoo_cleanup.terminate_owned_process_group(1234)
    assert calls == [(1234, 1234, 5.0)]


def test_focused_catalog_binding_uses_existing_public_path_boundary() -> None:
    root = Path(__file__).resolve().parents[2]
    focused = (root / "tests/integration/real_odoo/test_focused_failures.py").read_text(
        encoding="utf-8"
    )
    assert "odoo_instance_sdk.internal.port_allocation.get_catalog_path" not in focused
    assert "odoo_instance_sdk.resources.postgres.get_catalog_path" not in focused
    assert "odoo_instance_sdk.resources.monitor.get_catalog_path" not in focused
    assert "odoo_instance_sdk.internal.paths.get_catalog_path" in focused
