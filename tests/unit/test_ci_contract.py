from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = _REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"


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
