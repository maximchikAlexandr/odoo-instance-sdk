from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
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


def _run_measured_process(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[subprocess.CompletedProcess[str], float]:
    started = clock()
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, clock() - started


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
    'odoo_instance_sdk.internal.dbprep.source',
    'odoo_instance_sdk.internal.dbprep.source_restore',
    'odoo_instance_sdk.internal.dbreplace.planning',
    'odoo_instance_sdk.internal.pg.drop',
    'alembic',
    'sqlalchemy',
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
        "odoo_instance_sdk.internal.dbprep.source": False,
        "odoo_instance_sdk.internal.dbprep.source_restore": False,
        "odoo_instance_sdk.internal.dbreplace.planning": False,
        "odoo_instance_sdk.internal.pg.drop": False,
        "alembic": False,
        "sqlalchemy": False,
        "expression": False,
        "httpx": False,
    }


METADATA_IMPORT_BOUNDARIES = {
    "--help": {
        "httpx": False,
        "odoo_instance_sdk.resources.monitor": False,
        "odoo_instance_sdk.execution": True,
        "odoo_instance_sdk.internal.proc": True,
        "odoo_instance_sdk.internal.dbprep.source": False,
        "odoo_instance_sdk.internal.dbprep.source_restore": False,
        "odoo_instance_sdk.internal.dbreplace.planning": False,
        "odoo_instance_sdk.internal.pg.drop": False,
        "alembic": False,
        "sqlalchemy": False,
        "expression": False,
    },
    "--version": {
        "httpx": False,
        "odoo_instance_sdk.resources.monitor": False,
        "odoo_instance_sdk.execution": True,
        "odoo_instance_sdk.internal.proc": True,
        "odoo_instance_sdk.internal.dbprep.source": False,
        "odoo_instance_sdk.internal.dbprep.source_restore": False,
        "odoo_instance_sdk.internal.dbreplace.planning": False,
        "odoo_instance_sdk.internal.pg.drop": False,
        "alembic": False,
        "sqlalchemy": False,
        "expression": False,
    },
}


@pytest.mark.parametrize(
    ("option", "expected_output", "expected_boundary"),
    [
        pytest.param(
            "--help",
            "--version",
            METADATA_IMPORT_BOUNDARIES["--help"],
            id="help",
        ),
        pytest.param(
            "--version",
            f"odcli, version {importlib.metadata.version('odoo-instance-sdk')}",
            METADATA_IMPORT_BOUNDARIES["--version"],
            id="version",
        ),
    ],
)
def test_metadata_options_are_lightweight_in_fresh_interpreters(
    tmp_path: Path,
    option: str,
    expected_output: str,
    expected_boundary: dict[str, bool],
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
    'odoo_instance_sdk.internal.dbprep.source',
    'odoo_instance_sdk.internal.dbprep.source_restore',
    'odoo_instance_sdk.internal.dbreplace.planning',
    'odoo_instance_sdk.internal.pg.drop',
    'alembic',
    'sqlalchemy',
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
    assert boundary == expected_boundary


INSTALLED_ODCLI = Path(sys.executable).with_name("odcli")


@pytest.mark.skipif(
    not INSTALLED_ODCLI.is_file(), reason="installed odcli executable is unavailable"
)
@pytest.mark.parametrize(
    ("option", "expected_output"),
    [
        pytest.param("--help", "Usage:", id="help"),
        pytest.param(
            "--version",
            f"odcli, version {importlib.metadata.version('odoo-instance-sdk')}",
            id="version",
        ),
    ],
)
@pytest.mark.parametrize("run_number", [1, 2], ids=["first", "second"])
def test_installed_executable_metadata_startup_is_measured_outside_checkout(
    tmp_path: Path, option: str, expected_output: str, run_number: int
) -> None:
    result, elapsed = _run_measured_process(
        [str(INSTALLED_ODCLI), option],
        cwd=tmp_path,
        env=_clean_environment(),
    )
    assert result.returncode == 0, result.stderr
    assert expected_output in result.stdout
    print(f"INSTALLED_STARTUP {option} run={run_number} elapsed_seconds={elapsed:.6f}")


def test_startup_measurement_uses_injected_clock(
    tmp_path: Path,
) -> None:
    ticks = iter((1.25, 1.75))

    def clock() -> float:
        return next(ticks)

    result, elapsed = _run_measured_process(
        [sys.executable, "-c", "print('ready')"],
        cwd=tmp_path,
        env=_clean_environment(),
        clock=clock,
    )

    assert result.returncode == 0
    assert result.stdout == "ready\n"
    assert elapsed == 0.5


def test_postgres_command_registration_keeps_resource_transport_lazy(tmp_path: Path) -> None:
    result = _fresh_process(
        """
import json
import sys
from odoo_instance_sdk.commands.pg import postgres_group
assert postgres_group.name == 'postgres'
print(json.dumps({name: name in sys.modules for name in (
    'odoo_instance_sdk.commands.pg',
    'odoo_instance_sdk.resources.postgres',
    'odoo_instance_sdk.internal.pg.transport',
    'odoo_instance_sdk.internal.proc',
)}))
""",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "odoo_instance_sdk.commands.pg": True,
        "odoo_instance_sdk.resources.postgres": False,
        "odoo_instance_sdk.internal.pg.transport": False,
        "odoo_instance_sdk.internal.proc": False,
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


def test_cli_lazy_export_boundary_resolves_only_closed_entries(tmp_path: Path) -> None:
    result = _fresh_process(
        """
import importlib
import sys

module = importlib.import_module('odoo_instance_sdk.cli')
assert all(isinstance(path, str) for path in module._LAZY_EXPORTS.values())
assert module._LAZY_EXPORTS['build_launch_profile'] == 'odoo_instance_sdk.internal.vscode_generate'
assert 'odoo_instance_sdk.internal.vscode_generate' not in sys.modules
assert 'odoo_instance_sdk.internal.automation' not in sys.modules

resolved = getattr(module, 'build_launch_profile')
assert resolved is getattr(module, 'build_launch_profile')
assert 'odoo_instance_sdk.internal.vscode_generate' in sys.modules
assert 'odoo_instance_sdk.internal.automation' not in sys.modules

try:
    getattr(module, 'not_declared')
except AttributeError:
    pass
else:
    raise AssertionError('undeclared lazy export was resolved')
print('ok')
""",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
