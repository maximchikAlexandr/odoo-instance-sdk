"""Unit tests for ``odcli update`` / ``update_command()``."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from odoo_instance_sdk.exceptions import (
    LockConflictError,
    UpdateError,
)
from odoo_instance_sdk.execution import ProcessStep
from odoo_instance_sdk.internal.proc import RecordingExecutor
from odoo_instance_sdk.internal.self_update import (
    InstalledProvenance,
    read_uv_tool_direct_url,
    update,
    update_command,
)

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
    monkeypatch.setattr("odoo_instance_sdk.internal.self_update.shutil.which", lambda _name: "uv")
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
    monkeypatch.setattr("odoo_instance_sdk.internal.self_update.shutil.which", lambda _name: "uv")
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.get_user_root",
        lambda **_: tmp_path / ".odcli",
    )
    (tmp_path / ".odcli").mkdir()

    provenance = read_uv_tool_direct_url()

    assert provenance.source_repo == "https://github.com/maximchikAlexandr/odoo-instance-sdk.git"
    assert provenance.commit_id == _SHA_A


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
