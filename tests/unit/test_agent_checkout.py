from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.internal import agent_checkout
from odoo_instance_sdk.internal.agent_checkout import (
    CHECKOUT_MARKER,
    AgentCheckoutError,
    NumberedCheckoutOptions,
    _clone_checkout,
    _next_number,
    _safe_source,
    _validate_checkout,
    create_numbered_installation,
)
from odoo_instance_sdk.internal.alternate_tool import (
    REPOSITORY_URL,
    AlternateManifest,
    identity,
    publish_alternate_linkage,
    read_alternate_manifest,
    render_launcher,
)
from odoo_instance_sdk.models import EnvironmentDatabaseMode, EnvironmentState
from odoo_instance_sdk.resources.environment import EnvironmentCheckoutOptions


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


def _environment_options(tmp_path: Path) -> EnvironmentCheckoutOptions:
    lock = tmp_path / "requirements.lock"
    lock.write_text("odoo==1\n", encoding="utf-8")
    return EnvironmentCheckoutOptions(
        db_mode=EnvironmentDatabaseMode.COPY,
        base_ref="main",
        python=str(tmp_path / "python"),
        create_venv=True,
        odoo_bin=tmp_path / "odoo-bin",
        hash_lock=lock,
        hash_lock_sha256=hashlib.sha256(lock.read_bytes()).hexdigest(),
        remote_name="staging",
    )


def _environment(tmp_path: Path, checkout: Path) -> SimpleNamespace:
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    lock = tmp_path / "environment.lock"
    lock.write_text("odoo==1\n", encoding="utf-8")
    return SimpleNamespace(
        id=uuid.uuid4(),
        state=EnvironmentState.READY,
        worktree_path=str(checkout),
        checkout_repository_root=str(checkout),
        python_environment_path=str(venv),
        python_environment_owned=True,
        dependency_lock_path=str(lock),
        http_port=8069,
        target_db_name="copy-db",
    )


def _fake_install(monkeypatch: pytest.MonkeyPatch) -> None:
    def install(identity_value: object, sha: str, **_kwargs: object) -> AlternateManifest:
        identity_value.manager_root.mkdir(parents=True, exist_ok=True)  # type: ignore[attr-defined]
        identity_value.tool_root.mkdir()  # type: ignore[attr-defined]
        identity_value.tool_bin.mkdir()  # type: ignore[attr-defined]
        identity_value.executable.write_text("#!/usr/bin/env python3\n", encoding="utf-8")  # type: ignore[attr-defined]
        identity_value.executable.chmod(0o700)  # type: ignore[attr-defined]
        base = AlternateManifest(
            identity_value,  # type: ignore[arg-type]
            sha,
            REPOSITORY_URL,
            identity_value.executable,  # type: ignore[attr-defined]
            "",
        )
        manifest = AlternateManifest(
            identity_value,  # type: ignore[arg-type]
            sha,
            REPOSITORY_URL,
            identity_value.executable,  # type: ignore[attr-defined]
            render_launcher(base),
        )
        identity_value.launcher.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[attr-defined]
        identity_value.launcher.write_text(manifest.shim, encoding="utf-8")  # type: ignore[attr-defined]
        identity_value.launcher.chmod(0o700)  # type: ignore[attr-defined]
        identity_value.lock.touch()  # type: ignore[attr-defined]
        identity_value.manifest.write_text(  # type: ignore[attr-defined]
            json.dumps(manifest.to_json()) + "\n", encoding="utf-8"
        )
        return manifest

    monkeypatch.setattr(agent_checkout, "install_alternate_tool", install)


def _fake_clone(monkeypatch: pytest.MonkeyPatch) -> None:
    def clone(
        _reference: Path,
        checkout: Path,
        *,
        number: str,
        repository: str,
        sha: str,
    ) -> Path:
        checkout.mkdir(parents=True)
        marker = checkout / ".git" / CHECKOUT_MARKER
        marker.parent.mkdir()
        marker.write_text(json.dumps({"number": number, "repository": repository, "sha": sha}))
        return marker

    monkeypatch.setattr(agent_checkout, "_clone_checkout", clone)


def _numbered_options(
    tmp_path: Path,
    reference: Path,
    sha: str,
    client: object,
) -> NumberedCheckoutOptions:
    return NumberedCheckoutOptions(
        client=client,  # type: ignore[arg-type]
        project=tmp_path,
        reference_checkout=reference,
        checkout_parent=tmp_path / "checkouts",
        reference_sha=sha,
        sdk_sha="b" * 40,
        environment_options=_environment_options(tmp_path),
        home=tmp_path,
    )


def test_allocator_skips_every_occupied_dimension(tmp_path: Path) -> None:
    checkout_parent = tmp_path / "checkouts"
    identity("numbered", "1", home=tmp_path).state_root.mkdir(parents=True)
    identity("numbered", "2", home=tmp_path).manager_root.mkdir(parents=True)
    (checkout_parent / "odcli-3").mkdir(parents=True)

    number, checkout = _next_number(tmp_path, checkout_parent)

    assert number == "4"
    assert checkout == checkout_parent / "odcli-4"


def test_concurrent_allocations_reserve_distinct_dimensions(tmp_path: Path) -> None:
    checkout_parent = tmp_path / "checkouts"

    def reserve() -> str:
        with agent_checkout.allocation_lock(tmp_path):
            number, checkout = _next_number(tmp_path, checkout_parent)
            identity("numbered", number, home=tmp_path).manager_root.mkdir(
                parents=True, exist_ok=False
            )
            checkout.mkdir(parents=True)
            return number

    with ThreadPoolExecutor(max_workers=2) as executor:
        numbers = sorted(executor.map(lambda _item: reserve(), range(2)))

    assert numbers == ["1", "2"]


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


def test_marker_drift_blocks_external_removal(tmp_path: Path) -> None:
    reference, sha = _reference_repo(tmp_path)
    checkout = tmp_path / "checkouts" / "odcli-1"
    marker = _clone_checkout(reference, checkout, number="1", repository=str(reference), sha=sha)
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["sha"] = "f" * 40
    marker.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(AgentCheckoutError, match="marker"):
        _validate_checkout(
            checkout,
            number="1",
            repository=str(reference),
            sha=sha,
            require_pushed=False,
        )


def test_unpushed_commit_blocks_external_removal(tmp_path: Path) -> None:
    reference, sha = _reference_repo(tmp_path)
    checkout = tmp_path / "checkouts" / "odcli-1"
    _clone_checkout(reference, checkout, number="1", repository=str(reference), sha=sha)
    _git(checkout, "config", "user.email", "test@example.test")
    _git(checkout, "config", "user.name", "Test")
    _git(checkout, "branch", "--set-upstream-to=origin/main", "main")
    (checkout / "tracked.txt").write_text("unpushed\n", encoding="utf-8")
    _git(checkout, "add", "tracked.txt")
    _git(checkout, "commit", "-m", "unpushed")
    current_sha = _git(checkout, "rev-parse", "HEAD")
    marker = checkout / ".git" / CHECKOUT_MARKER
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["sha"] = current_sha
    marker.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(AgentCheckoutError, match="unpushed"):
        _validate_checkout(
            checkout,
            number="1",
            repository=str(reference),
            sha=current_sha,
            require_pushed=True,
        )


def test_symlink_and_containment_guards_fail_closed(tmp_path: Path) -> None:
    reference, sha = _reference_repo(tmp_path)
    real_checkout = tmp_path / "real" / "odcli-1"
    _clone_checkout(reference, real_checkout, number="1", repository=str(reference), sha=sha)
    symlink = tmp_path / "checkouts" / "odcli-1"
    symlink.parent.mkdir()
    symlink.symlink_to(real_checkout, target_is_directory=True)

    with pytest.raises(AgentCheckoutError, match="symlink"):
        _validate_checkout(
            symlink,
            number="1",
            repository=str(reference),
            sha=sha,
            require_pushed=False,
        )
    with pytest.raises(AgentCheckoutError, match="escapes"):
        _validate_checkout(
            real_checkout,
            number="1",
            repository=str(reference),
            sha=sha,
            require_pushed=False,
            checkout_parent=tmp_path / "expected",
        )


def test_attempt_owned_clone_rollback_removes_partial_checkout(tmp_path: Path) -> None:
    reference, _sha = _reference_repo(tmp_path)
    checkout = tmp_path / "checkouts" / "odcli-1"

    with pytest.raises(AgentCheckoutError):
        _clone_checkout(
            reference,
            checkout,
            number="1",
            repository=str(reference),
            sha="f" * 40,
        )

    assert not checkout.exists()


def test_create_adopts_and_publishes_fixed_linkage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reference, sha = _reference_repo(tmp_path)
    adopted: list[Path] = []
    environment = _environment(tmp_path, tmp_path / "placeholder")

    def adopt(_project: object, checkout: Path, *, options: object) -> SimpleNamespace:
        del options
        environment.worktree_path = str(checkout)
        environment.checkout_repository_root = str(checkout)
        adopted.append(checkout)
        return environment

    client = SimpleNamespace(environments=SimpleNamespace(adopt=adopt))
    _fake_install(monkeypatch)
    _fake_clone(monkeypatch)

    result = create_numbered_installation(_numbered_options(tmp_path, reference, sha, client))

    assert result.identity.identifier == "1"
    assert adopted == [tmp_path / "checkouts" / "odcli-1"]
    manifest = read_alternate_manifest(result.identity)
    assert manifest is not None
    assert manifest.checkout_path == str(tmp_path / "checkouts" / "odcli-1")
    assert manifest.environment_id == str(environment.id)
    assert not (result.identity.manager_root / "reservation.json").exists()


def test_post_adopt_failure_retains_linkage_for_retry_after_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reference, sha = _reference_repo(tmp_path)
    environment = _environment(tmp_path, tmp_path / "wrong-checkout")
    remove_calls: list[object] = []

    def adopt(_project: object, _checkout: Path, *, options: object) -> SimpleNamespace:
        del options
        return environment

    def fail_remove(value: object) -> None:
        remove_calls.append(value)
        raise RuntimeError("environment cleanup retained")

    client = SimpleNamespace(
        environments=SimpleNamespace(adopt=adopt, remove=fail_remove, get=lambda _id: environment)
    )
    _fake_install(monkeypatch)
    _fake_clone(monkeypatch)

    with pytest.raises(RuntimeError, match="retained"):
        create_numbered_installation(_numbered_options(tmp_path, reference, sha, client))

    identity_value = identity("numbered", "1", home=tmp_path)
    manifest = read_alternate_manifest(identity_value)
    assert manifest is not None
    assert manifest.environment_id == str(environment.id)
    checkout = tmp_path / "checkouts" / "odcli-1"
    assert checkout.is_dir()
    assert (identity_value.manager_root / "reservation.json").is_file()

    environment.worktree_path = str(checkout)
    environment.checkout_repository_root = str(checkout)
    environment.state = EnvironmentState.REMOVED.value
    monkeypatch.setattr(agent_checkout, "_validate_checkout", lambda *args, **kwargs: {})
    agent_checkout.remove_numbered_installation(
        "1", client=cast("OdooClient", client), home=tmp_path
    )

    assert len(remove_calls) == 1
    assert not checkout.exists()
    assert not identity_value.manager_root.exists()


def test_successful_remove_is_ordered_once_and_preserves_neighbor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkout = tmp_path / "checkouts" / "odcli-1"
    checkout.mkdir(parents=True)
    neighbor = tmp_path / "checkouts" / "odcli-2"
    neighbor.mkdir()
    identity_value = identity("numbered", "1", home=tmp_path)
    environment = _environment(tmp_path, checkout)
    base = AlternateManifest(
        identity_value,
        "a" * 40,
        REPOSITORY_URL,
        identity_value.executable,
        "",
    )
    manifest = AlternateManifest(
        identity_value,
        base.sha,
        base.repository,
        base.executable,
        render_launcher(base),
        checkout_path=str(checkout),
        checkout_repository="origin",
        checkout_sha="b" * 40,
        environment_id=str(environment.id),
        environment_path=environment.python_environment_path,
        environment_lock=environment.dependency_lock_path,
        interpreter_path=str(Path(environment.python_environment_path) / "bin" / "python"),
        hash_lock_sha256="c" * 64,
    )
    identity_value.manager_root.mkdir(parents=True, exist_ok=True)
    identity_value.tool_root.mkdir()
    identity_value.tool_bin.mkdir()
    identity_value.executable.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    identity_value.executable.chmod(0o700)
    identity_value.launcher.parent.mkdir(parents=True, exist_ok=True)
    identity_value.launcher.write_text(manifest.shim, encoding="utf-8")
    identity_value.launcher.chmod(0o700)
    identity_value.lock.touch()
    identity_value.manifest.write_text(json.dumps(manifest.to_json()), encoding="utf-8")
    environment_remove_order: list[bool] = []

    def remove(_environment: object) -> None:
        environment_remove_order.append(checkout.exists())
        environment.state = EnvironmentState.REMOVED.value

    client = SimpleNamespace(
        environments=SimpleNamespace(get=lambda _id: environment, remove=remove)
    )
    monkeypatch.setattr(agent_checkout, "_validate_checkout", lambda *args, **kwargs: {})
    agent_checkout.remove_numbered_installation(
        "1", client=cast("OdooClient", client), home=tmp_path
    )

    assert environment_remove_order == [True]
    assert not checkout.exists()
    assert neighbor.is_dir()


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
        agent_checkout.remove_numbered_installation(
            "1", client=cast("OdooClient", client), home=tmp_path
        )

    assert checkout.is_dir()
    assert identity_value.manager_root.is_dir()
