from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands import env
from odoo_instance_sdk.exceptions import StalePlanError
from odoo_instance_sdk.execution import ActionStep, Command, ExecutionPlan
from odoo_instance_sdk.internal import git_worktree
from odoo_instance_sdk.internal.git_worktree import GitError
from odoo_instance_sdk.models import DevelopmentEnvironment
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment import EnvironmentCheckoutOptions

if TYPE_CHECKING:
    from odoo_instance_sdk import OdooClient


def _allocation_client(rows: list[dict[str, str]]) -> SimpleNamespace:
    catalog = SimpleNamespace(list_environments=lambda **_: rows)
    return SimpleNamespace(get_catalog=lambda: catalog)


def test_ticket_is_validated_before_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def resolve(*_: object, **__: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("invalid ticket reached allocation")

    monkeypatch.setattr(env, "_resolve_ticket_allocation", resolve)
    result = CliRunner().invoke(cli, ["env", "checkout", "bad-ticket"])

    assert result.exit_code == 2
    assert "expected ticket like PROJ-123" in result.stderr
    assert not called


def test_ticket_allocation_uses_sparse_union_and_removed_catalogue_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    common = (root / ".git").resolve()
    monkeypatch.setattr(env, "rev_parse_toplevel", lambda _: root)
    monkeypatch.setattr(env, "rev_parse_git_common_dir", lambda _: common)
    monkeypatch.setattr(
        ProjectConfig, "load", lambda _: SimpleNamespace(default_base_ref="develop")
    )
    monkeypatch.setattr(
        env, "local_branch_names", lambda _: ("PROJ-123", "PROJ-123_1", "PROJ-123_0")
    )
    monkeypatch.setattr(env, "remote_branch_names", lambda _, __: ("PROJ-123_2", "PROJ-123_x"))
    client = _allocation_client(
        [
            {"repository_root": str(root), "git_common_dir": str(common), "branch": "PROJ-123_3"},
            {
                "repository_root": str(root / "other"),
                "git_common_dir": str(common),
                "branch": "PROJ-123_99",
            },
        ]
    )

    allocation = env._resolve_ticket_allocation(cast("OdooClient", client), root, "PROJ-123", None)

    assert allocation.branch == "PROJ-123_4"
    assert allocation.base_ref == "develop"
    assert allocation.catalogue_heads == ("PROJ-123_3",)
    assert env._ticket_provenance(allocation) == {
        "ticket_allocation": {
            "ticket": "PROJ-123",
            "resolved_branch": "PROJ-123_4",
            "base_ref": "develop",
            "evidence": {
                "local": {
                    "heads": ["PROJ-123", "PROJ-123_0", "PROJ-123_1"],
                    "total": 3,
                    "truncated": False,
                },
                "catalogue": {
                    "heads": ["PROJ-123_3"],
                    "total": 1,
                    "truncated": False,
                },
                "origin": {
                    "heads": ["PROJ-123_2", "PROJ-123_x"],
                    "total": 2,
                    "truncated": False,
                },
            },
        }
    }


def test_ticket_allocation_honors_explicit_base_and_exact_ticket(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    monkeypatch.setattr(env, "rev_parse_toplevel", lambda _: root)
    monkeypatch.setattr(env, "rev_parse_git_common_dir", lambda _: root / ".git")
    monkeypatch.setattr(
        ProjectConfig, "load", lambda _: SimpleNamespace(default_base_ref="develop")
    )
    monkeypatch.setattr(env, "local_branch_names", lambda _: ())
    monkeypatch.setattr(env, "remote_branch_names", lambda _, __: ())

    allocation = env._resolve_ticket_allocation(
        cast("OdooClient", _allocation_client([])), root, "PROJ-123", "release"
    )

    assert allocation.branch == "PROJ-123"
    assert allocation.base_ref == "release"


@pytest.mark.parametrize("source", ["local", "catalogue", "origin"])
def test_ticket_allocation_revalidation_is_stale_without_reallocation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str
) -> None:
    root = tmp_path.resolve()
    common = root / ".git"
    allocation = env._TicketAllocation(
        ticket="PROJ-123",
        branch="PROJ-123_1",
        repo_root=root,
        git_common_dir=common,
        base_ref="HEAD",
        local_heads=(),
        catalogue_heads=(),
        remote_heads=(),
    )
    monkeypatch.setattr(
        env, "local_branch_names", lambda _: ("PROJ-123_1",) if source == "local" else ()
    )
    monkeypatch.setattr(
        env,
        "_catalogue_branch_names",
        lambda *_: ("PROJ-123_1",) if source == "catalogue" else (),
    )
    monkeypatch.setattr(
        env,
        "remote_branch_names",
        lambda _, __: ("PROJ-123_1",) if source == "origin" else (),
    )

    with pytest.raises(StalePlanError) as error:
        env._revalidate_ticket_absence(cast("OdooClient", _allocation_client([])), allocation)

    assert error.value.actual == {"source": source, "branch": "PROJ-123_1"}


def test_ticket_checkout_passes_captured_base_to_existing_command() -> None:
    command = object()
    client = SimpleNamespace(environments=MagicMock())
    client.environments.checkout_command.return_value = command
    allocation = env._TicketAllocation(
        ticket="PROJ-123",
        branch="PROJ-123_2",
        repo_root=Path("/repo"),
        git_common_dir=Path("/repo/.git"),
        base_ref="release",
        local_heads=(),
        catalogue_heads=(),
        remote_heads=(),
    )

    result = env._ticket_checkout_command(
        cast("OdooClient", client),
        Path("/repo"),
        EnvironmentCheckoutOptions(create_venv=True),
        allocation,
    )

    assert result is command
    args, kwargs = client.environments.checkout_command.call_args
    assert args == (Path("/repo"), "PROJ-123_2")
    assert kwargs["options"].base_ref == "release"
    assert kwargs["options"].create_venv is True
    assert kwargs["options"].name is None


def test_origin_probe_is_ticket_aware_and_recorded_without_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(args: list[str], *, check: bool = True, cwd: str | Path | None = None) -> object:
        del check, cwd
        calls.append(args)
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "abc\trefs/heads/PROJ-123\ndef\trefs/heads/PROJ-123_1\nghi\trefs/heads/OTHER-9\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(git_worktree, "_run", run)

    assert git_worktree.remote_branch_names(Path("/repo"), "PROJ-123") == (
        "PROJ-123",
        "PROJ-123_1",
    )
    assert calls == [
        [
            "git",
            "-C",
            "/repo",
            "ls-remote",
            "--heads",
            "origin",
            "PROJ-123",
            "PROJ-123_*",
        ]
    ]


@pytest.mark.parametrize("unavailable", ["local", "catalogue", "origin"])
def test_unavailable_ticket_evidence_fails_before_checkout_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, unavailable: str
) -> None:
    root = tmp_path.resolve()
    common = root / ".git"
    calls: list[str] = []
    monkeypatch.setattr(env, "rev_parse_toplevel", lambda _: root)
    monkeypatch.setattr(env, "rev_parse_git_common_dir", lambda _: common)
    monkeypatch.setattr(ProjectConfig, "load", lambda _: SimpleNamespace(default_base_ref="HEAD"))

    def local(_: Path) -> tuple[str, ...]:
        calls.append("local")
        if unavailable == "local":
            raise GitError("local refs unavailable")
        return ()

    def catalogue(*_: object) -> tuple[str, ...]:
        calls.append("catalogue")
        if unavailable == "catalogue":
            raise RuntimeError("catalogue unavailable")
        return ()

    def origin(_: Path, __: str) -> tuple[str, ...]:
        calls.append("origin")
        if unavailable == "origin":
            raise GitError("origin unavailable")
        return ()

    monkeypatch.setattr(env, "local_branch_names", local)
    monkeypatch.setattr(env, "_catalogue_branch_names", catalogue)
    monkeypatch.setattr(env, "remote_branch_names", origin)

    with pytest.raises((GitError, RuntimeError)):
        env._resolve_ticket_allocation(
            cast("OdooClient", _allocation_client([])), root, "PROJ-123", None
        )

    assert calls == [
        "local",
        *(["catalogue"] if unavailable != "local" else []),
        *(["origin"] if unavailable == "origin" else []),
    ]


def test_ticket_provenance_is_present_in_rich_json_and_toon(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from toon import DecodeOptions, decode

    from odoo_instance_sdk.commands.output import OutputMode, emit, success_document

    allocation = env._TicketAllocation(
        ticket="PROJ-123",
        branch="PROJ-123_2",
        repo_root=Path("/repo"),
        git_common_dir=Path("/repo/.git"),
        base_ref="release",
        local_heads=("PROJ-123",),
        catalogue_heads=("PROJ-123_1",),
        remote_heads=("PROJ-123_2",),
    )
    expected = env._ticket_provenance(allocation)

    rich_status = emit(
        success_document(
            command="env.checkout",
            result={"plan": {"branch": "PROJ-123_2"}},
            provenance=expected,
            dry_run=True,
        ),
        OutputMode.RICH,
        rich=lambda document: "\n".join(env._ticket_rich_lines(document)),
    )
    rich_output = capsys.readouterr().out
    assert rich_status == 0
    assert "PROJ-123_2" in rich_output
    assert "base release" in rich_output
    assert "Ticket local: [PROJ-123]" in rich_output

    json_status = emit(
        success_document(
            command="env.checkout",
            result={"plan": {"branch": "PROJ-123_2"}},
            provenance=expected,
            dry_run=True,
        ),
        OutputMode.JSON,
    )
    json_payload = json.loads(capsys.readouterr().out)
    assert json_status == 0
    assert json_payload["provenance"] == expected

    toon_status = emit(
        success_document(
            command="env.checkout",
            result={"plan": {"branch": "PROJ-123_2"}},
            provenance=expected,
            dry_run=True,
        ),
        OutputMode.TOON,
    )
    toon_payload = decode(capsys.readouterr().out, DecodeOptions(indent=2, strict=True))
    assert toon_status == 0
    assert toon_payload["provenance"] == expected


def test_checkout_and_create_help_share_ticket_contract() -> None:
    runner = CliRunner()
    checkout = runner.invoke(cli, ["env", "checkout", "--help"])
    create = runner.invoke(cli, ["env", "create", "--help"])

    assert checkout.exit_code == create.exit_code == 0
    assert "TICKET" in checkout.output
    assert "--name" not in checkout.output
    assert "TICKET" in create.output
    assert "--name" not in create.output
    assert "Ticket branch" in checkout.output


def test_env_shell_completion_keeps_both_checkout_spellings_visible() -> None:
    result = CliRunner().invoke(
        cli,
        [],
        env={
            "_CLI_COMPLETE": "bash_complete",
            "COMP_WORDS": "cli env ",
            "COMP_CWORD": "2",
        },
    )

    assert result.exit_code == 0
    assert "plain,checkout" in result.stdout
    assert "plain,create" not in result.stdout


@pytest.mark.parametrize("spelling", ["checkout", "create"])
@pytest.mark.parametrize("mode", ["rich", "json", "toon"])
@pytest.mark.parametrize("dry_run", [True, False])
def test_cli_ticket_checkout_emits_one_shared_envelope_for_both_spellings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    spelling: str,
    mode: str,
    dry_run: bool,
) -> None:
    from odoo_instance_sdk.internal.proc import PreparedAction, RunContext
    from tests.unit.test_cli_output_modes import _matrix_checkout_plan, _matrix_public_environment

    plan = _matrix_checkout_plan()
    allocation = env._TicketAllocation(
        ticket="PROJ-123",
        branch="PROJ-123_2",
        repo_root=tmp_path,
        git_common_dir=tmp_path / ".git",
        base_ref="release",
        local_heads=("PROJ-123",),
        catalogue_heads=("PROJ-123_1",),
        remote_heads=("PROJ-123_2",),
    )
    action = PreparedAction("checkout.synthetic")

    def run(context: RunContext[DevelopmentEnvironment]) -> DevelopmentEnvironment:
        context.action("checkout.synthetic")
        context.complete_action("checkout.synthetic")
        return _matrix_public_environment()

    command = Command.create(
        ExecutionPlan(
            steps=(
                ActionStep(
                    step_id="checkout.synthetic",
                    action="checkout.synthetic",
                    description="Synthetic checkout",
                    mutating=True,
                ),
            )
        ),
        run,
        steps=(action,),
        private_projection=plan,
    )
    client = MagicMock()
    argv = ["env", spelling, "PROJ-123"]
    if dry_run:
        argv.append("--dry-run")
    if mode != "rich":
        argv.extend(["--format", mode])

    with (
        patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client),
        patch("odoo_instance_sdk.commands.env.resolve_project_path", return_value=tmp_path),
        patch(
            "odoo_instance_sdk.commands.env._build_ticket_checkout_command",
            return_value=(command, allocation),
        ),
    ):
        result = CliRunner().invoke(cli, argv)

    assert result.exit_code == 0, result.output
    expected_provenance = env._ticket_provenance(allocation)
    if mode == "rich":
        assert result.stdout.count("Ticket PROJ-123") == 1
        assert "PROJ-123_2" in result.stdout
        assert "base release" in result.stdout
        if dry_run:
            assert "checkout.synthetic" in result.stdout
        return
    if mode == "json":
        payload = json.loads(result.stdout)
    else:
        from toon import DecodeOptions, decode

        payload = decode(result.stdout, DecodeOptions(indent=2, strict=True))
    assert result.stdout.count("schema_version") == 1
    assert payload["dry_run"] is dry_run
    assert payload["provenance"]["ticket_allocation"] == expected_provenance["ticket_allocation"]
