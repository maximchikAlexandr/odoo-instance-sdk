from __future__ import annotations

import shutil
from pathlib import Path

import msgspec
import pytest
from click.testing import CliRunner
from rich.console import Console

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands import env as env_commands
from odoo_instance_sdk.models import ProjectSummary, Snapshot
from tests.unit.test_cli_backup import BACKUP_ID, _seed_backup
from tests.unit.test_cli_env_list_grouping import _env, _healthy_cluster, _snapshot


@pytest.mark.unit
@pytest.mark.parametrize("width", [80, 120, 180])
def test_env_list_rich_keeps_primary_fields_readable_at_supported_widths(width: int) -> None:
    environment = _env(
        name="environment-with-a-long-name",
        branch="feature/with-a-long-but-readable-branch-name",
    )
    snapshot = _snapshot((_project(),), (environment,))
    output = _render(
        snapshot, width=width, worktree_path=str(Path.home() / "projects" / "environment")
    )

    assert output.strip()
    assert "environment-with-a-long-name" in output or "environment-with" in output
    assert "feature/" in output
    assert "ready" in output
    assert "comerta" in output
    assert "↑2" in output
    assert "+10 -3" in output
    assert "worktree,registered,config,python,lock" in output
    assert "GIT_AHEA" not in output
    assert "ARTIFACT\nS" not in output
    assert all(len(line) <= width for line in output.splitlines())


@pytest.mark.unit
def test_env_list_rich_shortens_home_only_in_presentation() -> None:
    environment = _env()
    snapshot = _snapshot((_project(),), (environment,))
    absolute = str(Path.home() / "projects" / "environment")
    output = _render(snapshot, width=120, worktree_path=absolute)

    assert "~/projects/environment" in output
    machine = env_commands._cli_snapshot(snapshot, {environment.id: absolute})
    assert msgspec.to_builtins(machine)["environments"][0]["worktree_path"] == absolute


@pytest.mark.unit
def test_cli_composition_promotes_short_aliases_without_leaf_edits() -> None:
    aliases = (
        ("env", "create", "checkout"),
        ("env", "ls", "list"),
        ("env", "rm", "remove"),
        ("backup", "ls", "list"),
        ("backup", "inspect", "show"),
        ("backup", "rm", "delete"),
        ("db", "ls", "list"),
        ("db", "rm", "drop"),
        ("postgres", "ps", "status"),
        ("resource", "ls", "list"),
        ("module", "ls", "list"),
    )
    for group_name, canonical, alternate in aliases:
        group = cli.commands[group_name]
        command = group.get_command(None, canonical)
        assert command is not None
        assert group.get_command(None, alternate) is command
        assert command.name == canonical


@pytest.mark.unit
@pytest.mark.parametrize("width", [80, 120, 180])
@pytest.mark.parametrize(
    ("expected_status", "fmt", "valid_zip"),
    [("valid", "zip", True), ("invalid", "zip", False), ("unavailable", "dump", False)],
)
def test_backup_validate_rich_leaf_is_width_safe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    width: int,
    expected_status: str,
    fmt: str,
    valid_zip: bool,
) -> None:
    database_name = "database-with-a-realistically-long-name-for-terminal-width-validation"
    db_path, _backup_path = _seed_backup(
        tmp_path / expected_status,
        fmt=fmt,
        valid_zip=valid_zip,
        database_name=database_name,
    )
    monkeypatch.setattr("odoo_instance_sdk.cli.get_catalog_path", lambda **_kwargs: db_path)
    if expected_status == "unavailable":
        monkeypatch.setattr(shutil, "which", lambda _name: None)

    result = CliRunner().invoke(
        cli,
        ["backup", "validate", BACKUP_ID],
        env={"COLUMNS": str(width)},
    )

    assert result.exit_code == (0 if expected_status == "valid" else 1), result.output
    assert expected_status in result.stdout.lower() or expected_status in result.stderr.lower()
    assert all(len(line) <= width for line in (result.stdout + result.stderr).splitlines())


def _render(snapshot: Snapshot, *, width: int, worktree_path: str) -> str:
    console = Console(record=True, color_system=None, width=width)
    console.print(
        env_commands._render_env_list_rich(
            snapshot,
            {"11111111-1111-1111-1111-111111111111": worktree_path},
            width=width,
        )
    )
    return console.export_text()


def _project() -> ProjectSummary:
    return ProjectSummary(
        id="project_comerta_abc12345",
        name="comerta",
        display_hint="comerta_abc12345",
        environment_count=1,
        cluster=_healthy_cluster(),
        runtime=None,
    )
