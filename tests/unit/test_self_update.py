"""Unit tests for ``odcli update`` / ``update_command()``."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import msgspec
import pytest

from odoo_instance_sdk.exceptions import (
    LockConflictError,
    UpdateError,
    UpdateIncompleteError,
)
from odoo_instance_sdk.execution import ProcessStep
from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult, RecordingExecutor
from odoo_instance_sdk.internal.self_update import (
    InstalledProvenance,
    assert_update_not_blocking,
    read_uv_tool_direct_url,
    unfinished_update_journal,
    update_command,
)
from odoo_instance_sdk.models.update import UpdateResult

_SHA_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_SHA_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_SHA_OLD = "0000000000000000000000000000000000000000"
_VCS_DIRECT_URL = json.dumps(
    {
        "url": f"git+https://github.com/maximchikAlexandr/odoo-instance-sdk.git@{_SHA_A}",
        "vcs_info": {
            "vcs": "git",
            "commit_id": _SHA_A,
            "requested_revision": "main",
        },
    }
)
_MAINTENANCE_JSON = json.dumps(
    msgspec.to_builtins(
        UpdateResult(
            outcome="updated",
            source_repo="https://github.com/maximchikAlexandr/odoo-instance-sdk.git",
            previous_version="0.1.0",
            target_version="0.1.0",
            final_version="0.1.0",
            previous_sha=_SHA_B,
            target_sha=_SHA_B,
            final_sha=_SHA_B,
            executed_migration_ids=("catalog:head",),
            final_schema_versions={"catalog": "head", "storage": "complete"},
        )
    )
)


class _FakeDist:
    def __init__(self, *, version: str = "0.1.0", direct_url: str | None = _VCS_DIRECT_URL) -> None:
        self.version = version
        self._direct_url = direct_url
        self.files = None

    def read_text(self, filename: str) -> str | None:
        if filename == "direct_url.json":
            return self._direct_url
        return None


def _provenance(
    *,
    commit_id: str = _SHA_A,
    executable: Path | None = None,
) -> InstalledProvenance:
    return InstalledProvenance(
        version="0.1.0",
        source_repo="https://github.com/maximchikAlexandr/odoo-instance-sdk.git",
        commit_id=commit_id,
        requested_revision="main",
        is_uv_tool_vcs=True,
        uv_tool_bin_path=executable,
        uv_tool_env_path=Path("/tmp/uv-tool-env"),
        manual_argv=None,
    )


def _patch_provenance(
    monkeypatch: pytest.MonkeyPatch,
    provenance: InstalledProvenance,
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.read_uv_tool_direct_url",
        lambda *_args, **_kwargs: provenance,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._assert_runtime_environment",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.self_update.shutil.which", lambda _name: "uv")


def _patch_distribution(monkeypatch: pytest.MonkeyPatch, dist: _FakeDist) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update.distribution",
        lambda _name: dist,
    )


@pytest.fixture
def user_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".odcli"
    root.mkdir()
    locks = root / "locks"
    locks.mkdir()
    monkeypatch.setattr("odoo_instance_sdk.internal.self_update.get_user_root", lambda **_: root)
    monkeypatch.setattr("odoo_instance_sdk.internal.self_update.get_locks_dir", lambda **_: locks)
    return root


def _process_result(
    step: PreparedStep,
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> ProcessResult:
    return ProcessResult(
        argv=step.argv,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration=0.0,
        cwd=None,
        environment=(),
    )


def _executor_factory(effects: dict[str, object]) -> RecordingExecutor:
    def factory(step):
        if step.step_id == "update.resolve":
            return _process_result(
                step,
                returncode=cast("int", effects.get("resolve_rc", 0)),
                stdout=str(effects.get("uv_stdout", f"install {_SHA_B}")),
                stderr=str(effects.get("uv_stderr", "")),
            )
        if step.step_id == "update.resolve.check":
            return _process_result(
                step,
                returncode=cast("int", effects.get("install_rc", 0)),
                stdout=str(effects.get("uv_stdout", f"install {_SHA_B}")),
                stderr=str(effects.get("uv_stderr", "")),
            )
        if step.step_id == "update.install":
            return _process_result(step, returncode=cast("int", effects.get("install_rc", 0)))
        if step.step_id == "update.migrate":
            return _process_result(
                step,
                returncode=cast("int", effects.get("maintenance_rc", 0)),
                stdout=_MAINTENANCE_JSON
                if cast("int", effects.get("maintenance_rc", 0)) == 0
                else "",
            )
        if step.step_id == "update.recovery":
            return _process_result(step, returncode=cast("int", effects.get("rollback_rc", 0)))
        return _process_result(step)

    return RecordingExecutor(result_factory=factory)


_UPDATE_MATRIX = (
    pytest.param(
        {"ref": _SHA_A},
        {},
        "already_current",
        (),
        id="already-current-sha",
    ),
    pytest.param(
        {"ref": "main"},
        {"direct_url": json.dumps({"url": "https://example.com/pkg.whl"})},
        "unsupported_install",
        (),
        id="unsupported-wheel",
    ),
    pytest.param(
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
        (),
        id="unsupported-repo",
    ),
    pytest.param(
        {"ref": "main", "check": True},
        {"uv_stdout": f"would install {_SHA_B}\n"},
        "updated",
        (),
        id="check-resolve",
    ),
    pytest.param(
        {"ref": "main", "check": True},
        {"uv_stdout": f"resolved {_SHA_A}\n"},
        "already_current",
        (),
        id="check-already-current",
    ),
    pytest.param(
        {"ref": "main", "check": True},
        {"install_rc": 2, "uv_stderr": "error: unexpected argument '--dry-run' found"},
        "unsupported_install",
        (),
        id="check-dry-run-unsupported",
    ),
    pytest.param(
        {"ref": _SHA_OLD, "allow_downgrade": False},
        {},
        "preflight_failed",
        (),
        id="preflight-downgrade-refused",
    ),
    pytest.param(
        {"ref": _SHA_OLD, "allow_downgrade": True},
        {"install_rc": 0, "maintenance_rc": 0},
        "updated",
        (),
        id="downgrade-allowed",
    ),
    pytest.param(
        {"ref": "main"},
        {"install_rc": 1},
        None,
        (UpdateError,),
        id="install-failure",
    ),
    pytest.param(
        {"ref": "main"},
        {"install_rc": 0, "maintenance_rc": 1, "rollback_rc": 1},
        "update_incomplete",
        (),
        id="migration-failure-incomplete",
    ),
    pytest.param(
        {"ref": "main"},
        {"install_rc": 0, "maintenance_rc": 1, "rollback_rc": 0},
        "rolled_back",
        (),
        id="migration-failure-rollback",
    ),
    pytest.param(
        {"ref": "main"},
        {"install_rc": 0, "maintenance_rc": 0},
        "updated",
        (),
        id="successful-update",
    ),
)


@pytest.mark.parametrize(
    ("command_kwargs", "effects", "expected_outcome", "expected_errors"),
    _UPDATE_MATRIX,
)
def test_update_command_matrix(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
    command_kwargs: dict[str, str | bool],
    effects: dict[str, object],
    expected_outcome: str | None,
    expected_errors: tuple[type[Exception], ...],
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    provenance = _provenance(commit_id=_SHA_A, executable=executable)
    direct_url = effects.get("direct_url", _VCS_DIRECT_URL)
    _patch_distribution(monkeypatch, _FakeDist(direct_url=cast("str | None", direct_url)))
    if "direct_url" not in effects:
        _patch_provenance(monkeypatch, provenance)
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
    executor = _executor_factory(effects)
    if expected_errors:
        with pytest.raises(expected_errors):
            command = update_command(
                **cast("Any", command_kwargs),
                executor=executor,
            )
            command.run()
        return

    command = update_command(**cast("Any", command_kwargs), executor=executor)

    result = command.run()
    assert result.outcome == expected_outcome
    if expected_outcome == "unsupported_install":
        assert result.manual_argv is not None
        if command_kwargs.get("check") and effects.get("uv_stderr"):
            assert "uv " in (result.next_step or "")
            assert "dry-run" in (result.next_step or "").lower()
    if expected_outcome == "update_incomplete":
        assert result.recovery_argv is not None
        assert result.journal_state == "present"
    if expected_outcome == "rolled_back":
        assert result.rollback_outcome == "restored"
    if expected_outcome == "already_current":
        assert not any(step.step_id.startswith("update.install") for step in executor.executed)
    if expected_outcome == "updated" and not command_kwargs.get("check"):
        expected_steps = ["update.install", "update.migrate"]
        if str(command_kwargs.get("ref", "")).lower() != _SHA_OLD:
            expected_steps.insert(0, "update.resolve")
        assert [step.step_id for step in executor.executed] == expected_steps


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


def test_unfinished_journal_blocks_other_commands(user_root: Path) -> None:
    journal = user_root / "update" / "journal.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(
        json.dumps(
            {
                "version": 1,
                "phase": "migrate",
                "target_ref": "main",
                "snapshot_sha": _SHA_A,
                "maintenance_pid": None,
            }
        ),
        encoding="utf-8",
    )
    assert unfinished_update_journal() is not None
    with pytest.raises(UpdateIncompleteError, match="doctor"):
        assert_update_not_blocking("doctor")


def test_mutually_exclusive_check_and_dry_run_cli() -> None:
    from click.testing import CliRunner

    from odoo_instance_sdk.commands.update import update_command_cli

    result = CliRunner().invoke(
        update_command_cli,
        ["--check", "--dry-run"],
        prog_name="odcli",
    )
    assert result.exit_code == 2


def test_dry_run_command_has_frozen_process_steps(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    _patch_distribution(monkeypatch, _FakeDist())
    _patch_provenance(monkeypatch, _provenance(executable=executable))
    command = update_command(ref="main", dry_run=True)
    process_steps = [step for step in command.plan.steps if isinstance(step, ProcessStep)]
    assert len(process_steps) == 2
    assert process_steps[0].argv[0] == "uv"
    assert "update" in process_steps[1].argv
    assert process_steps[1].argv[-2:] == ("--format", "json")


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
        update_command(ref="main", executor=_executor_factory({})).run()


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
        update_command(
            ref="main",
            executor=_executor_factory({"install_rc": 1}),
        ).run()
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
    result = update_command(ref="main", executor=_executor_factory({})).run()
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
            "uv_stdout": f"installed {_SHA_B}\n",
        }
    )
    result = update_command(ref="main", executor=executor).run()
    assert result.outcome == "updated"
    assert result.final_sha == _SHA_B


def test_update_resumes_from_migrate_journal(
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
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._verify_installed_revision",
        lambda *_args, **_kwargs: None,
    )
    journal = user_root / "update" / "journal.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(
        json.dumps(
            {
                "version": 1,
                "phase": "migrate",
                "target_ref": _SHA_B,
                "snapshot_sha": _SHA_A,
                "maintenance_pid": None,
            }
        ),
        encoding="utf-8",
    )
    executor = _executor_factory({"maintenance_rc": 0})
    result = update_command(ref=_SHA_B, executor=executor).run()
    assert result.outcome == "updated"
    assert [step.step_id for step in executor.executed] == ["update.migrate"]


def test_migrate_resume_rollback_uses_journal_snapshot_sha(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    _patch_provenance(monkeypatch, _provenance(commit_id=_SHA_B, executable=executable))
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
    journal = user_root / "update" / "journal.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(
        json.dumps(
            {
                "version": 1,
                "phase": "migrate",
                "target_ref": _SHA_B,
                "snapshot_sha": _SHA_A,
                "maintenance_pid": None,
            }
        ),
        encoding="utf-8",
    )
    executor = _executor_factory({"maintenance_rc": 1, "rollback_rc": 0})
    result = update_command(ref=_SHA_B, executor=executor).run()
    assert result.outcome == "rolled_back"
    recovery_steps = [step for step in executor.executed if step.step_id == "update.recovery"]
    assert len(recovery_steps) == 1
    recovery_argv = " ".join(recovery_steps[0].argv)
    assert _SHA_A in recovery_argv
    assert _SHA_B not in recovery_argv


def test_update_resumes_from_install_journal(
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
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._verify_installed_revision",
        lambda *_args, **_kwargs: None,
    )
    journal = user_root / "update" / "journal.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(
        json.dumps(
            {
                "version": 1,
                "phase": "install",
                "target_ref": "main",
                "snapshot_sha": _SHA_A,
                "maintenance_pid": None,
            }
        ),
        encoding="utf-8",
    )
    executor = _executor_factory({"install_rc": 0, "maintenance_rc": 0})
    result = update_command(ref="main", executor=executor).run()
    assert result.outcome == "updated"
    assert [step.step_id for step in executor.executed] == ["update.resolve", "update.migrate"]


def test_update_no_input_without_yes_exits_before_mutations() -> None:
    from click.testing import CliRunner

    from odoo_instance_sdk.commands.update import update_command_cli

    result = CliRunner().invoke(
        update_command_cli,
        ["--no-input"],
        prog_name="odcli",
    )
    assert result.exit_code == 1
    assert "requires --yes" in result.output
