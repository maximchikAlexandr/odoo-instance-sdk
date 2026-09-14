"""Focused tests for real-Odoo log, cache, and workflow contracts."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import real_odoo_bootstrap as bootstrap
from scripts import real_odoo_ci as ci
from tests.integration.real_odoo import cleanup as real_odoo_cleanup
from tests.integration.real_odoo.cleanup import ResourceLedger


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


def test_source_cache_consumption_accepts_shallow_shared_clone_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "odoo.git"
    project = tmp_path / "runtime" / "project"
    (project / ".git").mkdir(parents=True)
    runtime = type("Runtime", (), {"root": tmp_path / "runtime"})()
    monkeypatch.setenv("ODCLI_E2E_ODOO_SOURCE_CACHE", str(cache))

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[-3:] == ["config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(command, 0, f"{cache}\n", "")
        return subprocess.CompletedProcess(
            command, 0, "cd992ceebbaf343c03e1941d39cfe423d35ba6c6\n", ""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert ci._runtime_consumed_source_cache(runtime)


def test_source_cache_consumption_is_captured_before_fixture_cleanup(
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
    from tests.integration.real_odoo import conftest as fixtures

    monkeypatch.setattr(ci, "_ENABLED", True)
    monkeypatch.setattr(ci, "_capture_service_logs", lambda _runtime: None)
    monkeypatch.setattr(ci, "_write_resource_manifest", lambda: None)
    monkeypatch.setattr(fixtures, "_cleanup", lambda _runtime, _failure: ledger.unwind())
    runtime = type("Runtime", (), {"root": runtime_root, "ledger": ledger, "finalized": False})()
    fixtures._finalize(runtime)
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
    ci._remember_source_cache_consumption(tmp_path / "odcli-e2e-run-a")
    assert not ci._source_cache_consumed()


@pytest.mark.parametrize("failed", [False, True], ids=["success", "failure"])
def test_finalize_writes_cache_consumption_after_target_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    """The final target teardown publishes its audit even when cleanup fails."""
    from tests.integration.real_odoo import conftest as fixtures

    runtime_root = tmp_path / "odcli-e2e-run-a"
    runtime_root.mkdir()
    evidence = tmp_path / "evidence"
    monkeypatch.setattr(ci, "_ENABLED", True)
    monkeypatch.setattr(ci, "_capture_service_logs", lambda _runtime: None)
    monkeypatch.setattr(ci, "_remember_source_cache_consumption", lambda *_args: None)
    monkeypatch.setattr(ci, "_write_resource_manifest", lambda: evidence.mkdir(exist_ok=True))

    def cleanup(_runtime: object, _failure: object) -> None:
        if failed:
            raise RuntimeError("cleanup failed")

    monkeypatch.setattr(fixtures, "_cleanup", cleanup)
    runtime = type(
        "Runtime",
        (),
        {"root": runtime_root, "ledger": ResourceLedger("run-a"), "finalized": False},
    )()
    if failed:
        with pytest.raises(RuntimeError, match="cleanup failed"):
            fixtures._finalize(runtime)
    else:
        fixtures._finalize(runtime)
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


def test_owned_fixture_cleanup_is_fail_closed_and_returns_status_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pytest

    from tests.integration.real_odoo import test_critical_path as critical_path

    worktree = tmp_path / "worktree"
    destination = worktree / "addons" / "odcli_e2e_probe"
    destination.mkdir(parents=True)
    (destination / "__manifest__.py").write_text("{}\n", encoding="utf-8")
    statuses = iter(
        (
            subprocess.CompletedProcess(
                ["git"], 0, "?? addons/odcli_e2e_probe/__manifest__.py\n", ""
            ),
            subprocess.CompletedProcess(["git"], 0, "", ""),
        )
    )
    monkeypatch.setattr(
        "tests.integration.real_odoo.test_critical_path.subprocess.run",
        lambda *_args, **_kwargs: next(statuses),
    )

    before, after = critical_path._remove_owned_fixture_tree(worktree, destination)
    assert before == ("?? addons/odcli_e2e_probe/__manifest__.py",)
    assert after == ()
    assert not destination.exists()

    destination.mkdir(parents=True)
    (destination / "__manifest__.py").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        "tests.integration.real_odoo.test_critical_path.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["git"], 0, "?? addons/foreign.txt\n", ""
        ),
    )
    with pytest.raises(AssertionError, match="foreign worktree change"):
        critical_path._remove_owned_fixture_tree(worktree, destination)
    assert destination.is_dir()


def test_port_reservation_release_is_idempotent() -> None:
    from tests.integration.real_odoo.compose import reserve_loopback_port

    reservation = reserve_loopback_port()
    reservation.release()
    reservation.release()
    assert reservation.socket.fileno() == -1


def test_full_workflow_installs_ldap_and_psql_prerequisites() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/real-odoo-tier.yml").read_text(encoding="utf-8")
    assert "Install approved Linux LDAP build prerequisites" in workflow
    assert (
        "sudo apt-get install --yes --no-install-recommends "
        "libldap2-dev libsasl2-dev postgresql-client" in workflow
    )
    assert "psql --version" in workflow
    deps = workflow.split("Install approved Linux LDAP build prerequisites", 1)[1].split(
        "Bootstrap required full prerequisites", 1
    )[0]
    assert "python-ldap" not in deps


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


def test_focused_state_preflight_requires_manifest_environment_and_postgres_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
    from tests.integration.real_odoo import focused_support

    project = tmp_path / "project"
    manifest = project / ".odcli" / "project.toml"
    manifest.parent.mkdir(parents=True)
    python = project / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    manifest.write_text(
        "[project]\n"
        f'python = "{python}"\n'
        "\n[postgres]\n"
        'mode = "compose"\n'
        'image = "postgres@sha256:approved"\n'
        "port = 55432\n"
        'user = "odoo"\n',
        encoding="utf-8",
    )
    (project / ".odcli" / "e2e-environment-id").write_text("project-id\n", encoding="ascii")
    catalog_path = tmp_path / "catalog.sqlite3"
    catalog = BackupCatalog(db_path=catalog_path)
    cluster = SimpleNamespace(_project_id="project-id")
    claim = catalog._ensure_postgres_cluster_pending("project-id", "owned-compose", "owned-volume")
    catalog._activate_postgres_cluster(
        claim.cluster_id, "project-id", "owned-compose", "owned-volume"
    )
    catalog.close()
    monkeypatch.setattr(focused_support, "registered_worktree", lambda *_: project)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project",
        lambda _project: cluster,
    )

    assert focused_support.assert_project_state_preflight(project, catalog_path) == "project-id"


def test_project_restore_accepts_matching_environment_runtime_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from typing import Any, cast

    from odoo_instance_sdk.resources import instance as instance_module

    runtime = {
        "owner_kind": "environment",
        "owner_id": "environment-id",
        "http_port": 12345,
        "root_pid": 4242,
        "create_time": 10.0,
    }
    snapshot = SimpleNamespace(environments=(({}, runtime),), project_runtimes=())
    catalog = SimpleNamespace(_monitor_snapshot_rows=lambda **_: snapshot)
    instance = SimpleNamespace(
        _runtime_binding=SimpleNamespace(
            owner_kind="project", owner_id="project-id", project_id="project-id"
        ),
        _client=SimpleNamespace(get_catalog=lambda: catalog),
    )
    process = SimpleNamespace(
        is_running=lambda: True,
        status=lambda: "running",
        create_time=lambda: 10.0,
    )
    monkeypatch.setattr("odoo_instance_sdk.resources.instance.psutil.Process", lambda _pid: process)

    assert instance_module._project_runtime_owns_port(
        cast("Any", instance), cast("Any", SimpleNamespace(http_port=12345))
    )


def test_smoke_workflow_preserves_sequential_cache_contract() -> None:
    root = Path(__file__).resolve().parents[2] / ".github/workflows"
    caller = (root / "ci.yml").read_text()
    workflow = (root.parent / "actions/real-odoo-smoke/action.yml").read_text()
    assert caller.count("uses: ./.github/actions/real-odoo-smoke") == 2
    assert "needs: real-odoo-smoke" in caller
    assert "cache_class: cold" in caller and "cache_class: warm" in caller
    for name, condition in (
        ("Force cold smoke cache miss", "cold"),
        ("Save cold smoke uv cache", "cold"),
        ("Restore warm smoke uv cache", "warm"),
        ("Require warm smoke cache hit", "warm"),
    ):
        step = workflow.split(f"- name: {name}", 1)[1].split("\n    - ", 1)[0]
        assert f"if: inputs.cache_class == '{condition}'" in step
    assert 'test "${{ steps.uv-cache.outputs.cache-hit }}" = true' in workflow
    assert "restore-keys:" not in workflow
    assert workflow.count("key: ${{ steps.uv-key.outputs.key }}") == 2
    assert "ODCLI_E2E_UV_CACHE_HIT: ${{ steps.uv-cache.outputs.cache-hit" in workflow
    assert "EXPECTED_CACHE_CLASS: ${{ inputs.cache_class }}" in workflow
    assert "real-odoo-smoke-evidence-${{ inputs.cache_class }}" in workflow


def test_e2e_workflows_pin_actions_and_gate_evidence_uploads() -> None:
    root = Path(__file__).resolve().parents[2] / ".github/workflows"
    for filename, tier in (
        ("../actions/real-odoo-smoke/action.yml", "smoke"),
        ("real-odoo-tier.yml", "full"),
    ):
        workflow = (root / filename).read_text()
        actions = re.findall(r"uses:\s+(\S+)", workflow)
        assert actions and all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", ref) for ref in actions)
        assert f"actions/cache/restore@{bootstrap.ACTIONS_CACHE}" in actions
        assert f"actions/cache/save@{bootstrap.ACTIONS_CACHE}" in actions
        assert "enable-cache: false" in workflow
        assert f"real_odoo and e2e_{tier}" in workflow
        assert "-p scripts.real_odoo_timing -p scripts.real_odoo_ci" in workflow
        assert "--junitxml=.artifacts/real-odoo-e2e/junit.xml" in workflow
        uploads = workflow.split("- uses: actions/upload-artifact@")[1:]
        assert len(uploads) == 2
        success, failure = uploads
        assert "if: always() && steps.package.outcome == 'success'" in success
        assert "if: always() && steps.package.outcome != 'success'" in failure
        assert "*-evidence.tar.gz" in success and "resource-manifest.json" in success
        assert "junit.xml" in failure and "packaging-error.json" in failure
        assert "bootstrap.json" not in failure
        assert all("retention-days: 7" in upload for upload in uploads)
