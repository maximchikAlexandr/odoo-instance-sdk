"""Native Odoo 19 scaffold rendering for one generated custom template."""

from __future__ import annotations

import ast
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import OdooClientConfig
from odoo_instance_sdk.project import ProjectConfig
from scripts.real_odoo_pins import E2E_PINS

from .conftest import E2ERuntime, odoo_runtime_python

pytestmark = [
    pytest.mark.real_odoo,
    pytest.mark.e2e_full,
    pytest.mark.serial,
    pytest.mark.timeout(300),
]


def _pinned_checkout(repository: Path, destination: Path) -> tuple[Path, Path]:
    clone = subprocess.run(
        ["git", "clone", "--shared", "--no-checkout", str(repository), str(destination)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert clone.returncode == 0, clone.stderr[-4000:]
    checkout = subprocess.run(
        ["git", "-C", str(destination), "checkout", "--detach", E2E_PINS.odoo_source_commit],
        capture_output=True,
        text=True,
        check=False,
    )
    assert checkout.returncode == 0, checkout.stderr[-4000:]
    relative = next(
        path for path in (Path("odoo-bin"), Path("odoo/odoo-bin")) if (destination / path).is_file()
    )
    return destination, destination / relative


def test_native_odoo19_scaffold_renders_custom_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_server: E2ERuntime,
) -> None:
    source = os.environ.get("ODCLI_E2E_ODOO_SOURCE_REPO") or os.environ.get(
        "ODCLI_E2E_ODOO_SOURCE_CACHE"
    )
    if not source:
        pytest.fail("required real-Odoo prerequisite is missing: ODCLI_E2E_ODOO_SOURCE_REPO")
    repository = Path(source).expanduser().resolve()
    source_root, odoo_bin = _pinned_checkout(repository, tmp_path / "odoo")
    python = odoo_runtime_python(source_server, source_root, odoo_bin)
    wrapper = tmp_path / "odoo-bin"
    wrapper.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(str(python))
        + " "
        + shlex.quote(str(odoo_bin))
        + ' "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("ODCLI_USER_ROOT", str(tmp_path / "user-root"))
    project_root = tmp_path / "project"
    (project_root / ".odcli").mkdir(parents=True)
    project = ProjectConfig(
        repository_root=project_root,
        odoo_bin=wrapper,
    )
    (project_root / ".odcli" / "project.toml").write_text(
        project.to_manifest() + '\n[scaffold.manifest]\nauthor = "OdCLI"\nlicense = "LGPL-3"\n',
        encoding="utf-8",
    )
    loaded = ProjectConfig.load(project_root)
    result = OdooClient(config=OdooClientConfig(executable=str(wrapper))).scaffolds.create(
        project=loaded,
        module_name="sale_extension",
        destination=project_root / "addons",
        include=("wizard",),
    )
    module = Path(result.destination)
    manifest = ast.literal_eval((module / "__manifest__.py").read_text(encoding="utf-8"))
    assert manifest["author"] == "OdCLI"
    assert manifest["license"] == "LGPL-3"
    assert "wizard/wizard_views.xml" in manifest["data"]
    init_text = (module / "__init__.py").read_text(encoding="utf-8")
    assert "wizard" in init_text
    probe = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import sys\n"
                "from odoo.modules.module import load_manifest\n"
                "info = load_manifest(sys.argv[1], sys.argv[2])\n"
                "assert info['author'] == 'OdCLI'\n"
                "assert info['license'] == 'LGPL-3'\n"
            ),
            result.module_name,
            str(module),
        ],
        cwd=source_root,
        env={**os.environ, "PYTHONPATH": str(source_root)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr[-4000:]
