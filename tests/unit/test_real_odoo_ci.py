from __future__ import annotations

import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast

import pytest

from scripts import real_odoo_bootstrap as bootstrap
from scripts import real_odoo_ci as ci
from scripts import real_odoo_evidence as evidence
from scripts import real_odoo_timing as timing
from tests.integration.real_odoo import test_smoke as smoke
from tests.integration.real_odoo.cleanup import ResourceLedger
from tests.integration.real_odoo.conftest import E2ERuntime


def _evidence_contract(
    source: Path, *, junit_failures: int = 0, source_cache_consumed: bool = True
) -> None:
    (source / "bootstrap.json").write_text(
        json.dumps(
            {
                "ok": True,
                "platform": "linux/amd64",
                "architecture": "X64",
                "cache": {
                    "class": "cold",
                    "source_hit": False,
                    "uv_hit": False,
                    "source_key": "odoo19-Linux-X64-pin",
                    "uv_key": "uv-Linux-X64-python-uv-digest",
                },
                "pins": {"uv": "0.10.8"},
            }
        )
        + "\n"
    )
    (source / "resource-manifest.json").write_text(
        json.dumps(
            {
                "resources": ["owned-run-resource"],
                "audit": {
                    "state": "clean",
                    "leaks": [],
                    "runs": [{"run_id": "owned-run", "state": "clean", "leaks": {}}],
                },
                "source_cache_consumed": source_cache_consumed,
            }
        )
        + "\n"
    )
    (source / "command-matrix.md").write_text(
        "# Public CLI traceability matrix\n\n| Public leaf | Evidence |\n",
        encoding="utf-8",
    )
    (source / "timing.json").write_text(
        json.dumps(
            {
                "schema": "odcli-real-odoo-timing-v1",
                "phases": {
                    "setup": {"duration_seconds": 1.0},
                    "test": {"duration_seconds": 2.0},
                    "cleanup": {"duration_seconds": 0.1},
                },
            }
        )
    )
    (source / "junit.xml").write_text(f"<testsuite tests='1' failures='{junit_failures}'/>\n")


def test_cache_keys_include_all_immutable_inputs() -> None:
    source = bootstrap.source_cache_key("Linux", "amd64")
    uv = bootstrap.uv_cache_key("Linux", "amd64", b"odoo requirements", b"uv lock")
    assert source == "odoo19-Linux-amd64-cd992ceebbaf343c03e1941d39cfe423d35ba6c6"
    assert uv.startswith("uv-Linux-amd64-3.12.13-0.10.8-")
    assert len(uv.rsplit("-", 1)[1]) == 64


def test_bootstrap_emits_fail_closed_machine_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        bootstrap,
        "prerequisite_checks",
        lambda _tier, _platform: {"docker": False, "compose": True},
    )
    output = tmp_path / "bootstrap.json"
    manifest = bootstrap.bootstrap("smoke", output, platform_name="linux/amd64", run_id="a" * 32)
    assert manifest["ok"] is False
    assert manifest["missing"] == ["docker"]
    assert json.loads(output.read_text(encoding="utf-8"))["pins"]
    assert not (tmp_path / ("a" * 32)).exists()


def test_full_bootstrap_verifies_an_arbitrary_commit_from_bare_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "odoo.git"
    cache.mkdir()
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "requirements\n", "")

    monkeypatch.setattr(bootstrap, "_source_cache_path", lambda: cache)
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path / "root")
    monkeypatch.setattr(bootstrap, "_run", fake_run)
    assert bootstrap._source_revision_is_available()
    assert not any("ls-remote" in command for command in calls)
    assert any("cat-file" in command for command in calls)


def test_evidence_is_bounded_and_canary_safe(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _evidence_contract(source)
    canary_file = tmp_path / "canary"
    canary_file.write_text("canary-value-1234\n", encoding="utf-8")
    output = tmp_path / "evidence.tar.gz"
    result = evidence.package_evidence(
        source,
        output,
        status="success",
        canary_file=canary_file,
        tier="smoke",
        cache_class="cold",
    )
    assert result["artifact_bytes"] == output.stat().st_size
    budget = result["budget"]
    assert isinstance(budget, dict)
    assert budget["artifact_bytes"] == output.stat().st_size
    assert result["retention_days"] == 7
    assert '"ok": true' in (source / "timing.json").read_text()
    assert "setup_seconds" in (source / "junit.xml").read_text()
    with tarfile.open(output, "r:gz") as archive:
        names = archive.getnames()
        assert "command-matrix.md" not in names
        assert "odoo.log" not in names
        assert "postgres.log" not in names
        assert "compose.log" not in names


def test_evidence_rejects_canary_and_writes_minimal_error(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _evidence_contract(source, junit_failures=1)
    for name in ("odoo.log", "postgres.log", "compose.log"):
        (source / name).write_text("safe bounded tail\n", encoding="utf-8")
    (source / "odoo.log").write_text("canary-value-1234\n", encoding="utf-8")
    canary_file = tmp_path / "canary"
    canary_file.write_text("canary-value-1234\n", encoding="utf-8")
    output = tmp_path / "evidence.tar.gz"
    with pytest.raises(ValueError, match="canary"):
        evidence.package_evidence(
            source,
            output,
            status="failure",
            canary_file=canary_file,
            tier="smoke",
            cache_class="cold",
        )
    assert (tmp_path / "packaging-error.json").is_file()
    assert "canary-value-1234" not in (tmp_path / "packaging-error.json").read_text()
    assert 'tests="0" failures="1"' in (source / "junit.xml").read_text()


def test_evidence_rejects_manifest_canary_and_replaces_junit(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _evidence_contract(source)
    bootstrap_value = json.loads((source / "bootstrap.json").read_text())
    bootstrap_value["canary"] = "canary-value-1234"
    (source / "bootstrap.json").write_text(json.dumps(bootstrap_value))
    canary = tmp_path / "canary"
    canary.write_text("canary-value-1234\n", encoding="utf-8")
    with pytest.raises(ValueError, match="canary"):
        evidence.package_evidence(
            source,
            tmp_path / "manifest-canary.tar.gz",
            status="success",
            canary_file=canary,
            tier="smoke",
            cache_class="cold",
        )
    assert "canary-value-1234" not in (source / "junit.xml").read_text()
    assert 'tests="0" failures="1"' in (source / "junit.xml").read_text()


def test_evidence_rejects_empty_success_and_oversized_failure_input(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="missing evidence contract"):
        evidence.package_evidence(
            empty,
            tmp_path / "empty.tar.gz",
            status="success",
            tier="smoke",
            cache_class="cold",
        )

    source = tmp_path / "oversized"
    source.mkdir()
    _evidence_contract(source, junit_failures=1)
    for name in ("odoo.log", "postgres.log", "compose.log"):
        (source / name).write_text("safe\n", encoding="utf-8")
    (source / "odoo.log").write_bytes(b"x" * (evidence.TEXT_LIMIT_BYTES + 1))
    canary = tmp_path / "canary"
    canary.write_text("canary-value-1234\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exceeds"):
        evidence.package_evidence(
            source,
            tmp_path / "oversized.tar.gz",
            status="failure",
            canary_file=canary,
            tier="smoke",
            cache_class="cold",
        )
    assert (tmp_path / "packaging-error.json").is_file()


def test_timing_ledger_uses_monotonic_phase_durations(tmp_path: Path) -> None:
    path = tmp_path / "timing.json"
    timing.start(path, "setup", now=10.0)
    timing.finish(path, "setup", now=12.5)
    assert json.loads(path.read_text())["phases"]["setup"]["duration_seconds"] == 2.5


def test_evidence_rejects_oversized_failure_bundle(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.tar.gz"
    with oversized.open("wb") as stream:
        stream.truncate(evidence.FAILURE_BUNDLE_LIMIT_BYTES + 1)
    with pytest.raises(ValueError, match="exceeds"):
        evidence._check_archive_size(oversized, "failure")


def test_evidence_enforces_phase_budget_after_cleanup(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _evidence_contract(source)
    timing_value = json.loads((source / "timing.json").read_text(encoding="utf-8"))
    timing_value["phases"]["setup"]["duration_seconds"] = 361
    (source / "timing.json").write_text(json.dumps(timing_value), encoding="utf-8")
    canary = tmp_path / "canary"
    canary.write_text("canary-value-1234\n", encoding="utf-8")
    with pytest.raises(ValueError, match="budget"):
        evidence.package_evidence(
            source,
            tmp_path / "budget.tar.gz",
            status="success",
            canary_file=canary,
            tier="smoke",
            cache_class="cold",
        )


def test_evidence_rejects_placeholder_success(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "bootstrap.json").write_text('{"ok": false, "pins": {}}\n')
    (source / "junit.xml").write_text("<testsuite tests='0' failures='1'/>\n")
    (source / "command-matrix.md").write_text("# Public CLI traceability matrix\n")
    (source / "resource-manifest.json").write_text('{"resources": [], "leaks": []}\n')
    (source / "timing.json").write_text(
        json.dumps(
            {
                "phases": {
                    "setup": {"duration_seconds": 0},
                    "test": {"duration_seconds": 0},
                    "cleanup": {"duration_seconds": 0},
                }
            }
        )
    )
    canary = tmp_path / "canary"
    canary.write_text("canary-value-1234\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bootstrap"):
        evidence.package_evidence(
            source,
            tmp_path / "placeholder.tar.gz",
            status="success",
            canary_file=canary,
            tier="smoke",
            cache_class="cold",
        )
    assert (tmp_path / "packaging-error.json").is_file()
    assert (source / "junit.xml").is_file()


def test_evidence_records_runtime_and_audit_properties(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _evidence_contract(source)
    canary = tmp_path / "canary"
    canary.write_text("canary-value-1234\n", encoding="utf-8")
    result = evidence.package_evidence(
        source,
        tmp_path / "evidence.tar.gz",
        status="success",
        canary_file=canary,
        tier="smoke",
        cache_class="cold",
    )
    resource = json.loads((source / "resource-manifest.json").read_text())
    assert resource["evidence"]["platform"] == "linux/amd64"
    assert resource["evidence"]["architecture"] == "X64"
    assert resource["evidence"]["cache"]["source_hit"] is False
    assert resource["evidence"]["pins"]["uv"] == "0.10.8"
    assert resource["evidence"]["timing"]["cleanup_seconds"] == 0.1
    assert resource["evidence"]["artifact_bytes"] == result["artifact_bytes"]
    assert resource["evidence"]["audit_state"] == "clean"
    junit = (source / "junit.xml").read_text()
    for property_name in (
        "platform",
        "architecture",
        "cache_source_hit",
        "cache_uv_hit",
        "pin_uv",
        "timing_cleanup_seconds",
        "artifact_bytes",
        "audit_state",
    ):
        assert f'name="{property_name}"' in junit


def test_failure_evidence_keeps_non_clean_completed_audit(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _evidence_contract(source, junit_failures=1)
    resource = json.loads((source / "resource-manifest.json").read_text())
    resource["audit"] = {
        "state": "failed",
        "leaks": [{"run_id": "owned-run", "state": "leaked"}],
        "runs": [{"run_id": "owned-run", "state": "leaked", "leaks": {"port": ["1"]}}],
    }
    (source / "resource-manifest.json").write_text(json.dumps(resource))
    for name in ("compose.log", "odoo.log", "postgres.log"):
        (source / name).write_text("bounded service tail\n")
    canary = tmp_path / "canary"
    canary.write_text("canary-value-1234\n", encoding="utf-8")
    result = evidence.package_evidence(
        source,
        tmp_path / "failure.tar.gz",
        status="failure",
        canary_file=canary,
        tier="smoke",
        cache_class="cold",
    )
    assert result["ok"] is True
    assert (
        json.loads((source / "resource-manifest.json").read_text())["evidence"]["audit_state"]
        == "failed"
    )
    with tarfile.open(tmp_path / "failure.tar.gz", "r:gz") as archive:
        assert "command-matrix.md" in archive.getnames()


def test_full_failure_evidence_requires_host_target_log(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _evidence_contract(source, junit_failures=1)
    for name in ("compose.log", "odoo.log", "postgres.log"):
        (source / name).write_text("bounded service tail\n")
    canary = tmp_path / "canary"
    canary.write_text("canary-value-1234\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"target-odoo\.log"):
        evidence.package_evidence(
            source,
            tmp_path / "missing-target-log.tar.gz",
            status="failure",
            canary_file=canary,
            tier="full",
            cache_class="cold",
        )


@pytest.mark.parametrize("cache_class", ["cold", "warm"])
def test_evidence_exercises_smoke_budget_classification(
    tmp_path: Path, cache_class: Literal["cold", "warm"]
) -> None:
    timing_path = tmp_path / "timing.json"
    timing_path.write_text(
        json.dumps(
            {
                "phases": {
                    "setup": {"duration_seconds": 1},
                    "test": {"duration_seconds": 2},
                    "cleanup": {"duration_seconds": 1},
                }
            }
        )
    )
    report = evidence._budget_report(
        timing_path,
        status="success",
        tier="smoke",
        cache_class=cache_class,
        artifact_bytes=10,
    )
    assert report["cache_class"] == cache_class
    assert report["ok"] is True


@pytest.mark.parametrize("cache_class", ["cold", "warm"])
def test_smoke_bootstrap_and_evidence_emit_cache_class_and_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cache_class: Literal["cold", "warm"],
) -> None:
    """Exercise the smoke cache matrix through bootstrap and packaging."""
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "_cache_os_arch", lambda: ("Linux", "X64"))
    monkeypatch.setattr(bootstrap, "prerequisite_checks", lambda _tier, _platform: {})
    (tmp_path / "uv.lock").write_bytes(b"repository lock")
    requirements = tmp_path / ".cache" / "odoo-requirements.txt"
    requirements.parent.mkdir()
    requirements.write_bytes(b"pinned requirements")
    source = tmp_path / "evidence"
    source.mkdir()
    _evidence_contract(source)
    monkeypatch.setenv("ODCLI_E2E_SOURCE_CACHE_HIT", str(cache_class == "warm").lower())
    monkeypatch.setenv("ODCLI_E2E_UV_CACHE_HIT", str(cache_class == "warm").lower())
    bootstrap_manifest = bootstrap.bootstrap(
        "smoke", source / "bootstrap.json", platform_name="linux/amd64", artifact_root=source
    )
    emitted_cache = bootstrap_manifest["cache"]
    assert isinstance(emitted_cache, dict)
    emitted_class = cast("Literal['cold', 'warm']", emitted_cache["class"])
    assert emitted_class == cache_class
    canary = tmp_path / "canary"
    canary.write_text("smoke-canary\n", encoding="utf-8")
    packaged = evidence.package_evidence(
        source,
        tmp_path / "smoke.tar.gz",
        status="success",
        canary_file=canary,
        tier="smoke",
        cache_class=emitted_class,
    )
    packaged_budget = packaged["budget"]
    assert isinstance(packaged_budget, dict)
    assert packaged_budget["cache_class"] == cache_class
    assert packaged_budget["ok"] is True


def test_smoke_target_wiring_keeps_source_and_target_endpoints_distinct(tmp_path: Path) -> None:
    source_password = tmp_path / "source-password"
    target_password = tmp_path / "target-password"
    source_password.write_text("source-master\n", encoding="utf-8")
    target_password.write_text("target-master\n", encoding="utf-8")
    target_secret = tmp_path / "target-pg-password"
    target_secret.write_text("pg-secret\n", encoding="utf-8")
    target = SimpleNamespace(
        master_password_file=target_password,
        secret_file=target_secret,
        container_config_file=tmp_path / "container.conf",
        config_file=tmp_path / "host.conf",
        root=tmp_path / "target-root",
        topology=SimpleNamespace(target_postgres_port=15432),
        reservations=(None, None, None, SimpleNamespace(port=18069)),
    )
    source = SimpleNamespace(master_password_file=source_password)
    smoke._align_target_master_password(cast("E2ERuntime", target), cast("E2ERuntime", source))
    host_config = (tmp_path / "host.conf").read_text(encoding="utf-8")
    container_config = (tmp_path / "container.conf").read_text(encoding="utf-8")
    assert "admin_passwd = source-master" in host_config
    assert "db_host = 127.0.0.1" in host_config
    assert "db_port = 15432" in host_config
    assert "http_port = 18069" in host_config
    assert "admin_passwd = source-master" in container_config
    assert "db_host = target_postgres" in container_config
    assert target.root.joinpath("target-data").as_posix() in host_config
    assert "source_postgres" not in host_config


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
    monkeypatch.setattr(
        ci,
        "_ORIGINAL_FINALIZE",
        lambda _runtime, _failure=None: finalized.append("done"),
    )
    ci._RUNTIMES.clear()
    ci._RESOURCE_SNAPSHOTS.clear()
    ci._SOURCE_CACHE_CONSUMED.clear()
    ci._instrumented_finalize(runtime)
    assert finalized == ["done"]
    timing_value = json.loads(timing_path.read_text())
    assert timing_value["phases"]["cleanup"]["duration_seconds"] == 5.0
    resource = json.loads((tmp_path / "evidence" / "resource-manifest.json").read_text())
    assert resource["resources"][0]["name"] == "owned-run-container"
    assert resource["audit"]["state"] == "clean"


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
        bootstrap, "_run", lambda _command: subprocess.CompletedProcess([], 0, "", "")
    )
    monkeypatch.setattr(bootstrap, "_version_is_exact", lambda _command, _expected: True)
    monkeypatch.setattr(bootstrap, "_image_manifest_is_pinned", lambda _image, _digest: True)
    monkeypatch.setattr(bootstrap, "_source_revision_is_available", lambda: True)
    checks = bootstrap.prerequisite_checks("full", "linux/amd64")
    assert checks["odoo_source_revision"] is True
    assert "odoo_source_checkout" not in checks


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
        "Linux", "X64", requirements.read_bytes(), (tmp_path / "uv.lock").read_bytes()
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
    assert "cache_class: [cold, warm]" in smoke_job
    assert "enable-cache: true" in smoke_job
    assert "ODCLI_E2E_SOURCE_CACHE_HIT" in smoke_job
    assert "ODCLI_E2E_UV_CACHE_HIT" in smoke_job
    assert "Assert smoke cache classification" in smoke_job
    assert "Assert smoke evidence budget" in smoke_job
    assert ".cache/odoo-source" in full
    assert ".cache/uv" in full
    assert "backups" not in full.lower()
    smoke_command = "uv run pytest -o addopts='' --junitxml=.artifacts/real-odoo-e2e/junit.xml -p scripts.real_odoo_timing -p scripts.real_odoo_ci -m 'real_odoo and e2e_smoke' tests/integration/real_odoo"
    full_command = "uv run pytest -o addopts='' --junitxml=.artifacts/real-odoo-e2e/junit.xml -p scripts.real_odoo_timing -p scripts.real_odoo_ci -m 'real_odoo and e2e_full' tests/integration/real_odoo"
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
        (smoke_job, "real-odoo-smoke-packaging-failure"),
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
    assert "ODCLI_E2E_SOURCE_CHECKOUT" not in full
    assert ".artifacts/real-odoo-e2e/odoo-source" not in full
    assert "from scripts.real_odoo_bootstrap import source_cache_key" in full
    assert "from scripts.real_odoo_bootstrap import uv_cache_key" in full
    assert "ODCLI_E2E_ODOO_SOURCE_CACHE: .cache/odoo-source" in full
    assert "ODCLI_E2E_SOURCE_CACHE: .cache/odoo-source" not in full
    action_refs = re.findall(r"uses:\s+[^@\s]+@([0-9a-f]{40})", smoke_job + full)
    assert action_refs and all(len(reference) == 40 for reference in action_refs)
