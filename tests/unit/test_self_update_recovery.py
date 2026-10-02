"""Recovery-state and resume-flow tests for ``odcli update``."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from odoo_instance_sdk.exceptions import UpdateError, UpdateIncompleteError
from odoo_instance_sdk.execution import Command
from odoo_instance_sdk.internal.proc import RecordingExecutor
from odoo_instance_sdk.internal.self_update import (
    assert_update_not_blocking,
    inspect_update_recovery_state,
    unfinished_update_journal,
    update,
    update_command,
)
from odoo_instance_sdk.internal.self_update_recovery import build_update_recovery_projection
from odoo_instance_sdk.models.update import UpdateResult

from .self_update_test_support import (
    _ANCESTRY_STEPS,
    _SHA_A,
    _SHA_B,
    _SHA_OLD,
    _executor_factory,
    _patch_provenance,
    _prepare_update_case,
    _provenance,
    _write_recovery_evidence,
)


def test_unfinished_journal_blocks_other_commands(user_root: Path) -> None:
    _write_recovery_evidence(
        user_root,
        journal={
            "phase": "migrate",
            "target_ref": _SHA_B,
            "snapshot_sha": _SHA_A,
            "maintenance_pid": None,
        },
    )
    assert unfinished_update_journal() is not None
    with pytest.raises(UpdateIncompleteError, match="doctor"):
        assert_update_not_blocking("doctor")


def test_recovery_inspection_reports_absent_without_creating_paths(user_root: Path) -> None:
    update_root = user_root / "update"

    state = inspect_update_recovery_state()

    assert state.journal_state == "absent"
    assert state.snapshot_state == "absent"
    assert state.valid is True
    assert not update_root.exists()


def test_check_reports_matching_sha_as_incomplete_with_dead_pid(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    _patch_provenance(monkeypatch, _provenance(commit_id=_SHA_A, executable=executable))
    _write_recovery_evidence(
        user_root,
        journal={
            "target_ref": _SHA_A,
            "snapshot_sha": _SHA_B,
            "maintenance_pid": 2_000_000,
        },
        metadata={"target_ref": _SHA_A, "snapshot_sha": _SHA_B},
    )
    update_root = user_root / "update"
    snapshot = update_root / "snapshot"
    state = inspect_update_recovery_state()
    assert state.maintenance_pid_alive is False
    executor = _executor_factory({})

    result = update_command(ref=_SHA_A, check=True, executor=executor).run()

    assert result.outcome == "update_incomplete"
    assert result.target_sha == _SHA_A
    assert result.journal_state == "present"
    assert result.snapshot_state == "present"
    assert result.recovery_argv == (
        str(executable.absolute()),
        "update",
        "--ref",
        _SHA_A,
        "--yes",
    )
    assert executor.executed == []
    assert (update_root / "journal.json").is_file()
    assert snapshot.is_dir()


def test_recovery_projection_owns_frozen_resume_command(
    user_root: Path,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "odcli"
    _write_recovery_evidence(
        user_root,
        journal={
            "target_ref": _SHA_A,
            "snapshot_sha": _SHA_B,
            "maintenance_pid": None,
        },
        metadata={"target_ref": _SHA_A, "snapshot_sha": _SHA_B},
    )

    projection = build_update_recovery_projection(user_root, executable)

    assert projection.resume_command is not None
    assert projection.resume_command.argv == (
        str(executable.absolute()),
        "update",
        "--ref",
        _SHA_A,
        "--yes",
    )
    with pytest.raises(FrozenInstanceError):
        projection.resume_command.argv = ()  # type: ignore[misc]


def test_check_preserves_malformed_recovery_evidence_without_resume_argv(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    _patch_provenance(monkeypatch, _provenance(executable=executable))
    update_root = user_root / "update"
    (update_root / "snapshot").mkdir(parents=True)
    (update_root / "journal.json").write_text(
        json.dumps(
            {
                "version": 1,
                "phase": "migrate",
                "target_ref": "not-an-immutable-sha",
                "snapshot_sha": _SHA_A,
                "maintenance_pid": None,
            }
        ),
        encoding="utf-8",
    )

    result = update_command(ref=_SHA_B, check=True, executor=_executor_factory({})).run()

    assert result.outcome == "update_incomplete"
    assert result.recovery_argv is None
    assert result.journal_state == "present"
    assert result.snapshot_state == "present"
    assert "immutable target_ref" in (result.next_step or "")


@pytest.mark.parametrize("version", [None, 2])
def test_check_rejects_missing_or_unsupported_journal_version(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
    version: int | None,
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    _patch_provenance(monkeypatch, _provenance(executable=executable))
    _write_recovery_evidence(user_root)
    journal_path = user_root / "update" / "journal.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if version is None:
        del journal["version"]
    else:
        journal["version"] = version
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    state = inspect_update_recovery_state()
    result = update_command(ref=_SHA_B, check=True, executor=_executor_factory({})).run()

    assert state.valid is False
    assert state.recoverable is False
    assert result.outcome == "update_incomplete"
    assert result.recovery_argv is None
    assert result.journal_state == "present"
    assert result.snapshot_state == "present"
    assert "version" in (result.next_step or "")


@pytest.mark.parametrize(
    ("metadata", "include_metadata"),
    [
        (None, False),
        ({"version": "invalid"}, True),
        ({"target_ref": _SHA_A}, True),
    ],
)
def test_check_rejects_missing_malformed_or_inconsistent_snapshot_metadata(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
    metadata: dict[str, Any] | None,
    include_metadata: bool,
) -> None:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    _patch_provenance(monkeypatch, _provenance(executable=executable))
    _write_recovery_evidence(
        user_root,
        metadata=metadata,
        include_metadata=include_metadata,
    )

    state = inspect_update_recovery_state()
    result = update_command(ref=_SHA_B, check=True, executor=_executor_factory({})).run()

    assert state.valid is False
    assert state.recoverable is False
    assert result.outcome == "update_incomplete"
    assert result.recovery_argv is None
    assert result.journal_state == "present"
    assert result.snapshot_state == "present"
    assert "snapshot metadata" in (result.next_step or "")


def test_verify_failure_consumes_version_once_and_preserves_recovery_state(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
) -> None:
    executor = _prepare_update_case(monkeypatch, tmp_path, {"verify_rc": 1})

    result = update(ref="main", executor=executor)

    assert result.outcome == "update_incomplete"
    assert [step.step_id for step in executor.executed].count("update.verify.version") == 1
    assert not any(step.step_id == "update.commit" for step in executor.executed)
    assert result.journal_state == "present"
    assert result.snapshot_state == "present"
    assert (user_root / "update" / "journal.json").is_file()
    assert (user_root / "update" / "snapshot").is_dir()


def _snapshot_resume_command(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
    *,
    installed_sha: str,
    executor: RecordingExecutor,
) -> Command[UpdateResult]:
    executable = tmp_path / "odcli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    _patch_provenance(monkeypatch, _provenance(commit_id=installed_sha, executable=executable))
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

    def fail_snapshot(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("snapshot overwritten")

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_commands._write_snapshot", fail_snapshot
    )
    _write_recovery_evidence(
        user_root,
        journal={
            "phase": "snapshot",
            "target_ref": _SHA_B,
            "snapshot_sha": _SHA_A,
            "maintenance_pid": None,
        },
    )
    return update_command(ref="main", executor=executor)


def test_snapshot_resume_installs_after_crash_before_install(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
) -> None:
    executor = _executor_factory({"maintenance_rc": 0})
    command = _snapshot_resume_command(
        monkeypatch,
        user_root,
        tmp_path,
        installed_sha=_SHA_A,
        executor=executor,
    )
    result = command.run()
    assert result.outcome == "updated"
    install = next(step for step in executor.executed if step.step_id == "update.install")
    assert any(_SHA_B in arg for arg in install.argv)


def test_snapshot_resume_skips_install_after_crash_before_journal_write(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
) -> None:
    executor = _executor_factory({"maintenance_rc": 0})
    command = _snapshot_resume_command(
        monkeypatch,
        user_root,
        tmp_path,
        installed_sha=_SHA_B,
        executor=executor,
    )
    result = command.run()
    assert result.outcome == "updated"
    assert [step.step_id for step in executor.executed] == [
        "update.migrate",
        "update.verify.version",
    ]


def test_snapshot_resume_rejects_unexpected_installed_revision(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
) -> None:
    executor = _executor_factory({"maintenance_rc": 0})
    command = _snapshot_resume_command(
        monkeypatch,
        user_root,
        tmp_path,
        installed_sha=_SHA_OLD,
        executor=executor,
    )
    with pytest.raises(UpdateError, match="unexpected installed revision"):
        command.run()
    assert [step.step_id for step in executor.executed] == _ANCESTRY_STEPS


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
    _write_recovery_evidence(
        user_root,
        journal={
            "phase": "migrate",
            "target_ref": _SHA_B,
            "snapshot_sha": _SHA_A,
            "maintenance_pid": None,
        },
    )
    executor = _executor_factory({"maintenance_rc": 0})
    result = update_command(ref=_SHA_B, executor=executor).run()
    assert result.outcome == "updated"
    assert [step.step_id for step in executor.executed] == [
        *_ANCESTRY_STEPS,
        "update.migrate",
        "update.verify.version",
    ]


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
    _write_recovery_evidence(
        user_root,
        journal={
            "phase": "migrate",
            "target_ref": _SHA_B,
            "snapshot_sha": _SHA_A,
            "maintenance_pid": None,
        },
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
    _write_recovery_evidence(
        user_root,
        journal={
            "phase": "install",
            "target_ref": _SHA_B,
            "snapshot_sha": _SHA_A,
            "maintenance_pid": None,
        },
    )
    executor = _executor_factory({"install_rc": 0, "maintenance_rc": 0})
    result = update_command(ref="main", executor=executor).run()
    assert result.outcome == "updated"
    assert [step.step_id for step in executor.executed] == [
        *_ANCESTRY_STEPS,
        "update.migrate",
        "update.verify.version",
    ]
