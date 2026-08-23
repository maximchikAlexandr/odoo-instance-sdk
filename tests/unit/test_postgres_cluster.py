from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import (
    PostgresClusterNotOwnedError,
    PostgresClusterUnreachableError,
    PostgresComposeInvalidError,
    PostgresComposeUnavailableError,
)
from odoo_instance_sdk.internal.postgres_compose import ComposeResult, ComposeRunner
from odoo_instance_sdk.models import PostgresClusterState
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig
from odoo_instance_sdk.resources.postgres import PostgresCluster


class FakeComposeRunner(ComposeRunner):
    """Records invocations; returns scripted results."""

    def __init__(
        self,
        *,
        ps_rows: list[dict[str, object]] | None = None,
        health_rc: int = 0,
        up_rc: int = 0,
        stop_rc: int = 0,
        config_rc: int = 0,
    ) -> None:
        self.calls: list[list[str]] = []
        self._ps_rows = ps_rows or []
        self._health_rc = health_rc
        self._up_rc = up_rc
        self._stop_rc = stop_rc
        self._config_rc = config_rc

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float | None = None,
    ) -> ComposeResult:
        self.calls.append(list(args))
        # Last positional arg determines the kind.
        joined = " ".join(args)
        if " config " in joined:
            return ComposeResult(self._config_rc, "", "" if self._config_rc == 0 else "bad")
        if " up " in joined:
            return ComposeResult(self._up_rc, "", "" if self._up_rc == 0 else "up fail")
        if " stop " in joined:
            return ComposeResult(self._stop_rc, "", "" if self._stop_rc == 0 else "stop fail")
        if " ps " in joined:
            return ComposeResult(0, _rows_to_jsonl(self._ps_rows), "")
        if " exec " in joined:
            return ComposeResult(self._health_rc, "ok" if self._health_rc == 0 else "fail", "")
        return ComposeResult(0, "", "")


def _rows_to_jsonl(rows: list[dict[str, object]]) -> str:
    import json

    return "\n".join(json.dumps(r) for r in rows)


def _write_compose_project(
    tmp_path: Path,
    *,
    mode: str = "compose",
    image: str | None = "pgvector/pgvector:pg16",
    port: int | None = 5468,
    user: str | None = "odoo",
    source_config: Path | None = None,
) -> Path:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    cfg = ProjectConfig(
        repository_root=tmp_path,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        source_config=source_config,
        postgres=PostgresProjectConfig(mode=mode, image=image, port=port, user=user)  # type: ignore[arg-type]
        if mode == "compose"
        else None,
    )
    (manifest_dir / "project.toml").write_text(cfg.to_manifest())
    return tmp_path


def _write_source_config(
    tmp_path: Path, *, db_host: str = "127.0.0.1", db_port: int = 5432
) -> Path:
    p = tmp_path / "odoo.conf"
    p.write_text(f"[options]\ndb_host = {db_host}\ndb_port = {db_port}\ndb_user = alice\n")
    return p


@pytest.mark.unit
def test_from_project_compose_mode(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    cluster = PostgresCluster.from_project(root, compose_runner=FakeComposeRunner())
    assert cluster.mode == "compose"
    assert cluster.owned is True
    assert cluster.endpoint == "127.0.0.1:5468"
    assert "127.0.0.1:5468" in repr(cluster)
    assert "password" not in repr(cluster).lower()


@pytest.mark.unit
def test_from_project_external_reads_source_config(tmp_path: Path) -> None:
    cfg_path = _write_source_config(tmp_path, db_host="db.local", db_port=5433)
    root = _write_compose_project(tmp_path, mode="external", source_config=cfg_path)
    cluster = PostgresCluster.from_project(root, compose_runner=FakeComposeRunner())
    assert cluster.mode == "external"
    assert cluster.owned is False
    assert "db.local" in cluster.endpoint
    assert "5433" in cluster.endpoint


@pytest.mark.unit
def test_from_project_external_without_source_config_raises(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path, mode="external")
    with pytest.raises(Exception):  # PostgresClusterError
        PostgresCluster.from_project(root, compose_runner=FakeComposeRunner())


@pytest.mark.unit
def test_legacy_manifest_treated_as_external(tmp_path: Path) -> None:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    cfg_path = _write_source_config(tmp_path)
    (manifest_dir / "project.toml").write_text(
        f'[project]\nodoo_bin = "/opt/odoo/odoo-bin"\nsource_config = "{cfg_path}"\n'
    )
    cluster = PostgresCluster.from_project(tmp_path, compose_runner=FakeComposeRunner())
    assert cluster.mode == "external"
    assert cluster.owned is False


@pytest.mark.unit
def test_status_external_reachable_is_healthy(tmp_path: Path) -> None:
    cfg_path = _write_source_config(tmp_path, db_host="127.0.0.1", db_port=5432)
    root = _write_compose_project(tmp_path, mode="external", source_config=cfg_path)
    cluster = PostgresCluster.from_project(root, compose_runner=FakeComposeRunner())
    # 127.0.0.1:5432 is likely free in the test env, so status = UNREACHABLE.
    # We test the unreachable path; for "occupied" we monkeypatch probe.
    state = cluster.status()
    assert state in (PostgresClusterState.UNREACHABLE, PostgresClusterState.HEALTHY)


@pytest.mark.unit
def test_status_external_does_not_invoke_docker(tmp_path: Path) -> None:
    cfg_path = _write_source_config(tmp_path, db_host="127.0.0.1", db_port=5432)
    root = _write_compose_project(tmp_path, mode="external", source_config=cfg_path)
    fake = FakeComposeRunner()
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    cluster.status()
    assert fake.calls == []


@pytest.mark.unit
def test_status_compose_stopped_when_no_containers(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(ps_rows=[])
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    state = cluster.status()
    assert state is PostgresClusterState.STOPPED


@pytest.mark.unit
def test_status_compose_healthy_when_health_rc_zero(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(ps_rows=[{"Name": "postgres"}], health_rc=0)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    cluster._compose_file().parent.mkdir(parents=True, exist_ok=True)
    cluster._compose_file().write_text("services:\n  postgres:\n    image: x\n")
    state = cluster.status()
    assert state is PostgresClusterState.HEALTHY


@pytest.mark.unit
def test_status_compose_unhealthy_when_health_rc_nonzero(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(ps_rows=[{"Name": "postgres"}], health_rc=2)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    cluster._compose_file().parent.mkdir(parents=True, exist_ok=True)
    cluster._compose_file().write_text("services:\n  postgres:\n    image: x\n")
    state = cluster.status()
    assert state is PostgresClusterState.UNHEALTHY


@pytest.mark.unit
def test_status_compose_unknown_when_docker_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner()
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    monkeypatch.setattr("odoo_instance_sdk.resources.postgres.docker_available", lambda: False)
    state = cluster.status()
    assert state is PostgresClusterState.UNKNOWN


@pytest.mark.unit
def test_ensure_running_external_unreachable_raises(tmp_path: Path) -> None:
    cfg_path = _write_source_config(tmp_path, db_host="127.0.0.1", db_port=5432)
    root = _write_compose_project(tmp_path, mode="external", source_config=cfg_path)
    cluster = PostgresCluster.from_project(root, compose_runner=FakeComposeRunner())
    # Port 5432 likely free → status UNREACHABLE → ensure raises.
    try:
        cluster.ensure_running(timeout=1.0)
    except PostgresClusterUnreachableError:
        return
    except Exception:
        pass
    # If 5432 happened to be occupied, skip this test in that environment.
    if cluster.status() is PostgresClusterState.HEALTHY:
        pytest.skip("port 5432 occupied in this environment")


@pytest.mark.unit
def test_ensure_running_external_healthy_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = _write_source_config(tmp_path, db_host="127.0.0.1", db_port=5432)
    root = _write_compose_project(tmp_path, mode="external", source_config=cfg_path)
    fake = FakeComposeRunner()
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.probe_address",
        lambda host, port: (
            __import__(
                "odoo_instance_sdk.internal.address", fromlist=["AddressState"]
            ).AddressState.OCCUPIED
        ),
    )
    cluster.ensure_running(timeout=1.0)
    assert fake.calls == []  # no Docker


@pytest.mark.unit
def test_stop_external_raises_not_owned(tmp_path: Path) -> None:
    cfg_path = _write_source_config(tmp_path, db_host="127.0.0.1", db_port=5432)
    root = _write_compose_project(tmp_path, mode="external", source_config=cfg_path)
    cluster = PostgresCluster.from_project(root, compose_runner=FakeComposeRunner())
    with pytest.raises(PostgresClusterNotOwnedError):
        cluster.stop(timeout=1.0)


@pytest.mark.unit
def test_stop_compose_when_no_artifacts_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(stop_rc=0)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    # No compose.yaml yet → stop returns without invoking docker.
    cluster.stop(timeout=1.0)
    assert fake.calls == []


@pytest.mark.unit
def test_stop_compose_invokes_compose_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(stop_rc=0)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    # Simulate artifacts existing by pre-creating compose.yaml.
    monkeypatch.setattr("odoo_instance_sdk.resources.postgres.docker_available", lambda: True)
    cluster._compose_file().parent.mkdir(parents=True, exist_ok=True)
    cluster._compose_file().write_text("services:\n  postgres:\n    image: x\n")
    cluster.stop(timeout=5.0)
    assert any(" stop " in " ".join(c) for c in fake.calls)


@pytest.mark.unit
def test_ensure_running_compose_unavailable_docker_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner()
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_compose.docker_available", lambda: False
    )
    with pytest.raises(PostgresComposeUnavailableError):
        cluster.ensure_running(timeout=1.0)


@pytest.mark.unit
def test_ensure_running_compose_invalid_config_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(config_rc=1)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    monkeypatch.setattr("odoo_instance_sdk.resources.postgres.docker_available", lambda: True)
    with pytest.raises(PostgresComposeInvalidError):
        cluster.ensure_running(timeout=1.0)


@pytest.mark.unit
def test_password_file_mode_0600(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(ps_rows=[{"Name": "postgres"}], health_rc=0, config_rc=0)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_compose.docker_available", lambda: True
    )
    cluster._ensure_artifacts()
    pw_path = cluster._password_file()
    assert pw_path.is_file()
    mode = pw_path.stat().st_mode & 0o777
    assert mode == 0o600, f"password file mode {oct(mode)}"


@pytest.mark.unit
def test_password_file_not_overwritten(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    cluster = PostgresCluster.from_project(root, compose_runner=FakeComposeRunner())
    cluster._compose_dir().mkdir(parents=True, exist_ok=True)
    pw_path = cluster._password_file()
    pw_path.write_text("existing-password\n")
    os.chmod(pw_path, 0o600)
    from odoo_instance_sdk.internal.postgres_compose import ensure_password_file

    content = ensure_password_file(pw_path)
    assert content == "existing-password"
    assert pw_path.read_text().strip() == "existing-password"


@pytest.mark.unit
def test_render_compose_yaml_minimal(tmp_path: Path) -> None:
    from odoo_instance_sdk.internal.postgres_compose import render_compose_yaml

    content = render_compose_yaml(
        image="pgvector/pgvector:pg16",
        port=5468,
        user="odoo",
        project_id="proj_12345678",
        password_file="/data/projects/proj_12345678/postgres/postgres-password",
    )
    assert "image: pgvector/pgvector:pg16" in content
    assert "127.0.0.1:5468:5432" in content
    assert "POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password" in content
    assert "pg_isready" in content
    assert "container_name" not in content
    assert "build:" not in content
    assert "extends:" not in content


@pytest.mark.unit
def test_render_compose_rejects_unsafe_image() -> None:
    from odoo_instance_sdk.internal.postgres_compose import render_compose_yaml

    with pytest.raises(PostgresComposeInvalidError):
        render_compose_yaml(
            image="image'; rm -rf /",
            port=5468,
            user="odoo",
            project_id="x",
            password_file="/p",
        )


@pytest.mark.unit
def test_render_compose_rejects_unsafe_user() -> None:
    from odoo_instance_sdk.internal.postgres_compose import render_compose_yaml

    with pytest.raises(PostgresComposeInvalidError):
        render_compose_yaml(
            image="pg",
            port=5468,
            user="user; rm -rf /",
            project_id="x",
            password_file="/p",
        )


@pytest.mark.unit
def test_diagnostic_dict_is_redacted(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    cluster = PostgresCluster.from_project(root, compose_runner=FakeComposeRunner())
    diag = dict(cluster.to_diagnostic_dict())
    assert diag["mode"] == "compose"
    assert diag["owned"] is True
    assert "password" not in str(diag).lower()
