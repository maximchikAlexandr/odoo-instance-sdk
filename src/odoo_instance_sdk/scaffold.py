"""Capture and run native Odoo addon scaffolding."""

from __future__ import annotations

import contextlib
import hashlib
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from odoo_instance_sdk.exceptions import PlanJsonValue, ScaffoldError
from odoo_instance_sdk.execution import (
    Command,
    ExecutionPlan,
    SemanticPlanObservation,
)
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PrivateJsonValue,
    ProcessResult,
    RunContext,
    SubprocessExecutor,
    prepared_command,
    prepared_step,
)
from odoo_instance_sdk.models.scaffold import (
    TEMPLATE_SOURCE,
    ManifestValue,
    ScaffoldConfig,
    ScaffoldResult,
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.scaffold_config import (
    load_user_config,
    merge_scaffold,
    normalize_module_name,
    render_manifest,
)

_PREPARE = "scaffold.template.prepare"
_PROCESS = "scaffold.odoo"
_ROLLBACK = "scaffold.destination.rollback"
_CLEANUP = "scaffold.template.cleanup"
_MODEL_PY = """from odoo import fields, models


class {{ name|pascal }}(models.Model):
    _name = "{{ name }}.record"
    _description = "{{ name }}"

    name = fields.Char(required=True)
"""
_WIZARD_PY = """from odoo import fields, models


class {{ name|pascal }}Wizard(models.TransientModel):
    _name = "{{ name }}.wizard"
    _description = "{{ name }} wizard"

    name = fields.Char(required=True)
"""
_CONTROLLER_PY = """from odoo import http


class {{ name|pascal }}Controller(http.Controller):
    @http.route("/{{ name }}/ping", auth="user", type="http")
    def ping(self, **kwargs):
        return "{{ name }}"
"""
_TEST_PY = """from odoo.tests import TransactionCase


class Test{{ name|pascal }}(TransactionCase):
    def test_record_name(self):
        record = self.env["{{ name }}.record"].create({"name": "{{ name }}"})
        self.assertEqual(record.name, "{{ name }}")
"""
_ACCESS_CSV = """id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
access_{{ name }}_record,{{ name }}.record,model_{{ name }}_record,base.group_user,1,1,1,1
"""
_VIEWS_XML = """<odoo>
    <record id="view_{{ name }}_record_list" model="ir.ui.view">
        <field name="name">{{ name }}.record.list</field>
        <field name="model">{{ name }}.record</field>
        <field name="arch" type="xml">
            <list>
                <field name="name"/>
            </list>
        </field>
    </record>
    <record id="action_{{ name }}_record" model="ir.actions.act_window">
        <field name="name">{{ name }}</field>
        <field name="res_model">{{ name }}.record</field>
        <field name="view_mode">list,form</field>
    </record>
    <menuitem id="menu_{{ name }}_root" name="{{ name }}" action="action_{{ name }}_record"/>
</odoo>
"""
_WIZARD_XML = """<odoo>
    <record id="view_{{ name }}_wizard_form" model="ir.ui.view">
        <field name="name">{{ name }}.wizard.form</field>
        <field name="model">{{ name }}.wizard</field>
        <field name="arch" type="xml">
            <form>
                <field name="name"/>
            </form>
        </field>
    </record>
</odoo>
"""
_DATA_XML = """<odoo>
    <record id="{{ name }}_record_sample" model="{{ name }}.record">
        <field name="name">{{ name }}</field>
    </record>
</odoo>
"""
_DEMO_XML = """<odoo>
    <record id="{{ name }}_record_demo" model="{{ name }}.record">
        <field name="name">{{ name }} demo</field>
    </record>
</odoo>
"""
_REPORT_XML = """<odoo>
    <record id="action_report_{{ name }}" model="ir.actions.report">
        <field name="name">{{ name }}</field>
        <field name="model">{{ name }}.record</field>
        <field name="report_type">qweb-pdf</field>
        <field name="report_name">{{ name }}.report_record</field>
        <field name="report_file">{{ name }}.report_record</field>
    </record>
    <template id="report_record">
        <t t-name="{{ name }}.report_record">
            <t t-call="web.html_container">
                <div class="page"><span t-out="docs[:1].name"/></div>
            </t>
        </t>
    </template>
</odoo>
"""
_DESCRIPTION_HTML = """<section><h2>{{ name }}</h2></section>
"""
_OPTIONAL_FILES: dict[str, dict[str, str]] = {
    "wizard": {
        "wizard/__init__.py": "from . import wizard\n",
        "wizard/wizard.py": _WIZARD_PY,
        "wizard/wizard_views.xml": _WIZARD_XML,
    },
    "controllers": {
        "controllers/__init__.py": "from . import controllers\n",
        "controllers/controllers.py": _CONTROLLER_PY,
    },
    "data": {"data/data.xml": _DATA_XML},
    "demo": {"demo/demo.xml": _DEMO_XML},
    "report": {"report/report.xml": _REPORT_XML},
    "tests": {
        "tests/__init__.py": "from . import test_record\n",
        "tests/test_record.py": _TEST_PY,
    },
    "static/description": {"static/description/index.html": _DESCRIPTION_HTML},
}


def create_scaffold_command(
    *,
    project: ProjectConfig,
    executable: str,
    module_name: str,
    destination: Path,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    manifest: Mapping[str, ManifestValue] | None = None,
    user_config_path: Path | None = None,
) -> Command[ScaffoldResult]:
    """Capture addon scaffolding without creating the template or destination."""

    normalized = normalize_module_name(module_name)
    user = load_user_config(user_config_path)
    cli_layer = ScaffoldConfig(
        include=include,
        exclude=exclude,
        manifest=dict(manifest or {}),
    )
    components, effective = merge_scaffold(
        user.scaffold,
        project.scaffold,
        cli_layer,
        module_name=normalized,
    )
    data_files, demo_files = _manifest_files(components)
    if data_files:
        effective["data"] = list(data_files)
    if demo_files:
        effective["demo"] = list(demo_files)
    binary = _executable(project, executable)
    parent, target, parent_existed = _destination(destination, normalized)
    files = _template_files(components, render_manifest(effective))
    temp_root = _template_root(normalized, target, files)
    template_dir = temp_root / "template"
    steps = (
        _action(
            _PREPARE,
            "Write the temporary Odoo scaffold template",
            _file_details(files, template_dir),
        ),
        prepared_step(
            str(binary),
            ("scaffold", "--template", str(template_dir), normalized, str(parent)),
            step_id=_PROCESS,
            cwd=str(project.repository_root),
            mutating=True,
        ),
        _action(
            _ROLLBACK,
            "Remove a partial addon after a native scaffold failure",
            {"destination": str(target)},
        ),
        _action(
            _CLEANUP, "Remove the temporary scaffold template", {"template_root": str(temp_root)}
        ),
    )

    def execute(context: RunContext[ScaffoldResult]) -> ScaffoldResult:
        def render() -> ScaffoldResult:
            _write_template(template_dir, files)
            context.complete_action(_PREPARE)
            _require_success(cast("ProcessResult", context.process(_PROCESS)))
            missing = [relative for relative in files if not (target / relative).is_file()]
            if missing:
                raise ScaffoldError(
                    "native Odoo scaffold left an incomplete addon",
                    code="scaffold_partial",
                    details={"missing": cast("list[PlanJsonValue]", missing)},
                )
            context.skip(_ROLLBACK)
            return ScaffoldResult(
                module_name=normalized,
                destination=str(target),
                components=components,
                created_files=tuple(files),
                manifest=effective,
                template_source=TEMPLATE_SOURCE,
            )

        context.action(_PREPARE)
        try:
            return render()
        except Exception:
            _rollback(context, target=target, parent=parent, parent_existed=parent_existed)
            raise
        finally:
            _cleanup(context, temp_root)

    return Command.from_prepared(
        ExecutionPlan(
            steps=tuple(step.public_projection() for step in steps),
            observations=(
                SemanticPlanObservation(
                    kind="semantic",
                    goal="Scaffold an Odoo addon with the native renderer",
                    targets=(str(target),),
                    mutations=tuple(files),
                ),
            ),
        ),
        prepared_command(execute, steps, executor=SubprocessExecutor()),
    )


def _action(
    step_id: str, description: str, details: Mapping[str, PrivateJsonValue]
) -> PreparedAction:
    return PreparedAction(
        step_id=step_id,
        action=step_id,
        description=description,
        details=details,
        mutating=True,
    )


def _file_details(files: Mapping[str, str], template_dir: Path) -> dict[str, PrivateJsonValue]:
    rendered: Mapping[str, PrivateJsonValue] = files
    return {"template": str(template_dir), "files": rendered}


def _executable(project: ProjectConfig, executable: str) -> Path:
    selected = project.odoo_bin if project.odoo_bin is not None else Path(executable)
    if not selected.is_absolute():
        selected = project.repository_root / selected
    resolved = selected.expanduser().resolve()
    if not resolved.is_file():
        raise ScaffoldError(
            f"Odoo executable does not exist: {resolved}",
            code="scaffold_executable",
            details={"executable": str(resolved)},
        )
    return resolved


def _destination(destination: Path, module_name: str) -> tuple[Path, Path, bool]:
    root = destination.expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    root = root.resolve()
    if root.exists() and not root.is_dir():
        raise ScaffoldError(
            "scaffold destination is not a directory",
            code="scaffold_invalid_destination",
            details={"destination": str(root)},
        )
    target = root / module_name
    if target.parent != root:
        raise ScaffoldError(
            "module name escapes the destination",
            code="scaffold_invalid_module",
            details={"module_name": module_name, "destination": str(root)},
        )
    if target.exists() or target.is_symlink():
        raise ScaffoldError(
            f"addon target already exists: {target}",
            code="scaffold_destination_exists",
            details={"destination": str(target)},
        )
    return root, target, root.exists()


def _template_files(components: tuple[str, ...], manifest_source: str) -> dict[str, str]:
    imports = ["from . import models"]
    for package in ("wizard", "controllers"):
        if package in components:
            imports.append(f"from . import {package}")
    files = {
        "__manifest__.py": manifest_source,
        "__init__.py": "\n".join(imports) + "\n",
        "models/__init__.py": "from . import models\n",
        "models/models.py": _MODEL_PY,
        "security/ir.model.access.csv": _ACCESS_CSV,
        "views/views.xml": _VIEWS_XML,
    }
    for component in components:
        files.update(_OPTIONAL_FILES.get(component, {}))
    return files


def _manifest_files(components: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    data = ["security/ir.model.access.csv", "views/views.xml"]
    if "wizard" in components:
        data.append("wizard/wizard_views.xml")
    if "data" in components:
        data.append("data/data.xml")
    if "report" in components:
        data.append("report/report.xml")
    demo = ("demo/demo.xml",) if "demo" in components else ()
    return tuple(data), demo


def _write_template(template_dir: Path, files: Mapping[str, str]) -> None:
    root = template_dir.parent
    if root.is_symlink() or template_dir.is_symlink() or (root.exists() and not root.is_dir()):
        raise ScaffoldError("temporary scaffold path is unsafe", code="scaffold_failed")
    if root.exists():
        shutil.rmtree(root)
    template_dir.mkdir(parents=True)
    for relative, content in files.items():
        path = template_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _template_root(module_name: str, destination: Path, files: Mapping[str, str]) -> Path:
    digest = hashlib.sha256(module_name.encode())
    digest.update(b"\0" + str(destination).encode())
    for relative, content in sorted(files.items()):
        digest.update(b"\0" + relative.encode() + b"\0" + content.encode())
    return Path(tempfile.gettempdir()) / f"odcli-scaffold-{digest.hexdigest()[:32]}"


def _require_success(completed: ProcessResult) -> None:
    if completed.returncode == 0:
        return
    raise ScaffoldError(
        "native Odoo scaffold failed",
        code="scaffold_native_failed",
        details={"returncode": completed.returncode},
    )


def _rollback(
    context: RunContext[ScaffoldResult],
    *,
    target: Path,
    parent: Path,
    parent_existed: bool,
) -> None:
    if context.consumed(_ROLLBACK):
        return
    context.action(_ROLLBACK)
    if target.is_symlink():
        target.unlink()
    elif target.exists():
        shutil.rmtree(target)
    if not parent_existed and parent.is_dir() and not parent.is_symlink():
        with contextlib.suppress(OSError):
            parent.rmdir()
    context.complete_action(_ROLLBACK)


def _cleanup(context: RunContext[ScaffoldResult], temp_root: Path) -> None:
    if context.consumed(_CLEANUP):
        return
    context.action(_CLEANUP)
    if temp_root.is_symlink():
        temp_root.unlink()
    elif temp_root.exists():
        shutil.rmtree(temp_root)
    context.complete_action(_CLEANUP)


__all__ = ["create_scaffold_command"]
