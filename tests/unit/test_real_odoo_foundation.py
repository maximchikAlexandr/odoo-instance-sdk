from __future__ import annotations

import stat
import subprocess
import zipfile
from pathlib import Path

import pytest

from tests.integration.real_odoo import conftest as e2e_fixtures
from tests.integration.real_odoo.archive import (
    ArchiveValidationError,
    archive_identity,
    odoo_initialization_command,
)
from tests.integration.real_odoo.cleanup import (
    FailureEvidence,
    LeakError,
    ResourceLedger,
    audit_no_leaks,
)
from tests.integration.real_odoo.compose import (
    ComposeTopology,
    reserve_ports,
    wait_for_compose_pg_isready,
)


def test_compose_topology_is_namespaced_and_loopback_only() -> None:
    reservations = reserve_ports()
    try:
        topology = ComposeTopology.create(
            "a" * 32, (reservations[0].port, reservations[1].port, reservations[2].port)
        )
        rendered = topology.render(
            secret_file=Path("/run/secret"),
            addon_root=Path("/addons"),
            source_root=Path("/source"),
        )
        assert topology.project_name in topology.names
        assert '"127.0.0.1:' in rendered
        assert topology.postgres_image in rendered
        assert "a" * 32 in rendered
    finally:
        for reservation in reservations:
            reservation.release()


def test_archive_identity_requires_dump_and_filestore(tmp_path: Path) -> None:
    path = tmp_path / "backup.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("dump.sql", "-- fixture")
        archive.writestr("filestore/db/blob", b"fixture")
    identity = archive_identity(path)
    assert identity.size_bytes == path.stat().st_size
    assert identity.sha256
    assert identity.filestore_members == ("filestore/db/blob",)

    invalid = tmp_path / "invalid.zip"
    with zipfile.ZipFile(invalid, "w") as archive:
        archive.writestr("dump.sql", "-- fixture")
    with pytest.raises(ArchiveValidationError, match="filestore"):
        archive_identity(invalid)


def test_ledger_unwinds_in_reverse_and_evidence_is_bounded(tmp_path: Path) -> None:
    events: list[str] = []
    ledger = ResourceLedger("run123")
    ledger.record("one", "run123-one", lambda: events.append("one"))
    ledger.record("two", "run123-two", lambda: events.append("two"))
    ledger.unwind()
    assert events == ["two", "one"]

    evidence = FailureEvidence("run123", "canary", tmp_path)
    evidence.add_log("odoo", "credentials omitted\n")
    files = evidence.write()
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in files)
    assert audit_no_leaks("run123").clean


def test_runtime_factory_keeps_two_runs_disjoint_and_uses_loopback_target(
    tmp_path: Path,
) -> None:
    first = e2e_fixtures._make_runtime(tmp_path / "first", "a" * 32)
    second = e2e_fixtures._make_runtime(tmp_path / "second", "b" * 32)
    try:
        assert first.run_id != second.run_id
        assert first.topology.project_name != second.topology.project_name
        assert (
            first.config_file.read_text(encoding="utf-8").find(
                f"db_port = {first.topology.target_postgres_port}"
            )
            >= 0
        )
        assert "db_host = 127.0.0.1" in first.config_file.read_text(encoding="utf-8")
        assert stat.S_IMODE(first.secret_file.stat().st_mode) == 0o600
    finally:
        e2e_fixtures._remove_runtime_files(first)
        e2e_fixtures._remove_runtime_files(second)
        for reservation in (*first.reservations, *second.reservations):
            reservation.release()


def test_pg_readiness_is_not_satisfied_by_tcp_alone() -> None:
    attempts = 0

    def runner(args: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
        nonlocal attempts
        attempts += 1
        return subprocess.CompletedProcess(args, 0 if attempts == 2 else 1, "", "")

    wait_for_compose_pg_isready(runner, "source_postgres", timeout=1.0)
    assert attempts == 2


def test_initialization_command_and_all_leak_categories_are_explicit(tmp_path: Path) -> None:
    command = odoo_initialization_command(
        Path("/opt/odoo/odoo-bin"), Path("/run/odoo.conf"), "db_run", addon="base"
    )
    assert command[-2:] == ("--without-demo=all", "--stop-after-init")
    assert "--init" in command and "base" in command

    categories = (
        "process",
        "container",
        "network",
        "volume",
        "port",
        "database",
        "filestore",
        "worktree",
        "catalog",
        "runtime-root",
    )
    probes = {
        category: lambda run_id, category=category: (f"{run_id}-{category}",)
        for category in categories
    }
    with pytest.raises(LeakError) as error:
        audit_no_leaks("run123", probes=probes)
    assert set(error.value.report.leaks) == set(categories)


def test_secret_canary_and_password_are_not_written(tmp_path: Path) -> None:
    evidence = FailureEvidence("run123", "random-canary", tmp_path)
    evidence.add_log("odoo", "admin_passwd=real-secret\ncredentials omitted\n")
    files = evidence.write()
    text = files[0].read_text(encoding="utf-8")
    assert "real-secret" not in text
    assert "<redacted>" in text
    evidence.add_log("postgres", "random-canary")
    with pytest.raises(AssertionError, match="canary"):
        evidence.write()


def test_fixture_provision_runs_both_initializers_and_reverses_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = e2e_fixtures._make_runtime(tmp_path, "c" * 32)
    commands: list[tuple[str, ...]] = []
    cleaned: list[str] = []

    class FakeLifecycle:
        def __init__(self, compose_file: Path, project_name: str) -> None:
            assert compose_file == runtime.compose_file
            assert project_name == runtime.topology.project_name

        def run(self, *args: str, timeout: float = 180.0) -> subprocess.CompletedProcess[str]:
            commands.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(e2e_fixtures, "ComposeLifecycle", FakeLifecycle)
    monkeypatch.setattr(e2e_fixtures, "wait_for_compose_pg_isready", lambda *args: None)
    monkeypatch.setattr(e2e_fixtures, "wait_for_http", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        e2e_fixtures, "compose_down", lambda *args, **kwargs: cleaned.append("compose")
    )
    monkeypatch.setattr(e2e_fixtures, "audit_no_leaks", lambda *args, **kwargs: None)
    try:
        e2e_fixtures._provision(runtime)
        assert commands[0][:3] == ("up", "--detach", "--wait")
        assert any(
            f"--database={runtime.topology.source_database}" in command for command in commands
        )
        assert any(
            f"--database={runtime.topology.target_sentinel_database}" in command
            for command in commands
        )
    finally:
        e2e_fixtures._finalize(runtime)
    assert cleaned == ["compose"]
    assert not runtime.root.exists()
    assert not runtime.artifact_root.exists()


def test_fixture_failure_still_unwinds_created_compose_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = e2e_fixtures._make_runtime(tmp_path, "d" * 32)
    cleaned: list[str] = []

    class FailingLifecycle:
        def __init__(self, compose_file: Path, project_name: str) -> None:
            del compose_file, project_name

        def run(self, *args: str, timeout: float = 180.0) -> subprocess.CompletedProcess[str]:
            if "--init=base" in args:
                raise RuntimeError("injected initialization failure")
            return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(e2e_fixtures, "ComposeLifecycle", FailingLifecycle)
    monkeypatch.setattr(e2e_fixtures, "wait_for_compose_pg_isready", lambda *args: None)
    monkeypatch.setattr(
        e2e_fixtures, "compose_down", lambda *args, **kwargs: cleaned.append("compose")
    )
    monkeypatch.setattr(e2e_fixtures, "audit_no_leaks", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="injected"):
        try:
            e2e_fixtures._provision(runtime)
        except RuntimeError as error:
            e2e_fixtures._finalize(runtime, error)
            raise
    assert cleaned == ["compose"]
    assert not runtime.root.exists()
