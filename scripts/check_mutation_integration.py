"""Prove the installed mutmut process can import the copied package."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "mutation_smoke"
MUTANT_FILTER = "odoo_instance_sdk.internal.db_name.validate_db_name*"
TERMINAL_STATUSES = ("killed", "survived", "timeout", "suspicious")


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["NO_COLOR"] = "1"
    return environment


def _mutmut_command(*args: str) -> list[str]:
    executable = shutil.which("mutmut")
    if executable:
        return [executable, *args]
    return [sys.executable, "-m", "mutmut", *args]


def _mutant_filter() -> str:
    # mutmut 3.7 mangles function names with an ``x_`` prefix internally.
    return MUTANT_FILTER.replace(".validate_db_name", ".x_validate_db_name")


def _copy_fixture(workspace: Path, test_name: str) -> None:
    shutil.copytree(ROOT / "src" / "odoo_instance_sdk", workspace / "src" / "odoo_instance_sdk")
    config = (FIXTURE / "pyproject.toml").read_text(encoding="utf-8")
    config = config.replace("tests/test_passing.py", f"tests/{test_name}")
    (workspace / "pyproject.toml").write_text(config, encoding="utf-8")
    (workspace / "tests").mkdir()
    shutil.copy(FIXTURE / test_name, workspace / "tests" / test_name)


def _run(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _mutmut_command(*args),
        cwd=workspace,
        env=_environment(),
        capture_output=True,
        text=True,
        check=False,
    )


def _combined(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stdout or "") + (result.stderr or "")


def _assert_success_case(workspace: Path) -> None:
    run = _run(workspace, "run", _mutant_filter())
    if run.returncode != 0:
        raise RuntimeError(f"bounded mutmut run failed:\n{_combined(run)}")
    results = _run(workspace, "results", "--all=true")
    report = _combined(results)
    if results.returncode != 0 or not report.strip():
        raise RuntimeError(f"bounded mutmut results failed or was empty:\n{report}")
    if not any(status in report for status in TERMINAL_STATUSES):
        raise RuntimeError(f"bounded mutmut report has no terminal classification:\n{report}")


def _assert_collection_failure(workspace: Path) -> None:
    run = _run(workspace, "run", _mutant_filter())
    report = _combined(run)
    if run.returncode == 0:
        raise RuntimeError(f"collection failure unexpectedly passed:\n{report}")
    if not report.strip():
        raise RuntimeError("collection failure produced an empty mutmut report")
    if "collection" not in report.lower() and "error" not in report.lower():
        raise RuntimeError(f"collection failure report lacks diagnostics:\n{report}")


def main() -> int:
    with TemporaryDirectory(prefix="odoo-mutmut-smoke-") as temporary:
        root = Path(temporary)
        success = root / "success"
        success.mkdir()
        _copy_fixture(success, "test_passing.py")
        _assert_success_case(success)

        failure = root / "failure"
        failure.mkdir()
        _copy_fixture(failure, "collection_failure.py")
        _assert_collection_failure(failure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
