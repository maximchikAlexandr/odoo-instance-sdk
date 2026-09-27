from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, cast

from scripts import run_mutation

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


def _mutation_job(name: str) -> str:
    workflow = _MUTATION_WORKFLOW.read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^  {re.escape(name)}:\n.*?(?=^  \w[^ ]*:|\Z)", workflow)
    assert match is not None
    return match.group()


def _uses_step(job: str, action: str) -> str:
    lines = job.splitlines()
    start = next(index for index, line in enumerate(lines) if line == f"      - uses: {action}")
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("      - ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def _run_step(job: str, command: str) -> str:
    lines = job.splitlines()
    start = next(index for index, line in enumerate(lines) if line == f"      - run: {command}")
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("      - ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def _named_step(job: str, name: str) -> str:
    lines = job.splitlines()
    start = next(index for index, line in enumerate(lines) if line == f"      - name: {name}")
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("      - ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


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


def test_ruff_combines_aliased_imports_from_the_same_module() -> None:
    config = tomllib.loads((_REPOSITORY_ROOT / "ruff.toml").read_text(encoding="utf-8"))

    assert config["lint"]["isort"]["combine-as-imports"] is True


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
    commands = run_mutation._commands()

    assert [label for label, _ in commands] == [
        "bounded mutmut integration",
        "mutmut run",
        "mutmut results",
    ]
    assert commands[0][1] == (
        sys.executable,
        str(_REPOSITORY_ROOT / "scripts" / "check_mutation_integration.py"),
    )
    assert commands[1][1] == (
        sys.executable,
        "-m",
        "mutmut",
        "run",
        "--max-children",
        "32",
    )
    assert commands[2][1] == (sys.executable, "-m", "mutmut", "results", "--all=true")


def test_mutation_workflow_fails_closed_and_uploads_diagnostics() -> None:
    workflow = _MUTATION_WORKFLOW.read_text(encoding="utf-8")

    assert re.search(r"(?ms)^on:\n  schedule:\n.*^  workflow_dispatch:\s*$", workflow)
    assert "continue-on-error" not in workflow

    prepare = _mutation_job("prepare")
    assert "    outputs:\n      matrix: ${{ steps.targets.outputs.matrix }}" in prepare
    prepare_checkout = _uses_step(
        prepare, "actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0"
    )
    assert "persist-credentials: false" in prepare_checkout
    assert (
        '      - id: targets\n        run: echo "matrix=$(python scripts/mutation_report.py matrix)"'
        in prepare
    )

    mutation = _mutation_job("mutation")
    assert re.search(r"(?m)^    needs: prepare$", mutation)
    assert re.search(r"(?m)^    timeout-minutes: 45$", mutation)
    assert "      fail-fast: false" in mutation
    assert "      matrix: ${{ fromJSON(needs.prepare.outputs.matrix) }}" in mutation
    mutation_run = _run_step(mutation, "make mutation")
    assert "MUTATION_TARGET: ${{ matrix.target }}" in mutation_run
    mutation_upload = _uses_step(mutation, "actions/upload-artifact@v4")
    assert "if: always()" in mutation_upload
    assert "name: mutation-results-${{ matrix.shard }}" in mutation_upload
    assert "path: .artifacts/mutation/results.txt" in mutation_upload
    assert "if-no-files-found: error" in mutation_upload

    aggregate = _mutation_job("aggregate")
    assert re.search(r"(?m)^    if: always\(\)$", aggregate)
    assert "    needs:\n      - prepare\n      - mutation" in aggregate
    aggregate_checkout = _uses_step(
        aggregate, "actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0"
    )
    assert "persist-credentials: false" in aggregate_checkout
    download = _uses_step(
        aggregate, "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0"
    )
    assert "pattern: mutation-results-*" in download
    assert "path: .artifacts/mutation/reports" in download
    aggregate_run = _named_step(aggregate, "Validate aggregate mutation report")
    assert "python scripts/mutation_report.py aggregate" in aggregate_run
    assert "--reports-dir .artifacts/mutation/reports" in aggregate_run
    assert "--summary .artifacts/mutation/summary.txt" in aggregate_run
    assert "--baseline .github/mutation-baseline.json" in aggregate_run
    summary_upload = _uses_step(
        aggregate, "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2"
    )
    assert "if: always()" in summary_upload
    assert "name: mutation-summary" in summary_upload
    assert "path: .artifacts/mutation/summary.txt" in summary_upload
    assert "if-no-files-found: error" in summary_upload
