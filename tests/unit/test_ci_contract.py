from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = _REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
_MUTATION_WORKFLOW = _REPOSITORY_ROOT / ".github" / "workflows" / "mutation.yml"
_MUTATION_TARGETS = [
    "src/odoo_instance_sdk/internal/redact.py",
    "src/odoo_instance_sdk/internal/sanitize.py",
    "src/odoo_instance_sdk/internal/db_name.py",
    "src/odoo_instance_sdk/internal/urls.py",
    "src/odoo_instance_sdk/internal/address.py",
]


def _test_conftest() -> ModuleType:
    script = _REPOSITORY_ROOT / "tests" / "conftest.py"
    spec = importlib.util.spec_from_file_location("repository_conftest", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dashboard_job() -> str:
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index("  dashboard-tests:")
    end = workflow.index("\n  compatibility:", start)
    return workflow[start:end]


def test_dashboard_ci_uses_the_clean_checkout_codegen_order() -> None:
    job = _dashboard_job()
    commands = (
        "uv sync --frozen --group test --extra dashboard",
        "cd src/odoo_instance_sdk/web && npm ci",
        "test ! -d src/odoo_instance_sdk/web/dist",
        "make web-codegen-check",
        "cd src/odoo_instance_sdk/web && npm test",
        "cd src/odoo_instance_sdk/web && npm run build",
    )
    positions = [job.index(command) for command in commands]
    assert positions == sorted(positions)
    assert "make dashboard" not in job


def test_core_install_contract_does_not_pull_dashboard_dependencies() -> None:
    project = tomllib.loads((_REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    core = set(project["dependencies"])
    dashboard = set(project["optional-dependencies"]["dashboard"])
    dashboard_names = {requirement.split(">", 1)[0].split("=", 1)[0] for requirement in dashboard}
    assert not core.intersection(dashboard_names)


def test_codegen_sources_are_canonical_repository_artifacts() -> None:
    openapi = _REPOSITORY_ROOT / "openapi.json"
    generated = _REPOSITORY_ROOT / "src" / "odoo_instance_sdk" / "web" / "src" / "generated"
    assert openapi.is_file()
    assert generated.is_dir()
    assert any(path.suffix == ".ts" for path in generated.iterdir())


def test_offline_selector_excludes_dashboard_dependent_nodes() -> None:
    makefile = (_REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "OFFLINE := not real_odoo and not packaging and not dashboard" in makefile
    assert '-m "$(OFFLINE) and not serial"' in makefile

    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", "not dashboard"],
        cwd=_REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    dashboard_nodes = (
        "tests/integration/test_monitor_smoke.py::",
        "tests/unit/test_openapi_export.py::",
        "tests/unit/test_http_contract.py::test_openapi_",
        "tests/unit/test_http_contract.py::test_snapshot_bytes_",
        "tests/unit/test_http_contract.py::test_sanitized_error_bytes_",
        "tests/unit/test_http_contract.py::test_unexpected_monitor_",
        "tests/unit/test_http_contract.py::test_static_free_ui_",
        "tests/unit/test_http_contract.py::test_headless_composition_",
        "tests/unit/test_http_contract.py::test_pgadmin_",
        "tests/unit/test_serve.py::test_healthz",
        "tests/unit/test_serve.py::test_snapshot_",
        "tests/unit/test_serve.py::test_headless_no_static_mount",
        "tests/unit/test_serve.py::test_ui_no_dist",
        "tests/unit/test_monitor_required_regressions.py::test_catalog_path_is_redacted_from_api_error",
        "tests/unit/test_web_codegen_check.py::test_check_codegen_accepts_",
    )
    assert not any(node in collected for node in dashboard_nodes)


def test_optional_gate_prerequisites_are_documented() -> None:
    contributing = (_REPOSITORY_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "`make package`" in contributing
    assert "`make dashboard`" in contributing
    assert "not real_odoo and not packaging and not dashboard" in contributing


def test_collection_guard_skips_dashboard_items_without_the_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conftest = _test_conftest()
    monkeypatch.setattr(conftest.importlib.util, "find_spec", lambda _name: None)

    class FakeItem:
        def __init__(self, dashboard: bool) -> None:
            self.path = Path(__file__)
            self.dashboard = dashboard
            self.markers: list[Any] = []

        def get_closest_marker(self, name: str) -> Any:
            return object() if name == "dashboard" and self.dashboard else None

        def add_marker(self, marker: Any) -> None:
            self.markers.append(marker)

    dashboard_item = FakeItem(dashboard=True)
    regular_item = FakeItem(dashboard=False)
    conftest.pytest_collection_modifyitems(
        cast("list[pytest.Item]", [dashboard_item, regular_item])
    )

    assert any(getattr(marker, "name", None) == "skip" for marker in dashboard_item.markers)
    assert not any(getattr(marker, "name", None) == "skip" for marker in regular_item.markers)


def test_make_test_recipe_fails_fast_between_verification_stages() -> None:
    makefile = _REPOSITORY_ROOT / "Makefile"
    recipe = makefile.read_text(encoding="utf-8")
    test_recipe = recipe.split("\ntest:\n", 1)[1].split("\ntargeted:\n", 1)[0]

    assert "set -e" in test_recipe


def test_mutation_configuration_copies_package_but_targets_exact_five_files() -> None:
    project = tomllib.loads((_REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    mutation = project["tool"]["mutmut"]

    assert mutation["source_paths"] == ["src/odoo_instance_sdk"]
    assert mutation["also_copy"] == [
        "scripts",
        "README.md",
        "CONTRIBUTING.md",
        "CHANGELOG.md",
        "docs",
        "openapi.json",
        ".github",
        ".agents",
        "AGENTS.md",
        "Makefile",
        "LICENSE",
        "CODE_OF_CONDUCT.md",
        "SECURITY.md",
        "examples",
        "ruff.toml",
    ]
    assert mutation["only_mutate"] == _MUTATION_TARGETS
    assert project["dependency-groups"]["mutation"] == ["mutmut>=3,<4"]
    assert mutation["pytest_add_cli_args_test_selection"] == [
        "tests/unit",
        "-m",
        "not real_odoo and not packaging and not serial",
    ]


def test_mutation_command_wires_permanent_bounded_regression() -> None:
    makefile = (_REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    runner = (_REPOSITORY_ROOT / "scripts" / "run_mutation.py").read_text(encoding="utf-8")
    harness = (_REPOSITORY_ROOT / "scripts" / "check_mutation_integration.py").read_text(
        encoding="utf-8"
    )

    assert "uv run python scripts/run_mutation.py" in makefile
    assert "check_mutation_integration.py" in runner
    assert 'ROOT / "src" / "odoo_instance_sdk"' in harness
    assert "validate_db_name*" in harness
    assert "TemporaryDirectory" in harness
    assert "PYTHONPATH" in harness


def test_mutation_command_runs_full_configured_scope_after_smoke() -> None:
    runner = (_REPOSITORY_ROOT / "scripts" / "run_mutation.py").read_text(encoding="utf-8")

    assert "AUDIT_MUTANTS" not in runner
    assert '"--max-children",\n        "32",' in runner


def test_mutation_workflow_fails_closed_and_uploads_diagnostics() -> None:
    workflow = _MUTATION_WORKFLOW.read_text(encoding="utf-8")

    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "prepare:" in workflow
    assert "fromJSON(needs.prepare.outputs.matrix)" in workflow
    assert "fail-fast: false" in workflow
    assert "timeout-minutes: 45" in workflow
    assert "make mutation" in workflow
    assert "MUTATION_TARGET" in workflow
    assert "matrix.target" in workflow
    assert "matrix.shard" in workflow
    assert "continue-on-error" not in workflow
    assert workflow.count("if: always()") >= 3
    assert "name: mutation-results-${{ matrix.shard }}" in workflow
    assert "path: .artifacts/mutation/results.txt" in workflow
    assert "if-no-files-found: error" in workflow
    assert "download-artifact@v4" in workflow
    assert "mutation_report.py aggregate" in workflow
    assert "name: mutation-summary" in workflow
