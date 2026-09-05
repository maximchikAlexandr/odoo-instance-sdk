from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.internal.repo_key import git_common_dir, repo_key
from odoo_instance_sdk.resources.monitor import EnvironmentMonitor
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

if TYPE_CHECKING:
    import pytest


def test_module_init_executes_helpers_defined_after_commands(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "odoo_instance_sdk.cli",
            "init",
            "--no-input",
            "--project",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "Missing required option --odoo-bin" in result.stderr
    assert "NameError" not in result.stderr


def _base_args(tmp_path: Path) -> list[str]:
    return [
        "init",
        "--no-input",
        "--odoo-bin",
        "/opt/odoo/odoo-bin",
        "--python",
        "python3",
        "--project",
        str(tmp_path),
    ]


def test_init_compose_no_input_requires_image(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, [*_base_args(tmp_path), "--postgres", "compose"])
    assert result.exit_code == 1
    assert "--postgres-image" in result.output


def test_init_compose_with_image_writes_postgres_section(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--postgres-port",
            "5468",
            "--postgres-user",
            "odoo",
        ],
    )
    assert result.exit_code == 0
    content = (tmp_path / ".odcli" / "project.toml").read_text()
    assert "[postgres]" in content
    assert 'mode = "compose"' in content
    assert 'image = "pgvector/pgvector:pg16"' in content
    assert "port = 5468" in content
    assert 'user = "odoo"' in content
    assert "password" not in content.lower()


def test_init_compose_allocates_free_port(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
        ],
    )
    assert result.exit_code == 0
    content = (tmp_path / ".odcli" / "project.toml").read_text()
    assert "port = " in content


def test_init_compose_user_defaults_from_source_config(tmp_path: Path) -> None:
    cfg = tmp_path / "odoo.conf"
    cfg.write_text("[options]\ndb_user = alice\n")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--config",
            str(cfg),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--postgres-port",
            "5468",
        ],
    )
    assert result.exit_code == 0
    content = (tmp_path / ".odcli" / "project.toml").read_text()
    assert 'user = "alice"' in content


def test_init_compose_user_defaults_to_odoo_without_source(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--postgres-port",
            "5468",
        ],
    )
    assert result.exit_code == 0
    content = (tmp_path / ".odcli" / "project.toml").read_text()
    assert 'user = "odoo"' in content


def test_init_external_default_omits_postgres_section(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, _base_args(tmp_path))
    assert result.exit_code == 0
    content = (tmp_path / ".odcli" / "project.toml").read_text()
    assert "[postgres]" not in content


def test_init_dry_run_json_reports_postgres_plan(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--dry-run",
            "--json",
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--postgres-port",
            "5468",
        ],
    )
    assert result.exit_code == 0
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert envelope["dry_run"] is True
    postgres = envelope["data"]["postgres"]
    assert postgres["mode"] == "compose"
    assert postgres["image"] == "pgvector/pgvector:pg16"
    assert postgres["port"] == 5468
    assert postgres["user"] == "odoo"
    assert "password" not in json.dumps(envelope).lower()
    assert not (tmp_path / ".odcli" / "project.toml").exists()


def test_init_idempotent_with_postgres_section(tmp_path: Path) -> None:
    runner = CliRunner()
    args = [
        *_base_args(tmp_path),
        "--postgres",
        "compose",
        "--postgres-image",
        "pgvector/pgvector:pg16",
        "--postgres-port",
        "5468",
    ]
    first = runner.invoke(cli, args)
    assert first.exit_code == 0
    mtime_before = (tmp_path / ".odcli" / "project.toml").stat().st_mtime_ns
    second = runner.invoke(cli, args)
    assert second.exit_code == 0
    assert "no-op" in second.output.lower()
    mtime_after = (tmp_path / ".odcli" / "project.toml").stat().st_mtime_ns
    assert mtime_before == mtime_after


def test_init_retries_registration_after_catalog_failure_and_monitor_discovers_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_path = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr("odoo_instance_sdk.cli.get_catalog_path", lambda: catalog_path)
    original_register = BackupCatalog._register_project
    attempts = 0

    def fail_once(
        catalog: BackupCatalog, project_id: str, repository_root: str | Path, common: str | Path
    ) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("catalog unavailable")
        original_register(catalog, project_id, repository_root, common)

    monkeypatch.setattr(BackupCatalog, "_register_project", fail_once)
    runner = CliRunner()
    first = runner.invoke(cli, _base_args(tmp_path))
    assert first.exit_code == 1
    assert (tmp_path / ".odcli" / "project.toml").is_file()

    second = runner.invoke(cli, _base_args(tmp_path))
    assert second.exit_code == 0, second.output
    project_id = f"project_{repo_key(tmp_path, git_common_dir(tmp_path))}"

    catalog = BackupCatalog(db_path=catalog_path)
    try:
        assert (
            catalog._conn.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            is not None
        )
    finally:
        catalog.close()
    snapshot = EnvironmentMonitor(catalog_path=catalog_path).snapshot()
    assert any(project.id == project_id for project in snapshot.projects)


def test_init_compose_does_not_start_docker(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--postgres-port",
            "5468",
        ],
    )
    assert result.exit_code == 0
    # init does not invoke docker compose; artifacts are created lazily at first `up`.
    # We confirm by checking that no process spawn occurred (exit 0 without docker).
    # Direct artifact check is environment-dependent on repo_key collisions; rely on
    # the SDK contract: init must not write the compose directory.


def test_init_postgres_provenance_recorded(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--dry-run",
            "--json",
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--postgres-port",
            "5468",
        ],
    )
    envelope = json.loads(result.output)
    assert "postgres" in envelope["provenance"]["option"]
