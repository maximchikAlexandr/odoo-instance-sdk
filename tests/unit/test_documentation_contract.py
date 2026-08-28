from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import click
import pytest

from odoo_instance_sdk.cli import cli

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
SDK_DOC = ROOT / "docs" / "python-sdk.md"
INVENTORY = re.compile(
    r"<!-- cli-command-inventory:start -->(.*?)<!-- cli-command-inventory:end -->",
    re.DOTALL,
)
COMMAND = re.compile(r"^- `odcli ([^`]+)` — \S.+\.$", re.MULTILINE)
FENCE = re.compile(r"^```([^\n]*)\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL)
LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def _leaf_commands(group: click.Group, prefix: tuple[str, ...] = ()) -> set[str]:
    leaves: set[str] = set()
    for name, command in group.commands.items():
        path = (*prefix, name)
        if isinstance(command, click.Group):
            leaves.update(_leaf_commands(command, path))
        else:
            leaves.add(" ".join(path))
    return leaves


def _fences(path: Path, language: str) -> tuple[str, ...]:
    text = path.read_text(encoding="utf-8")
    return tuple(body for info, body in FENCE.findall(text) if info.strip() == language)


@pytest.mark.unit
def test_readme_command_inventory_matches_click_tree() -> None:
    match = INVENTORY.search(README.read_text(encoding="utf-8"))
    assert match is not None
    documented = COMMAND.findall(match.group(1))
    assert len(documented) == len(set(documented)), "duplicate documented command"
    assert set(documented) == _leaf_commands(cli)


@pytest.mark.unit
def test_python_examples_compile_and_import_public_package() -> None:
    examples = _fences(README, "python") + _fences(SDK_DOC, "python")
    assert examples
    for index, source in enumerate(examples):
        tree = ast.parse(source, filename=f"python-example-{index}.py")
        compile(tree, f"python-example-{index}.py", "exec")
        imports: list[ast.stmt] = [
            node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        import_module = ast.Module(body=imports, type_ignores=[])
        exec(compile(import_module, f"python-imports-{index}.py", "exec"), {})


@pytest.mark.unit
@pytest.mark.parametrize("path", [README, ROOT / "CONTRIBUTING.md"])
def test_shell_examples_parse(path: Path) -> None:
    examples = _fences(path, "bash")
    assert examples
    for source in examples:
        subprocess.run(["bash", "-n"], input=source, text=True, check=True)


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [README, ROOT / "CONTRIBUTING.md", ROOT / "CHANGELOG.md", SDK_DOC],
)
def test_relative_markdown_links_resolve(path: Path) -> None:
    for target in LINK.findall(path.read_text(encoding="utf-8")):
        if "://" in target or target.startswith("#"):
            continue
        relative = target.split("#", 1)[0]
        assert (path.parent / relative).exists(), f"{path}: missing {target}"
