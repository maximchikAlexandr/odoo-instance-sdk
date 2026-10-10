from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src"
OPERATIONS = SOURCE / "odoo_instance_sdk" / "operations"


def _python_files(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(root.rglob("*.py")))


@pytest.mark.unit
def test_production_inventory_is_single_and_tests_use_separate_vectors() -> None:
    definitions: list[Path] = []
    for path in _python_files(SOURCE):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            targets: tuple[ast.expr, ...]
            if isinstance(node, ast.Assign):
                targets = tuple(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = (node.target,)
            else:
                continue
            if any(
                isinstance(target, ast.Name) and target.id == "PUBLIC_LEAF_CASES"
                for target in targets
            ):
                definitions.append(path.relative_to(ROOT))

    assert definitions == [Path("src/odoo_instance_sdk/operations/contracts.py")]
    fixture_source = (ROOT / "tests/unit/test_cli_output_modes.py").read_text(encoding="utf-8")
    assert "PUBLIC_LEAF_TEST_CASES = tuple(_PUBLIC_LEAF_DATA)" in fixture_source
    assert "PUBLIC_LEAF_CASES = PUBLIC_LEAF_TEST_CASES" not in fixture_source


@pytest.mark.unit
def test_production_has_no_test_imports_or_operation_lifecycle_framework() -> None:
    forbidden_imports = {
        "tests",
        "odoo_instance_sdk.daemon",
        "odoo_instance_sdk.rpc",
        "odoo_instance_sdk.telemetry",
        "odoo_instance_sdk.outbox",
    }
    lifecycle_names = {"install", "uninstall", "update", "remove", "hot_reload"}

    for path in _python_files(SOURCE):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported = {node.module}
            else:
                continue
            assert not any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for module in imported
                for forbidden in forbidden_imports
            ), f"{path}: forbidden production import"

    for path in _python_files(OPERATIONS):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert not any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in lifecycle_names
            for node in ast.walk(tree)
        ), f"{path}: provider lifecycle function found"


@pytest.mark.unit
def test_go_consumer_is_schema_only_and_has_no_domain_operation_list() -> None:
    for path in sorted((ROOT / "tools" / "go-consumer").rglob("*.go")):
        source = path.read_text(encoding="utf-8")
        assert "odcli." not in source
        assert "odoo_instance_sdk" not in source


@pytest.mark.unit
def test_operation_boundary_keeps_existing_execution_and_expression_safety_gates() -> None:
    execution = (SOURCE / "odoo_instance_sdk" / "execution.py").read_text(encoding="utf-8")
    automation = (SOURCE / "odoo_instance_sdk" / "internal" / "automation.py").read_text(
        encoding="utf-8"
    )
    operation = (OPERATIONS / "contracts.py").read_text(encoding="utf-8")

    for marker in (
        "PlanPrecondition",
        "PreparedCommand",
        "StalePlanError",
        "private executable snapshot",
    ):
        assert marker in execution
    assert "eval_expression_command" in automation
    assert "run_shell_script_command" in automation
    assert "_builtin_factory" in operation
    assert "eval_expression_command" in operation
    assert "exec_script_command" in operation


@pytest.mark.unit
def test_builtin_factory_uses_concrete_adapters_without_reflection_dispatch() -> None:
    source = (OPERATIONS / "contracts.py").read_text(encoding="utf-8")

    assert "class SdkTarget" not in source
    assert "class SdkMethod" not in source
    assert '.startswith("OdooInstance.")' not in source
    assert "getattr(target" not in source
