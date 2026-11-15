"""Focused checks for the odcli-codex core seam."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import msgspec
import pytest
from click.testing import CliRunner

from odoo_instance_sdk.catalog_validation import catalog_validation_command
from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.codex_integration import INSTALL_HINT
from odoo_instance_sdk.commands.translations import TranslationExportResult
from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.execution import ActionStep, ProcessStep, action_command
from odoo_instance_sdk.internal.executables import OptionalExecutable
from odoo_instance_sdk.resources.git import GitResource
from tests.unit.test_cli_git import _context
from tests.unit.test_git_resource import _git, _instance, _repo


def _runtime(root: Path) -> object:
    context = _context(root)
    context.worktree_path = lambda: root
    return context


def test_missing_msgfmt_is_an_action_and_present_msgfmt_keeps_one_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = tmp_path / "ru.po"
    monkeypatch.setattr(
        "odoo_instance_sdk.catalog_validation.resolve_optional_executable",
        lambda name: OptionalExecutable(name=name, path=None),
    )
    missing = catalog_validation_command(catalog)
    assert isinstance(missing.plan.steps[0], ActionStep)
    assert missing.plan.steps[0].step_id == "translations.msgfmt.absent"
    assert missing.run().status == "skipped"

    monkeypatch.setattr(
        "odoo_instance_sdk.catalog_validation.resolve_optional_executable",
        lambda name: OptionalExecutable(name=name, path="/usr/bin/msgfmt"),
    )
    present = catalog_validation_command(catalog)
    step = present.plan.steps[0]
    assert isinstance(step, ProcessStep)
    assert step.argv[1:] == ("--check", "--statistics", "-o", "/dev/null", str(catalog))


def test_staged_patch_redacts_secrets_without_touching_the_index(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "secret.txt").write_text("api_key=supersecretvalue\n", encoding="utf-8")
    _git(tmp_path, "add", "secret.txt")
    before = _git(tmp_path, "write-tree").strip()
    resource = GitResource(_instance(tmp_path))
    command = resource.staged_patch_command()
    plan = msgspec.json.encode(command.plan).decode()
    patch = command.run()

    assert _git(tmp_path, "write-tree").strip() == before
    assert "supersecretvalue" not in plan
    assert "supersecretvalue" not in patch.text
    assert "<redacted>" in patch.text


def test_core_metadata_and_extension_sources_stay_isolated() -> None:
    root = Path("pyproject.toml")
    metadata = tomllib.loads(root.read_text(encoding="utf-8"))
    assert all("openai-codex" not in item for item in metadata["project"]["dependencies"])
    offenders = [
        str(path)
        for path in Path("packages/odcli-codex").rglob("*.py")
        if "odoo_instance_sdk.internal" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def _export_command(path: Path) -> object:
    result = [
        TranslationExportResult(
            module="sale",
            requested_lang="ru_RU",
            actual_filename="ru.po",
            path=path,
            bytes_written=4,
        )
    ]
    return action_command(
        (
            ActionStep(
                step_id="translations.export",
                action="export",
                description="Export catalogs",
                read_only=True,
            ),
        ),
        (lambda: None,),
        lambda: result,
    )


@pytest.mark.parametrize(
    ("extra", "calls_filler"),
    [((), False), (("--auto-translate", "--dry-run"), False), (("--auto-translate",), True)],
)
def test_auto_translate_fills_only_after_a_live_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: tuple[str, ...], calls_filler: bool
) -> None:
    catalog = tmp_path / "ru.po"
    seen: list[Path] = []
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.translations.cli_context.ready_instance",
        lambda _ctx: _runtime(tmp_path),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.translations.export_translations_command",
        lambda *_args, **_kwargs: _export_command(catalog),
    )

    def filler(paths: tuple[Path, ...], **_kwargs: object) -> tuple[()]:
        seen.extend(paths)
        return ()

    monkeypatch.setattr("odoo_instance_sdk.commands.translations.load_catalog_fill", lambda: filler)
    result = CliRunner().invoke(
        cli,
        [
            "translations",
            "export",
            "--module",
            "sale",
            "--language",
            "ru_RU",
            *extra,
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["ok"] is True
    assert seen == ([catalog] if calls_filler else [])


def test_auto_translate_without_the_extension_emits_the_install_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.translations.cli_context.ready_instance",
        lambda _ctx: _runtime(tmp_path),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.translations.export_translations_command",
        lambda *_args, **_kwargs: _export_command(tmp_path / "ru.po"),
    )

    def missing() -> object:
        raise ConfigError(INSTALL_HINT)

    monkeypatch.setattr("odoo_instance_sdk.commands.translations.load_catalog_fill", missing)
    result = CliRunner().invoke(
        cli,
        [
            "translations",
            "export",
            "--module",
            "sale",
            "--language",
            "ru_RU",
            "--auto-translate",
            "--format",
            "json",
        ],
    )

    assert result.exit_code != 0
    assert INSTALL_HINT in result.output
    assert result.stdout.count("{") == result.stdout.count("}")


@pytest.mark.parametrize(
    "args",
    [
        ("git", "commit", "change", "--generate"),
        ("git", "commit", "--context", "notes.md"),
        ("git", "commit", "--model", "gpt"),
        ("git", "commit"),
        ("git", "commit", "--generate", "--repo", "/tmp/repo", "--member-id", "user"),
    ],
)
def test_git_commit_rejects_ambiguous_description_sources(args: tuple[str, ...]) -> None:
    result = CliRunner().invoke(cli, [*args])

    assert result.exit_code == 2, result.output


def test_generated_and_piped_descriptions_share_one_commit_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo(tmp_path)
    (tmp_path / "change.txt").write_text("change\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.git.cli_context.ready_instance",
        lambda _ctx: _runtime(tmp_path),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.git.cli_context.resolve_project_path",
        lambda _ctx: tmp_path,
    )
    flags = ["--ticket", "PROJ-123", "--tag", "IMP", "--yes", "--format", "json"]
    piped = CliRunner().invoke(cli, ["git", "commit", "-", *flags], input="change\n")
    piped_message = _git(tmp_path, "log", "-1", "--format=%s").strip()
    (tmp_path / "change.txt").write_text("change again\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.git.generated_description",
        lambda *_args, **_kwargs: "change",
    )
    generated = CliRunner().invoke(cli, ["git", "commit", "--generate", *flags])

    assert piped.exit_code == 0, piped.output
    assert generated.exit_code == 0, generated.output
    assert _git(tmp_path, "log", "-1", "--format=%s").strip() == piped_message


def test_missing_extension_does_not_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _repo(tmp_path)
    (tmp_path / "change.txt").write_text("change\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")
    head = _git(tmp_path, "rev-parse", "HEAD").strip()
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.git.cli_context.ready_instance",
        lambda _ctx: _runtime(tmp_path),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.git.cli_context.resolve_project_path",
        lambda _ctx: tmp_path,
    )
    monkeypatch.setitem(sys.modules, "odcli_codex.integration", None)
    result = CliRunner().invoke(cli, ["git", "commit", "--generate", "--yes", "--format", "json"])

    assert result.exit_code != 0, result.output
    assert INSTALL_HINT in result.output
    assert _git(tmp_path, "rev-parse", "HEAD").strip() == head


def test_generate_dry_run_previews_the_request_without_committing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("odcli_codex")
    _repo(tmp_path)
    (tmp_path / "change.txt").write_text("UNIQUE_PATCH_BODY\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")
    head = _git(tmp_path, "rev-parse", "HEAD").strip()
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.git.cli_context.ready_instance",
        lambda _ctx: _runtime(tmp_path),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.git.cli_context.resolve_project_path", lambda _ctx: tmp_path
    )
    (tmp_path / ".odcli").mkdir()
    (tmp_path / ".odcli" / "project.toml").write_text(
        '[codex]\nmodel = "test-model"\n', encoding="utf-8"
    )
    result = CliRunner().invoke(
        cli, ["git", "commit", "--generate", "--dry-run", "--format", "json"]
    )
    payload = json.loads(result.stdout)

    assert result.exit_code == 0, result.output
    assert payload["dry_run"] is True
    encoded = json.dumps(payload)
    assert "UNIQUE_PATCH_BODY" not in encoded
    assert "codex.git-message" in encoded
    assert _git(tmp_path, "rev-parse", "HEAD").strip() == head
