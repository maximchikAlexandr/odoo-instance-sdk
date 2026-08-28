"""Check canonical OpenAPI and generated frontend sources without worktree writes."""

from __future__ import annotations

import os
import subprocess
import tempfile
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = REPOSITORY_ROOT / "src" / "odoo_instance_sdk" / "web"
COMMITTED_OPENAPI = REPOSITORY_ROOT / "openapi.json"
COMMITTED_GENERATED = WEB_ROOT / "src" / "generated"


def _export_openapi(path: Path) -> Path:
    exporter_path = Path(__file__).with_name("export_openapi.py")
    spec = spec_from_file_location("odoo_sdk_export_openapi", exporter_path)
    if spec is None or spec.loader is None:
        raise CodegenCheckError("cannot load OpenAPI exporter")
    exporter: Any = module_from_spec(spec)
    spec.loader.exec_module(exporter)
    return cast("Path", exporter.export_openapi(path))


class CodegenCheckError(RuntimeError):
    """Raised when generated output is stale or nondeterministic."""


def _files(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def _verify_file(actual: Path, expected: Path, reason: str) -> None:
    if not actual.exists():
        raise CodegenCheckError(f"{reason}: missing {actual.name}")
    if not expected.exists():
        raise CodegenCheckError(f"{reason}: missing committed {expected.name}")
    if actual.read_bytes() != expected.read_bytes():
        raise CodegenCheckError(f"{reason}: {expected.name} differs")


def _verify_tree(actual: Path, expected: Path, reason: str) -> None:
    actual_files = _files(actual)
    expected_files = _files(expected)
    if actual_files != expected_files:
        missing = sorted(str(path) for path in expected_files - actual_files)
        extra = sorted(str(path) for path in actual_files - expected_files)
        details = ", ".join([f"missing: {missing}"] if missing else [])
        if extra:
            details = f"{details}; " if details else ""
            details += f"extra: {extra}"
        raise CodegenCheckError(f"{reason}: generated file set differs ({details})")
    for relative_path in sorted(actual_files):
        if (actual / relative_path).read_bytes() != (expected / relative_path).read_bytes():
            raise CodegenCheckError(f"{reason}: generated/{relative_path} differs")


def _verify_outputs(
    first_openapi: Path,
    second_openapi: Path,
    first_generated: Path,
    second_generated: Path,
    committed_openapi: Path,
    committed_generated: Path,
) -> None:
    _verify_file(first_openapi, second_openapi, "nondeterministic OpenAPI output")
    _verify_tree(first_generated, second_generated, "nondeterministic generated output")
    _verify_file(first_openapi, committed_openapi, "stale committed OpenAPI")
    _verify_tree(first_generated, committed_generated, "stale committed generated output")


def _run_once(run_root: Path) -> tuple[Path, Path]:
    run_root.mkdir()
    openapi_path = run_root / "openapi.json"
    generated_path = run_root / "generated"
    _export_openapi(openapi_path)
    environment = os.environ.copy()
    environment.update(
        {
            "OPENAPI_TS_INPUT": str(openapi_path),
            "OPENAPI_TS_OUTPUT": str(generated_path),
        }
    )
    try:
        subprocess.run(
            ["npx", "--no-install", "openapi-ts", "--silent"],
            cwd=WEB_ROOT,
            env=environment,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        raise CodegenCheckError("frontend generation failed") from error
    return openapi_path, generated_path


def check_codegen() -> None:
    """Run two isolated exports/generations and compare them to the worktree."""
    with tempfile.TemporaryDirectory(prefix="odoo-sdk-web-codegen-") as temporary:
        temporary_root = Path(temporary)
        first_openapi, first_generated = _run_once(temporary_root / "first")
        second_openapi, second_generated = _run_once(temporary_root / "second")
        _verify_outputs(
            first_openapi,
            second_openapi,
            first_generated,
            second_generated,
            COMMITTED_OPENAPI,
            COMMITTED_GENERATED,
        )


if __name__ == "__main__":
    check_codegen()
