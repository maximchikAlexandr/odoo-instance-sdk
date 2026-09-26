from __future__ import annotations

import json
import sys
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner, Result

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands.context import ResolvedContext
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.execution import Command, ExecutionPlan, ProcessStep
from odoo_instance_sdk.internal.automation import ModuleUpdatePlan as AdapterPlan
from odoo_instance_sdk.internal.proc import PreparedStep, RecordingExecutor
from odoo_instance_sdk.models import (
    CommandResult,
    DevelopmentEnvironment,
    ModuleUpdatePlan,
    StartConfig,
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.module import ModuleResource


def _module_instance(config: InstanceConfig) -> OdooInstance:
    return cast("OdooInstance", SimpleNamespace(config=config))


def test_module_info_and_install_order_are_machine_readable(tmp_path: Path) -> None:
    addons = tmp_path / "addons"
    (addons / "base").mkdir(parents=True)
    (addons / "sale").mkdir()
    (addons / "base" / "__manifest__.py").write_text("{}\n")
    (addons / "sale" / "__manifest__.py").write_text("{'depends': ['base']}\n")
    instance = _module_instance(
        InstanceConfig(
            base_url="http://127.0.0.1:8069",
            default_cwd=tmp_path,
            start_config=StartConfig(addons_path=["addons"]),
        )
    )
    instance.modules = ModuleResource(instance)
    context = ResolvedContext(
        client=None,  # type: ignore[arg-type]
        source=None,  # type: ignore[arg-type]
        instance=instance,
        provenance="cwd",
    )

    with patch(
        "odoo_instance_sdk.commands.module.cli_context.ready_instance", return_value=context
    ):
        result = CliRunner().invoke(cli, ["module", "install-order", "sale", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["result"]["modules"] == ["base", "sale"]


def test_module_where_and_deps_have_rich_projections_and_no_fields_option(
    tmp_path: Path,
) -> None:
    addons = tmp_path / "addons"
    (addons / "base").mkdir(parents=True)
    (addons / "sale").mkdir()
    (addons / "base" / "__manifest__.py").write_text("{}\n")
    (addons / "sale" / "__manifest__.py").write_text("{'depends': ['base', 'missing']}\n")
    instance = _module_instance(
        InstanceConfig(
            base_url="http://127.0.0.1:8069",
            default_cwd=tmp_path,
            start_config=StartConfig(addons_path=["addons"]),
        )
    )
    instance.modules = ModuleResource(instance)
    context = ResolvedContext(
        client=None,  # type: ignore[arg-type]
        source=None,  # type: ignore[arg-type]
        instance=instance,
        provenance="cwd",
    )
    with patch(
        "odoo_instance_sdk.commands.module.cli_context.ready_instance", return_value=context
    ):
        where = CliRunner().invoke(cli, ["module", "where", "sale"])
        deps = CliRunner().invoke(cli, ["module", "deps", "sale"])
        fields = CliRunner().invoke(
            cli, ["module", "where", "sale", "--format", "json", "--fields", "path"]
        )

    assert where.exit_code == 0, where.output
    assert "Odoo module location" in where.output
    assert "ddons/sale" in where.output
    assert deps.exit_code == 0, deps.output
    assert "missing" in deps.output
    assert fields.exit_code == 2
    assert "fields" in fields.output.lower()


@pytest.mark.parametrize(
    ("stderr", "stdout", "expected_code"),
    [
        (
            "",
            "odoo.exceptions.UserError: another module is being processed",
            "module_operation_in_progress",
        ),
        (
            "",
            "odoo.exceptions.UserError: invalid module manifest",
            "module.update_startup_failed",
        ),
        ("worker failed", "", "module.update_startup_failed"),
    ],
)
def test_module_update_cli_classifies_failures_without_retry(
    stderr: str, stdout: str, expected_code: str, tmp_path: Path
) -> None:
    project = ProjectConfig(
        repository_root=tmp_path,
        python=sys.executable,
        odoo_bin=Path(sys.executable),
    )
    modules = MagicMock()
    instance = SimpleNamespace(
        config=SimpleNamespace(
            start_config=StartConfig(db_name="db"),
            command_prefix=(sys.executable, str(tmp_path / "odoo-bin")),
        ),
        modules=modules,
    )
    context = ResolvedContext(
        client=object(),  # type: ignore[arg-type]
        source=project,
        instance=instance,  # type: ignore[arg-type]
        provenance="cwd",
    )
    calls = 0

    def run(_context: object) -> CommandResult:
        nonlocal calls
        calls += 1
        return CommandResult(args=[], returncode=2, stdout=stdout, stderr=stderr, duration=0.0)

    command = Command.create(ExecutionPlan(), run)
    modules.plan_update.return_value = ModuleUpdatePlan(modules=("sale",))
    modules.update_command.return_value = command
    with patch(
        "odoo_instance_sdk.commands.module.cli_context.ready_instance", return_value=context
    ):
        result = CliRunner().invoke(cli, ["module", "update", "sale", "--yes", "--format", "json"])

    assert result.exit_code == 1, result.output
    assert calls == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == expected_code


def _update_context(tmp_path: Path, owner: str) -> tuple[ResolvedContext, OdooInstance]:
    source: ProjectConfig | DevelopmentEnvironment
    if owner == "project":
        source = ProjectConfig(
            repository_root=tmp_path,
            python=sys.executable,
            odoo_bin=Path(sys.executable),
            default_base_ref="HEAD~1",
        )
    else:
        source = cast(
            "DevelopmentEnvironment",
            SimpleNamespace(
                id="env-1",
                name="dev",
                worktree_path=str(tmp_path),
                repository_root=str(tmp_path),
                python_environment_path=sys.executable,
                base_ref="HEAD~1",
            ),
        )
    instance = _module_instance(
        InstanceConfig(
            base_url="http://127.0.0.1:8069",
            default_cwd=tmp_path,
            start_config=StartConfig(addons_path=["addons"], db_name="db"),
            command_prefix=(sys.executable, "odoo-bin"),
        )
    )
    instance.modules = ModuleResource(instance)
    return (
        ResolvedContext(
            client=None,  # type: ignore[arg-type]
            source=source,
            instance=instance,
            provenance="cwd",
        ),
        instance,
    )


def _invoke_module_update_dry_run(
    tmp_path: Path, *, owner: str, changed: bool, mode: str
) -> tuple[Result, int]:
    context, _instance = _update_context(tmp_path, owner)
    selected = SimpleNamespace(
        base_source="explicit",
        requested_base="HEAD~1",
        resolved_base="merge-base",
        merge_base="merge-base",
        head="captured-head",
        changed_files=("addons/sale/models.py",),
        modules=("sale", "stock"),
        ignored_paths=(),
        unmapped_paths=(),
    )
    executed = 0

    def run(_context: object) -> CommandResult:
        nonlocal executed
        executed += 1
        return CommandResult(args=[], returncode=0, stdout="", stderr="", duration=0.0)

    command = Command.create(
        ExecutionPlan(
            steps=(
                ProcessStep(
                    step_id="module.probe",
                    argv=("git", "status"),
                    display="git status",
                    executable="git",
                    read_only=True,
                ),
                ProcessStep(
                    step_id="module.update",
                    argv=("odoo-bin", "--upgrade"),
                    display="odoo-bin --upgrade",
                    executable="odoo-bin",
                    mutating=True,
                ),
            )
        ),
        run,
        steps=(
            PreparedStep(
                step_id="module.probe",
                argv=("git", "status"),
                read_only=True,
            ),
            PreparedStep(
                step_id="module.update",
                argv=("odoo-bin", "--upgrade"),
                mutating=True,
            ),
        ),
    )
    patches = [
        patch(
            "odoo_instance_sdk.internal.automation.plan_module_update",
            return_value=AdapterPlan(modules=["sale"], not_installed=["stock"]),
        ),
        patch(
            "odoo_instance_sdk.internal.automation.update_modules_command",
            return_value=command,
        ),
    ]
    if changed:
        patches.append(
            patch(
                "odoo_instance_sdk.resources.module.resolve_changed_selection",
                return_value=selected,
            )
        )
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "odoo_instance_sdk.commands.module.cli_context.ready_instance",
                return_value=context,
            )
        )
        for item in patches:
            stack.enter_context(item)
        args = ["module", "update"]
        if changed:
            args.append("--changed")
        else:
            args.append("sale")
        args.extend(["--dry-run", "--format", mode])
        result = CliRunner().invoke(cli, args)
    return result, executed


@pytest.mark.parametrize(
    ("owner", "changed", "expected_head", "expected_changed_files"),
    [
        pytest.param(
            "project",
            True,
            "captured-head",
            ["addons/sale/models.py"],
            id="project-changed",
        ),
        pytest.param("project", False, None, [], id="project-explicit"),
        pytest.param(
            "environment",
            True,
            "captured-head",
            ["addons/sale/models.py"],
            id="environment-changed",
        ),
        pytest.param("environment", False, None, [], id="environment-explicit"),
    ],
)
def test_module_update_dry_run_preserves_selection(
    owner: str,
    changed: bool,
    expected_head: str | None,
    expected_changed_files: list[str] | None,
    tmp_path: Path,
) -> None:
    result, executed = _invoke_module_update_dry_run(
        tmp_path, owner=owner, changed=changed, mode="json"
    )

    assert result.exit_code == 0, result.output
    assert executed == 0
    payload = json.loads(result.stdout)
    assert payload["result"]["modules"] == ["sale"]
    assert payload["result"]["not_installed"] == ["stock"]
    assert payload["result"].get("head") == expected_head
    assert payload["result"].get("changed_files") == expected_changed_files


def _assert_module_update_json_output(result: Result) -> None:
    payload = json.loads(result.stdout)
    assert payload["result"] == payload["data"]
    assert payload["result"]["modules"] == ["sale"]
    assert payload["result"]["not_installed"] == ["stock"]
    assert [step["step_id"] for step in payload["result"]["plan"]["steps"]] == [
        "module.probe",
        "module.update",
    ]


def _assert_module_update_toon_output(result: Result) -> None:
    from toon import DecodeOptions, decode

    payload = decode(result.stdout, DecodeOptions(indent=2, strict=True))
    assert payload["result"] == payload["data"]
    assert payload["result"]["modules"] == ["sale"]
    assert payload["result"]["not_installed"] == ["stock"]
    assert [step["step_id"] for step in payload["result"]["plan"]["steps"]] == [
        "module.probe",
        "module.update",
    ]


def test_module_update_dry_run_renders_rich_output(tmp_path: Path) -> None:
    result, executed = _invoke_module_update_dry_run(
        tmp_path, owner="project", changed=False, mode="rich"
    )

    assert result.exit_code == 0, result.output
    assert executed == 0
    assert "git status" in result.output
    assert "odoo-bin --upgrade" in result.output


@pytest.mark.parametrize(
    ("mode", "verify"),
    [
        pytest.param("json", _assert_module_update_json_output, id="json"),
        pytest.param("toon", _assert_module_update_toon_output, id="toon"),
    ],
)
def test_module_update_dry_run_serializes_structured_output(
    mode: str, verify: Callable[[Result], None], tmp_path: Path
) -> None:
    result, executed = _invoke_module_update_dry_run(
        tmp_path, owner="project", changed=False, mode=mode
    )

    assert result.exit_code == 0, result.output
    assert executed == 0
    verify(result)


_NONCE = "deadbeefdeadbeef"


def _framed_command(value: CommandResult, *, nonce: str = _NONCE) -> Command[CommandResult]:
    step = PreparedStep(step_id="instance.shell_script", argv=("odoo",), wrapper_nonce=nonce)
    return Command.create(
        ExecutionPlan(steps=(step.public_projection(),)),
        lambda context: context.process(step.step_id),
        (step,),
        executor=RecordingExecutor(results={step.step_id: value}),
    )


def _framed_stdout(payload: dict[str, object], *, nonce: str = _NONCE) -> str:
    marker_open = f"__ODCLI_PAYLOAD__{nonce}__"
    marker_close = f"__END_PAYLOAD__{nonce}__"
    return f"startup noise\n{marker_open} {json.dumps(payload)} {marker_close}\n"


def _module_update_context(command: Command[CommandResult], tmp_path: Path) -> ResolvedContext:
    project = ProjectConfig(
        repository_root=tmp_path,
        python=sys.executable,
        odoo_bin=Path(sys.executable),
    )
    modules = MagicMock()
    modules.plan_update.return_value = ModuleUpdatePlan(modules=("sale",))
    modules.update_command.return_value = command
    instance = SimpleNamespace(
        config=SimpleNamespace(
            start_config=StartConfig(db_name="db"),
            command_prefix=(sys.executable, str(tmp_path / "odoo-bin")),
        ),
        modules=modules,
    )
    return ResolvedContext(
        client=object(),  # type: ignore[arg-type]
        source=project,
        instance=instance,  # type: ignore[arg-type]
        provenance="cwd",
    )


def test_module_update_long_startup_log_does_not_hide_traceback(tmp_path: Path) -> None:
    long_prefix = "INFO: odoo: starting\n" * 2000
    traceback_tail = (
        "Traceback (most recent call last):\n"
        '  File "addons/sale/models.py", line 42, in upgrade\n'
        "    raise ValueError('missing manifest key')\n"
        "ValueError: missing manifest key\n"
    )
    stderr = long_prefix + traceback_tail
    value = CommandResult(
        args=[], returncode=1, stdout="no nonce frame here", stderr=stderr, duration=0.0
    )
    command = _framed_command(value)
    context = _module_update_context(command, tmp_path)
    with patch(
        "odoo_instance_sdk.commands.module.cli_context.ready_instance", return_value=context
    ):
        result = CliRunner().invoke(cli, ["module", "update", "sale", "--yes", "--format", "json"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "module.update_startup_failed"
    message = payload["error"]["message"]
    assert "ValueError" in message
    assert "missing manifest key" in message
    assert "truncated to last 8192 bytes" in message


def test_module_update_framed_user_error_has_priority_over_stderr_tail(
    tmp_path: Path,
) -> None:
    payload = {
        "ok": False,
        "commit": False,
        "transaction": "rollback",
        "result": None,
        "user_stdout": "",
        "user_error": {
            "type": "UserError",
            "message": "another module is being processed",
            "source": None,
        },
        "finalization_error": None,
        "truncated": False,
    }
    stdout = _framed_stdout(payload)
    stderr = "Traceback (most recent call last):\nRuntimeError: should not win\n"
    value = CommandResult(args=[], returncode=1, stdout=stdout, stderr=stderr, duration=0.0)
    command = _framed_command(value)
    context = _module_update_context(command, tmp_path)
    with patch(
        "odoo_instance_sdk.commands.module.cli_context.ready_instance", return_value=context
    ):
        result = CliRunner().invoke(cli, ["module", "update", "sale", "--yes", "--format", "json"])

    assert result.exit_code == 1, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "module.update_user_code_failed"
    assert "UserError" in envelope["error"]["message"]
    assert "another module is being processed" in envelope["error"]["message"]
    assert "should not win" not in envelope["error"]["message"]


def test_module_update_malformed_payload_falls_back_to_bounded_redacted_tail(
    tmp_path: Path,
) -> None:
    malformed = f"__ODCLI_PAYLOAD__{_NONCE}__ {{not valid json}} __END_PAYLOAD__{_NONCE}__\n"
    long_prefix = "INFO: odoo: starting\n" * 2000
    traceback_tail = (
        "Traceback (most recent call last):\n"
        "psycopg2.OperationalError: could not connect to server password=hunter2\n"
    )
    stderr = long_prefix + traceback_tail
    value = CommandResult(args=[], returncode=1, stdout=malformed, stderr=stderr, duration=0.0)
    command = _framed_command(value)
    context = _module_update_context(command, tmp_path)
    with patch(
        "odoo_instance_sdk.commands.module.cli_context.ready_instance", return_value=context
    ):
        result = CliRunner().invoke(cli, ["module", "update", "sale", "--yes", "--format", "json"])

    assert result.exit_code == 1, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "module.update_startup_failed"
    message = envelope["error"]["message"]
    assert "OperationalError" in message
    assert "could not connect to server" in message
    assert "truncated to last 8192 bytes" in message
    assert "hunter2" not in message
    assert "<redacted>" in message


@pytest.mark.parametrize("mode", ["json", "toon"])
def test_module_update_returns_same_stable_error_code_across_formats(
    mode: str, tmp_path: Path
) -> None:
    long_prefix = "INFO: odoo: starting\n" * 2000
    traceback_tail = (
        "Traceback (most recent call last):\n"
        "AttributeError: 'NoneType' object has no attribute 'split'\n"
    )
    stderr = long_prefix + traceback_tail
    value = CommandResult(
        args=[], returncode=1, stdout="no nonce frame", stderr=stderr, duration=0.0
    )
    command = _framed_command(value)
    context = _module_update_context(command, tmp_path)
    with patch(
        "odoo_instance_sdk.commands.module.cli_context.ready_instance", return_value=context
    ):
        result = CliRunner().invoke(cli, ["module", "update", "sale", "--yes", "--format", mode])

    assert result.exit_code == 1, result.output
    if mode == "json":
        envelope = json.loads(result.stdout)
    else:
        from toon import DecodeOptions, decode

        envelope = decode(result.stdout, DecodeOptions(indent=2, strict=True))
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "module.update_startup_failed"
    assert "AttributeError" in envelope["error"]["message"]
    assert "'NoneType' object has no attribute 'split'" in envelope["error"]["message"]
