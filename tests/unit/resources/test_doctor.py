from __future__ import annotations

import json
import shutil
import socket
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.internal.doctor import DoctorReport, run_doctor
from odoo_instance_sdk.resources.environment import (
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
)

if TYPE_CHECKING:
    from click.testing import CliRunner

    from odoo_instance_sdk import OdooClient


def _checkout_shared(
    env_client: OdooClient, project_manifest: Path, fake_python: Path, branch: str
) -> Any:
    opts = EnvironmentCheckoutOptions(
        python=str(fake_python),
        db_mode=EnvironmentDatabaseMode.SHARED,
        source_database="comerta",
    )
    return env_client.environments.checkout(project_manifest, branch, options=opts)


def _doctor(env_client: OdooClient, project_manifest: Path) -> DoctorReport:
    return run_doctor(env_client, project_manifest)


class TestDoctorMissingWorktree:
    def test_missing_worktree_warns(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        env = _checkout_shared(env_client, project_manifest, fake_python, "feat/doc-wt")
        shutil.rmtree(Path(env.worktree_path), ignore_errors=True)
        report = _doctor(env_client, project_manifest)
        assert any(c.name == "worktree" and c.status == "warn" for c in report.checks)
        assert report.ok is True


class TestDoctorMissingConfig:
    def test_missing_generated_config_warns(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        env = _checkout_shared(env_client, project_manifest, fake_python, "feat/doc-cfg")
        Path(env.generated_config_path).unlink(missing_ok=True)
        report = _doctor(env_client, project_manifest)
        assert any(c.name == "config" and c.status == "warn" for c in report.checks)


class TestDoctorMissingUv:
    def test_missing_uv_warns(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _checkout_shared(env_client, project_manifest, fake_python, "feat/doc-uv")
        _real_which = shutil.which

        def fake_which(name: str, *_args: Any, **_kw: Any) -> str | None:
            if name == "uv":
                return None
            return _real_which(name)

        monkeypatch.setattr("odoo_instance_sdk.internal.doctor.shutil.which", fake_which)
        report = _doctor(env_client, project_manifest)
        assert any(c.name == "uv" and c.status == "warn" for c in report.checks)


class TestDoctorPythonMissing:
    def test_python_missing_warns(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        env = _checkout_shared(env_client, project_manifest, fake_python, "feat/doc-py")
        Path(env.python_environment_path).unlink(missing_ok=True)
        report = _doctor(env_client, project_manifest)
        assert any(c.name == "python" and c.status == "warn" for c in report.checks)

    def test_python_ownership_mismatch_warns(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        env = _checkout_shared(env_client, project_manifest, fake_python, "feat/doc-pyown")
        catalog = env_client.get_catalog()
        catalog.update_environment(
            str(env.id),
            {"python_environment_owned": 1, "python_environment_path": "/tmp/outside/venv"},
        )
        report = _doctor(env_client, project_manifest)
        assert any(
            c.name == "python" and c.status == "warn" and "ownership" in c.detail
            for c in report.checks
        )


class TestDoctorMissingLock:
    def test_missing_dependency_lock_warns(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        env = _checkout_shared(env_client, project_manifest, fake_python, "feat/doc-lock")
        Path(env.dependency_lock_path).unlink(missing_ok=True)
        report = _doctor(env_client, project_manifest)
        assert any(c.name == "dependencies" and c.status == "warn" for c in report.checks)


class TestDoctorOrphanedArtifacts:
    def test_orphaned_dir_warns(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        _checkout_shared(env_client, project_manifest, fake_python, "feat/doc-orphan")
        from odoo_instance_sdk.internal.paths import get_environments_root

        envs_root = get_environments_root()
        repo_key_dir = next((p for p in envs_root.iterdir() if p.is_dir()), None)
        assert repo_key_dir is not None
        fake_id = str(uuid.uuid4())
        (repo_key_dir / fake_id).mkdir(parents=True)
        (repo_key_dir / fake_id / "junk").write_text("x")
        report = _doctor(env_client, project_manifest)
        assert any(
            c.name == "orphaned" and c.status == "warn" and fake_id in c.detail
            for c in report.checks
        )


class TestDoctorOccupiedPort:
    @pytest.mark.serial
    def test_occupied_port_is_info_not_error(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        env = _checkout_shared(env_client, project_manifest, fake_python, "feat/doc-port")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", env.http_port))
            s.listen(1)
            report = _doctor(env_client, project_manifest)
        finally:
            s.close()
        port_checks = [c for c in report.checks if c.name == "port"]
        assert any(c.status == "info" and "occupied" in c.detail for c in port_checks)
        assert report.ok is True


class TestDoctorMissingOwnedBackup:
    def test_missing_owned_backup_file_warns(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        env = _checkout_shared(env_client, project_manifest, fake_python, "feat/doc-bak")
        backup_id = str(uuid.uuid4())
        catalog = env_client.get_catalog()
        catalog._conn.execute(
            "INSERT INTO backups (id, source_base_url, database_name, format, "
            "filestore_requested, path, state, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                backup_id,
                "http://127.0.0.1:8069",
                "comerta",
                "zip",
                1,
                "/nonexistent/backup.zip",
                "available",
                "2026-01-01T00:00:00",
            ),
        )
        catalog._conn.commit()
        catalog.update_environment(
            str(env.id),
            {"backup_id": backup_id, "db_mode": "copy"},
        )
        report = _doctor(env_client, project_manifest)
        backup_checks = [c for c in report.checks if c.name == "backup"]
        assert any(c.status == "warn" and "missing" in c.detail for c in backup_checks)


class TestDoctorJsonEnvelope:
    def test_json_envelope_stable(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        _checkout_shared(env_client, project_manifest, fake_python, "feat/doc-json")
        runner = _runner()
        result = runner.invoke(
            cli,
            ["--project", str(project_manifest), "doctor", "--json"],
        )
        assert result.exit_code == 0
        envelope = json.loads(result.output)
        assert envelope["schema_version"] == 1
        assert envelope["command"] == "doctor"
        assert "checks" in envelope["data"]
        assert "warnings" in envelope
        assert "context" in envelope


class TestDoctorExitCodes:
    def test_no_fix_flag_advertised(self) -> None:
        runner = _runner()
        result = runner.invoke(cli, ["doctor", "--help"])
        assert "--fix" not in result.output
        assert result.exit_code == 0

    def test_warnings_exit_zero(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        env = _checkout_shared(env_client, project_manifest, fake_python, "feat/doc-exit0")
        Path(env.generated_config_path).unlink(missing_ok=True)
        runner = _runner()
        result = runner.invoke(cli, ["--project", str(project_manifest), "doctor"])
        assert result.exit_code == 0
        assert "WARN" in result.output

    def test_errors_exit_nonzero(self, tmp_path: Path) -> None:
        runner = _runner()
        result = runner.invoke(cli, ["--project", str(tmp_path), "doctor"])
        assert result.exit_code == 1


def _runner() -> CliRunner:
    from click.testing import CliRunner

    return CliRunner()
