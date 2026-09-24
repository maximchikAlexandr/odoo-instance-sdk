from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec
import pytest
from click.testing import CliRunner
from rich.console import Console

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands.env import checkout as env_commands
from tests.unit.test_cli_backup import BACKUP_ID, _seed_backup
from tests.unit.test_cli_env_list_grouping import (
    _env,
    _healthy_cluster,
    _snapshot,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.models import ProjectSummary


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
    assert "feat/x" in output
    assert "running" in output
    assert "comerta" in output
    assert "↑2" in output
    assert "+10 -3" in output
    assert "OBSERVED" not in output
    assert "ODOO_PID" not in output
    assert "GIT_AHEA" not in output
    assert "┌" in output and "┼" in output and "└" in output
    expected_headers = (
        ("NAME", "STATE", "DETAILS")
        if width < 120
        else ("NAME", "BRANCH / STATUS", "DATABASE", "GIT A/D", "WORKTREE")
        if width < 180
        else ("KIND", "NAME", "BRANCH", "STATUS", "GIT", "DB_MODE", "DATABASE", "WORKTREE")
    )
    assert all(header in output for header in expected_headers)
    if width == 120:
        assert "shared comerta" in output
    assert all(len(line) <= width for line in output.splitlines())


@pytest.mark.unit
def test_env_list_rich_shortens_home_only_in_presentation() -> None:
    environment = _env()
    snapshot = _snapshot((_project(),), (environment,))
    absolute = str(Path.home() / "projects" / "environment")
    output = _render(snapshot, width=120, worktree_path=absolute)

    assert "~/projects/environment" in output
    from odoo_instance_sdk.internal.checkout_inventory import build_checkout_inventory

    inventory = build_checkout_inventory(
        snapshot,
        worktree_paths={environment.id: absolute},
        git_collector=lambda _path, _ref: environment.git,
    )
    env_row = next(row for row in inventory.rows if row.kind == "environment")
    assert env_row.worktree_path == absolute


@pytest.mark.unit
def test_env_list_rich_empty_inventory_remains_bordered() -> None:
    output = _render(
        _snapshot((), ()), width=80, worktree_path=str(Path.home() / "projects" / "environment")
    )

    assert "No environments found" in output
    assert all(header in output for header in ("NAME", "STATE", "DETAILS"))
    assert "┌" in output and "└" in output


@pytest.mark.unit
def test_env_list_rich_compact_keeps_provider_facts_in_details_only() -> None:
    from odoo_instance_sdk.internal.checkout_inventory import build_checkout_inventory
    from odoo_instance_sdk.models import EnvironmentFactsSummary

    environment = _env()
    inventory = build_checkout_inventory(
        _snapshot((_project(),), (environment,)),
        worktree_paths={environment.id: "/worktree"},
        git_collector=lambda _path, _ref: environment.git,
    )
    row = msgspec.structs.replace(
        inventory.rows[0],
        facts=(
            EnvironmentFactsSummary(
                provider="provider",
                state="available",
                text="provider-value",
            ),
        ),
    )
    inventory = msgspec.structs.replace(inventory, rows=(row, *inventory.rows[1:]))
    console = Console(record=True, color_system=None, width=80)
    console.print(env_commands._render_env_list_rich(inventory, width=80))
    output = console.export_text()

    assert "provider-value" in output
    assert output.count("provider-value") == 1
    assert "provider" in output
    assert "PROVIDER" not in output


@pytest.mark.unit
def test_cli_composition_promotes_short_aliases_without_leaf_edits() -> None:
    loaded = CliRunner().invoke(cli, ["module", "--help"])
    assert loaded.exit_code == 0, loaded.output

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
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda **_kwargs: db_path
    )
    if expected_status == "unavailable":
        monkeypatch.setattr(shutil, "which", lambda _name: None)

    result = CliRunner().invoke(
        cli,
        ["backup", "validate", BACKUP_ID],
        env={"COLUMNS": str(width)},
    )

    assert result.exit_code == (0 if expected_status == "valid" else 1), result.output
    if expected_status == "valid":
        assert expected_status in result.stdout.lower()
    else:
        assert result.stdout == ""
        assert "┌" in result.stderr
        assert "Status" in result.stderr
        assert expected_status in result.stderr.lower()
    assert all(len(line) <= width for line in (result.stdout + result.stderr).splitlines())


def _render(snapshot: object, *, width: int, worktree_path: str) -> str:
    from odoo_instance_sdk.internal.checkout_inventory import build_checkout_inventory

    inventory = build_checkout_inventory(
        snapshot,  # type: ignore[arg-type]
        worktree_paths={"11111111-1111-1111-1111-111111111111": worktree_path},
        git_collector=lambda _path, _ref: _env().git,
    )
    console = Console(record=True, color_system=None, width=width)
    console.print(env_commands._render_env_list_rich(inventory, width=width))
    return console.export_text()


def _project() -> ProjectSummary:
    from odoo_instance_sdk.models import ProjectSummary

    return ProjectSummary(
        id="project_comerta_abc12345",
        name="comerta",
        display_hint="comerta_abc12345",
        repository_root="/repo/comerta",
        environment_count=1,
        cluster=_healthy_cluster(),
        runtime=None,
    )
