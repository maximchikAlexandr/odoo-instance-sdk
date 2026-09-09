from __future__ import annotations

import json
import shutil
import socket
import subprocess
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.internal.applied_settings import LEGACY_UNKNOWN_APPLIED_SETTINGS_JSON
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
    def test_drift_projection_is_in_sync_after_checkout(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        env = _checkout_shared(env_client, project_manifest, fake_python, "feat/doc-drift-sync")

        report = _doctor(env_client, project_manifest)
        drift = next(item for item in report.drift if item.environment_id == str(env.id))

        assert {item.component: item.status for item in drift.components} == {
            "python": "in_sync",
            "dependencies": "in_sync",
            "odoo_config": "in_sync",
            "addons": "unknown",
            "git_provenance": "in_sync",
        }
        assert drift.git_context["dirty"] is False

    def test_legacy_applied_evidence_is_unknown_without_mutation(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        env = _checkout_shared(env_client, project_manifest, fake_python, "feat/doc-drift-legacy")
        catalog = env_client.get_catalog()
        before = catalog.get_environment(str(env.id))["applied_settings_json"]
        catalog.update_environment(
            str(env.id), {"applied_settings_json": LEGACY_UNKNOWN_APPLIED_SETTINGS_JSON}
        )

        for raw in (LEGACY_UNKNOWN_APPLIED_SETTINGS_JSON, "{malformed"):
            catalog.update_environment(str(env.id), {"applied_settings_json": raw})
            report = _doctor(env_client, project_manifest)
            drift = next(item for item in report.drift if item.environment_id == str(env.id))

            assert all(item.status == "unknown" for item in drift.components)
            assert catalog.get_environment(str(env.id))["applied_settings_json"] == raw
        assert before != LEGACY_UNKNOWN_APPLIED_SETTINGS_JSON

    def test_config_drift_is_field_isolated(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        source_config: Path,
    ) -> None:
        source_config.write_text(
            source_config.read_text(encoding="utf-8") + "limit_memory_soft = 1\n",
            encoding="utf-8",
        )
        env = _checkout_shared(env_client, project_manifest, fake_python, "feat/doc-drift-config")
        generated = Path(env.generated_config_path)
        generated.write_text(
            generated.read_text(encoding="utf-8").replace(
                "limit_memory_soft = 1", "limit_memory_soft = 2"
            ),
            encoding="utf-8",
        )

        report = _doctor(env_client, project_manifest)
        drift = next(item for item in report.drift if item.environment_id == str(env.id))

        statuses = {item.component: item.status for item in drift.components}
        assert statuses == {
            "python": "in_sync",
            "dependencies": "in_sync",
            "odoo_config": "drifted",
            "addons": "unknown",
            "git_provenance": "in_sync",
        }

    def test_doctor_drift_is_format_parity_and_write_free(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        env = _checkout_shared(env_client, project_manifest, fake_python, "feat/doc-drift-output")
        catalog = env_client.get_catalog()
        before = catalog.get_environment(str(env.id))["applied_settings_json"]
        runner = _runner()

        json_result = runner.invoke(
            cli, ["--project", str(project_manifest), "doctor", "--format", "json"]
        )
        toon_result = runner.invoke(
            cli, ["--project", str(project_manifest), "doctor", "--format", "toon"]
        )
        rich_result = runner.invoke(cli, ["--project", str(project_manifest), "doctor"])

        assert json_result.exit_code == toon_result.exit_code == rich_result.exit_code == 0
        json_document = json.loads(json_result.output)
        from toon import DecodeOptions, decode

        toon_document = decode(toon_result.output, DecodeOptions(indent=2, strict=True))
        assert json_document["data"] == toon_document["data"]
        for item in json_document["data"]["drift"]:
            for component in item["components"]:
                assert component["reason"] in rich_result.output
                assert component["remediation"] in rich_result.output
        assert catalog.get_environment(str(env.id))["applied_settings_json"] == before

    def test_python_selector_and_artifact_fail_closed(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        env = _checkout_shared(env_client, project_manifest, fake_python, "feat/doc-drift-python")
        manifest = project_manifest / ".odcli" / "project.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                f'python = "{fake_python}"', 'python = "/missing/python"'
            ),
            encoding="utf-8",
        )

        selector_report = _doctor(env_client, project_manifest)
        selector_drift = next(
            item for item in selector_report.drift if item.environment_id == str(env.id)
        )
        selector = next(item for item in selector_drift.components if item.component == "python")
        assert selector.status == "drifted"
        assert "selector" in selector.reason

        fake_python.unlink()
        artifact_report = _doctor(env_client, project_manifest)
        artifact_drift = next(
            item for item in artifact_report.drift if item.environment_id == str(env.id)
        )
        python = next(item for item in artifact_drift.components if item.component == "python")
        assert python.status == "unknown"
        assert "artifact" in python.reason

    def test_source_and_generated_config_addon_drift_are_isolated(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        source_config: Path,
    ) -> None:
        source_config.write_text(
            source_config.read_text(encoding="utf-8")
            + "limit_memory_soft = 1\naddons_path = addons-a\n",
            encoding="utf-8",
        )
        env = _checkout_shared(
            env_client, project_manifest, fake_python, "feat/doc-drift-config-input"
        )
        source_config.write_text(
            "# equivalent formatting\n"
            + source_config.read_text(encoding="utf-8").replace(
                "limit_memory_soft = 1", "limit_memory_soft    =    1"
            ),
            encoding="utf-8",
        )
        equal_report = _doctor(env_client, project_manifest)
        equal_drift = next(
            item for item in equal_report.drift if item.environment_id == str(env.id)
        )
        equal_status = {item.component: item.status for item in equal_drift.components}
        assert equal_status["odoo_config"] == equal_status["addons"] == "in_sync"
        source_config.write_text(
            source_config.read_text(encoding="utf-8")
            .replace("limit_memory_soft    =    1", "limit_memory_soft = 2")
            .replace("addons_path = addons-a", "addons_path = addons-b"),
            encoding="utf-8",
        )
        source_report = _doctor(env_client, project_manifest)
        source_drift = next(
            item for item in source_report.drift if item.environment_id == str(env.id)
        )
        source_status = {item.component: item.status for item in source_drift.components}
        assert source_status["odoo_config"] == source_status["addons"] == "drifted"

        source_config.write_text(
            source_config.read_text(encoding="utf-8")
            .replace("limit_memory_soft = 2", "limit_memory_soft = 1")
            .replace("addons_path = addons-b", "addons_path = addons-a"),
            encoding="utf-8",
        )
        generated = Path(env.generated_config_path)
        generated_text = generated.read_text(encoding="utf-8")
        generated_text = "\n".join(
            (
                f"addons_path = {Path(env.worktree_path) / 'addons-c'}"
                if line.startswith("addons_path = ")
                else line.replace("limit_memory_soft = 1", "limit_memory_soft = 3")
            )
            for line in generated_text.splitlines()
        )
        generated.write_text(
            generated_text + "\n",
            encoding="utf-8",
        )
        artifact_report = _doctor(env_client, project_manifest)
        artifact_drift = next(
            item for item in artifact_report.drift if item.environment_id == str(env.id)
        )
        artifact_status = {item.component: item.status for item in artifact_drift.components}
        assert artifact_status["odoo_config"] == artifact_status["addons"] == "drifted"

    def test_live_git_identity_and_context_are_separate(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        env = _checkout_shared(env_client, project_manifest, fake_python, "feat/doc-drift-git")
        subprocess.run(
            ["git", "switch", "-c", "doctor-live-branch"],
            cwd=env.worktree_path,
            check=True,
            capture_output=True,
            text=True,
        )
        report = _doctor(env_client, project_manifest)
        drift = next(item for item in report.drift if item.environment_id == str(env.id))
        git = next(item for item in drift.components if item.component == "git_provenance")
        assert git.status == "drifted"
        assert "branch" in git.reason

        clean_env = _checkout_shared(
            env_client, project_manifest, fake_python, "feat/doc-drift-git-context"
        )
        (Path(clean_env.worktree_path) / "untracked.txt").write_text("context\n", encoding="utf-8")
        context_report = _doctor(env_client, project_manifest)
        context_drift = next(
            item for item in context_report.drift if item.environment_id == str(clean_env.id)
        )
        context_git = next(
            item for item in context_drift.components if item.component == "git_provenance"
        )
        assert context_git.status == "in_sync"
        assert context_drift.git_context["dirty"] is True

    def test_dependency_drift_uses_semantic_entries_and_unknown_on_unreadable_input(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
    ) -> None:
        requirements = project_manifest / "requirements.txt"
        requirements.write_text("requests>=1\n", encoding="utf-8")
        subprocess.run(["git", "add", "requirements.txt"], cwd=project_manifest, check=True)
        subprocess.run(
            ["git", "commit", "-m", "doctor requirements"], cwd=project_manifest, check=True
        )
        manifest = project_manifest / ".odcli" / "project.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + 'requirements = ["requirements.txt"]\n',
            encoding="utf-8",
        )
        env = _checkout_shared(env_client, project_manifest, fake_python, "feat/doc-drift-deps")
        worktree_requirements = Path(env.worktree_path) / "requirements.txt"

        worktree_requirements.write_text(
            "# comment\nrequests >= 1  # equivalent\n", encoding="utf-8"
        )
        equal_report = _doctor(env_client, project_manifest)
        equal_drift = next(
            item for item in equal_report.drift if item.environment_id == str(env.id)
        )
        assert (
            next(item for item in equal_drift.components if item.component == "dependencies").status
            == "in_sync"
        )

        worktree_requirements.unlink()
        unknown_report = _doctor(env_client, project_manifest)
        unknown_drift = next(
            item for item in unknown_report.drift if item.environment_id == str(env.id)
        )
        assert (
            next(
                item for item in unknown_drift.components if item.component == "dependencies"
            ).status
            == "unknown"
        )

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
        drift = next(item for item in report.drift if item.environment_id == str(env.id))
        statuses = {item.component: item.status for item in drift.components}
        assert statuses["odoo_config"] == statuses["addons"] == "unknown"


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
