from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from odoo_instance_sdk.commands.context import RuntimeView
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.execution import SemanticPlanObservation
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.module import ModuleResource


def _resource(tmp_path: Path, roots: list[str] | None = None) -> ModuleResource:
    return ModuleResource(
        cast(
            "OdooInstance",
            SimpleNamespace(
                config=InstanceConfig(
                    base_url="http://127.0.0.1:8069",
                    default_cwd=tmp_path,
                    start_config=StartConfig(addons_path=roots or ["addons"]),
                )
            ),
        )
    )


def _manifest(root: Path, name: str, content: str = "{}") -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "__manifest__.py").write_text(content + "\n", encoding="utf-8")
    return path


def test_catalogue_uses_root_precedence_and_reports_shadowed_candidates(tmp_path: Path) -> None:
    first = tmp_path / "addons"
    second = tmp_path / "extra"
    _manifest(first, "sale", "{'version': 'first'}")
    _manifest(second, "sale", "{'version': 'second'}")
    resource = _resource(tmp_path, ["addons", "extra"])

    module = resource.info("sale")

    assert module.path == str(first / "sale")
    assert module.version == "first"
    assert module.shadowed_paths == (str(second / "sale"),)


def test_manifest_is_literal_only(tmp_path: Path) -> None:
    addons = tmp_path / "addons"
    marker = tmp_path / "executed"
    _manifest(addons, "unsafe", f"__import__('pathlib').Path({str(marker)!r}).touch()")

    with pytest.raises(ConfigError, match="safely parse manifest"):
        _resource(tmp_path).catalogue()
    assert not marker.exists()


def test_install_order_is_stable_and_reports_missing_and_cycles(tmp_path: Path) -> None:
    addons = tmp_path / "addons"
    _manifest(addons, "base")
    _manifest(addons, "sale", "{'depends': ['base']}")
    _manifest(addons, "stock", "{'depends': ['sale']}")
    resource = _resource(tmp_path)

    assert resource.install_order(("stock", "sale")).modules == ("base", "sale", "stock")

    _manifest(addons, "broken", "{'depends': ['missing']}")
    with pytest.raises(ConfigError, match="missing module dependency: broken -> missing"):
        resource.install_order("broken")

    (addons / "base" / "__manifest__.py").write_text("{'depends': ['stock']}\n")
    with pytest.raises(ConfigError, match="module dependency cycle"):
        resource.install_order("stock")


def test_omitted_module_resolves_nearest_addon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    addons = tmp_path / "addons"
    module_path = _manifest(addons, "sale")
    nested = module_path / "models"
    nested.mkdir()
    monkeypatch.chdir(nested)

    assert _resource(tmp_path).info().name == "sale"


def test_changed_update_with_no_addon_changes_is_a_successful_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = cast(
        "RuntimeView",
        SimpleNamespace(
            root=tmp_path,
            start_config=None,
            base_ref=None,
            owner_kind="project",
        ),
    )
    selected = SimpleNamespace(
        base_source="explicit",
        requested_base="HEAD~1",
        resolved_base="base",
        merge_base="base",
        head="head",
        changed_files=(),
        modules=(),
        ignored_paths=(),
        unmapped_paths=(),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.module.resolve_changed_selection",
        lambda *_args, **_kwargs: selected,
    )

    command = _resource(tmp_path).changed_update_command(runtime)

    assert command.run().returncode == 0
    assert command.plan.steps == ()
    observation = command.plan.observations[0]
    assert isinstance(observation, SemanticPlanObservation)
    assert observation.kind == "semantic"
    assert {item.name for item in observation.preconditions} == {
        "changed base",
        "captured HEAD",
        "changed paths",
    }


def test_catalogue_rejects_unsafe_roots_and_symlinked_modules(tmp_path: Path) -> None:
    addons = tmp_path / "addons"
    outside = tmp_path.parent / "outside-modules"
    outside.mkdir()
    (outside / "outside").mkdir()
    (outside / "outside" / "__manifest__.py").write_text("{}\n", encoding="utf-8")
    addons.mkdir()
    (addons / "linked").symlink_to(outside / "outside", target_is_directory=True)

    resource = _resource(tmp_path, ["addons", "../outside-modules", "addons/linked"])

    assert resource.catalogue() == ()
