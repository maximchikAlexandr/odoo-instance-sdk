from __future__ import annotations

import json
from pathlib import Path

import msgspec
import pytest

import odoo_instance_sdk
from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.test_selection import (
    normalize_eligible_roots,
    resolve_test_selection,
)
from odoo_instance_sdk.models import OdooTestResult, OdooTestSpec, StartConfig


def _addon(root: Path, name: str, *, tests: bool = True) -> Path:
    module = root / name
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}\n")
    if tests:
        (module / "tests").mkdir()
        (module / "tests" / "test_order.py").write_text("# test\n")
    return module


def test_public_test_models_are_frozen_serializable_and_exported() -> None:
    spec = OdooTestSpec(
        modules=("sale", "stock"),
        test_tags=":TestSale.test_confirm",
        reload_tests=True,
        allow_empty=True,
    )
    result = OdooTestResult(
        counts={"tests": 3, "successful": 2, "failed": 1, "errors": 0, "skipped": 0},
        failures=True,
        zero_tests=False,
        exit_code=1,
    )

    assert odoo_instance_sdk.OdooTestSpec is OdooTestSpec
    assert odoo_instance_sdk.OdooTestResult is OdooTestResult
    assert "TestResource" not in odoo_instance_sdk.__all__
    assert msgspec.json.decode(msgspec.json.encode(spec), type=OdooTestSpec) == spec
    assert msgspec.json.decode(msgspec.json.encode(result), type=OdooTestResult) == result
    with pytest.raises((AttributeError, TypeError)):
        spec.test_tags = "/stock"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("modules", "tags"),
    [
        ((), "/sale"),
        (("stock", "sale"), "/sale"),
        (("sale", "sale"), "/sale"),
        (("sale",), " "),
    ],
)
def test_spec_rejects_invalid_operation_boundary(modules: tuple[str, ...], tags: str) -> None:
    with pytest.raises(ValueError):
        OdooTestSpec(modules=modules, test_tags=tags)


@pytest.mark.parametrize(
    "counts",
    [
        {"tests": 1, "successful": 1, "failed": 0, "errors": 0},
        {"tests": 1, "successful": 1, "failed": 0, "errors": 0, "skipped": 0, "extra": 0},
        {"tests": 1, "successful": True, "failed": 0, "errors": 0, "skipped": 0},
    ],
)
def test_result_requires_exact_non_negative_integer_count_surface(
    counts: dict[str, int],
) -> None:
    with pytest.raises(ValueError):
        OdooTestResult(counts=counts, failures=False, zero_tests=False, exit_code=0)


def test_relative_roots_are_resolved_and_unsafe_roots_are_rejected(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    safe = worktree / "addons"
    safe.mkdir()
    _addon(safe, "sale")
    external = tmp_path / "external"
    external.mkdir()
    missing = worktree / "missing"

    roots = normalize_eligible_roots(
        worktree,
        StartConfig(addons_path=["addons", str(external), str(missing)]),
    )

    assert roots == (safe,)
    selection = resolve_test_selection(
        worktree,
        ["addons", str(external), str(missing)],
        target="sale",
        cwd=worktree,
    )
    assert selection.rejected_roots
    assert [diagnostic.configured for diagnostic in selection.rejected_roots] == [
        str(external),
        str(missing),
    ]


def test_external_root_cannot_supply_module(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    _addon(external, "sale")

    with pytest.raises(ConfigError, match="not found in safe configured roots"):
        resolve_test_selection(worktree, [str(external)], target="sale", cwd=worktree)


def test_duplicate_module_diagnostics_are_safe_and_deterministic(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    first = worktree / "addons_a"
    second = worktree / "addons_b"
    first.mkdir()
    second.mkdir()
    _addon(first, "sale")
    _addon(second, "sale")

    with pytest.raises(ConfigError) as error:
        resolve_test_selection(worktree, [str(second), str(first)], target="sale", cwd=worktree)

    message = str(error.value)
    assert message.index(str(first / "sale")) < message.index(str(second / "sale"))


def test_symlinked_root_module_and_manifest_never_become_candidates(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    roots = worktree / "addons"
    roots.mkdir()
    real = tmp_path / "real"
    real.mkdir()
    _addon(real, "outside")
    (roots / "outside").symlink_to(real / "outside", target_is_directory=True)
    (roots / "root_link").symlink_to(real, target_is_directory=True)
    unsafe_manifest = _addon(roots, "unsafe_manifest")
    manifest = unsafe_manifest / "__manifest__.py"
    manifest.unlink()
    manifest.symlink_to(real / "outside" / "__manifest__.py")

    assert normalize_eligible_roots(worktree, [str(roots), str(roots / "root_link")]) == (roots,)
    with pytest.raises(ConfigError, match="not found"):
        resolve_test_selection(worktree, [str(roots)], target="outside", cwd=worktree)
    with pytest.raises(ConfigError, match="not found"):
        resolve_test_selection(worktree, [str(roots)], target="unsafe_manifest", cwd=worktree)


def test_project_root_and_traversal_do_not_select_an_addon(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    addons = worktree / "addons"
    addons.mkdir()
    _addon(addons, "sale")
    outside = tmp_path / "test_sale.py"
    outside.write_text("# outside\n")

    with pytest.raises(ConfigError, match="not inside a safe configured addon"):
        resolve_test_selection(worktree, ["addons"], cwd=worktree)
    with pytest.raises(ConfigError, match="outside the registered worktree"):
        resolve_test_selection(worktree, ["addons"], target="../test_sale.py", cwd=worktree)


def test_cwd_selects_nearest_safe_manifest_and_native_module_tag(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    addons = worktree / "addons"
    addons.mkdir()
    module = _addon(addons, "sale")
    nested = module / "tests" / "nested"
    nested.mkdir()

    selection = resolve_test_selection(worktree, ["addons"], cwd=nested)

    assert selection.provenance.kind == "cwd"
    assert selection.modules == ("sale",)
    assert selection.test_tags == "/sale"


@pytest.mark.parametrize("filename", ["__init__.py", "helper.py", "missing.py"])
def test_explicit_file_requires_existing_test_python_file(tmp_path: Path, filename: str) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    addons = worktree / "addons"
    addons.mkdir()
    module = _addon(addons, "sale")
    if filename != "missing.py":
        (module / "tests" / filename).write_text("# test\n")

    with pytest.raises(ConfigError):
        resolve_test_selection(worktree, ["addons"], target=filename, cwd=module / "tests")


def test_explicit_file_accepts_relative_and_absolute_paths(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    addons = worktree / "addons"
    addons.mkdir()
    module = _addon(addons, "sale")
    nested = module / "tests" / "nested"
    nested.mkdir()
    test_file = nested / "test_order.py"
    test_file.write_text("# test\n")

    relative = resolve_test_selection(
        worktree, ["addons"], target="nested/test_order.py", cwd=module / "tests"
    )
    absolute = resolve_test_selection(worktree, ["addons"], target=str(test_file), cwd=worktree)

    assert relative.test_tags == "/sale/tests/nested/test_order.py"
    assert absolute.test_tags == relative.test_tags
    assert relative.provenance.file_path == test_file


def test_file_outside_tests_and_file_symlink_are_rejected(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    addons = worktree / "addons"
    addons.mkdir()
    module = _addon(addons, "sale")
    outside = module / "test_outside.py"
    outside.write_text("# outside\n")
    linked = module / "tests" / "test_link.py"
    linked.symlink_to(outside)

    with pytest.raises(ConfigError, match="tests"):
        resolve_test_selection(worktree, ["addons"], target=str(outside), cwd=worktree)
    with pytest.raises(ConfigError, match="symlink"):
        resolve_test_selection(worktree, ["addons"], target=str(linked), cwd=worktree)


def test_explicit_tags_are_byte_exact_and_file_tags_are_incompatible(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    addons = worktree / "addons"
    addons.mkdir()
    module = _addon(addons, "sale")
    exact = " standard,/stock,-slow "

    selection = resolve_test_selection(
        worktree, ["addons"], target="sale", tags=exact, cwd=worktree
    )
    assert selection.test_tags == exact

    with pytest.raises(ConfigError, match="combined"):
        resolve_test_selection(
            worktree,
            ["addons"],
            target="test_order.py",
            tags=exact,
            cwd=module / "tests",
        )


def test_selector_has_no_click_dependency_and_is_json_compatible(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    addons = worktree / "addons"
    addons.mkdir()
    _addon(addons, "sale")
    selection = resolve_test_selection(worktree, ["addons"], target="sale", cwd=worktree)

    assert "click" not in selection.__class__.__module__
    assert json.dumps({"modules": selection.modules, "tags": selection.test_tags})
