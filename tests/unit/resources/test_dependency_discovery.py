from __future__ import annotations

from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment.checkout_planning import discover_dependency_inputs


def test_discovery_walks_nested_modules_and_deduplicates_in_order(tmp_path: Path) -> None:
    addons = tmp_path / "addons"
    first = addons / "a" / "nested" / "module_a"
    second = addons / "b" / "module_b"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    for module in (first, second):
        (module / "__manifest__.py").write_text("{}", encoding="utf-8")
    (first / "requirements.txt").write_text("a", encoding="utf-8")
    (first.parent / "requirements.txt").write_text("shared", encoding="utf-8")
    (second.parent / "requirements.txt").symlink_to(first.parent / "requirements.txt")
    (tmp_path / "odoo-bin").write_text("", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("odoo", encoding="utf-8")
    explicit = tmp_path / "explicit.txt"
    explicit.write_text("explicit", encoding="utf-8")

    result = discover_dependency_inputs(
        ProjectConfig(
            repository_root=tmp_path,
            addon_roots=(Path("addons"),),
            odoo_bin=Path("odoo-bin"),
            requirements=("explicit.txt",),
        ),
        tmp_path,
        tmp_path,
    )

    assert result == tuple(
        str(path.resolve())
        for path in (
            first / "requirements.txt",
            first.parent / "requirements.txt",
            tmp_path / "requirements.txt",
            explicit,
        )
    )


def test_explicit_missing_requirement_fails_before_planning(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="dependency input is unavailable"):
        discover_dependency_inputs(
            ProjectConfig(repository_root=tmp_path, requirements=("missing.txt",)),
            tmp_path,
            tmp_path,
        )
