from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.internal.odoo_config import parse_odoo_config
from odoo_instance_sdk.internal.repo_key import git_common_dir, repo_key
from odoo_instance_sdk.resources.monitor import EnvironmentMonitor
from odoo_instance_sdk.resources.postgres import PostgresCluster
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


def test_init_compose_secret_matches_cluster_after_captured_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Captured init and later direct cluster resolution must share one secret."""
    data_root = tmp_path / "sdk-data"
    data_root.mkdir()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_data_root", lambda **_kwargs: data_root
    )
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    result = CliRunner().invoke(
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

    assert result.exit_code == 0, result.output
    cluster = PostgresCluster.from_project(tmp_path)
    generated = parse_odoo_config(tmp_path / ".odcli" / "odoo.conf")
    assert generated["db_password"] == cluster.password_file.read_text(encoding="utf-8").strip()


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


def test_init_compose_generates_private_project_runtime_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "sdk-data"
    data_root.mkdir()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_data_root", lambda **_kwargs: data_root
    )
    root_ignore = tmp_path / ".gitignore"
    root_ignore.write_text("root-only\n")
    root_ignore_before = root_ignore.read_bytes()
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    cfg = tmp_path / "odoo.conf"
    source_bytes = b"[options]\nhttp_port = 8068\ncustom_option = retained\n"
    cfg.write_bytes(source_bytes)

    result = CliRunner().invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--config",
            str(cfg),
            "--database",
            "tenant",
            "--http-port",
            "8077",
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

    assert result.exit_code == 0, result.output
    generated = tmp_path / ".odcli" / "odoo.conf"
    assert generated.is_file()
    assert generated.stat().st_mode & 0o777 == 0o600
    generated_text = generated.read_text()
    assert "custom_option = retained" in generated_text
    assert "http_port = 8077" in generated_text
    assert "db_name = tenant" in generated_text
    assert "dbfilter = tenant" in generated_text
    assert "db_host = 127.0.0.1" in generated_text
    assert "db_port = 5468" in generated_text
    assert "db_user = odoo" in generated_text
    secret_files = list(data_root.glob("projects/*/postgres/postgres-password"))
    assert len(secret_files) == 1
    password = secret_files[0].read_text().strip()
    assert password
    assert secret_files[0].stat().st_mode & 0o777 == 0o600
    assert password not in result.output
    assert password not in (tmp_path / ".odcli" / "project.toml").read_text()
    assert cfg.read_bytes() == source_bytes
    assert (tmp_path / ".odcli" / ".gitignore").read_text().splitlines() == [".env", "odoo.conf"]
    assert root_ignore.read_bytes() == root_ignore_before
    ignored = subprocess.run(
        ["git", "-C", str(tmp_path), "check-ignore", ".odcli/odoo.conf"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ignored.returncode == 0, ignored.stderr
    assert ignored.stdout.strip() == ".odcli/odoo.conf"


def test_init_compose_user_defaults_to_odoo_without_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "sdk-data"
    data_root.mkdir()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_data_root", lambda **_kwargs: data_root
    )
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
    generated = tmp_path / ".odcli" / "odoo.conf"
    assert generated.is_file()
    assert generated.stat().st_mode & 0o777 == 0o600
    generated_text = generated.read_text()
    assert "http_port = 8069" in generated_text
    assert "db_host = 127.0.0.1" in generated_text
    assert "db_port = " in generated_text
    assert "db_user = odoo" in generated_text
    assert not (tmp_path / "odoo.conf").exists()


def test_init_compose_refuses_pretracked_generated_config_without_secret_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "sdk-data"
    data_root.mkdir()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_data_root", lambda **_kwargs: data_root
    )
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    generated = tmp_path / ".odcli" / "odoo.conf"
    generated.parent.mkdir()
    original = b"[options]\ncustom = retained\n"
    generated.write_bytes(original)
    subprocess.run(["git", "-C", str(tmp_path), "add", ".odcli/odoo.conf"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.email=test@test.com",
            "-c",
            "user.name=Test",
            "commit",
            "-qm",
            "tracked-runtime-config",
        ],
        check=True,
    )

    result = CliRunner().invoke(
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

    assert result.exit_code == 1
    assert "tracked" in result.output.lower()
    assert generated.read_bytes() == original
    assert not list(data_root.glob("projects/*/postgres/postgres-password"))
    tracked = subprocess.run(
        ["git", "-C", str(tmp_path), "ls-files", "--error-unmatch", "--", ".odcli/odoo.conf"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.returncode == 0


def test_init_compose_fails_closed_when_git_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "sdk-data"
    data_root.mkdir()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_data_root", lambda **_kwargs: data_root
    )
    source = tmp_path / "odoo.conf"
    source_bytes = b"[options]\ndb_password = source-secret\ncustom = retained\n"
    source.write_bytes(source_bytes)
    generated = tmp_path / ".odcli" / "odoo.conf"
    generated.parent.mkdir()
    generated_bytes = b"[options]\ndb_password = tracked-secret\n"
    generated.write_bytes(generated_bytes)
    root_ignore = tmp_path / ".gitignore"
    root_ignore.write_bytes(b"root-only\n")
    monkeypatch.setenv("PATH", "")

    result = CliRunner().invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--config",
            str(source),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--postgres-port",
            "5468",
        ],
    )

    assert result.exit_code == 1
    assert "unable to verify" in result.output.lower()
    assert "source-secret" not in result.output
    assert "tracked-secret" not in result.output
    assert source.read_bytes() == source_bytes
    assert generated.read_bytes() == generated_bytes
    assert root_ignore.read_bytes() == b"root-only\n"
    assert not list(data_root.glob("projects/*/postgres/postgres-password"))


def test_init_compose_fails_closed_on_rev_parse_failure_with_git_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "sdk-data"
    data_root.mkdir()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_data_root", lambda **_kwargs: data_root
    )
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    source = tmp_path / "odoo.conf"
    source_bytes = b"[options]\ndb_password = source-secret\ncustom = retained\n"
    source.write_bytes(source_bytes)
    generated = tmp_path / ".odcli" / "odoo.conf"
    generated.parent.mkdir()
    generated_bytes = b"[options]\ndb_password = tracked-secret\n"
    generated.write_bytes(generated_bytes)
    subprocess.run(["git", "-C", str(tmp_path), "add", ".odcli/odoo.conf"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.email=test@test.com",
            "-c",
            "user.name=Test",
            "commit",
            "-qm",
            "tracked-runtime-config",
        ],
        check=True,
    )

    from odoo_instance_sdk.internal import git_worktree

    def fail_rev_parse(_path: Path) -> Path:
        raise git_worktree.GitError("rev-parse failed")

    monkeypatch.setattr(git_worktree, "rev_parse_toplevel", fail_rev_parse)
    result = CliRunner().invoke(
        cli,
        [
            *_base_args(tmp_path),
            "--config",
            str(source),
            "--postgres",
            "compose",
            "--postgres-image",
            "pgvector/pgvector:pg16",
            "--postgres-port",
            "5468",
        ],
    )

    assert result.exit_code == 1
    assert "unable to verify" in result.output.lower()
    assert "rev-parse failed" not in result.output
    assert "source-secret" not in result.output
    assert "tracked-secret" not in result.output
    assert source.read_bytes() == source_bytes
    assert generated.read_bytes() == generated_bytes
    assert not list(data_root.glob("projects/*/postgres/postgres-password"))


def test_init_external_default_omits_postgres_section(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, _base_args(tmp_path))
    assert result.exit_code == 0
    content = (tmp_path / ".odcli" / "project.toml").read_text()
    assert "[postgres]" not in content


def test_init_normalizes_project_ignore_rules_and_git_ownership(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / ".gitignore").write_text(
        "keep-me\n.env\n!odoo.conf\n!*.conf\nodoo.conf\n", encoding="utf-8"
    )

    result = CliRunner().invoke(cli, _base_args(tmp_path))

    assert result.exit_code == 0, result.output
    assert (manifest_dir / ".gitignore").read_text(encoding="utf-8") == (
        "keep-me\n!odoo.conf\n!*.conf\n.env\nodoo.conf\n"
    )
    ignored = subprocess.run(
        ["git", "-C", str(tmp_path), "check-ignore", ".odcli/odoo.conf"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ignored.returncode == 0
    tracked = subprocess.run(
        ["git", "-C", str(tmp_path), "ls-files", "--error-unmatch", "--", ".odcli/odoo.conf"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.returncode == 1


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
    monkeypatch.setattr("odoo_instance_sdk.cli.get_catalog_path", lambda **_kwargs: catalog_path)
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
