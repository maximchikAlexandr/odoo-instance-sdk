from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

from odoo_instance_sdk import __all__ as SDK_EXPORTS
from odoo_instance_sdk import models
from odoo_instance_sdk.commands.output import OutputMode, build_envelope


def _package_root() -> Path:
    spec = importlib.util.find_spec("odoo_instance_sdk")
    assert spec is not None and spec.origin is not None
    return Path(spec.origin).parent


def _python_files(relative_dir: str) -> tuple[Path, ...]:
    directory = _package_root() / relative_dir
    return tuple(sorted(directory.rglob("*.py"))) if directory.is_dir() else ()


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


@pytest.mark.unit
def test_public_resources_and_workflows_are_transport_free() -> None:
    files = _python_files("resources") + _python_files("workflows")
    assert files, "public resource package must be discoverable"
    forbidden = ("click", "fastapi")
    for path in files:
        imported = _imported_modules(path)
        assert not any(
            module == name or module.startswith(f"{name}.")
            for module in imported
            for name in forbidden
        ), path


@pytest.mark.unit
def test_cli_output_policy_is_not_a_public_or_fastapi_model() -> None:
    assert "OutputMode" not in SDK_EXPORTS
    assert not hasattr(models, "OutputMode")
    assert set(OutputMode) == {OutputMode.RICH, OutputMode.JSON, OutputMode.TOON}

    serve = _package_root() / "internal" / "serve.py"
    serve_source = serve.read_text(encoding="utf-8")
    assert "commands.output" not in serve_source
    assert "build_envelope" not in serve_source
    assert "OutputMode" not in serve_source
    envelope = build_envelope(ok=True, command="boundary", result={"value": 1})
    assert envelope["result"] == envelope["data"]


@pytest.mark.unit
def test_boundary_has_no_generic_framework_modules_or_types() -> None:
    root = _package_root()
    forbidden_parts = {
        "application",
        "container",
        "di",
        "registry",
        "registries",
        "rendering",
    }
    assert not any(path.name in forbidden_parts for path in root.iterdir())

    for path in (root / "commands").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        classes = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
        assert not classes.intersection({"Application", "Container", "Registry", "Renderer"}), path


@pytest.mark.unit
def test_readme_describes_supported_cli_boundary_without_overpromising() -> None:
    readme = Path(__file__).resolve().parents[2] / "README.md"
    text = readme.read_text(encoding="utf-8")
    required = (
        "--format rich|json|toon",
        "--json",
        "TOON",
        "--watch",
        "--interval",
        "interactive TTY",
        "active-only",
        "observed_port",
        "artifacts",
        "odoo_instance_sdk.cli:cli",
    )
    for phrase in required:
        assert phrase in text, phrase

    assert "Supplying `--json` with `--format json`" in text
    assert "root command" in text
    assert "logs --follow" in text
    assert "interactive shell" in text
