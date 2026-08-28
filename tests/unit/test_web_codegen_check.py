from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _checker() -> ModuleType:
    script = Path(__file__).parents[2] / "scripts" / "check_web_codegen.py"
    spec = importlib.util.spec_from_file_location("check_web_codegen", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture_tree(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path, Path]:
    committed_openapi = tmp_path / "openapi.json"
    first_openapi = tmp_path / "first-openapi.json"
    second_openapi = tmp_path / "second-openapi.json"
    committed_generated = tmp_path / "committed-generated"
    first = tmp_path / "first-generated"
    second = tmp_path / "second-generated"
    canonical = b'{"openapi": "3.1.0"}\n'
    committed_openapi.write_bytes(canonical)
    first_openapi.write_bytes(canonical)
    second_openapi.write_bytes(canonical)
    committed_generated.mkdir()
    first.mkdir()
    second.mkdir()
    (committed_generated / "index.ts").write_bytes(b"// @generated\n")
    (first / "index.ts").write_bytes(b"// @generated\n")
    (second / "index.ts").write_bytes(b"// @generated\n")
    return committed_openapi, committed_generated, first_openapi, second_openapi, first, second


def test_verify_outputs_accepts_clean_deterministic_output(tmp_path: Path) -> None:
    checker = _checker()
    committed_openapi, committed_generated, first_openapi, second_openapi, first, second = (
        _fixture_tree(tmp_path)
    )

    checker._verify_outputs(
        first_openapi,
        second_openapi,
        first,
        second,
        committed_openapi,
        committed_generated,
    )


def test_verify_outputs_reports_stale_openapi_without_mutation(tmp_path: Path) -> None:
    checker = _checker()
    committed_openapi, committed_generated, first_openapi, second_openapi, first, second = (
        _fixture_tree(tmp_path)
    )
    committed_openapi.write_bytes(b'{"openapi": "3.0.0"}\n')
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    expected_error = "stale committed OpenAPI"

    with pytest.raises(checker.CodegenCheckError, match=expected_error):
        checker._verify_outputs(
            first_openapi,
            second_openapi,
            first,
            second,
            committed_openapi,
            committed_generated,
        )

    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before


def test_verify_outputs_reports_nondeterminism_without_mutation(tmp_path: Path) -> None:
    checker = _checker()
    committed_openapi, committed_generated, first_openapi, second_openapi, first, second = (
        _fixture_tree(tmp_path)
    )
    (second / "index.ts").write_bytes(b"// @generated\nchanged\n")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    with pytest.raises(checker.CodegenCheckError, match="nondeterministic generated output"):
        checker._verify_outputs(
            first_openapi,
            second_openapi,
            first,
            second,
            committed_openapi,
            committed_generated,
        )

    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before
    assert (first / "index.ts").read_bytes() == b"// @generated\n"
    assert (second / "index.ts").read_bytes() == b"// @generated\nchanged\n"


def test_verify_outputs_reports_nondeterministic_openapi_without_mutation(tmp_path: Path) -> None:
    checker = _checker()
    committed_openapi, committed_generated, first_openapi, second_openapi, first, second = (
        _fixture_tree(tmp_path)
    )
    second_openapi.write_bytes(b'{"openapi": "3.0.0"}\n')
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    with pytest.raises(checker.CodegenCheckError, match="nondeterministic OpenAPI output"):
        checker._verify_outputs(
            first_openapi,
            second_openapi,
            first,
            second,
            committed_openapi,
            committed_generated,
        )

    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


def test_verify_outputs_reports_stale_generated_output_without_mutation(tmp_path: Path) -> None:
    checker = _checker()
    committed_openapi, committed_generated, first_openapi, second_openapi, first, second = (
        _fixture_tree(tmp_path)
    )
    (first / "index.ts").write_bytes(b"// @generated\nstale\n")
    (second / "index.ts").write_bytes(b"// @generated\nstale\n")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    with pytest.raises(checker.CodegenCheckError, match="stale committed generated output"):
        checker._verify_outputs(
            first_openapi,
            second_openapi,
            first,
            second,
            committed_openapi,
            committed_generated,
        )

    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


@pytest.mark.dashboard
def test_check_codegen_accepts_the_committed_worktree() -> None:
    _checker().check_codegen()
