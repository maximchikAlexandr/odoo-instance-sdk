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
from odoo_instance_sdk.execution import Command, ProcessStep
from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult, RecordingExecutor
from odoo_instance_sdk.internal.self_update import (
    InstalledProvenance,
    assert_update_not_blocking,
    inspect_update_recovery_state,
    read_uv_tool_direct_url,
    unfinished_update_journal,
    update,
    update_command,
)
from odoo_instance_sdk.models.update import UpdateResult

_SHA_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_SHA_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_SHA_OLD = "0000000000000000000000000000000000000000"
_ANCESTRY_STEPS = [
    "update.inspect.ancestry-init",
    "update.inspect.ancestry-fetch",
    "update.inspect.ancestry-installed",
    "update.inspect.ancestry-target",
]
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
        "odoo_instance_sdk.internal.self_update_commands.read_uv_tool_direct_url",
        lambda *_args, **_kwargs: provenance,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update._assert_runtime_environment",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.self_update_commands._verify_installed_revision",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.self_update.shutil.which", lambda _name: "uv")


def _write_recovery_evidence(
    user_root: Path,
    *,
    journal: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    include_metadata: bool = True,
) -> None:
    update_root = user_root / "update"
    snapshot = update_root / "snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    journal_payload: dict[str, Any] = {
        "version": 1,
        "phase": "migrate",
        "target_ref": _SHA_B,
        "snapshot_sha": _SHA_A,
        "maintenance_pid": None,
    }
    journal_payload.update(journal or {})
    (update_root / "journal.json").write_text(json.dumps(journal_payload), encoding="utf-8")
    if include_metadata:
        metadata_payload: dict[str, Any] = {
            "version": 1,
            "previous_version": "0.1.0",
            "package_revision": "0.1.0",
            "previous_sha": _SHA_A,
            "install_requirement": f"odoo-instance-sdk @ git+https://example.test/repo.git@{_SHA_B}",
            "source_repo": "https://example.test/repo.git",
            "target_ref": _SHA_B,
            "snapshot_sha": _SHA_A,
        }
        metadata_payload.update(metadata or {})
        (snapshot / "metadata.json").write_text(json.dumps(metadata_payload), encoding="utf-8")


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
                stdout=str(effects.get("resolve_stdout", f"{_SHA_B}\trefs/heads/main")),
                stderr=str(effects.get("resolve_stderr", "")),
            )
        if step.step_id == "update.resolve.check":
            return _process_result(
                step,
                returncode=cast("int", effects.get("resolve_rc", 0)),
                stdout=str(effects.get("resolve_stdout", f"{_SHA_B}\trefs/heads/main")),
                stderr=str(effects.get("resolve_stderr", "")),
            )
        if step.step_id == "update.inspect.ancestry-installed":
            relation = effects.get("ancestry_relation", "descendant")
            return _process_result(
                step,
                returncode=0 if relation in {"descendant", "same"} else 1,
            )
        if step.step_id == "update.inspect.ancestry-target":
            relation = effects.get("ancestry_relation", "descendant")
            return _process_result(step, returncode=0 if relation == "ancestor" else 1)
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
        if step.step_id == "update.verify.version":
            return _process_result(
                step,
                returncode=cast("int", effects.get("verify_rc", 0)),
            )
        if step.step_id == "update.recovery":
            return _process_result(step, returncode=cast("int", effects.get("rollback_rc", 0)))
        return _process_result(step)

    return RecordingExecutor(result_factory=factory)


_UPDATE_COMMAND_MATRIX = (
    pytest.param(
        {"ref": _SHA_A},
        {},
        "already_current",
        (),
        id="already-current-sha",
    ),
    pytest.param(
        {"ref": "main", "check": True},
        {"resolve_stdout": f"{_SHA_B}\trefs/heads/main\n"},
        "updated",
        (),
        id="check-resolve",
    ),
    pytest.param(
        {"ref": "main", "check": True},
        {"resolve_stdout": f"{_SHA_A}\trefs/heads/main\n"},
        "already_current",
        (),
        id="check-already-current",
    ),
    pytest.param(
        {"ref": "v0.2.0", "check": True},
        {"resolve_stdout": (f"{_SHA_A}\trefs/tags/v0.2.0\n{_SHA_B}\trefs/tags/v0.2.0^{{}}\n")},
        "updated",
        (),
        id="check-annotated-tag",
    ),
    pytest.param(
        {"ref": _SHA_B, "check": True},
        {},
        "updated",
        (),
        id="check-exact-sha",
    ),
    pytest.param(
        {"ref": "main", "check": True},
        {"resolve_rc": 2, "resolve_stderr": "fatal: remote ref not found"},
        "unsupported_install",
        (),
        id="check-ref-unavailable",
    ),
    pytest.param(
        {"ref": _SHA_OLD, "allow_downgrade": False},
        {"ancestry_relation": "ancestor"},
        "preflight_failed",
        (),
        id="preflight-downgrade-refused",
    ),
    pytest.param(
        {"ref": _SHA_OLD, "allow_downgrade": True},
        {"install_rc": 0, "maintenance_rc": 0, "ancestry_relation": "ancestor"},
        "updated",
        (),
        id="downgrade-allowed",
    ),
)


_UPDATE_COORDINATOR_MATRIX = (
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


def _prepare_update_case(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    effects: dict[str, object],
) -> RecordingExecutor:
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
    return _executor_factory(effects)


@pytest.mark.parametrize(
    ("command_kwargs", "effects", "expected_outcome", "expected_errors"),
    _UPDATE_COMMAND_MATRIX,
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
    executor = _prepare_update_case(monkeypatch, tmp_path, effects)
    command = update_command(**cast("Any", command_kwargs), executor=executor)
    if expected_errors:
        with pytest.raises(expected_errors):
            command.run()
        return

    result = command.run()
    assert result.outcome == expected_outcome
    if expected_outcome == "unsupported_install":
        assert result.manual_argv is not None
        if command_kwargs.get("check") and effects.get("resolve_stderr"):
            assert "remote ref not found" in (result.next_step or "")
    if expected_outcome == "update_incomplete":
        assert result.recovery_argv is not None
        assert result.journal_state == "present"
    if expected_outcome == "rolled_back":
        assert result.rollback_outcome == "restored"
    if expected_outcome == "already_current":
        assert not any(step.step_id.startswith("update.install") for step in executor.executed)
    if expected_outcome == "updated" and not command_kwargs.get("check"):
        expected_steps = [
            *_ANCESTRY_STEPS,
            "update.install",
            "update.migrate",
            "update.verify.version",
        ]
        if str(command_kwargs.get("ref", "")).lower() != _SHA_OLD:
            expected_steps.insert(0, "update.resolve")
        assert [step.step_id for step in executor.executed] == expected_steps
        install = next(
            (step for step in executor.executed if step.step_id == "update.install"), None
        )
        assert command_kwargs.get("ref") != "main" or (
            install is not None and any(_SHA_B in arg for arg in install.argv)
        )


@pytest.mark.parametrize(
    ("command_kwargs", "effects", "expected_outcome", "expected_errors"),
    _UPDATE_COORDINATOR_MATRIX,
)
def test_update_coordinator_matrix(
    monkeypatch: pytest.MonkeyPatch,
    user_root: Path,
    tmp_path: Path,
    command_kwargs: dict[str, str | bool],
    effects: dict[str, object],
    expected_outcome: str | None,
    expected_errors: tuple[type[Exception], ...],
) -> None:
    executor = _prepare_update_case(monkeypatch, tmp_path, effects)
    if expected_errors:
        with pytest.raises(expected_errors):
            update(**cast("Any", command_kwargs), executor=executor)
        return

    result = update(**cast("Any", command_kwargs), executor=executor)
    assert result.outcome == expected_outcome
    if expected_outcome == "unsupported_install":
        assert result.manual_argv is not None
    if expected_outcome == "update_incomplete":
        assert result.recovery_argv is not None
        assert result.journal_state == "present"
    if expected_outcome == "rolled_back":
        assert result.rollback_outcome == "restored"
    if expected_outcome == "updated":
        assert [step.step_id for step in executor.executed] == [
            "update.resolve",
            *_ANCESTRY_STEPS,
            "update.install",
            "update.migrate",
            "update.verify.version",
        ]
        install = next(step for step in executor.executed if step.step_id == "update.install")
        assert any(_SHA_B in arg for arg in install.argv)
        assert not (user_root / "update" / "journal.json").exists()
        assert not (user_root / "update" / "snapshot").exists()


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
    assert command.plan.steps == tuple(
        step.public_projection() for step in command._prepared().steps
    )
    assert executor.executed[0].step_id == "update.resolve"


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
