from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _clean_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}


def _fresh_process(code: str, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=cwd,
        env=_clean_environment(),
        capture_output=True,
        text=True,
        check=False,
    )


def _boundary_from_output(output: str) -> tuple[str, dict[str, bool]]:
    prefix, boundary = output.rsplit("BOUNDARY=", 1)
    return prefix, json.loads(boundary)


def test_bare_package_import_is_lightweight_in_a_fresh_interpreter(tmp_path: Path) -> None:
    result = _fresh_process(
        """
import json
import sys
import odoo_instance_sdk
print(json.dumps({name: name in sys.modules for name in (
    'odoo_instance_sdk.client',
    'odoo_instance_sdk.resources.monitor',
    'odoo_instance_sdk.execution',
    'odoo_instance_sdk.internal.proc',
    'expression',
    'httpx',
)}))
""",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "odoo_instance_sdk.client": False,
        "odoo_instance_sdk.resources.monitor": False,
        "odoo_instance_sdk.execution": False,
        "odoo_instance_sdk.internal.proc": False,
        "expression": False,
        "httpx": False,
    }


@pytest.mark.parametrize(
    ("option", "expected_output"),
    [
        ("--help", "--version"),
        ("--version", f"odcli, version {importlib.metadata.version('odoo-instance-sdk')}"),
    ],
)
def test_metadata_options_are_lightweight_in_fresh_interpreters(
    tmp_path: Path, option: str, expected_output: str
) -> None:
    result = _fresh_process(
        """
import json
import sys
from odoo_instance_sdk.cli import cli
try:
    cli(prog_name='odcli')
except SystemExit as exc:
    exit_code = exc.code
else:
    exit_code = 0
print('BOUNDARY=' + json.dumps({name: name in sys.modules for name in (
    'httpx',
    'odoo_instance_sdk.resources.monitor',
    'odoo_instance_sdk.execution',
    'odoo_instance_sdk.internal.proc',
    'expression',
)}))
raise SystemExit(exit_code)
""",
        option,
        cwd=tmp_path,
    )

    output, boundary = _boundary_from_output(result.stdout)
    assert result.returncode == 0, result.stderr
    assert expected_output in output
    assert boundary == {
        "httpx": False,
        "odoo_instance_sdk.resources.monitor": False,
        "odoo_instance_sdk.execution": False,
        "odoo_instance_sdk.internal.proc": False,
        "expression": False,
    }


def test_lazy_exports_preserve_order_identity_import_syntax_and_errors() -> None:
    sdk = importlib.import_module("odoo_instance_sdk")
    expected_order = tuple(sdk._LAZY_EXPORTS)
    assert sdk.__all__ == list(expected_order)
    assert len(expected_order) == len(sdk.__all__)

    direct = {name: getattr(sdk, name) for name in sdk.__all__}
    star_namespace: dict[str, object] = {}
    exec("from odoo_instance_sdk import *", star_namespace)
    for name in sdk.__all__:
        module_name, attribute_name = sdk._LAZY_EXPORTS[name]
        canonical = getattr(importlib.import_module(module_name), attribute_name)
        assert direct[name] is canonical
        assert star_namespace[name] is canonical
        assert getattr(sdk, name) is canonical

    with pytest.raises(AttributeError, match="not_declared"):
        getattr(sdk, "not_declared")
