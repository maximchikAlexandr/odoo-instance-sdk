from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [pytest.mark.packaging, pytest.mark.timeout(180)]

ROOT = Path(__file__).resolve().parents[2]


def _artifact() -> Path:
    wheels = sorted((ROOT / "dist").glob("*.whl")) if (ROOT / "dist").is_dir() else []
    if len(wheels) != 1:
        pytest.skip("package wheel is unavailable; run `make package` for the packaging gate")
    return wheels[0]


def _isolated_env(root: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    for name in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        path = root / name.lower()
        path.mkdir(parents=True, exist_ok=True)
        env[name] = str(path)
    return env


@dataclass(frozen=True)
class InstalledPackage:
    python: Path
    odcli: Path
    cwd: Path
    env: dict[str, str]


@pytest.fixture(scope="module")
def installed_package(tmp_path_factory: pytest.TempPathFactory) -> InstalledPackage:
    tmp_path = tmp_path_factory.mktemp("installed-operation-contract")
    wheel = _artifact()
    venv = tmp_path / "venv"
    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["uv", "venv", "--python", sys.executable, str(venv)], check=True, cwd=tmp_path)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    odcli = venv / ("Scripts/odcli.exe" if os.name == "nt" else "bin/odcli")
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(wheel)], check=True, cwd=tmp_path
    )
    return InstalledPackage(python, odcli, empty, _isolated_env(tmp_path / "user-state"))


def _run_cli(installed_package: InstalledPackage, *args: str) -> subprocess.CompletedProcess[str]:
    odcli = installed_package.odcli
    empty = installed_package.cwd
    env = installed_package.env
    return subprocess.run(
        [str(odcli), *args],
        cwd=empty,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_installed_package_exports_canonical_contract(installed_package: InstalledPackage) -> None:
    exported = _run_cli(installed_package, "contract", "export", "--format", "json")
    bundle = json.loads(exported.stdout)
    assert exported.stderr == ""
    assert bundle["contract_version"] == 1
    alias_schema = bundle["schemas"]["DepsMissingImport"]["$defs"]["DepsMissingImport"]
    assert "import" in alias_schema["properties"]
    assert "import_name" not in alias_schema["properties"]


def test_installed_package_invokes_operation_contract(installed_package: InstalledPackage) -> None:
    invoked = _run_cli(installed_package, "operation", "invoke", "odcli.contract.export")
    document = json.loads(invoked.stdout)
    assert invoked.stderr == ""
    assert document["ok"] is True
    assert document["result"]["contract_version"] == 1
    assert "schema_version" in document
    assert str(ROOT) not in invoked.stdout
    assert str(Path.home()) not in invoked.stdout


def test_installed_package_invokes_sdk_operation(installed_package: InstalledPackage) -> None:
    script = r"""
import json
from pathlib import Path

import msgspec

from odoo_instance_sdk.commands.context import OperationContext
from odoo_instance_sdk.operations import (
    OperationBinding,
    OperationDescriptor,
    OperationResult,
    OperationTransport,
    invoke_local,
)

class Request(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    value: str

context = OperationContext(cwd=Path("/private/project"), project_selector="selected")
binding = OperationBinding(
    OperationDescriptor(
        operation_id="fixture.read",
        canonical_path=("fixture", "read"),
        request_type=Request,
        result_type=OperationResult,
        error_types=(),
        transport=OperationTransport.DOCUMENT,
    ),
    factory=lambda request, _context: OperationResult(status="domain-negative"),
)
read = invoke_local("fixture.read", {"value": "ok"}, context, registry=__import__(
    "odoo_instance_sdk.operations", fromlist=["build_registry"]
).build_registry((binding,)))
assert read.document.ok is True
assert read.value == {"status": "domain-negative"}
print(json.dumps({"read": read.value}))
"""
    result = subprocess.run(
        [str(installed_package.python), "-c", script],
        cwd=installed_package.cwd,
        env=installed_package.env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == {"read": {"status": "domain-negative"}}
    assert result.stderr == ""


def test_installed_package_runs_approval_session(installed_package: InstalledPackage) -> None:
    python = installed_package.python
    empty = installed_package.cwd
    env = installed_package.env
    session_script = r"""
import io
import json
from odoo_instance_sdk.execution import ActionStep, Command, ExecutionPlan
from odoo_instance_sdk.internal.proc import PreparedAction
from odoo_instance_sdk.operations import run_approval_session

built = []
executed = []
def build():
    private = PreparedAction(step_id="write", action="write", description="write", mutating=True)
    def callback(run_context):
        executed.append("ran")
        run_context.action("write")
        run_context.complete_action("write")
        return {"status": "mutated"}
    command = Command.create(
        ExecutionPlan(steps=(ActionStep(step_id="write", action="write", description="write", mutating=True),)),
        callback,
        (private,),
    )
    built.append(command)
    return command

preview = run_approval_session("fixture.write", build, io.StringIO(), io.StringIO())
fingerprint = preview.records[1]["fingerprint"]
approved = run_approval_session(
    "fixture.write",
    lambda: built[0],
    io.StringIO(json.dumps({"decision": "approve", "fingerprint": fingerprint}) + "\n"),
    io.StringIO(),
)
assert approved.records[-1]["event"] == "result"
assert executed == ["ran"]
print(json.dumps({"session": approved.records[-1]["event"]}))
"""
    session = subprocess.run(
        [str(python), "-c", session_script],
        cwd=empty,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(session.stdout) == {"session": "result"}
    assert session.stderr == ""


def test_installed_package_keeps_native_cli_boundary(installed_package: InstalledPackage) -> None:
    odcli = installed_package.odcli
    empty = installed_package.cwd
    env = installed_package.env
    native = subprocess.run(
        [str(odcli), "logs", "--tail", "0"],
        cwd=empty,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert native.returncode != 0
    assert "schema_version" not in native.stdout
    assert "Do you want" not in native.stdout
