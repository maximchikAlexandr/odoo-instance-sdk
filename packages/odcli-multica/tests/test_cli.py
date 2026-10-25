from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, NoReturn, cast

import pytest
from click.testing import CliRunner
from multica_py import CommandCancelledError
from odcli_multica.cli import cli

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.commands.output import action_command

if TYPE_CHECKING:
    from click.testing import Result
    from odcli_multica import ContextRequest, VerifiedTaskContext
    from odcli_multica.git_execution import GitResourceLike

    from odoo_instance_sdk import Command
    from odoo_instance_sdk.execution import ExecutionPlan


class _CliClient:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error

    def context_command(self, _request: ContextRequest) -> Command[VerifiedTaskContext]:
        if self.error is not None:
            raise self.error
        return cast(
            "Command[VerifiedTaskContext]",
            action_command(
                "test.context",
                lambda: cast("VerifiedTaskContext", SimpleNamespace()),
                description="test context",
            ),
        )


class _GitCliClient:
    def __init__(self) -> None:
        self.raw_args: tuple[str, ...] | None = None
        self.sync_options: dict[str, object] | None = None
        self.run_count = 0

    def context(self, request: ContextRequest) -> SimpleNamespace:
        return SimpleNamespace(checkout_path=request.checkout_path)

    def git_command(self, _context: object, args: tuple[str, ...], **_kwargs: object) -> object:
        self.raw_args = args

        def run() -> SimpleNamespace:
            self.run_count += 1
            return SimpleNamespace(returncode=0)

        return action_command("test.git", run, description="test Git passthrough")

    def sync_command(self, _context: object, **kwargs: object) -> object:
        self.sync_options = kwargs

        def run() -> SimpleNamespace:
            self.run_count += 1
            return SimpleNamespace()

        return action_command("test.git.sync", run, description="test Git sync")


class _GitPlanCliClient:
    def __init__(self, core: OdooClient) -> None:
        self.core = core
        self.plan: ExecutionPlan | None = None

    def context(self, request: ContextRequest) -> SimpleNamespace:
        return SimpleNamespace(
            checkout_path=str(request.checkout_path), repository_url=request.repository_url
        )

    def git_command(
        self,
        _context: object,
        args: tuple[str, ...],
        *,
        git_resource: GitResourceLike,
        **_kwargs: object,
    ) -> object:
        command = git_resource.passthrough_command(args)
        self.plan = command.plan
        return command

    def sync_command(
        self,
        _context: object,
        *,
        git_resource: GitResourceLike,
        base: str | None = None,
        push: bool = False,
        **_kwargs: object,
    ) -> object:
        command = git_resource.sync_command(base=base, push=push)
        self.plan = command.plan
        return command

    def publish_merge_request_command(
        self, _context: object, *, git_resource: GitResourceLike, **_kwargs: object
    ) -> object:
        command = git_resource.passthrough_command(("status",))
        self.plan = command.plan
        return command


def _invoke_context(
    monkeypatch: pytest.MonkeyPatch, client: _CliClient, tmp_path: Path, *extra: str
) -> Result:
    module = importlib.import_module("odcli_multica.cli")

    def fake_client(_profile: str | None, _workspace_id: str | None, _odoo_bin: str) -> _CliClient:
        return client

    monkeypatch.setattr(module, "_client", fake_client)
    checkout = tmp_path / "checkout"
    checkout.mkdir(exist_ok=True)
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return CliRunner().invoke(
        cli,
        [
            "context",
            str(checkout),
            "--project",
            str(project),
            "--multica-project",
            "project",
            "--issue",
            "issue",
            "--run",
            "run",
            "--repository-url",
            "https://example.test/repo",
            "--format",
            "json",
            *extra,
        ],
    )


def test_cli_dry_run_emits_one_machine_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = _invoke_context(monkeypatch, _CliClient(), tmp_path, "--dry-run")

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["dry_run"] is True


def test_cli_error_and_interruption_are_bounded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    failed = _invoke_context(monkeypatch, _CliClient(CommandCancelledError("cancelled")), tmp_path)
    assert failed.exit_code == 1
    assert json.loads(failed.output)["ok"] is False

    interrupted = _invoke_context(monkeypatch, _CliClient(KeyboardInterrupt()), tmp_path)
    assert interrupted.exit_code == 1
    assert json.loads(interrupted.output)["error"]["code"] == "interrupted"


def test_cli_initialization_preserves_interruption(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = importlib.import_module("odcli_multica.cli")

    def interrupting_client(
        _profile: str | None, _workspace_id: str | None, _odoo_bin: str
    ) -> NoReturn:
        raise KeyboardInterrupt()

    monkeypatch.setattr(module, "_client", interrupting_client)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    result = CliRunner().invoke(
        cli,
        [
            "context",
            str(checkout),
            "--project",
            str(project),
            "--multica-project",
            "project",
            "--issue",
            "issue",
            "--run",
            "run",
            "--repository-url",
            "https://example.test/repo",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "interrupted"


def _git_context_options(project: Path) -> list[str]:
    return [
        "--project",
        str(project),
        "--multica-project",
        "project",
        "--issue",
        "issue",
        "--run",
        "run",
        "--repository-url",
        "https://example.test/repo",
        "--format",
        "json",
        "--dry-run",
    ]


def test_git_resource_uses_task_checkout_with_core_config(tmp_path: Path) -> None:
    module = importlib.import_module("odcli_multica.cli")
    project = tmp_path / "project"
    project.mkdir()
    (project / "odoo.conf").write_text("[options]\nhttp_interface = 127.0.0.1\nhttp_port = 8069\n")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    client = SimpleNamespace(core=OdooClient(config=OdooClientConfig(executable="odoo")))

    resource = module._git_resource(client, project, str(checkout))

    assert resource.passthrough_command(("status",)).plan.steps[0].cwd == str(checkout)


_GIT_CONTEXT_ARGS = (
    "--project",
    "{project}",
    "--multica-project",
    "project",
    "--issue",
    "issue",
    "--run",
    "run",
    "--repository-url",
    "https://example.test/repo",
    "--format",
    "json",
    "--dry-run",
)

_GIT_ROUTE_ARGV = (
    pytest.param(("git", *_GIT_CONTEXT_ARGS, "{checkout}", "--", "status", "--short"), id="git"),
    pytest.param(
        (
            "git",
            "sync",
            *_GIT_CONTEXT_ARGS,
            "{checkout}",
            "--push",
            "--base",
            "HEAD",
        ),
        id="sync",
    ),
    pytest.param(
        (
            "gitlab",
            "mr",
            "publish",
            "{checkout}",
            "--project",
            "{project}",
            "--multica-project",
            "project",
            "--issue",
            "issue",
            "--run",
            "run",
            "--repository-url",
            "https://example.test/repo",
            "--source",
            "feature",
            "--target",
            "main",
            "--title",
            "Test MR",
            "--description-file",
            "{description}",
            "--format",
            "json",
            "--dry-run",
        ),
        id="publish",
    ),
)


def _init_git_repo(path: Path, branch: str) -> None:
    subprocess.run(("git", "init", "--quiet", "--initial-branch", branch, str(path)), check=True)


def _git_route_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    (project / "odoo.conf").write_text("[options]\nhttp_interface = 127.0.0.1\nhttp_port = 8069\n")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _init_git_repo(project, "project")
    _init_git_repo(checkout, "feature")
    description = tmp_path / "description.md"
    description.write_text("Test merge request description")
    return project, checkout, description


def _git_route_client(monkeypatch: pytest.MonkeyPatch) -> _GitPlanCliClient:
    module = importlib.import_module("odcli_multica.cli")
    client = _GitPlanCliClient(OdooClient(config=OdooClientConfig(executable="odoo")))
    monkeypatch.setattr(module, "_client", lambda *_args: client)
    return client


def _invoke_git_route(
    template: tuple[str, ...], project: Path, checkout: Path, description: Path
) -> Result:
    values = {
        "project": str(project),
        "checkout": str(checkout),
        "description": str(description),
    }
    args = [part.format(**values) for part in template]
    return CliRunner().invoke(cli, args)


def _assert_git_plan_cwd(client: _GitPlanCliClient, project: Path, checkout: Path) -> None:
    assert client.plan is not None
    cwd = client.plan.process_steps[0].cwd
    assert cwd == str(checkout.resolve())
    assert cwd != str(project.resolve())


@pytest.mark.parametrize("argv", _GIT_ROUTE_ARGV)
def test_git_cli_routes_plan_in_verified_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, argv: tuple[str, ...]
) -> None:
    project, checkout, description = _git_route_paths(tmp_path)
    client = _git_route_client(monkeypatch)
    result = _invoke_git_route(argv, project, checkout, description)

    assert result.exit_code == 0, result.output
    _assert_git_plan_cwd(client, project, checkout)


def test_git_raw_passthrough_routes_after_literal_delimiter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = importlib.import_module("odcli_multica.cli")
    client = _GitCliClient()
    monkeypatch.setattr(module, "_client", lambda *_args: client)
    monkeypatch.setattr(module, "_git_resource", lambda *_args: object())
    project = tmp_path / "project"
    checkout = tmp_path / "checkout"

    result = CliRunner().invoke(
        cli,
        [
            "git",
            *_git_context_options(project),
            str(checkout),
            "--",
            "status",
            "--short",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["command"] == "git"
    assert client.raw_args == ("status", "--short")
    assert client.run_count == 0


def test_git_raw_passthrough_accepts_sync_checkout_before_literal_delimiter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = importlib.import_module("odcli_multica.cli")
    client = _GitCliClient()
    monkeypatch.setattr(module, "_client", lambda *_args: client)
    monkeypatch.setattr(module, "_git_resource", lambda *_args: object())
    project = tmp_path / "project"

    result = CliRunner().invoke(
        cli,
        ["git", "sync", *_git_context_options(project), "--", "status"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["command"] == "git"
    assert client.raw_args == ("status",)
    assert client.run_count == 0


def test_git_sync_routes_named_leaf_and_preserves_dry_run_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    help_result = CliRunner().invoke(cli, ["git", "sync", "--help"])
    assert help_result.exit_code == 0
    assert "sync [OPTIONS]" in help_result.output
    assert "CHECKOUT_PATH" in help_result.output

    module = importlib.import_module("odcli_multica.cli")
    client = _GitCliClient()
    monkeypatch.setattr(module, "_client", lambda *_args: client)
    monkeypatch.setattr(module, "_git_resource", lambda *_args: object())
    project = tmp_path / "project"
    checkout = tmp_path / "checkout"

    result = CliRunner().invoke(
        cli,
        [
            "git",
            "sync",
            str(checkout),
            "--project",
            str(project),
            "--multica-project",
            "project",
            "--issue",
            "issue",
            "--run",
            "run",
            "--repository-url",
            "https://example.test/repo",
            "--base",
            "origin/main",
            "--push",
            "--format",
            "json",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["command"] == "git.sync"
    assert client.sync_options is not None
    assert client.sync_options["base"] == "origin/main"
    assert client.sync_options["push"] is True
    assert client.run_count == 0


def test_git_raw_passthrough_rejects_extra_positional_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = importlib.import_module("odcli_multica.cli")
    client = _GitCliClient()
    monkeypatch.setattr(module, "_client", lambda *_args: client)
    project = tmp_path / "project"
    checkout = tmp_path / "checkout"

    result = CliRunner().invoke(
        cli,
        [
            "git",
            *_git_context_options(project),
            str(checkout),
            "unexpected-extra",
            "--",
            "status",
        ],
    )

    assert result.exit_code == 2
    assert "exactly one CHECKOUT_PATH" in result.output
    assert client.raw_args is None
