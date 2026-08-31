from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.exceptions import PostgresClusterTimeoutError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.postgres_compose import ComposeRunner
from odoo_instance_sdk.models import PostgresClusterState
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig
from odoo_instance_sdk.resources.postgres import PostgresCluster

T = TypeVar("T")


def _command(callback: Callable[[], T]) -> Command[T]:
    return Command.create(ExecutionPlan(), lambda _context: callback(), ())


class FakeComposeRunner(ComposeRunner):
    requires_docker = False

    def __init__(
        self,
        *,
        ps_rows: list[dict[str, object]] | None = None,
        health_rc: int = 0,
    ) -> None:
        self.calls: list[list[str]] = []
        self._ps_rows = ps_rows or []
        self._health_rc = health_rc

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        joined = " ".join(args)
        if " image inspect " in joined:
            return subprocess.CompletedProcess(
                args, 0, "docker.io/library/postgres@sha256:" + "a" * 64, ""
            )
        if " image pull " in joined:
            return subprocess.CompletedProcess(args, 0, "", "")
        if " ps " in joined:
            return subprocess.CompletedProcess(
                args, 0, "\n".join(json.dumps(r) for r in self._ps_rows), ""
            )
        if " exec " in joined:
            return subprocess.CompletedProcess(
                args, self._health_rc, "ok" if self._health_rc == 0 else "fail", ""
            )
        return subprocess.CompletedProcess(args, 0, "", "")


def _write_project(
    tmp_path: Path,
    *,
    mode: str = "compose",
    source_config: Path | None = None,
) -> Path:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    postgres: PostgresProjectConfig | None = None
    if mode == "compose":
        postgres = PostgresProjectConfig(mode="compose", image="pg", port=5468, user="odoo")
    cfg = ProjectConfig(
        repository_root=tmp_path,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        source_config=source_config,
        postgres=postgres,
    )
    (manifest_dir / "project.toml").write_text(cfg.to_manifest())
    return tmp_path


def _write_source_config(tmp_path: Path) -> Path:
    p = tmp_path / "odoo.conf"
    p.write_text("[options]\ndb_host = 127.0.0.1\ndb_port = 5432\n")
    return p


@pytest.fixture(autouse=True)
def _patch_cluster(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch PostgresCluster.from_project to use a fake runner by default."""

    def fake_from_project(
        project_path: str | Path,
        *,
        compose_runner: ComposeRunner | None = None,
    ) -> PostgresCluster:
        import odoo_instance_sdk.resources.postgres as pg_mod
        from odoo_instance_sdk.project import ProjectConfig as _PC

        runner = compose_runner or FakeComposeRunner()
        cfg = _PC.load(Path(project_path))
        return pg_mod.PostgresCluster._from_config(
            cfg,
            repository_root=Path(project_path).resolve(),
            compose_runner=runner,
        )

    monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(fake_from_project))


@pytest.mark.unit
def test_postgres_status_compose_json(tmp_path: Path) -> None:
    root = _write_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["--project", str(root), "postgres", "status", "--json"])
    assert result.exit_code == 0  # STOPPED → diagnostic exit 0
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert envelope["command"] == "postgres.status"
    assert envelope["data"]["mode"] == "compose"
    assert envelope["data"]["owned"] is True
    assert "password" not in result.output.lower()


@pytest.mark.unit
def test_postgres_approve_image_json_requires_exact_digest(tmp_path: Path) -> None:
    root = _write_project(tmp_path)
    runner = CliRunner()
    digest = "docker.io/library/postgres@sha256:" + "a" * 64
    result = runner.invoke(
        cli,
        ["--project", str(root), "postgres", "approve-image", "--image-digest", digest, "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["digest"] == digest


@pytest.mark.unit
def test_postgres_approve_image_human_and_missing_digest_error(tmp_path: Path) -> None:
    root = _write_project(tmp_path)
    runner = CliRunner()
    digest = "docker.io/library/postgres@sha256:" + "a" * 64
    human = runner.invoke(
        cli, ["--project", str(root), "postgres", "approve-image", "--image-digest", digest]
    )
    assert human.exit_code == 0, human.output
    assert digest in human.output
    missing = runner.invoke(cli, ["--project", str(root), "postgres", "approve-image"])
    assert missing.exit_code == 2
    assert "--image-digest" in missing.output


@pytest.mark.unit
def test_postgres_approve_image_forwards_bounded_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_project(tmp_path)
    digest = "docker.io/library/postgres@sha256:" + "a" * 64

    class ApprovalCluster:
        def __init__(self) -> None:
            self.calls: list[tuple[str, float]] = []

        def approve_image_command(
            self, image_digest: str, *, timeout: float | None = None
        ) -> Command[None]:
            def approve() -> None:
                assert timeout is not None
                self.calls.append((image_digest, timeout))

            return _command(approve)

        def to_diagnostic_dict(self) -> dict[str, object]:
            return {"image": "postgres:16"}

    cluster = ApprovalCluster()
    monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(lambda _path: cluster))
    result = CliRunner().invoke(
        cli,
        [
            "--project",
            str(root),
            "postgres",
            "approve-image",
            "--image-digest",
            digest,
            "--timeout",
            "4.5",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert cluster.calls == [(digest, 4.5)]


@pytest.mark.unit
def test_postgres_status_external_no_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _write_source_config(tmp_path)
    root = _write_project(tmp_path, mode="external", source_config=cfg)
    # External status uses probe_address only; ensure no docker call.
    from odoo_instance_sdk.internal.address import AddressState

    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.probe_address",
        lambda host, port: AddressState.OCCUPIED,
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["--project", str(root), "postgres", "status"])
    assert result.exit_code == 0
    assert "mode=external" in result.output
    assert "owned=False" in result.output


@pytest.mark.unit
def test_postgres_stop_external_fails(tmp_path: Path) -> None:
    cfg = _write_source_config(tmp_path)
    root = _write_project(tmp_path, mode="external", source_config=cfg)
    runner = CliRunner()
    result = runner.invoke(cli, ["--project", str(root), "postgres", "stop"])
    assert result.exit_code == 1
    assert "not owned" in result.output.lower() or "externally" in result.output.lower()


@pytest.mark.unit
def test_postgres_up_external_reachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _write_source_config(tmp_path)
    root = _write_project(tmp_path, mode="external", source_config=cfg)
    from odoo_instance_sdk.internal.address import AddressState

    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.probe_address",
        lambda host, port: AddressState.OCCUPIED,
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["--project", str(root), "postgres", "up"])
    assert result.exit_code == 0


@pytest.mark.unit
def test_postgres_up_external_unreachable_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _write_source_config(tmp_path)
    root = _write_project(tmp_path, mode="external", source_config=cfg)
    from odoo_instance_sdk.internal.address import AddressState

    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.probe_address",
        lambda host, port: AddressState.FREE,
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["--project", str(root), "postgres", "up"])
    assert result.exit_code == 1


@pytest.mark.unit
def test_postgres_status_resolves_project_without_arg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["postgres", "status", "--json"], catch_exceptions=False)
    assert result.exit_code == 0  # STOPPED → diagnostic exit 0
    assert json.loads(result.output)["command"] == "postgres.status"


@pytest.mark.unit
def test_postgres_up_and_stop_forward_timeouts_and_emit_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_project(tmp_path)

    class RecordingCluster:
        mode = "compose"
        owned = True
        endpoint = "127.0.0.1:5468"

        def __init__(self) -> None:
            self.ensure_timeouts: list[float] = []
            self.stop_timeouts: list[float] = []

        def ensure_running_command(self, *, timeout: float) -> Command[None]:
            def ensure() -> None:
                self.ensure_timeouts.append(timeout)

            return _command(ensure)

        def stop_command(self, *, timeout: float) -> Command[None]:
            def stop() -> None:
                self.stop_timeouts.append(timeout)

            return _command(stop)

        def status_command(self) -> Command[PostgresClusterState]:
            return _command(lambda: PostgresClusterState.HEALTHY)

        def to_diagnostic_dict(self) -> dict[str, object]:
            return {"mode": self.mode, "owned": self.owned, "endpoint": self.endpoint}

    cluster = RecordingCluster()
    monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(lambda _path: cluster))
    runner = CliRunner()
    up = runner.invoke(
        cli,
        ["--project", str(root), "postgres", "up", "--wait-timeout", "12.5", "--json"],
    )
    assert up.exit_code == 0, up.output
    assert cluster.ensure_timeouts == [12.5]
    assert json.loads(up.output)["command"] == "postgres.up"
    stop = runner.invoke(
        cli,
        ["--project", str(root), "postgres", "stop", "--timeout", "7.5", "--json"],
    )
    assert stop.exit_code == 0, stop.output
    assert cluster.stop_timeouts == [7.5]
    assert json.loads(stop.output)["command"] == "postgres.stop"


@pytest.mark.unit
def test_postgres_up_failure_uses_command_specific_json_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_project(tmp_path)

    class FailingCluster:
        def ensure_running_command(self, *, timeout: float) -> Command[None]:
            def ensure() -> None:
                raise PostgresClusterTimeoutError(timeout)

            return _command(ensure)

    monkeypatch.setattr(
        PostgresCluster, "from_project", staticmethod(lambda _path: FailingCluster())
    )
    result = CliRunner().invoke(
        cli, ["--project", str(root), "postgres", "up", "--wait-timeout", "3", "--json"]
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["command"] == "postgres.up"
    assert payload["error"]["code"] == "postgres_up_failed"
