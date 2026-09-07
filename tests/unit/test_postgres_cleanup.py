from __future__ import annotations

import subprocess
from pathlib import Path
from typing import cast

import pytest

from tests import conftest as test_conftest
from tests.integration import postgres_cleanup, test_postgres_drop


class _FakeWaiter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        self.calls.append("terminate")

    def kill(self) -> None:
        self.calls.append("kill")

    def wait(self, timeout: float | None = None) -> None:
        self.calls.append(f"wait:{timeout}")
        if self.calls.count("wait:10") == 1:
            raise subprocess.TimeoutExpired("psql", timeout or 0.0)


def test_drop_waiter_cleanup_escalates_after_timeout() -> None:
    waiter = _FakeWaiter()

    test_postgres_drop._terminate_waiter(cast("subprocess.Popen[str]", waiter))

    assert waiter.calls == ["terminate", "wait:10", "kill", "wait:10"]


def _docker_result(
    *, returncode: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["docker"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_volume_absence_accepts_only_explicit_docker_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        postgres_cleanup,
        "_run_volume_inspect",
        lambda _name: _docker_result(
            returncode=1,
            stdout="[]\n",
            stderr="Error response from daemon: get pgdata_absent: no such volume\n",
        ),
    )

    postgres_cleanup._assert_volume_absent("pgdata_absent")


@pytest.mark.parametrize(
    ("result", "match"),
    [
        (
            _docker_result(
                returncode=0,
                stdout='[{"Name":"pgdata_present","Labels":{}}]',
            ),
            "disposable volume remains",
        ),
        (
            _docker_result(returncode=125, stderr="Cannot connect to the Docker daemon"),
            "probe failed",
        ),
        (
            _docker_result(returncode=0, stdout="not-json"),
            "malformed output",
        ),
    ],
)
def test_volume_absence_rejects_present_probe_errors_and_malformed_output(
    result: subprocess.CompletedProcess[str],
    match: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(postgres_cleanup, "_run_volume_inspect", lambda _name: result)

    with pytest.raises(AssertionError, match=match):
        postgres_cleanup._assert_volume_absent("pgdata_present")


def test_volume_absence_surfaces_probe_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(_name: str) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("docker volume inspect", 60.0)

    monkeypatch.setattr(postgres_cleanup, "_run_volume_inspect", timeout)

    with pytest.raises(subprocess.TimeoutExpired):
        postgres_cleanup._assert_volume_absent("pgdata_timeout")


def test_resource_delta_accepts_present_volume_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        test_conftest,
        "_run_docker_volume_ls",
        lambda: _docker_result(returncode=0, stdout="pgdata_keep\nother_volume\n"),
    )
    monkeypatch.setattr(
        test_conftest,
        "_run_docker_volume_inspect",
        lambda _name: _docker_result(
            returncode=0,
            stdout='[{"Name":"pgdata_keep","Labels":{"owner":"test"}}]',
        ),
    )

    assert test_conftest._docker_project_volumes() == {
        "pgdata_keep": (("owner", "test"),),
    }


def test_resource_delta_accepts_absent_generated_volume_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        test_conftest,
        "_run_docker_volume_ls",
        lambda: _docker_result(returncode=0, stdout="other_volume\n"),
    )

    assert test_conftest._docker_project_volumes() == {}


@pytest.mark.parametrize(
    "probe",
    [
        lambda: _docker_result(returncode=125, stderr="Cannot connect to the Docker daemon"),
        lambda: _docker_result(returncode=0, stdout="not a valid volume name\n"),
    ],
)
def test_resource_delta_rejects_listing_probe_errors(
    probe: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(test_conftest, "_run_docker_volume_ls", probe)

    with pytest.raises(AssertionError):
        test_conftest._docker_project_volumes()


def test_resource_delta_rejects_inspection_probe_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        test_conftest,
        "_run_docker_volume_ls",
        lambda: _docker_result(returncode=0, stdout="pgdata_probe\n"),
    )
    monkeypatch.setattr(
        test_conftest,
        "_run_docker_volume_inspect",
        lambda _name: _docker_result(returncode=1, stderr="permission denied"),
    )

    with pytest.raises(AssertionError, match="cannot inspect"):
        test_conftest._docker_project_volumes()


def test_resource_delta_surfaces_probe_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout() -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("docker volume ls", 60.0)

    monkeypatch.setattr(test_conftest, "_run_docker_volume_ls", timeout)

    with pytest.raises(subprocess.TimeoutExpired):
        test_conftest._docker_project_volumes()


def test_cleanup_before_compose_artifact_preserves_primary_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def inspect(volume_name: str) -> None:
        calls.append(volume_name)

    monkeypatch.setattr(postgres_cleanup, "_assert_volume_absent", inspect)
    primary = RuntimeError("forced pre-artifact failure")

    with pytest.raises(RuntimeError, match="forced pre-artifact failure"):
        postgres_cleanup.cleanup_postgres_project(
            compose_file=tmp_path / "missing-compose.yaml",
            compose_project_name="odcli_pg_pre",
            volume_name="pgdata_pre",
            primary_failure=primary,
        )

    assert calls == ["pgdata_pre"]


def test_cleanup_after_compose_artifact_reports_primary_and_cleanup_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n")
    calls: list[str] = []

    def compose_cleanup(_compose_file: Path, _compose_project_name: str) -> None:
        calls.append("compose")
        raise AssertionError("compose cleanup failed: forced down failure")

    def inspect(_volume_name: str) -> None:
        calls.append("volume")
        raise AssertionError("disposable volume remains after cleanup")

    monkeypatch.setattr(postgres_cleanup, "_run_compose_cleanup", compose_cleanup)
    monkeypatch.setattr(postgres_cleanup, "_assert_volume_absent", inspect)
    primary = RuntimeError("forced post-artifact failure")

    with pytest.raises(BaseExceptionGroup) as raised:
        postgres_cleanup.cleanup_postgres_project(
            compose_file=compose_file,
            compose_project_name="odcli_pg_post",
            volume_name="pgdata_post",
            primary_failure=primary,
        )

    messages = [str(error) for error in raised.value.exceptions]
    assert any("forced post-artifact failure" in message for message in messages)
    assert any("compose cleanup failed" in message for message in messages)
    assert any("disposable volume remains" in message for message in messages)
    assert calls == ["compose", "volume"]
