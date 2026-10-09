from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from odoo_instance_sdk.commands.context import RuntimeView
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.execution import Command, ExecutionPlan, SemanticPlanObservation
from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResultLike, RecordingExecutor
from odoo_instance_sdk.models import CommandResult, StartConfig
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.module import ModuleResource


def _resource(
    tmp_path: Path, roots: list[str] | None = None, *, database: str | None = None
) -> ModuleResource:
    return ModuleResource(
        cast(
            "OdooInstance",
            SimpleNamespace(
                config=InstanceConfig(
                    base_url="http://127.0.0.1:8069",
                    default_cwd=tmp_path,
                    start_config=StartConfig(addons_path=roots or ["addons"], db_name=database),
                )
            ),
        )
    )


def _manifest(root: Path, name: str, content: str = "{}") -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "__manifest__.py").write_text(content + "\n", encoding="utf-8")
    return path


def _init_git(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "modules"], check=True)


def _payload_stdout(result: list[dict[str, object]], nonce: str = "deadbeefdeadbeef") -> str:
    payload = json.dumps({"result": result})
    return f"__ODCLI_PAYLOAD__{nonce}__ {payload} __END_PAYLOAD__{nonce}__\n"


def _shell_builder(value: CommandResult, *, git_root: Path) -> object:
    def build(
        _source: str,
        *,
        extra_steps: tuple[PreparedStep, ...] = (),
        result_converter: object = None,
        preflight: object = None,
        **_kwargs: object,
    ) -> Command[object]:
        shell_step = PreparedStep(
            step_id="instance.shell_script",
            argv=("odoo",),
            cwd=str(git_root),
            text=True,
        )
        results: dict[str, ProcessResultLike] = {shell_step.step_id: value}
        for step in extra_steps:
            if step.step_id.endswith(".root"):
                results[step.step_id] = CommandResult(
                    args=list(step.argv),
                    returncode=0,
                    stdout=str(Path(step.cwd or git_root).resolve()),
                    stderr="",
                    duration=0.0,
                )
            else:
                results[step.step_id] = CommandResult(
                    args=list(step.argv),
                    returncode=0,
                    stdout="",
                    stderr="",
                    duration=0.0,
                )
        executor = RecordingExecutor(results=results)

        def execute(context: object) -> object:
            if callable(preflight):
                preflight(context)
            raw = cast("Any", context).process(shell_step.step_id)
            return cast("Any", result_converter)(raw) if callable(result_converter) else raw

        steps = (*extra_steps, shell_step)
        return Command.create(
            ExecutionPlan(steps=tuple(step.public_projection() for step in steps)),
            execute,
            steps,
            executor=executor,
        )

    return build


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


def test_context_combines_provenance_changes_and_partial_database_availability(
    tmp_path: Path,
) -> None:
    addons = tmp_path / "addons"
    extra = tmp_path / "extra"
    _manifest(addons, "base", "{'version': '1.0'}")
    sale = _manifest(addons, "sale", "{'version': 'first', 'depends': ['base']}")
    _manifest(extra, "sale", "{'version': 'shadow'}")
    (sale / "models.py").write_text("# tracked\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "modules"], check=True)
    untracked = sale / "untracked.py"
    untracked.write_text("# not committed\n", encoding="utf-8")
    before = (sale / "__manifest__.py").read_bytes()

    value = _resource(tmp_path, ["addons", "extra"]).context()

    sale_module = next(module for module in value.modules if module.name == "sale")
    assert sale_module.version == "first"
    assert sale_module.shadowed_paths == (str(extra / "sale"),)
    assert sale_module.shadowed[0].path == str(extra / "sale")
    assert sale_module.repository == str(tmp_path)
    assert {change.kind for change in sale_module.changes} >= {"committed", "untracked"}
    assert sale_module.dependency_details[0].path == str(addons / "base")
    assert value.database.state == "unavailable"
    assert value.database.reason == "database is not selected"
    assert (sale / "__manifest__.py").read_bytes() == before


def test_context_uses_declared_repository_order_and_keeps_provenance(
    tmp_path: Path,
) -> None:
    first = tmp_path / "client-addons"
    second = tmp_path / "product-addons"
    _manifest(first, "sale", "{'version': 'client', 'depends': ['base']}")
    _manifest(second, "sale", "{'version': 'product'}")
    _manifest(second, "base", "{'version': 'base'}")
    _init_git(first)
    _init_git(second)
    manifest = tmp_path / ".odcli" / "project.toml"
    manifest.parent.mkdir()
    manifest.write_text(
        '[project]\naddon_repositories = ["client-addons", "product-addons"]\n',
        encoding="utf-8",
    )

    value = _resource(tmp_path, ["client-addons", "product-addons"]).context()

    sale = next(module for module in value.modules if module.name == "sale")
    assert sale.path == str(first / "sale")
    assert sale.repository == str(first)
    assert sale.repository_path == str(first)
    assert sale.shadowed[0].path == str(second / "sale")
    assert sale.shadowed[0].repository == str(second)
    assert sale.shadowed[0].repository_path == str(second)
    assert sale.dependency_details[0].path == str(second / "base")


def test_context_preserves_filesystem_facts_when_git_probe_fails(tmp_path: Path) -> None:
    _manifest(tmp_path / "addons", "sale", "{'version': '1.0'}")

    value = _resource(tmp_path).context()

    assert [module.name for module in value.modules] == ["sale"]
    assert value.filesystem.state == "available"
    assert value.git.state == "unavailable"
    assert value.git.reason is not None
    assert len(value.git.reason) <= 240


def test_context_preserves_filesystem_facts_when_database_probe_fails(tmp_path: Path) -> None:
    _manifest(tmp_path / "addons", "sale", "{'version': '1.0'}")
    instance = _resource(tmp_path, database="selected")._instance
    setattr(
        instance,
        "_shell_script_command",
        _shell_builder(
            CommandResult(
                args=[],
                returncode=1,
                stdout="",
                stderr="database probe failed " + ("x" * 500),
                duration=0.0,
            ),
            git_root=tmp_path,
        ),
    )

    value = ModuleResource(instance).context()

    assert [module.name for module in value.modules] == ["sale"]
    assert value.filesystem.state == "available"
    assert value.database.state == "unavailable"
    assert value.database.reason is not None
    assert len(value.database.reason) <= 240


def test_context_attributes_staged_and_unstaged_changes_to_module(tmp_path: Path) -> None:
    sale = _manifest(tmp_path / "addons", "sale", "{'version': '1.0'}")
    source = sale / "models.py"
    source.write_text("initial\n", encoding="utf-8")
    _init_git(tmp_path)
    source.write_text("staged\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", str(source)], check=True)
    source.write_text("unstaged\n", encoding="utf-8")

    value = _resource(tmp_path).context()

    changes = next(module for module in value.modules if module.name == "sale").changes
    assert {(change.kind, change.path) for change in changes} >= {
        ("staged", "addons/sale/models.py"),
        ("unstaged", "addons/sale/models.py"),
    }


def test_context_distinguishes_manifest_and_installed_versions(tmp_path: Path) -> None:
    _manifest(tmp_path / "addons", "sale", "{'version': '19.0.1'}")
    instance = _resource(tmp_path, database="selected")._instance
    setattr(
        instance,
        "_shell_script_command",
        _shell_builder(
            CommandResult(
                args=[],
                returncode=0,
                stdout=_payload_stdout(
                    [
                        {
                            "name": "sale",
                            "state": "installed",
                            "installed_version": "19.0.0",
                            "latest_version": "19.0.1",
                        }
                    ]
                ),
                stderr="",
                duration=0.0,
            ),
            git_root=tmp_path,
        ),
    )

    sale = next(
        module for module in ModuleResource(instance).context().modules if module.name == "sale"
    )

    assert sale.manifest_version == "19.0.1"
    assert sale.installed_state == "installed"
    assert sale.installed_version == "19.0.0"


def test_context_rejects_roots_outside_checkout_without_inspection(tmp_path: Path) -> None:
    outside = tmp_path.parent / "module-context-outside"
    outside.mkdir()
    _manifest(outside, "secret", "{'version': 'outside'}")

    value = _resource(tmp_path, ["../module-context-outside"]).context()

    assert value.modules == ()
    assert value.filesystem.state == "unavailable"
    assert any("outside allowed repositories" in warning for warning in value.warnings)
