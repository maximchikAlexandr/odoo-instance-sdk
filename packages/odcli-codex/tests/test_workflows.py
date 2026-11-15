"""Package tests for catalog splicing, context limits, and injected Codex turns."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import msgspec
import pytest
from click.testing import CliRunner

from odcli_codex.cli import cli
from odcli_codex.client import CodexOdooClient
from odcli_codex.config import effective_model, load_settings
from odcli_codex.context_files import select_context
from odcli_codex.errors import CodexWorkflowError
from odcli_codex.po_catalog import CatalogEntry, apply_translations, read_catalog
from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.resources.git_runtime import repository_resource

_CATALOG = """msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"
"Language: ru_RU\\n"

msgid "Hello %s"
msgstr ""

#, fuzzy
msgid "Fuzzy"
msgstr "Нечеткое"

msgid "Done"
msgstr "Готово"
"""


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ | {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _project(root: Path, *, model: str = "test-model", limit: int = 262144) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "tests@example.test")
    _git(root, "config", "user.name", "tests")
    manifest = root / ".odcli"
    manifest.mkdir()
    (manifest / "project.toml").write_text(
        f'[codex]\nmodel = "{model}"\nmax_context_bytes = {limit}\n',
        encoding="utf-8",
    )
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md", ".odcli/project.toml")
    _git(root, "commit", "-qm", "base")


def _client(complete: Callable[[str, str], str]) -> CodexOdooClient:
    return CodexOdooClient(
        OdooClient(config=OdooClientConfig(executable="odoo")),
        complete=complete,
        listed_models=lambda: ("test-model",),
    )


def _plan(command: object) -> str:
    return msgspec.json.encode(getattr(command, "plan")).decode()


def test_settings_and_language_defaults(tmp_path: Path) -> None:
    assert load_settings(tmp_path).max_context_bytes == 262144
    _project(tmp_path, model="from-project")
    settings = load_settings(tmp_path)
    assert effective_model(settings, None) == "from-project"
    assert effective_model(settings, "cli-model") == "cli-model"
    with pytest.raises(CodexWorkflowError, match=r"set \[codex\].model"):
        effective_model(load_settings(tmp_path / "missing"), None)


@pytest.mark.parametrize(
    ("proposal", "rejected", "filled"),
    [
        ({"Hello %s": "Привет %s"}, (), 1),
        ({"Hello %s": "Привет"}, ("Hello %s",), 0),
        ({"Fuzzy": "Другое"}, (), 0),
    ],
)
def test_empty_entries_are_filled_and_unsafe_replacements_stay_empty(
    tmp_path: Path, proposal: dict[str, str], rejected: tuple[str, ...], filled: int
) -> None:
    catalog = tmp_path / "ru.po"
    catalog.write_text(_CATALOG, encoding="utf-8")
    _original, entries = read_catalog(catalog)
    updated, actual_rejected, actual_filled = apply_translations(entries, proposal)

    assert actual_rejected == rejected
    assert actual_filled == filled
    assert 'msgid "Fuzzy"' in updated
    assert "Нечеткое" in updated
    if filled:
        assert 'msgstr "Привет %s"' in updated
    else:
        assert 'msgstr "Привет' not in updated


@pytest.mark.parametrize(
    ("proposal", "filled"),
    [("Строка", 0), ("Строка\n", 1)],
)
def test_newline_escapes_must_survive_splicing(tmp_path: Path, proposal: str, filled: int) -> None:
    catalog = tmp_path / "ru.po"
    catalog.write_text('msgid "Line\\n"\nmsgstr ""\n', encoding="utf-8")
    _original, entries = read_catalog(catalog)
    updated, rejected, actual_filled = apply_translations(entries, {"Line\n": proposal})

    assert actual_filled == filled
    assert rejected == (() if filled else ("Line\n",))
    if filled:
        assert 'msgstr "Строка\\n"' in updated


def test_empty_plural_form_uses_the_singular_source(tmp_path: Path) -> None:
    catalog = tmp_path / "ru.po"
    catalog.write_text(
        'msgid "One"\nmsgid_plural "%d files"\nmsgstr[0] ""\nmsgstr[1] "файлов %d"\n',
        encoding="utf-8",
    )
    _original, entries = read_catalog(catalog)
    updated, rejected, filled = apply_translations(entries, {"One": ("Один", "файлов %d")})

    assert filled == 1
    assert rejected == ()
    assert 'msgstr[0] "Один"' in updated
    assert 'msgstr[1] "файлов %d"' in updated


def test_message_does_not_plan_the_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path)
    (tmp_path / "change.txt").write_text("staged\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")

    def refuse(self: object, description: str, **_kwargs: object) -> object:
        raise AssertionError(description)

    monkeypatch.setattr("odoo_instance_sdk.resources.git.GitResource.commit_context", refuse)
    text = _client(lambda _model, _prompt: "fix the total").git.message(
        repository_resource(tmp_path), project_root=tmp_path, model="test-model"
    )

    assert text == "fix the total"


def test_rejected_fill_keeps_the_catalog_bytes(tmp_path: Path) -> None:
    _project(tmp_path)
    path = tmp_path / "ru.po"
    path.write_text('msgid "Hello %s"\nmsgstr ""\n', encoding="utf-8")
    original = path.read_bytes()

    def complete(_model: str, _prompt: str) -> str:
        return json.dumps({"translations": {"Hello %s": "Привет"}})

    result = _client(complete).translations.fill(path, project_root=tmp_path, model="test-model")

    assert result.filled == 0
    assert result.rejected == ("Hello %s",)
    assert path.read_bytes() == original


def test_failed_line_replace_is_rejected() -> None:
    entry = CatalogEntry('msgid "A"\nmsgstr "kept"', {"msgid": "A", "msgstr": ""}, False)
    _text, rejected, filled = apply_translations((entry,), {"A": "B"})

    assert rejected == ("A",)
    assert filled == 0


@pytest.mark.parametrize(
    ("name", "text", "match"),
    [
        ("outside", "text\n", "outside the project"),
        ("credentials.json", "ok\n", "looks secret"),
        ("notes.md", "api_key=hidden\n", "looks secret"),
        ("notes.md", "x" * 20, "exceeds"),
    ],
)
def test_context_selection_rejects_unsafe_files(
    tmp_path: Path, name: str, text: str, match: str
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _project(root, limit=8)
    if name == "outside":
        selection = str(tmp_path / "outside.txt")
        (tmp_path / "outside.txt").write_text(text, encoding="utf-8")
    else:
        selection = name
        (root / name).write_text(text, encoding="utf-8")
        _git(root, "add", name)
        _git(root, "commit", "-qm", "context")

    with pytest.raises(CodexWorkflowError, match=match):
        select_context(root, (selection,), limit_bytes=8)


def test_fill_dry_run_does_not_call_codex_or_write(tmp_path: Path) -> None:
    _project(tmp_path)
    catalog = tmp_path / "ru.po"
    catalog.write_text(_CATALOG, encoding="utf-8")
    original = catalog.read_text(encoding="utf-8")
    calls: list[str] = []

    def complete(_model: str, prompt: str) -> str:
        calls.append(prompt)
        return "{}"

    command = _client(complete).translations.fill_command(
        catalog, project_root=tmp_path, model="test-model"
    )

    assert calls == []
    assert catalog.read_text(encoding="utf-8") == original
    assert "Glossary" not in _plan(command)
    assert "Hello %s" not in _plan(command)


def test_fill_rejects_a_bad_placeholder_and_writes_the_valid_entry(tmp_path: Path) -> None:
    _project(tmp_path)
    path = tmp_path / "ru.po"
    path.write_text(
        _CATALOG.replace('msgid "Done"\nmsgstr "Готово"', 'msgid "Save"\nmsgstr ""'),
        encoding="utf-8",
    )

    def complete(_model: str, _prompt: str) -> str:
        return json.dumps({"translations": {"Hello %s": "Привет", "Save": "Сохранить"}})

    result = _client(complete).translations.fill(path, project_root=tmp_path, model="test-model")
    written = path.read_text(encoding="utf-8")

    assert result.rejected == ("Hello %s",)
    assert result.filled == 1
    assert 'msgstr "Сохранить"' in written
    assert 'msgid "Hello %s"\nmsgstr ""' in written


def test_message_plan_omits_the_patch_and_rejects_unsafe_lines(tmp_path: Path) -> None:
    _project(tmp_path)
    (tmp_path / "change.txt").write_text("UNIQUE_PATCH_BODY\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")
    calls: list[str] = []

    def unused(_model: str, prompt: str) -> str:
        calls.append(prompt)
        return "fix the total"

    resource = repository_resource(tmp_path)
    command = _client(unused).git.message_command(
        resource, project_root=tmp_path, model="test-model"
    )

    assert calls == []
    assert "UNIQUE_PATCH_BODY" not in _plan(command)

    def unsafe(_model: str, _prompt: str) -> str:
        return "bad\nbad"

    with pytest.raises(CodexWorkflowError, match="one non-empty line"):
        _client(unsafe).git.message(resource, project_root=tmp_path, model="test-model")


def test_plain_stdout_and_fill_dry_run_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path)
    catalog = tmp_path / "ru.po"
    catalog.write_text(_CATALOG, encoding="utf-8")
    (tmp_path / "change.txt").write_text("staged\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")
    monkeypatch.setattr(
        "odcli_codex.cli._client",
        lambda: _client(lambda _model, _prompt: "fix the total"),
    )
    monkeypatch.setattr("odcli_codex.cli.resolve_project_path", lambda _ctx: tmp_path)
    runner = CliRunner()
    plain = runner.invoke(cli, ["git", "message", "--plain"])
    dry = runner.invoke(
        cli,
        ["translations", "fill", str(catalog), "--dry-run", "--format", "json"],
    )

    assert plain.exit_code == 0, plain.output
    assert plain.stdout == "fix the total\n"
    assert dry.exit_code == 0, dry.output
    assert catalog.read_text(encoding="utf-8") == _CATALOG
    assert "Glossary" not in dry.stdout
