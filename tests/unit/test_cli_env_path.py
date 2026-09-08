from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.models import DevelopmentEnvironment
from odoo_instance_sdk.resources.environment import EnvironmentDatabaseMode, EnvironmentState


def _environment(
    worktree: Path,
    *,
    env_id: uuid.UUID | None = None,
    name: str = "demo",
    state: EnvironmentState = EnvironmentState.READY,
) -> DevelopmentEnvironment:
    return DevelopmentEnvironment(
        id=env_id or uuid.uuid4(),
        name=name,
        repository_root=str(worktree.parent),
        git_common_dir=str(worktree.parent / ".git"),
        branch="main",
        base_ref="HEAD",
        worktree_path=str(worktree),
        generated_config_path=str(worktree / "odoo.conf"),
        python_environment_path=str(worktree / "venv"),
        python_environment_owned=False,
        dependency_lock_path=str(worktree / "requirements.lock"),
        http_interface="127.0.0.1",
        http_port=8069,
        db_mode=EnvironmentDatabaseMode.SHARED,
        state=state,
        created_at=datetime.now(UTC),
    )


def _invoke(
    environments: list[DevelopmentEnvironment],
    args: list[str],
) -> object:
    client = MagicMock()
    client.environments.list.return_value = environments
    with (
        patch("odoo_instance_sdk.commands.context.OdooClient", return_value=client),
        patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client),
    ):
        return CliRunner().invoke(cli, args, catch_exceptions=False)


@pytest.mark.unit
def test_env_path_omitted_inside_registered_worktree_prints_exact_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmp_path / "worktree with spaces"
    worktree.mkdir()
    env = _environment(worktree)
    monkeypatch.chdir(worktree)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.context.git_worktree.rev_parse_toplevel",
        lambda _path: worktree.parent,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.context.git_worktree.rev_parse_git_common_dir",
        lambda _path: worktree.parent / ".git",
    )

    result = _invoke([env], ["env", "path"])

    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    assert result.stdout == f"{worktree}\n"  # type: ignore[attr-defined]
    assert result.stderr == ""  # type: ignore[attr-defined]
    assert "\x1b" not in result.stdout  # type: ignore[attr-defined]


@pytest.mark.unit
def test_env_path_name_and_uuid_have_json_toon_parity(tmp_path: Path) -> None:
    worktree = tmp_path / "registered"
    worktree.mkdir()
    env = _environment(worktree, name="feature/demo", env_id=uuid.uuid4())
    documents: list[dict[str, object]] = []
    for mode in ("json", "toon"):
        result = _invoke([env], ["env", "path", str(env.id), "--format", mode])
        assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
        if mode == "json":
            document = json.loads(result.stdout)  # type: ignore[attr-defined]
        else:
            from toon import DecodeOptions, decode

            document = decode(result.stdout, DecodeOptions(indent=2, strict=True))  # type: ignore[attr-defined]
        assert document["result"] == document["data"]
        assert set(document["result"]) == {"environment_id", "name", "worktree_path"}
        assert document["result"] == {
            "environment_id": str(env.id),
            "name": env.name,
            "worktree_path": str(worktree),
        }
        documents.append(document)

    assert documents[0] == documents[1]
    name_result = _invoke([env], ["env", "path", env.name, "--format", "json"])
    assert name_result.exit_code == 0, name_result.output  # type: ignore[attr-defined]
    assert json.loads(name_result.stdout)["result"]["worktree_path"] == str(worktree)  # type: ignore[attr-defined]


@pytest.mark.unit
@pytest.mark.parametrize(
    "environments, selector",
    [
        ([], "missing"),
        (
            [_environment(Path("/does/not/exist"), name="removed", state=EnvironmentState.REMOVED)],
            "removed",
        ),
        (
            [
                _environment(Path("/tmp/a"), name="ambiguous"),
                _environment(Path("/tmp/b"), name="ambiguous"),
            ],
            "ambiguous",
        ),
        ([_environment(Path("/tmp/not-a-directory"))], "demo"),
        ([_environment(Path("relative/path"))], "demo"),
        ([_environment(Path("/tmp/creating"), state=EnvironmentState.CREATING)], "demo"),
    ],
)
def test_env_path_invalid_selection_is_actionable_without_path(
    environments: list[DevelopmentEnvironment], selector: str
) -> None:
    result = _invoke(environments, ["env", "path", selector, "--format", "json"])

    assert result.exit_code == 1, result.output  # type: ignore[attr-defined]
    document = json.loads(result.stdout)  # type: ignore[attr-defined]
    assert document["ok"] is False
    assert "result" not in document
    assert "worktree_path" not in result.stdout  # type: ignore[attr-defined]


@pytest.mark.unit
def test_env_path_rejects_root_env_for_omitted_selector(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    env = _environment(worktree)

    result = _invoke([env], ["--env", str(env.id), "env", "path"])

    assert result.exit_code == 2, result.output  # type: ignore[attr-defined]
    assert str(worktree) not in result.stdout  # type: ignore[attr-defined]
    assert str(worktree) not in result.stderr  # type: ignore[attr-defined]


@pytest.mark.unit
def test_env_path_help_documents_shell_command_without_env_cd() -> None:
    result = CliRunner().invoke(cli, ["env", "path", "--help"])

    assert result.exit_code == 0, result.output
    help_text = " ".join(result.stdout.split())
    assert 'cd "$(odcli env path <environment>)"' in help_text
    assert "env cd" not in help_text
