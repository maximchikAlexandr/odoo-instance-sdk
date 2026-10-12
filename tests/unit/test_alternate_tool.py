from __future__ import annotations

import contextlib
import importlib
import json
import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from odoo_instance_sdk.internal import alternate_tool
from odoo_instance_sdk.internal.alternate_tool import (
    REPOSITORY_URL,
    AlternateIdentity,
    AlternateManifest,
    AlternateToolError,
    identity,
    inspect_alternate_tool,
    lifecycle_lock,
    list_alternate_tools,
    remove_alternate_tool,
    render_launcher,
)
from odoo_instance_sdk.internal.paths import get_user_root
from odoo_instance_sdk.internal.proc import ProcessResult


def _published(identity_value: AlternateIdentity, *, sha: str = "a" * 40) -> AlternateManifest:
    identity_value.tool_root.mkdir(mode=0o700, parents=True)
    identity_value.tool_bin.mkdir(mode=0o700, parents=True)
    identity_value.executable.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    identity_value.executable.chmod(0o700)
    manifest = AlternateManifest(
        identity_value,
        sha,
        "https://github.com/maximchikAlexandr/odoo-instance-sdk.git",
        identity_value.executable,
        "",
        pr=119 if identity_value.kind == "hotfix" else None,
        review_sha=sha if identity_value.kind == "hotfix" else None,
        review_session="session" if identity_value.kind == "hotfix" else None,
    )
    shim = render_launcher(manifest)
    manifest = AlternateManifest(
        identity_value,
        sha,
        manifest.repository,
        identity_value.executable,
        shim,
        pr=manifest.pr,
        review_sha=manifest.review_sha,
        review_session=manifest.review_session,
    )
    identity_value.launcher.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    identity_value.launcher.write_text(shim, encoding="utf-8")
    identity_value.launcher.chmod(0o700)
    identity_value.manifest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    identity_value.manifest.write_text(json.dumps(manifest.to_json()) + "\n", encoding="utf-8")
    identity_value.manifest.chmod(0o600)
    return manifest


class _StagedRunner:
    """Deterministic process double keyed by the lifecycle step being exercised."""

    def __init__(
        self,
        interpreter: Path,
        provenance_sha: str,
        *,
        stage_barrier: threading.Barrier | None = None,
        uv_symlink: bool = False,
    ) -> None:
        self.interpreter = interpreter
        self.provenance_sha = provenance_sha
        self.stage_barrier = stage_barrier
        self.uv_symlink = uv_symlink
        self.steps: list[str] = []

    def _stage(self, env: dict[str, str] | None) -> str:
        assert env is not None
        Path(env["UV_TOOL_DIR"]).mkdir(mode=0o700, parents=True)
        tool_bin = Path(env["UV_TOOL_BIN_DIR"])
        tool_bin.mkdir(mode=0o700, parents=True)
        binary = tool_bin / "odcli"
        script_interpreter = self.interpreter
        if self.uv_symlink:
            package_bin = Path(env["UV_TOOL_DIR"]) / "odoo-instance-sdk" / "bin"
            package_bin.mkdir(mode=0o700, parents=True)
            interpreter = package_bin / "python"
            interpreter.write_text('#!/bin/sh\nexec /usr/bin/env python3 "$@"\n', encoding="utf-8")
            interpreter.chmod(0o700)
            binary = package_bin / "odcli"
            script_interpreter = interpreter
        binary.write_text(f"#!{script_interpreter}\n", encoding="utf-8")
        binary.chmod(0o700)
        if self.uv_symlink:
            (tool_bin / "odcli").symlink_to(binary)
        if self.stage_barrier is not None:
            self.stage_barrier.wait(timeout=5)
        return ""

    def _provenance(self, _env: dict[str, str] | None) -> str:
        return json.dumps(
            {
                "url": "https://github.com/maximchikAlexandr/odoo-instance-sdk.git",
                "vcs_info": {"commit_id": self.provenance_sha},
            }
        )

    def _empty(self, _env: dict[str, str] | None) -> str:
        return ""

    def __call__(
        self,
        executable: str,
        args: tuple[str, ...] = (),
        *,
        env: dict[str, str] | None = None,
        step_id: str = "",
        **_kwargs: object,
    ) -> ProcessResult:
        self.steps.append(step_id)
        response = {
            "alternate-tool-install": self._stage,
            "alternate-provenance": self._provenance,
        }.get(step_id, self._empty)(env)
        return ProcessResult((executable, *args), 0, response, "", 0.0, None, ())


@contextlib.contextmanager
def _reject_exclusive_lock(_identity_value: AlternateIdentity, *, exclusive: bool) -> Iterator[int]:
    if exclusive:
        raise AlternateToolError("alternate launcher is still running: odcli-1")
    yield 0


def _probe_success(
    _identity_value: AlternateIdentity, _interpreter: Path, _env: dict[str, str]
) -> None:
    return None


@dataclass(slots=True)
class _InstallAttempt:
    identity_value: AlternateIdentity
    successes: list[AlternateManifest]
    errors: list[AlternateToolError]

    def __call__(self) -> None:
        try:
            self.successes.append(
                alternate_tool.install_alternate_tool(self.identity_value, "c" * 40)
            )
        except AlternateToolError as error:
            self.errors.append(error)


@pytest.mark.parametrize("value", ["0", "-1", "01", " 1", "1 ", "one"])
def test_identity_rejects_noncanonical_numbers(value: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive decimal"):
        identity("numbered", value, home=tmp_path)


def test_explicit_root_does_not_change_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from odoo_instance_sdk.internal import paths

    importlib.reload(paths)
    selected = tmp_path / "selected"
    monkeypatch.setenv("ODCLI_USER_ROOT", str(selected))
    original_home = os.environ.get("HOME")
    assert get_user_root() == selected.resolve()
    assert os.environ.get("HOME") == original_home


def test_numbered_and_hotfix_share_renderer_inspection_and_cleanup(tmp_path: Path) -> None:
    numbered = identity("numbered", "1", home=tmp_path)
    hotfix = identity("hotfix", "42", home=tmp_path)
    _published(numbered)
    _published(hotfix, sha="b" * 40)

    assert inspect_alternate_tool(numbered)["healthy"] is True
    assert inspect_alternate_tool(hotfix)["healthy"] is True
    listed = list_alternate_tools("numbered", home=tmp_path)
    assert listed[0]["identifier"] == "1"
    assert listed[0]["state"] == str(tmp_path / ".odcli-1")
    assert "ODCLI_USER_ROOT" in numbered.launcher.read_text(encoding="utf-8")
    assert "ODCLI_USER_ROOT" in hotfix.launcher.read_text(encoding="utf-8")

    remove_alternate_tool(numbered)
    assert not numbered.launcher.exists()
    assert hotfix.launcher.exists()
    assert hotfix.manager_root.exists()


def test_running_alternate_cannot_be_removed(tmp_path: Path) -> None:
    numbered = identity("numbered", "1", home=tmp_path)
    _published(numbered)
    with (
        lifecycle_lock(numbered, exclusive=True),
        pytest.raises(AlternateToolError, match="still running"),
    ):
        remove_alternate_tool(numbered)


def test_remove_rejects_identity_swapped_manifest_after_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    numbered = identity("numbered", "1", home=tmp_path)
    neighboring = identity("numbered", "2", home=tmp_path)
    _published(numbered)
    _published(neighboring, sha="b" * 40)
    for identity_value in (numbered, neighboring):
        identity_value.state_root.mkdir(mode=0o700)
        (identity_value.state_root / "sentinel").write_text("keep", encoding="utf-8")

    @contextlib.contextmanager
    def swapping_lock(_identity_value: AlternateIdentity, *, exclusive: bool) -> Iterator[int]:
        assert exclusive
        original = numbered.manifest.read_bytes()
        numbered.manifest.write_bytes(neighboring.manifest.read_bytes())
        try:
            yield 0
        finally:
            numbered.manifest.write_bytes(original)

    monkeypatch.setattr(alternate_tool, "lifecycle_lock", swapping_lock)
    with pytest.raises(AlternateToolError, match="identity mismatch"):
        remove_alternate_tool(numbered)

    for identity_value in (numbered, neighboring):
        assert identity_value.launcher.exists()
        assert identity_value.manifest.exists()
        assert identity_value.tool_root.exists()
        assert identity_value.tool_bin.exists()
        assert (identity_value.state_root / "sentinel").read_text(encoding="utf-8") == "keep"


def test_install_uses_one_staged_provenance_probe_and_preserves_numbered_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    numbered = identity("numbered", "1", home=tmp_path)
    _published(numbered)
    numbered.state_root.mkdir(mode=0o700)
    sentinel = numbered.state_root / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    interpreter = tmp_path / "python"
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    runner = _StagedRunner(interpreter, "c" * 40)
    monkeypatch.setattr(alternate_tool, "run_captured", runner)
    manifest = alternate_tool.install_alternate_tool(numbered, "c" * 40, replace=True)

    assert manifest.sha == "c" * 40
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert inspect_alternate_tool(numbered)["sha"] == "c" * 40
    assert runner.steps == [
        "alternate-tool-install",
        "alternate-provenance",
        "alternate-selector-probe",
    ]


def test_install_materializes_uv_tool_bin_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    numbered = identity("numbered", "1", home=tmp_path)
    interpreter = tmp_path / "python"
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    runner = _StagedRunner(interpreter, "c" * 40, uv_symlink=True)
    monkeypatch.setattr(alternate_tool, "run_captured", runner)

    manifest = alternate_tool.install_alternate_tool(numbered, "c" * 40)

    assert manifest.sha == "c" * 40
    assert numbered.executable.is_file()
    assert not numbered.executable.is_symlink()
    expected_interpreter = numbered.tool_root / "odoo-instance-sdk" / "bin" / "python"
    assert numbered.executable.read_text(encoding="utf-8").splitlines()[0] == (
        f"#!{expected_interpreter}"
    )
    assert expected_interpreter.is_file()
    assert inspect_alternate_tool(numbered)["healthy"] is True


def test_numbered_install_accepts_only_its_prepared_state_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    numbered = identity("numbered", "1", home=tmp_path)
    numbered.state_root.mkdir(mode=0o700)
    sentinel = numbered.state_root / "sentinel"
    sentinel.write_text("environment", encoding="utf-8")
    interpreter = tmp_path / "python"
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(alternate_tool, "run_captured", _StagedRunner(interpreter, "c" * 40))

    with pytest.raises(AlternateToolError, match="already exists"):
        alternate_tool.install_alternate_tool(numbered, "c" * 40)
    manifest = alternate_tool.install_alternate_tool(
        numbered, "c" * 40, allow_existing_state_root=True
    )

    assert manifest.sha == "c" * 40
    assert sentinel.read_text(encoding="utf-8") == "environment"


def test_hotfix_install_uses_the_shared_provenance_and_probe_lifecycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hotfix = identity("hotfix", "42", home=tmp_path)
    interpreter = tmp_path / "python"
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    runner = _StagedRunner(interpreter, "d" * 40)
    monkeypatch.setattr(alternate_tool, "run_captured", runner)
    manifest = alternate_tool.install_alternate_tool(
        hotfix,
        "d" * 40,
        replace=False,
        pr=119,
        review_sha="d" * 40,
        review_session="session",
    )

    assert manifest.identity == hotfix
    assert inspect_alternate_tool(hotfix)["sha"] == "d" * 40
    assert manifest.pr == 119
    assert runner.steps == [
        "alternate-tool-install",
        "alternate-provenance",
        "alternate-selector-probe",
    ]

    remove_alternate_tool(hotfix)
    assert not hotfix.launcher.exists()


def test_install_rejects_invalid_sha_before_creating_any_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    numbered = identity("numbered", "1", home=tmp_path)
    monkeypatch.setattr(
        alternate_tool, "run_captured", lambda *_args, **_kwargs: pytest.fail("unexpected run")
    )
    with pytest.raises(ValueError, match="complete lowercase SHA"):
        alternate_tool.install_alternate_tool(numbered, "not-a-sha")
    assert not numbered.manager_root.exists()
    assert not numbered.state_root.exists()


def test_install_without_replace_preserves_existing_slot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    numbered = identity("numbered", "1", home=tmp_path)
    _published(numbered)
    original = numbered.manifest.read_bytes()
    monkeypatch.setattr(
        alternate_tool, "run_captured", lambda *_args, **_kwargs: pytest.fail("unexpected run")
    )
    with pytest.raises(AlternateToolError, match="already exists"):
        alternate_tool.install_alternate_tool(numbered, "b" * 40)
    assert numbered.manifest.read_bytes() == original


def test_provenance_mismatch_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    numbered = identity("numbered", "1", home=tmp_path)
    interpreter = tmp_path / "python"
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    interpreter.chmod(0o700)

    monkeypatch.setattr(alternate_tool, "run_captured", _StagedRunner(interpreter, "b" * 40))
    with pytest.raises(AlternateToolError, match="does not match"):
        alternate_tool.install_alternate_tool(numbered, "c" * 40)
    assert not numbered.launcher.exists()
    assert not numbered.manifest.exists()
    assert not numbered.state_root.exists()


def test_running_slot_blocks_replacement_before_publication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    numbered = identity("numbered", "1", home=tmp_path)
    _published(numbered)

    monkeypatch.setattr(alternate_tool, "lifecycle_lock", _reject_exclusive_lock)
    interpreter = tmp_path / "python"
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(alternate_tool, "_interpreter", lambda _path: interpreter)
    monkeypatch.setattr(
        alternate_tool,
        "_provenance",
        lambda _path, _env: {"repository": REPOSITORY_URL, "sha": "b" * 40},
    )
    monkeypatch.setattr(alternate_tool, "_probe", _probe_success)
    monkeypatch.setattr(alternate_tool, "run_captured", _StagedRunner(interpreter, "b" * 40))
    with pytest.raises(AlternateToolError, match="still running"):
        alternate_tool.install_alternate_tool(numbered, "b" * 40, replace=True)


def test_remove_rejects_symlinked_lock_and_launcher(
    tmp_path: Path,
) -> None:
    numbered = identity("numbered", "1", home=tmp_path)
    neighboring = identity("numbered", "2", home=tmp_path)
    _published(numbered)
    _published(neighboring, sha="b" * 40)
    numbered.lock.parent.mkdir(mode=0o700, exist_ok=True)
    numbered.lock.touch(mode=0o600)
    numbered.lock.unlink()
    numbered.lock.symlink_to(neighboring.lock)
    with pytest.raises(AlternateToolError, match=r"identity mismatch|symlink"):
        remove_alternate_tool(numbered)
    numbered.lock.unlink()
    numbered.lock.touch(mode=0o600)
    numbered.launcher.unlink()
    numbered.launcher.symlink_to(neighboring.launcher)
    with pytest.raises(AlternateToolError, match=r"identity mismatch|symlink"):
        remove_alternate_tool(numbered)
    assert neighboring.launcher.exists()
    assert neighboring.manifest.exists()


def test_concurrent_no_replace_install_has_one_winner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    numbered = identity("numbered", "1", home=tmp_path)
    interpreter = tmp_path / "python"
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    interpreter.chmod(0o700)
    runner = _StagedRunner(interpreter, "c" * 40, stage_barrier=threading.Barrier(2))
    monkeypatch.setattr(alternate_tool, "run_captured", runner)
    successes: list[AlternateManifest] = []
    errors: list[AlternateToolError] = []
    attempts = tuple(_InstallAttempt(numbered, successes, errors) for _ in range(2))
    threads = [threading.Thread(target=attempt) for attempt in attempts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert len(successes) == 1
    assert len(errors) == 1
    assert inspect_alternate_tool(numbered)["sha"] == "c" * 40
