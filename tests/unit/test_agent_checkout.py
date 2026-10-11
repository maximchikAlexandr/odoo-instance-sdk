from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from odoo_instance_sdk.internal import agent_checkout
from odoo_instance_sdk.internal.agent_checkout import (
    CHECKOUT_MARKER,
    AgentCheckoutError,
    _clone_checkout,
    _next_number,
    _safe_source,
    _validate_checkout,
)
from odoo_instance_sdk.internal.alternate_tool import (
    AlternateManifest,
    identity,
    publish_alternate_linkage,
    read_alternate_manifest,
    render_launcher,
)


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _reference_repo(tmp_path: Path) -> tuple[Path, str]:
    reference = tmp_path / "reference"
    reference.mkdir()
    _git(reference, "init", "-b", "main")
    _git(reference, "config", "user.email", "test@example.test")
    _git(reference, "config", "user.name", "Test")
    (reference / "tracked.txt").write_text("reference\n", encoding="utf-8")
    _git(reference, "add", "tracked.txt")
    _git(reference, "commit", "-m", "initial")
    _git(reference, "remote", "add", "origin", str(reference))
    sha = _git(reference, "rev-parse", "HEAD")
    return reference, sha


def test_allocator_skips_every_occupied_dimension(tmp_path: Path) -> None:
    checkout_parent = tmp_path / "checkouts"
    identity("numbered", "1", home=tmp_path).state_root.mkdir(parents=True)
    identity("numbered", "2", home=tmp_path).manager_root.mkdir(parents=True)
    (checkout_parent / "odcli-3").mkdir(parents=True)

    number, checkout = _next_number(tmp_path, checkout_parent)

    assert number == "4"
    assert checkout == checkout_parent / "odcli-4"


def test_clone_is_independent_and_marker_is_exact(tmp_path: Path) -> None:
    reference, sha = _reference_repo(tmp_path)
    source = _safe_source(reference, sha, str(reference))
    checkout = tmp_path / "checkouts" / "odcli-1"

    marker = _clone_checkout(
        reference,
        checkout,
        number="1",
        repository=source,
        sha=sha,
    )

    assert marker == checkout / ".git" / CHECKOUT_MARKER
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "identifier": "1",
        "kind": "numbered",
        "repository": source,
        "sha": sha,
        "version": 1,
    }
    assert (reference / "tracked.txt").stat().st_ino != (checkout / "tracked.txt").stat().st_ino
    assert _git(checkout, "remote", "get-url", "origin") == source


def test_dirty_or_marker_drift_blocks_external_removal(tmp_path: Path) -> None:
    reference, sha = _reference_repo(tmp_path)
    checkout = tmp_path / "checkouts" / "odcli-1"
    _clone_checkout(reference, checkout, number="1", repository=str(reference), sha=sha)
    (checkout / "uncommitted.txt").write_text("do not remove\n", encoding="utf-8")

    with pytest.raises(AgentCheckoutError, match="dirty"):
        _validate_checkout(
            checkout,
            number="1",
            repository=str(reference),
            sha=sha,
            require_pushed=True,
        )


def test_numbered_manifest_round_trips_fixed_linkage(tmp_path: Path) -> None:
    numbered = identity("numbered", "1", home=tmp_path)
    numbered.tool_root.mkdir(parents=True)
    numbered.tool_bin.mkdir(parents=True)
    numbered.executable.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    numbered.executable.chmod(0o700)
    base = AlternateManifest(
        numbered,
        "a" * 40,
        "https://github.com/maximchikAlexandr/odoo-instance-sdk.git",
        numbered.executable,
        "",
    )
    shim = render_launcher(base)
    base = AlternateManifest(
        numbered,
        base.sha,
        base.repository,
        base.executable,
        shim,
    )
    numbered.launcher.parent.mkdir(parents=True)
    numbered.launcher.write_text(shim, encoding="utf-8")
    numbered.launcher.chmod(0o700)
    numbered.manifest.parent.mkdir(parents=True, exist_ok=True)
    numbered.manifest.write_text(json.dumps(base.to_json()) + "\n", encoding="utf-8")

    linked = publish_alternate_linkage(
        numbered,
        checkout_path=str(tmp_path / "checkout"),
        checkout_repository="https://example.test/project.git",
        checkout_sha="b" * 40,
        environment_id="env-1",
        environment_path=str(tmp_path / "venv"),
        environment_lock=str(tmp_path / "requirements.lock"),
        interpreter_path=str(tmp_path / "venv/bin/python"),
        hash_lock_sha256="c" * 64,
    )

    assert linked.checkout_sha == "b" * 40
    restored = read_alternate_manifest(numbered)
    assert restored is not None
    assert restored.environment_id == "env-1"
    assert restored.hash_lock_sha256 == "c" * 64


def test_remove_order_keeps_linkage_when_environment_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    identity_value = identity("numbered", "1", home=tmp_path)
    identity_value.manager_root.mkdir(parents=True)
    identity_value.lock.touch()
    checkout = tmp_path / "checkouts" / "odcli-1"
    checkout.mkdir(parents=True)
    manifest = SimpleNamespace(
        checkout_path=str(checkout),
        checkout_repository="origin",
        checkout_sha="a" * 40,
        environment_id="env-1",
        environment_path=str(tmp_path / "venv"),
        environment_lock=str(tmp_path / "requirements.lock"),
        interpreter_path=str(tmp_path / "venv/bin/python"),
        hash_lock_sha256="b" * 64,
    )
    environment = SimpleNamespace(
        id="env-1",
        state="ready",
        worktree_path=str(checkout),
        python_environment_path=str(tmp_path / "venv"),
    )
    client = SimpleNamespace(
        environments=SimpleNamespace(
            get=lambda _id: environment,
            remove=lambda _env: (_ for _ in ()).throw(RuntimeError("retain")),
        )
    )
    monkeypatch.setattr(agent_checkout, "read_alternate_manifest", lambda _identity: manifest)
    monkeypatch.setattr(agent_checkout, "_validate_checkout", lambda *args, **kwargs: {})

    with pytest.raises(RuntimeError, match="retain"):
        agent_checkout.remove_numbered_installation("1", client=client, home=tmp_path)

    assert checkout.is_dir()
    assert identity_value.manager_root.is_dir()
