from __future__ import annotations

import stat
import subprocess
import sys
import zipfile
from hashlib import sha256
from pathlib import Path

import pytest

from tests.integration.real_odoo import cleanup as e2e_cleanup
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
    default_leak_probes,
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


def test_runtime_factory_uses_docker_visible_tmp_spelling() -> None:
    source = Path("/private/tmp/odcli-e2e-check")
    visible = e2e_fixtures.docker_visible_root(source)
    if sys.platform == "darwin":
        digest = sha256(str(source).encode("utf-8")).hexdigest()[:16]
        assert visible == Path(e2e_fixtures.__file__).parents[3] / ".odcli-e2e" / digest
    else:
        assert visible == Path("/private/tmp/odcli-e2e-check")


def test_pg_readiness_is_not_satisfied_by_tcp_alone() -> None:
    attempts = 0

    def runner(args: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
        nonlocal attempts
        attempts += 1
        return subprocess.CompletedProcess(args, 0 if attempts == 2 else 1, "", "")

    wait_for_compose_pg_isready(runner, "source_postgres", timeout=1.0)
    assert attempts == 2


def test_compose_down_waits_for_released_ports_without_masking_leaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(
        "tests.integration.real_odoo.cleanup.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""),
    )
    states = iter(((8069,), ()))
    monkeypatch.setattr(
        "tests.integration.real_odoo.cleanup._bound_ports", lambda _ports: next(states)
    )
    monkeypatch.setattr("tests.integration.real_odoo.cleanup.time.sleep", lambda _seconds: None)
    e2e_cleanup.compose_down(compose_file, "odcli-e2e-project", timeout=1.0, ports=(8069,))

    monkeypatch.setattr("tests.integration.real_odoo.cleanup._bound_ports", lambda _ports: (8069,))
    with pytest.raises(RuntimeError, match="left owned ports bound"):
        e2e_cleanup.compose_down(compose_file, "odcli-e2e-project", timeout=0, ports=(8069,))


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
    evidence = FailureEvidence("run123", "random-canary", tmp_path, ("real-secret",))
    evidence.add_log("odoo", "admin_passwd=real-secret\ncredentials omitted\n")
    files = evidence.write()
    text = files[0].read_text(encoding="utf-8")
    assert "real-secret" not in text
    assert "<redacted>" in text
    evidence.add_log("postgres", "random-canary")
    with pytest.raises(AssertionError, match="canary"):
        evidence.write()


def test_target_fixture_provisions_only_target_and_retains_host_http_port(
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
            f"--database={runtime.topology.target_sentinel_database}" in command
            for command in commands
        )
        assert not any("source_odoo" in command for command in commands)
        assert all(reservation.socket.fileno() == -1 for reservation in runtime.reservations[:3])
        assert runtime.reservations[3].socket.fileno() != -1
    finally:
        e2e_fixtures._finalize(runtime)
    assert cleaned == ["compose"]
    assert not runtime.root.exists()
    assert not runtime.artifact_root.exists()


def test_source_fixture_does_not_provision_target_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = e2e_fixtures._make_runtime(tmp_path, "e" * 32, scope="source")
    commands: list[tuple[str, ...]] = []

    class FakeLifecycle:
        def __init__(self, compose_file: Path, project_name: str) -> None:
            del compose_file, project_name

        def run(self, *args: str, timeout: float = 180.0) -> subprocess.CompletedProcess[str]:
            del timeout
            commands.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(e2e_fixtures, "ComposeLifecycle", FakeLifecycle)
    monkeypatch.setattr(e2e_fixtures, "wait_for_compose_pg_isready", lambda *args: None)
    monkeypatch.setattr(e2e_fixtures, "wait_for_http", lambda *args, **kwargs: None)
    monkeypatch.setattr(e2e_fixtures, "compose_down", lambda *args, **kwargs: None)
    monkeypatch.setattr(e2e_fixtures, "audit_no_leaks", lambda *args, **kwargs: None)
    try:
        e2e_fixtures._provision(runtime)
        assert any("source_odoo" in command for command in commands)
        assert any(
            f"--database={runtime.topology.source_database}" in command for command in commands
        )
        assert not any(
            "target_postgres" in command or "target_init" in command for command in commands
        )
        recorded = {item.name for item in runtime.ledger.records}
        assert runtime.topology.target_postgres_name not in recorded
    finally:
        e2e_fixtures._finalize(runtime)


def test_failed_runtime_keeps_only_sanitized_artifacts_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = e2e_fixtures._make_runtime(tmp_path, "a" * 32)
    runtime.failed = True
    monkeypatch.setenv("ODCLI_E2E_KEEP_FAILED", "1")
    evidence = FailureEvidence(runtime.run_id, "secret-canary", runtime.artifact_root)
    evidence.add_log("odoo", "admin_passwd=secret\ncredentials omitted\n")
    files = evidence.write()
    e2e_fixtures._remove_runtime_files(runtime)
    assert runtime.artifact_root.exists()
    assert all("secret-canary" not in path.read_text(encoding="utf-8") for path in files)
    monkeypatch.delenv("ODCLI_E2E_KEEP_FAILED")
    e2e_fixtures._remove_runtime_files(runtime)
    assert not runtime.artifact_root.exists()


def test_real_default_docker_probes_include_stopped_containers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("tests.integration.real_odoo.cleanup.shutil.which", lambda name: "docker")
    monkeypatch.setattr("tests.integration.real_odoo.cleanup.subprocess.run", run)
    probes = default_leak_probes("f" * 32, compose_project="odcli-e2e-project")
    assert tuple(probes["container"]("f" * 32)) == ()
    command = calls[0]
    assert command[:4] == ["docker", "container", "ls", "--all"]
    assert "{{.Names}}" in command
    assert "{{.Name}}" not in command
    assert tuple(probes["network"]("f" * 32)) == ()
    assert tuple(probes["volume"]("f" * 32)) == ()
    assert sum(command[-1] == "{{.Name}}" for command in calls) == 2


def test_default_audit_can_confirm_a_real_clean_finalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("tests.integration.real_odoo.cleanup.shutil.which", lambda name: "docker")
    monkeypatch.setattr("tests.integration.real_odoo.cleanup.subprocess.run", run)
    report = audit_no_leaks("1" * 32, compose_project="odcli-e2e-project")
    assert report.clean
    assert any(command[:4] == ["docker", "container", "ls", "--all"] for command in calls)


def test_default_database_probe_checks_both_fixture_postgres_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(args)
        container = args[2]
        stdout = "odcli_e2e_source_run123\n" if "source-pg" in container else ""
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr("tests.integration.real_odoo.cleanup.shutil.which", lambda name: "docker")
    monkeypatch.setattr("tests.integration.real_odoo.cleanup.subprocess.run", run)
    probes = default_leak_probes("run123", compose_project="odcli-e2e-project")
    assert tuple(probes["database"]("run123")) == ("odcli_e2e_source_run123",)
    assert [command[2] for command in calls] == [
        "odcli-e2e-source-pg-run123",
        "odcli-e2e-target-pg-run123",
    ]


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


def test_fixture_failure_publishes_bounded_sanitized_compose_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = e2e_fixtures._make_runtime(tmp_path, "b" * 32)
    runtime.failed = True
    monkeypatch.setenv("ODCLI_E2E_KEEP_FAILED", "1")
    commands: list[tuple[str, ...]] = []

    class FakeLifecycle:
        def __init__(self, compose_file: Path, project_name: str) -> None:
            del compose_file, project_name

        def run(self, *args: str, timeout: float = 180.0) -> subprocess.CompletedProcess[str]:
            del timeout
            commands.append(args)
            return subprocess.CompletedProcess(args, 0, "db_password=runtime-secret\nready\n", "")

    monkeypatch.setattr(e2e_fixtures, "ComposeLifecycle", FakeLifecycle)
    monkeypatch.setattr(e2e_fixtures, "compose_down", lambda *args, **kwargs: None)
    monkeypatch.setattr(e2e_fixtures, "audit_no_leaks", lambda *args, **kwargs: None)
    try:
        error = RuntimeError("injected initialization failure")
        with pytest.raises(RuntimeError, match="injected"):
            e2e_fixtures._finalize(runtime, error)
        assert commands == [
            ("logs", "--no-color", "--tail", "200", "target_postgres"),
            ("logs", "--no-color", "--tail", "200", "target_init"),
        ]
        logs = sorted(runtime.artifact_root.glob("*.log"))
        assert {path.name for path in logs} == {"odoo.log", "postgres.log"}
        assert all("runtime-secret" not in path.read_text(encoding="utf-8") for path in logs)
        assert all(path.stat().st_size <= 2 * 1024 * 1024 for path in logs)
    finally:
        monkeypatch.delenv("ODCLI_E2E_KEEP_FAILED")
        e2e_fixtures._remove_runtime_files(runtime)
