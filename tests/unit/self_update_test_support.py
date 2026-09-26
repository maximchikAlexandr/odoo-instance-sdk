"""Shared test support for the self-update unit slices."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import msgspec

from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult, RecordingExecutor
from odoo_instance_sdk.internal.self_update import InstalledProvenance
from odoo_instance_sdk.models.update import UpdateResult

if TYPE_CHECKING:
    import pytest

__all__ = (
    "_ANCESTRY_STEPS",
    "_SHA_A",
    "_SHA_B",
    "_SHA_OLD",
    "_FakeDist",
    "_executor_factory",
    "_patch_distribution",
    "_patch_provenance",
    "_prepare_update_case",
    "_process_result",
    "_provenance",
    "_write_recovery_evidence",
)

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
