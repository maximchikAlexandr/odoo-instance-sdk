"""Unit tests for ``odcli update`` / ``update_command()``."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import pytest

from odoo_instance_sdk.exceptions import (
    LockConflictError,
    UnsupportedInstallError,
    UpdateError,
)
from odoo_instance_sdk.execution import Command, ProcessStep
from odoo_instance_sdk.internal.proc import RecordingExecutor
from odoo_instance_sdk.internal.self_update import (
    InstalledProvenance,
    _install_argv,
    _snapshot_metadata,
    assert_direct_update_allowed,
    read_uv_tool_direct_url,
    update,
    update_command,
)
from odoo_instance_sdk.internal.self_update_composition import (
    InstalledCompanion,
    _protected_tool_file,
    read_uv_tool_companion,
)
from odoo_instance_sdk.models.update import UpdateResult

from .self_update_test_support import (
    _ANCESTRY_STEPS,
    _SHA_A,
    _SHA_B,
    _SHA_OLD,
    _executor_factory,
    _FakeDist,
    _patch_distribution,
    _patch_provenance,
    _prepare_update_case,
    _provenance,
)


@dataclass(frozen=True)
class _UpdateCase:
    id: str
    command_kwargs: dict[str, str | bool]
    effects: dict[str, object]
    expected_outcome: str | None
    expected_errors: tuple[type[Exception], ...] = ()
    expected_steps: tuple[str, ...] | None = None
    expected_install_arg: str | None = None


_UPDATE_COMMAND_MATRIX = (
    _UpdateCase("already-current-sha", {"ref": _SHA_A}, {}, "already_current", expected_steps=()),
    _UpdateCase(
        "check-resolve",
        {"ref": "main", "check": True},
        {"resolve_stdout": f"{_SHA_B}\trefs/heads/main\n"},
        "updated",
    ),
    _UpdateCase(
        "check-already-current",
        {"ref": "main", "check": True},
        {"resolve_stdout": f"{_SHA_A}\trefs/heads/main\n"},
        "already_current",
    ),
    _UpdateCase(
        "check-annotated-tag",
        {"ref": "v0.2.0", "check": True},
        {"resolve_stdout": f"{_SHA_A}\trefs/tags/v0.2.0\n{_SHA_B}\trefs/tags/v0.2.0^{{}}\n"},
        "updated",
    ),
    _UpdateCase("check-exact-sha", {"ref": _SHA_B, "check": True}, {}, "updated"),
    _UpdateCase(
        "check-ref-unavailable",
        {"ref": "main", "check": True},
        {"resolve_rc": 2, "resolve_stderr": "fatal: remote ref not found"},
        "unsupported_install",
    ),
    _UpdateCase(
        "preflight-downgrade-refused",
        {"ref": _SHA_OLD, "allow_downgrade": False},
        {"ancestry_relation": "ancestor"},
        "preflight_failed",
    ),
    _UpdateCase(
        "downgrade-allowed",
        {"ref": _SHA_OLD, "allow_downgrade": True},
        {"install_rc": 0, "maintenance_rc": 0, "ancestry_relation": "ancestor"},
        "updated",
        expected_steps=(
            *_ANCESTRY_STEPS,
            "update.install",
            "update.migrate",
            "update.verify.version",
        ),
        expected_install_arg=_SHA_OLD,
    ),
)


_UPDATE_COORDINATOR_MATRIX = (
    _UpdateCase(
        "unsupported-wheel",
        {"ref": "main"},
        {"direct_url": json.dumps({"url": "https://example.com/pkg.whl"})},
        "unsupported_install",
    ),
    _UpdateCase(
        "unsupported-repo",
        {"ref": "main"},
        {
            "direct_url": json.dumps(
                {
                    "url": "git+https://github.com/other/odoo-instance-sdk.git@main",
                    "vcs_info": {"commit_id": _SHA_B, "requested_revision": "main"},
                }
            )
        },
        "unsupported_install",
    ),
    _UpdateCase("install-failure", {"ref": "main"}, {"install_rc": 1}, None, (UpdateError,)),
    _UpdateCase(
        "migration-failure-incomplete",
        {"ref": "main"},
        {"install_rc": 0, "maintenance_rc": 1, "rollback_rc": 1},
        "update_incomplete",
    ),
    _UpdateCase(
        "migration-failure-rollback",
        {"ref": "main"},
        {"install_rc": 0, "maintenance_rc": 1, "rollback_rc": 0},
        "rolled_back",
        expected_steps=(
            "update.resolve",
            *_ANCESTRY_STEPS,
            "update.install",
            "update.migrate",
            "update.recovery",
        ),
        expected_install_arg=_SHA_B,
    ),
    _UpdateCase(
        "successful-update",
        {"ref": "main"},
        {"install_rc": 0, "maintenance_rc": 0},
        "updated",
        expected_steps=(
            "update.resolve",
            *_ANCESTRY_STEPS,
            "update.install",
            "update.migrate",
            "update.verify.version",
        ),
        expected_install_arg=_SHA_B,
    ),
)


@pytest.mark.parametrize(
    ("kind", "identifier", "remediation"),
    (
        ("numbered", "2", "odcli-developer-work service workflow"),
        ("hotfix", "42", "skill-managed canonical"),
    ),
)
def test_alternate_launcher_rejects_direct_self_update_before_provenance(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    identifier: str,
    remediation: str,
) -> None:
    from odoo_instance_sdk.internal import self_update_commands

    monkeypatch.setenv("ODCLI_LAUNCHER_KIND", kind)
    monkeypatch.setenv("ODCLI_LAUNCHER_ID", identifier)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.read_uv_tool_direct_url",
        lambda: pytest.fail("alternate update inspected installed provenance"),
    )
    monkeypatch.setattr(
        self_update_commands,
        "read_uv_tool_direct_url",
        lambda: pytest.fail("alternate update inspected installed provenance"),
    )

    with pytest.raises(UpdateError, match=remediation):
        assert_direct_update_allowed()
    with pytest.raises(UpdateError, match=remediation):
        update_command(ref=_SHA_B)


@pytest.mark.parametrize(
    ("kind", "identifier", "remediation"),
    (
        ("numbered", "3", "odcli-developer-work service workflow"),
        ("hotfix", "43", "skill-managed canonical"),
    ),
)
def test_cli_alternate_update_rejects_before_maintenance(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    identifier: str,
    remediation: str,
) -> None:
    from click.testing import CliRunner

    from odoo_instance_sdk.commands.update import update_command_cli

    monkeypatch.setenv("ODCLI_LAUNCHER_KIND", kind)
    monkeypatch.setenv("ODCLI_LAUNCHER_ID", identifier)
    touched: list[str] = []

    def unexpected(name: str) -> Callable[..., object]:
        def record(*_args: object, **_kwargs: object) -> object:
            touched.append(name)
            pytest.fail(f"alternate update crossed guard through {name}")

        return record

    for target in (
        "odoo_instance_sdk.commands.update.prepare_maintenance_environment",
        "odoo_instance_sdk.commands.update.update_command",
        "odoo_instance_sdk.commands.update.preflight_update_command",
        "odoo_instance_sdk.internal.self_update.read_uv_tool_direct_url",
        "odoo_instance_sdk.internal.self_update_commands.read_uv_tool_direct_url",
        "odoo_instance_sdk.internal.self_update._update_lock_path",
        "odoo_instance_sdk.internal.self_update._update_journal_path",
        "odoo_instance_sdk.internal.self_update._update_snapshot_dir",
        "odoo_instance_sdk.internal.self_update_commands.exclusive_lock",
        "odoo_instance_sdk.internal.self_update_commands.SubprocessExecutor",
    ):
        monkeypatch.setattr(target, unexpected(target))

    for mode, args in (
        ("normal", ["--yes", "--format", "json"]),
        ("check", ["--check", "--format", "json"]),
        ("dry-run", ["--dry-run", "--format", "json"]),
    ):
        result = CliRunner().invoke(update_command_cli, args, prog_name="odcli")

        assert result.exit_code == 1, f"{mode}: {result.output}"
        assert remediation in result.output
    assert touched == []


@pytest.mark.parametrize(
    ("kind", "identifier", "remediation"),
    (
        ("numbered", "4", "odcli-developer-work service workflow"),
        ("hotfix", "44", "skill-managed canonical"),
    ),
)
def test_alternate_sdk_and_maintenance_entrypoints_reject_before_effects(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    identifier: str,
    remediation: str,
) -> None:
    from odoo_instance_sdk.internal import self_update

    monkeypatch.setenv("ODCLI_LAUNCHER_KIND", kind)
    monkeypatch.setenv("ODCLI_LAUNCHER_ID", identifier)
    touched: list[str] = []

    def unexpected(name: str) -> Callable[..., object]:
        def record(*_args: object, **_kwargs: object) -> object:
            touched.append(name)
            pytest.fail(f"alternate update crossed guard through {name}")

        return record

    for target in (
        "odoo_instance_sdk.internal.self_update.read_uv_tool_direct_url",
        "odoo_instance_sdk.internal.self_update_commands.read_uv_tool_direct_url",
        "odoo_instance_sdk.internal.self_update._update_lock_path",
        "odoo_instance_sdk.internal.self_update._update_journal_path",
        "odoo_instance_sdk.internal.self_update._update_snapshot_dir",
        "odoo_instance_sdk.internal.self_update_commands.exclusive_lock",
        "odoo_instance_sdk.internal.self_update_commands.SubprocessExecutor",
    ):
        monkeypatch.setattr(target, unexpected(target))

    entrypoints = (
        ("update", self_update.update),
        ("update_command", lambda: update_command(ref=_SHA_B)),
        ("update_check", lambda: update_command(ref=_SHA_B, check=True)),
        ("update_dry_run", lambda: update_command(ref=_SHA_B, dry_run=True)),
        (
            "preflight",
            lambda: self_update.preflight_update_command(ref=_SHA_B),
        ),
        ("maintenance_setup", self_update.prepare_maintenance_environment),
        ("maintenance", self_update.run_maintenance),
        ("maintenance_recovery", self_update._maintenance_run),
    )
    for _name, entrypoint in entrypoints:
        with pytest.raises(UpdateError, match=remediation):
            entrypoint()
    assert touched == []


def _assert_expected_command_result(
    case: _UpdateCase, result: Any, executor: RecordingExecutor
) -> None:
    assert result.outcome == case.expected_outcome
    if case.expected_steps is not None:
        assert [step.step_id for step in executor.executed] == list(case.expected_steps)
    if case.expected_install_arg is not None:
        install = next(
            (step for step in executor.executed if step.step_id == "update.install"), None
        )
        assert install is not None and any(case.expected_install_arg in arg for arg in install.argv)


def _validate_command_already_current(
    case: _UpdateCase, result: Any, executor: RecordingExecutor
) -> None:
    _assert_expected_command_result(case, result, executor)
    assert not any(step.step_id.startswith("update.install") for step in executor.executed)


def _validate_command_unavailable(
    case: _UpdateCase, result: Any, executor: RecordingExecutor
) -> None:
    _assert_expected_command_result(case, result, executor)
    assert result.manual_argv is not None
    assert "remote ref not found" in (result.next_step or "")


_CommandCaseValidator = Callable[[_UpdateCase, Any, RecordingExecutor], None]


_COMMAND_CASE_VALIDATORS: dict[str, _CommandCaseValidator] = {
    "already-current-sha": _validate_command_already_current,
    "check-ref-unavailable": _validate_command_unavailable,
}


def _validate_coordinator_error(
    case: _UpdateCase, executor: RecordingExecutor, _user_root: Path
) -> None:
    with pytest.raises(case.expected_errors):
        update(**cast("Any", case.command_kwargs), executor=executor)


def _run_coordinator_result(case: _UpdateCase, executor: RecordingExecutor) -> Any:
    return update(**cast("Any", case.command_kwargs), executor=executor)


def _assert_expected_coordinator_result(
    case: _UpdateCase, result: Any, executor: RecordingExecutor
) -> None:
    if case.expected_steps is not None:
        assert [step.step_id for step in executor.executed] == list(case.expected_steps)
    if case.expected_install_arg is not None:
        install = next(step for step in executor.executed if step.step_id == "update.install")
        assert any(case.expected_install_arg in arg for arg in install.argv)


def _validate_coordinator_unsupported(
    case: _UpdateCase, executor: RecordingExecutor, _user_root: Path
) -> None:
    result = _run_coordinator_result(case, executor)
    assert result.outcome == "unsupported_install"
    assert result.manual_argv is not None


def _validate_coordinator_incomplete(
    case: _UpdateCase, executor: RecordingExecutor, _user_root: Path
) -> None:
    result = _run_coordinator_result(case, executor)
    assert result.outcome == "update_incomplete"
    assert result.recovery_argv is not None
    assert result.journal_state == "present"


def _validate_coordinator_rollback(
    case: _UpdateCase, executor: RecordingExecutor, _user_root: Path
) -> None:
    result = _run_coordinator_result(case, executor)
    assert result.outcome == "rolled_back"
    assert result.rollback_outcome == "restored"
    _assert_expected_coordinator_result(case, result, executor)


def _validate_coordinator_success(
    case: _UpdateCase, executor: RecordingExecutor, user_root: Path
) -> None:
    result = _run_coordinator_result(case, executor)
    assert result.outcome == "updated"
    assert not (user_root / "update" / "journal.json").exists()
    assert not (user_root / "update" / "snapshot").exists()
    _assert_expected_coordinator_result(case, result, executor)


_CoordinatorCaseValidator = Callable[[_UpdateCase, RecordingExecutor, Path], None]


_COORDINATOR_CASE_VALIDATORS: dict[str, _CoordinatorCaseValidator] = {
    "unsupported-wheel": _validate_coordinator_unsupported,
    "unsupported-repo": _validate_coordinator_unsupported,
    "install-failure": _validate_coordinator_error,
    "migration-failure-incomplete": _validate_coordinator_incomplete,
    "migration-failure-rollback": _validate_coordinator_rollback,
    "successful-update": _validate_coordinator_success,
}


@pytest.mark.parametrize(
    "case",
    [pytest.param(case, id=case.id) for case in _UPDATE_COMMAND_MATRIX],
)
def test_update_command_matrix(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
    case: _UpdateCase,
) -> None:
    executor = _prepare_update_case(monkeypatch, tmp_path, case.effects)
    command = update_command(**cast("Any", case.command_kwargs), executor=executor)
    validator = _COMMAND_CASE_VALIDATORS.get(case.id, _assert_expected_command_result)
    validator(case, command.run(), executor)


@pytest.mark.parametrize(
    "case",
    [pytest.param(case, id=case.id) for case in _UPDATE_COORDINATOR_MATRIX],
)
def test_update_coordinator_matrix(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
    case: _UpdateCase,
) -> None:
    executor = _prepare_update_case(monkeypatch, tmp_path, case.effects)
    _COORDINATOR_CASE_VALIDATORS[case.id](case, executor, user_root)


def test_read_uv_tool_direct_url_uses_pep610_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    _patch_distribution(monkeypatch, _FakeDist())
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._uv_tool_executable_path",
        lambda: executable,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.shutil.which",
        lambda name: "uv" if name == "uv" else None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.get_user_root",
        lambda **_: tmp_path / ".odcli",
    )
    (tmp_path / ".odcli").mkdir()
    provenance = read_uv_tool_direct_url()
    assert provenance.commit_id == _SHA_A
    assert provenance.source_repo is not None
    assert provenance.source_repo.endswith("odoo-instance-sdk.git")


def test_read_uv_tool_direct_url_accepts_uv_bare_vcs_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    direct_url = json.dumps(
        {
            "url": "https://github.com/maximchikAlexandr/odoo-instance-sdk.git",
            "vcs_info": {
                "vcs": "git",
                "commit_id": _SHA_A,
                "requested_revision": _SHA_A,
            },
        }
    )
    _patch_distribution(monkeypatch, _FakeDist(direct_url=direct_url))
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._uv_tool_executable_path",
        lambda: executable,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.shutil.which",
        lambda name: "uv" if name == "uv" else None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.get_user_root",
        lambda **_: tmp_path / ".odcli",
    )
    (tmp_path / ".odcli").mkdir()

    provenance = read_uv_tool_direct_url()

    assert provenance.source_repo == "https://github.com/maximchikAlexandr/odoo-instance-sdk.git"
    assert provenance.commit_id == _SHA_A


def test_read_uv_tool_direct_url_captures_uv_receipt_composition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    receipt = tmp_path / "uv-receipt.toml"
    receipt.write_text(
        "[tool]\n"
        "requirements = [{ name = 'odoo-instance-sdk', extras = ['dashboard'], "
        "git = 'https://github.com/maximchikAlexandr/odoo-instance-sdk.git?rev=main' }]\n",
        encoding="utf-8",
    )

    class _ReceiptDist(_FakeDist):
        files = ()

        def locate_file(self, _name: str) -> Path:
            return receipt

    _patch_distribution(monkeypatch, _ReceiptDist())
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._uv_tool_executable_path",
        lambda: executable,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.shutil.which",
        lambda name: "uv" if name == "uv" else None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.get_user_root",
        lambda **_: tmp_path / ".odcli",
    )
    (tmp_path / ".odcli").mkdir()

    provenance = read_uv_tool_direct_url()

    assert provenance.direct_requirement == (
        "odoo-instance-sdk @ git+https://github.com/maximchikAlexandr/odoo-instance-sdk.git@main"
    )
    assert provenance.requested_extras == ("dashboard",)
    assert _install_argv(_SHA_B, source_repo=provenance.source_repo, extras=provenance.extras)[
        -1
    ] == (
        "odoo-instance-sdk[dashboard] @ "
        f"git+https://github.com/maximchikAlexandr/odoo-instance-sdk.git@{_SHA_B}"
    )
    assert _snapshot_metadata(provenance, _SHA_B)["requested_extras"] == ["dashboard"]


def test_read_uv_tool_direct_url_rejects_ambiguous_uv_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "uv-receipt.toml"
    receipt.write_text(
        "[tool]\n"
        "requirements = ["
        "{ name = 'odoo-instance-sdk', extras = ['dashboard'] }, "
        "{ name = 'odoo-instance-sdk', extras = [] }]\n",
        encoding="utf-8",
    )

    class _ReceiptDist(_FakeDist):
        files = ()

        def locate_file(self, _name: str) -> Path:
            return receipt

    _patch_distribution(monkeypatch, _ReceiptDist())
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.shutil.which",
        lambda name: "uv" if name == "uv" else None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._uv_tool_executable_path",
        lambda: tmp_path / "odcli",
    )
    (tmp_path / "odcli").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "odcli").chmod(0o755)

    with pytest.raises(UnsupportedInstallError, match="ambiguous direct requirement"):
        read_uv_tool_direct_url()


def test_mutually_exclusive_check_and_dry_run_cli() -> None:
    from click.testing import CliRunner

    from odoo_instance_sdk.commands.update import update_command_cli

    result = CliRunner().invoke(
        update_command_cli,
        ["--check", "--dry-run"],
        prog_name="odcli",
    )
    assert result.exit_code == 2


def test_dry_run_resolves_target_without_execution(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    _patch_distribution(monkeypatch, _FakeDist())
    _patch_provenance(monkeypatch, _provenance(executable=executable))
    executor = _executor_factory({"resolve_stdout": f"{_SHA_B}\trefs/heads/main\n"})
    command = update_command(ref="main", dry_run=True, executor=executor)
    process_steps = [step for step in command.plan.steps if isinstance(step, ProcessStep)]
    assert [step.step_id for step in process_steps] == ["update.resolve"]
    assert executor.executed == []
    assert process_steps[0].argv == (
        "git",
        "ls-remote",
        "--exit-code",
        "https://github.com/maximchikAlexandr/odoo-instance-sdk.git",
        "refs/heads/main",
        "refs/tags/main",
        "refs/tags/main^{}",
    )
    resolution = command.run()
    assert resolution.target_sha == _SHA_B
    assert command.plan.steps == tuple(
        step.public_projection() for step in command._prepared().steps
    )
    assert executor.executed[0].step_id == "update.resolve"


def test_update_plan_contains_frozen_mutation_steps(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    _patch_distribution(monkeypatch, _FakeDist())
    _patch_provenance(monkeypatch, _provenance(executable=executable))
    executor = _executor_factory({})
    mutation = update_command(ref=_SHA_B, executor=executor)
    mutation_steps = [step for step in mutation.plan.steps if isinstance(step, ProcessStep)]
    assert [step.step_id for step in mutation_steps] == [
        *_ANCESTRY_STEPS,
        "update.install",
        "update.migrate",
        "update.verify.version",
    ]
    install = next(step for step in mutation_steps if step.step_id == "update.install")
    assert install.argv[0] == "uv"
    assert any(_SHA_B in arg for arg in install.argv)
    verify = next(step for step in mutation_steps if step.step_id == "update.verify.version")
    assert verify.argv == (str(executable.absolute()), "--version")
    assert verify.read_only is True


def test_update_plan_freezes_companion_install_and_revision_checks(
    monkeypatch: pytest.MonkeyPatch, user_root: Path, tmp_path: Path
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    companion_launcher = tmp_path / "odcli-multica"
    companion_launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    companion_launcher.chmod(0o755)
    companion_env = tmp_path / "tools" / "odcli-multica"
    (companion_env / "bin").mkdir(parents=True)
    _patch_distribution(monkeypatch, _FakeDist())
    provenance = replace(
        _provenance(executable=executable),
        companion=InstalledCompanion(
            launcher=companion_launcher,
            environment=companion_env,
            version="0.2.0",
            package_sha=_SHA_A,
            sdk_sha=_SHA_A,
            source_repo="https://github.com/maximchikAlexandr/odoo-instance-sdk.git",
        ),
    )
    _patch_provenance(monkeypatch, provenance)

    command = update_command(ref=_SHA_B, executor=_executor_factory({}))
    process_steps = [step for step in command.plan.steps if isinstance(step, ProcessStep)]
    by_id = {step.step_id: step for step in process_steps}
    assert "update.install.companion" in by_id
    assert any("odcli-multica @" in arg for arg in by_id["update.install.companion"].argv)
    assert _SHA_B in " ".join(by_id["update.install.companion"].argv)
    assert by_id["update.verify.companion.version"].argv == (str(companion_launcher), "--version")
    assert "update.verify.companion.revisions" in by_id
    assert "update.recovery.companion" in by_id
    assert any(step.step_id == "update.warn.companion" for step in command.plan.steps)


def test_companion_revision_mismatch_keeps_two_tool_recovery_evidence(
    monkeypatch: pytest.MonkeyPatch, user_root: Path, tmp_path: Path
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    companion_launcher = tmp_path / "odcli-multica"
    companion_launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    companion_launcher.chmod(0o755)
    companion_env = tmp_path / "tools" / "odcli-multica"
    (companion_env / "bin").mkdir(parents=True)
    companion = InstalledCompanion(
        launcher=companion_launcher,
        environment=companion_env,
        version="0.2.0",
        package_sha=_SHA_A,
        sdk_sha=_SHA_A,
        source_repo="https://github.com/maximchikAlexandr/odoo-instance-sdk.git",
    )
    provenance = replace(_provenance(executable=executable), companion=companion)
    _patch_distribution(monkeypatch, _FakeDist())
    _patch_provenance(monkeypatch, provenance)
    executor = _executor_factory(
        {
            "companion_revision_json": json.dumps(
                {"odcli-multica": _SHA_B, "odoo-instance-sdk": _SHA_A}
            )
        }
    )

    result = update(ref=_SHA_B, executor=executor)

    assert result.outcome == "update_incomplete"
    assert result.recovery_argv == (str(executable), "update", "--ref", _SHA_B, "--yes")
    assert result.journal_state == "present"
    journal = json.loads((user_root / "update" / "journal.json").read_text(encoding="utf-8"))
    snapshot = json.loads(
        (user_root / "update" / "snapshot" / "metadata.json").read_text(encoding="utf-8")
    )
    assert journal["phase"] == "snapshot"
    assert journal["companion"]["previous_sdk_sha"] == _SHA_A
    assert snapshot["companion"]["previous_sha"] == _SHA_A
    assert "update.verify.companion.revisions" in {step.step_id for step in executor.executed}
    assert not any(step.step_id == "update.commit" for step in executor.executed)


def test_companion_inspection_requires_receipt_and_both_vcs_revisions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    environment = tmp_path / "tools" / "odcli-multica"
    launcher = environment / "bin" / "odcli-multica"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    receipt = environment / "uv-receipt.toml"
    receipt.write_text(
        "[tool]\nrequirements = ["
        "{ name = 'odcli-multica', git = 'https://github.com/maximchikAlexandr/odoo-instance-sdk.git' }, "
        "{ name = 'odoo-instance-sdk', git = 'https://github.com/maximchikAlexandr/odoo-instance-sdk.git' }"
        "]\n",
        encoding="utf-8",
    )
    site = environment / "lib" / "python3.12" / "site-packages"
    for name, revision in (("odcli_multica", _SHA_A), ("odoo_instance_sdk", _SHA_B)):
        metadata = site / f"{name}-0.1.0.dist-info"
        metadata.mkdir(parents=True)
        package = name.replace("_", "-")
        (metadata / "METADATA").write_text(f"Name: {package}\nVersion: 0.1.0\n", encoding="utf-8")
        (metadata / "direct_url.json").write_text(
            json.dumps(
                {
                    "url": "https://github.com/maximchikAlexandr/odoo-instance-sdk.git",
                    "vcs_info": {"vcs": "git", "commit_id": revision},
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_composition.shutil.which",
        lambda _name: str(launcher),
    )

    result = read_uv_tool_companion(
        source_repo="https://github.com/maximchikAlexandr/odoo-instance-sdk.git"
    )

    assert result is not None
    assert result.package_sha == _SHA_A
    assert result.sdk_sha == _SHA_B
    assert result.environment == environment


def test_companion_reader_rejects_ambiguous_launcher_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launcher = tmp_path / "unexpected-name"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_composition.shutil.which",
        lambda _name: str(launcher),
    )

    with pytest.raises(UnsupportedInstallError, match="launcher path is ambiguous"):
        read_uv_tool_companion(source_repo="https://example.com/repo.git")


def test_companion_reader_rejects_nonregular_launcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launcher = tmp_path / "odcli-multica"
    launcher.mkdir()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_composition.shutil.which",
        lambda _name: str(launcher),
    )

    with pytest.raises(UnsupportedInstallError, match="not a regular file"):
        read_uv_tool_companion(source_repo="https://example.com/repo.git")


def test_companion_reader_rejects_unsupported_launcher_location(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launcher = tmp_path / "odcli-multica"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_composition.shutil.which",
        lambda _name: str(launcher),
    )

    with pytest.raises(UnsupportedInstallError, match="outside a supported uv tool"):
        read_uv_tool_companion(source_repo="https://example.com/repo.git")


def test_companion_reader_rejects_nonexecutable_launcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launcher = tmp_path / "tool" / "bin" / "odcli-multica"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o644)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_composition.shutil.which",
        lambda _name: str(launcher),
    )

    with pytest.raises(UnsupportedInstallError, match="escapes its protected environment"):
        read_uv_tool_companion(source_repo="https://example.com/repo.git")


def test_companion_reader_rejects_symlinked_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    environment = tmp_path / "tool"
    launcher = environment / "bin" / "odcli-multica"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    outside_receipt = tmp_path / "receipt.toml"
    outside_receipt.write_text("[tool]\nrequirements = []\n", encoding="utf-8")
    (environment / "uv-receipt.toml").symlink_to(outside_receipt)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_composition.shutil.which",
        lambda _name: str(launcher),
    )

    with pytest.raises(UnsupportedInstallError, match="receipt is missing or unreadable"):
        read_uv_tool_companion(source_repo="https://example.com/repo.git")


def test_companion_metadata_rejects_unresolvable_and_escaping_paths(tmp_path: Path) -> None:
    environment = tmp_path / "tool"
    environment.mkdir()
    metadata_dir = environment / "lib" / "package.dist-info"
    metadata_dir.parent.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("metadata", encoding="utf-8")
    escaping = metadata_dir / "METADATA"
    escaping.parent.mkdir()
    escaping.symlink_to(outside)

    with pytest.raises(UpdateError, match="missing or unreadable"):
        _protected_tool_file(environment, environment / "missing.json")
    with pytest.raises(UpdateError, match="escapes its protected environment"):
        _protected_tool_file(environment, escaping)


def test_separate_uv_tool_companion_is_included_in_coordinated_update(
    monkeypatch: pytest.MonkeyPatch, user_root: Path, tmp_path: Path
) -> None:
    core_launcher = tmp_path / "core-bin" / "odcli"
    core_launcher.parent.mkdir()
    core_launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    core_launcher.chmod(0o755)
    core_environment = tmp_path / "core-tool"
    core_environment.mkdir()
    core_receipt = core_environment / "uv-receipt.toml"
    core_receipt.write_text(
        "[tool]\nrequirements = ["
        "{ name = 'odoo-instance-sdk', git = 'https://github.com/maximchikAlexandr/odoo-instance-sdk.git' }"
        "]\n",
        encoding="utf-8",
    )
    companion_env = tmp_path / "tools" / "odcli-multica"
    launcher = companion_env / "bin" / "odcli-multica"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    (companion_env / "uv-receipt.toml").write_text(
        "[tool]\nrequirements = ["
        "{ name = 'odcli-multica', git = 'https://github.com/maximchikAlexandr/odoo-instance-sdk.git' }, "
        "{ name = 'odoo-instance-sdk', git = 'https://github.com/maximchikAlexandr/odoo-instance-sdk.git' }"
        "]\n",
        encoding="utf-8",
    )
    site = companion_env / "lib" / "python3.12" / "site-packages"
    for name, revision in (("odcli_multica", _SHA_A), ("odoo_instance_sdk", _SHA_B)):
        metadata = site / f"{name}-0.1.0.dist-info"
        metadata.mkdir(parents=True)
        package = name.replace("_", "-")
        (metadata / "METADATA").write_text(f"Name: {package}\nVersion: 0.1.0\n", encoding="utf-8")
        (metadata / "direct_url.json").write_text(
            json.dumps(
                {
                    "url": "https://github.com/maximchikAlexandr/odoo-instance-sdk.git",
                    "vcs_info": {"vcs": "git", "commit_id": revision},
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_composition.shutil.which",
        lambda name: str(launcher if name == "odcli-multica" else core_launcher),
    )

    class _CoreDistribution(_FakeDist):
        def locate_file(self, _path: str) -> Path:
            return core_receipt

    _patch_distribution(monkeypatch, _CoreDistribution())
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._assert_runtime_environment",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._uv_tool_executable_path",
        lambda: core_launcher,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_commands._verify_installed_revision",
        lambda *_args, **_kwargs: None,
    )

    executor = _executor_factory({})
    command = update_command(ref=_SHA_B, executor=executor)
    result = command.run()
    step_ids = {step.step_id for step in command.plan.steps}

    assert result.outcome == "updated"
    executed_ids = {step.step_id for step in executor.executed}
    assert "update.install.companion" in executed_ids, (
        "public update did not execute companion install"
    )
    assert "update.verify.companion.version" in executed_ids, (
        "public update skipped companion version check"
    )
    assert "update.verify.companion.revisions" in executed_ids, (
        "public update skipped companion revision check"
    )
    assert "update.install.companion" in step_ids
    assert "update.verify.companion.version" in step_ids
    assert "update.verify.companion.revisions" in step_ids
    assert "update.recovery.companion" in step_ids
    companion_install = next(
        step
        for step in command.plan.steps
        if isinstance(step, ProcessStep) and step.step_id == "update.install.companion"
    )
    assert "odcli-multica @" in " ".join(companion_install.argv)
    assert "odoo-instance-sdk @" in " ".join(companion_install.argv)
    assert _SHA_B in " ".join(companion_install.argv)


def test_separate_uv_tool_companion_rejects_ambiguous_source_provenance(
    monkeypatch: pytest.MonkeyPatch, user_root: Path, tmp_path: Path
) -> None:
    core_launcher = tmp_path / "core-bin" / "odcli"
    core_launcher.parent.mkdir()
    core_launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    core_launcher.chmod(0o755)
    core_environment = tmp_path / "core-tool"
    core_environment.mkdir()
    core_receipt = core_environment / "uv-receipt.toml"
    core_receipt.write_text(
        "[tool]\nrequirements = ["
        "{ name = 'odoo-instance-sdk', git = 'https://github.com/maximchikAlexandr/odoo-instance-sdk.git' }"
        "]\n",
        encoding="utf-8",
    )
    environment = tmp_path / "tools" / "odcli-multica"
    launcher = environment / "bin" / "odcli-multica"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    (environment / "uv-receipt.toml").write_text(
        "[tool]\nrequirements = ["
        "{ name = 'odcli-multica', git = 'https://example.com/untrusted.git' }, "
        "{ name = 'odoo-instance-sdk', git = 'https://github.com/maximchikAlexandr/odoo-instance-sdk.git' }"
        "]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_composition.shutil.which",
        lambda name: str(launcher if name == "odcli-multica" else core_launcher),
    )

    class _CoreDistribution(_FakeDist):
        def locate_file(self, _path: str) -> Path:
            return core_receipt

    _patch_distribution(monkeypatch, _CoreDistribution())
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._assert_runtime_environment",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._uv_tool_executable_path",
        lambda: core_launcher,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.shutil.disk_usage",
        lambda _path: type("Usage", (), {"free": 10 * 1024**3})(),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._catalog_schema_version", lambda: "head"
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._validate_catalog_migration_path", lambda: None
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._validate_storage_migration_path", lambda: None
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_commands._verify_installed_revision",
        lambda *_args, **_kwargs: None,
    )
    result = update_command(ref=_SHA_B, executor=_executor_factory({})).run()

    assert result.outcome == "unsupported_install"
    assert result.next_step is not None and "source conflicts" in result.next_step


def test_update_lock_conflict_propagates(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    _patch_provenance(monkeypatch, _provenance(executable=executable))
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.shutil.disk_usage",
        lambda _p: type("U", (), {"free": 10 * 1024**3})(),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._catalog_schema_version",
        lambda: "head",
    )

    @contextlib.contextmanager
    def _conflict(_path: Path) -> Iterator[int]:
        raise LockConflictError(str(_path), mode="exclusive")
        yield 0

    monkeypatch.setattr("odoo_instance_sdk.internal.self_update_commands.exclusive_lock", _conflict)
    with pytest.raises(LockConflictError):
        update(ref="main", executor=_executor_factory({}))


def test_install_failure_clears_journal_and_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    _patch_provenance(monkeypatch, _provenance(executable=executable))
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.shutil.disk_usage",
        lambda _p: type("U", (), {"free": 10 * 1024**3})(),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._catalog_schema_version",
        lambda: "head",
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._validate_catalog_migration_path",
        lambda: None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._validate_storage_migration_path",
        lambda: None,
    )
    with pytest.raises(UpdateError, match="uv tool install failed"):
        update(
            ref="main",
            executor=_executor_factory({"install_rc": 1}),
        )
    assert not (user_root / "update" / "journal.json").exists()
    assert not (user_root / "update" / "snapshot").exists()


def test_preflight_failed_reports_disk_bytes(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    _patch_provenance(monkeypatch, _provenance(executable=executable))
    reserve = 1024 * 1024 * 1024
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.shutil.disk_usage",
        lambda _p: type("U", (), {"free": reserve})(),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._catalog_schema_version",
        lambda: "head",
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._validate_catalog_migration_path",
        lambda: None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._validate_storage_migration_path",
        lambda: None,
    )
    result = update(ref="main", executor=_executor_factory({}))
    assert result.outcome == "preflight_failed"
    message = result.next_step or ""
    assert "measured" in message
    assert "reserve" in message
    assert "available" in message
    assert str(reserve) in message


def test_companion_preflight_failure_accounts_for_frozen_steps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    companion_launcher = tmp_path / "odcli-multica"
    companion_launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    companion_env = tmp_path / "tools" / "odcli-multica"
    (companion_env / "bin").mkdir(parents=True)
    companion = InstalledCompanion(
        companion_launcher,
        companion_env,
        "0.2.0",
        _SHA_A,
        _SHA_A,
        "https://github.com/maximchikAlexandr/odoo-instance-sdk.git",
    )
    _patch_distribution(monkeypatch, _FakeDist())
    _patch_provenance(monkeypatch, replace(_provenance(executable=executable), companion=companion))
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.shutil.disk_usage",
        lambda _path: type("Usage", (), {"free": 1})(),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._catalog_schema_version", lambda: "head"
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._validate_catalog_migration_path", lambda: None
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._validate_storage_migration_path", lambda: None
    )

    result = update(ref=_SHA_B, executor=_executor_factory({}))

    assert result.outcome == "preflight_failed"
    assert result.next_step is not None and "available" in result.next_step


def test_companion_verification_failure_returns_recoverable_update_result(
    monkeypatch: pytest.MonkeyPatch, user_root: Path, tmp_path: Path
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    companion_launcher = tmp_path / "tools" / "odcli-multica" / "bin" / "odcli-multica"
    companion_launcher.parent.mkdir(parents=True)
    companion_launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    companion_launcher.chmod(0o755)
    companion_env = companion_launcher.parent.parent
    companion = InstalledCompanion(
        companion_launcher,
        companion_env,
        "0.2.0",
        _SHA_A,
        _SHA_A,
        "https://github.com/maximchikAlexandr/odoo-instance-sdk.git",
    )
    _patch_distribution(monkeypatch, _FakeDist())
    _patch_provenance(monkeypatch, replace(_provenance(executable=executable), companion=companion))
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.shutil.disk_usage",
        lambda _path: type("Usage", (), {"free": 10 * 1024**3})(),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._catalog_schema_version", lambda: "head"
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._validate_catalog_migration_path", lambda: None
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._validate_storage_migration_path", lambda: None
    )
    executor = _executor_factory({"companion_verify_rc": 1})

    result = update(ref=_SHA_B, executor=executor)

    assert result.outcome == "update_incomplete"
    assert result.journal_state == "present"
    assert result.recovery_argv is not None
    assert "update.verify.companion.version" in {step.step_id for step in executor.executed}
    assert json.loads((user_root / "update" / "journal.json").read_text())["phase"] == "snapshot"


def test_companion_update_command_verification_failure_keeps_recovery_evidence(
    monkeypatch: pytest.MonkeyPatch, user_root: Path, tmp_path: Path
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    companion_launcher = tmp_path / "tools" / "odcli-multica" / "bin" / "odcli-multica"
    companion_launcher.parent.mkdir(parents=True)
    companion_launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    companion_launcher.chmod(0o755)
    companion_env = companion_launcher.parent.parent
    companion = InstalledCompanion(
        companion_launcher,
        companion_env,
        "0.2.0",
        _SHA_A,
        _SHA_A,
        "https://github.com/maximchikAlexandr/odoo-instance-sdk.git",
    )
    _patch_distribution(monkeypatch, _FakeDist())
    _patch_provenance(monkeypatch, replace(_provenance(executable=executable), companion=companion))
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.shutil.disk_usage",
        lambda _path: type("Usage", (), {"free": 10 * 1024**3})(),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._catalog_schema_version", lambda: "head"
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._validate_catalog_migration_path", lambda: None
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._validate_storage_migration_path", lambda: None
    )
    executor = _executor_factory({"companion_verify_rc": 1})
    command = update_command(ref=_SHA_B, executor=executor)

    result = command.run()

    assert result.outcome == "update_incomplete"
    assert result.journal_state == "present"
    assert result.recovery_argv is not None
    assert "update.verify.companion.version" in {step.step_id for step in executor.executed}
    assert json.loads((user_root / "update" / "journal.json").read_text())["phase"] == "snapshot"


def _companion_verification_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    effects: dict[str, object],
) -> tuple[Command[UpdateResult], RecordingExecutor]:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    companion_launcher = tmp_path / "tools" / "odcli-multica" / "bin" / "odcli-multica"
    companion_launcher.parent.mkdir(parents=True)
    companion_launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    companion_launcher.chmod(0o755)
    companion = InstalledCompanion(
        companion_launcher,
        companion_launcher.parent.parent,
        "0.2.0",
        _SHA_A,
        _SHA_A,
        "https://github.com/maximchikAlexandr/odoo-instance-sdk.git",
    )
    _patch_distribution(monkeypatch, _FakeDist())
    _patch_provenance(monkeypatch, replace(_provenance(executable=executable), companion=companion))
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.shutil.disk_usage",
        lambda _path: type("Usage", (), {"free": 10 * 1024**3})(),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._catalog_schema_version", lambda: "head"
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._validate_catalog_migration_path", lambda: None
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._validate_storage_migration_path", lambda: None
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._verify_installed_revision",
        lambda *_args, **_kwargs: None,
    )
    executor = _executor_factory(effects)
    return update_command(ref=_SHA_B, executor=executor), executor


def test_companion_core_version_failure_accounts_for_companion_verification_steps(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
) -> None:
    command, executor = _companion_verification_command(monkeypatch, tmp_path, {"verify_rc": 1})

    result = command.run()

    assert result.outcome == "update_incomplete"
    assert result.next_step == "odcli --version failed after update"
    assert result.recovery_argv is not None
    assert result.snapshot_state == "present"
    assert result.journal_state == "present"
    executed_ids = {step.step_id for step in executor.executed}
    assert "update.install.companion" in executed_ids
    assert "update.verify.version" in executed_ids
    assert "update.verify.companion.version" not in executed_ids
    assert "update.verify.companion.revisions" not in executed_ids
    assert json.loads((user_root / "update" / "journal.json").read_text())["phase"] == "snapshot"


def test_companion_invalid_maintenance_result_accounts_for_verification_steps(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
) -> None:
    command, executor = _companion_verification_command(
        monkeypatch, tmp_path, {"maintenance_stdout": "{"}
    )

    result = command.run()

    assert result.outcome == "update_incomplete"
    assert result.next_step is not None and result.next_step.startswith(
        "maintenance produced invalid JSON:"
    )
    assert result.recovery_argv is not None
    assert result.snapshot_state == "present"
    assert result.journal_state == "present"
    executed_ids = {step.step_id for step in executor.executed}
    assert "update.install.companion" in executed_ids
    assert "update.verify.version" not in executed_ids
    assert "update.verify.companion.version" not in executed_ids
    assert "update.verify.companion.revisions" not in executed_ids
    assert json.loads((user_root / "update" / "journal.json").read_text())["phase"] == "snapshot"


def test_install_failure_rechecks_revision_before_failing(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    _patch_provenance(monkeypatch, _provenance(commit_id=_SHA_A, executable=executable))
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.shutil.disk_usage",
        lambda _p: type("U", (), {"free": 10 * 1024**3})(),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._catalog_schema_version",
        lambda: "head",
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._validate_catalog_migration_path",
        lambda: None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._validate_storage_migration_path",
        lambda: None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._verify_installed_revision",
        lambda *_args, **_kwargs: None,
    )
    updated = _provenance(commit_id=_SHA_B, executable=executable)
    reads = iter(
        [
            _provenance(commit_id=_SHA_A, executable=executable),
            updated,
            updated,
        ]
    )

    def read_provenance(*_args: object, **_kwargs: object) -> InstalledProvenance:
        return next(reads)

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.read_uv_tool_direct_url",
        read_provenance,
    )
    executor = _executor_factory(
        {
            "install_rc": 1,
            "maintenance_rc": 0,
            "resolve_stdout": f"{_SHA_B}\trefs/heads/main\n",
        }
    )
    result = update(ref="main", executor=executor)
    assert result.outcome == "updated"
    assert result.final_sha == _SHA_B
