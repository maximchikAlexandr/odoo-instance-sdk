from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.internal import agent_checkout, numbered_command_plan
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
    AlternateIdentity,
    AlternateManifest,
    identity,
    publish_alternate_linkage,
    read_alternate_manifest,
    render_launcher,
)
from odoo_instance_sdk.internal.numbered_command_plan import (
    _CreateCommandPlan,
    _RemoveCommandPlan,
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
        marker.write_text(
            json.dumps(agent_checkout._marker_payload(number, repository, sha)), encoding="utf-8"
        )
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


@dataclass
class _OneShotFault:
    target: str
    real: Callable[..., None]
    before_error: Callable[[], None]
    message: str
    fired: bool = False

    def __call__(self, path: str, *, dir_fd: int | None = None) -> None:
        if path == self.target and not self.fired:
            self.fired = True
            self.before_error()
            raise OSError(self.message)
        self.real(path, dir_fd=dir_fd)


@dataclass
class _StepValidation:
    failures: dict[int, str]
    calls: list[bool] = field(default_factory=list)

    def __call__(
        self,
        _checkout: Path,
        *,
        require_pushed: bool,
        **_kwargs: object,
    ) -> dict[str, object]:
        step = len(self.calls)
        self.calls.append(require_pushed)
        message = self.failures.get(step)
        if message is not None:
            raise AgentCheckoutError(message)
        return {}


@dataclass
class _EnvironmentCleanupFault:
    checkout: Path
    environment: SimpleNamespace
    calls: list[object] = field(default_factory=list)

    def __call__(self, value: object) -> None:
        self.calls.append(value)
        original = self.checkout.with_name("odcli-1-original")
        self.checkout.rename(original)
        marker = self.checkout / ".git" / CHECKOUT_MARKER
        marker.parent.mkdir(parents=True)
        marker.write_text(
            json.dumps(agent_checkout._marker_payload("1", REPOSITORY_URL, "b" * 40)),
            encoding="utf-8",
        )
        self.environment.state = EnvironmentState.REMOVED.value
        raise RuntimeError("environment cleanup interrupted")


def _published_identity(
    tmp_path: Path, checkout: Path, environment: SimpleNamespace
) -> AlternateIdentity:
    identity_value = identity("numbered", "1", home=tmp_path)
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
        checkout_repository=REPOSITORY_URL,
        checkout_sha="b" * 40,
        environment_id=str(environment.id),
        environment_path=environment.python_environment_path,
        environment_lock=environment.dependency_lock_path,
        interpreter_path=str(Path(environment.python_environment_path) / "bin" / "python"),
        hash_lock_sha256=hashlib.sha256(
            Path(environment.dependency_lock_path).read_bytes()
        ).hexdigest(),
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
    return identity_value


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
    real_plan = _CreateCommandPlan

    def altered_plan(**kwargs: object) -> _CreateCommandPlan:
        plan = cast("Callable[..., _CreateCommandPlan]", real_plan.from_options)(**kwargs)
        return replace(
            plan,
            sdk_sha="c" * 40,
            environment_hash_lock_sha256="d" * 64,
        )

    monkeypatch.setattr(
        numbered_command_plan,
        "_CreateCommandPlan",
        SimpleNamespace(from_options=altered_plan),
    )
    monkeypatch.setattr(agent_checkout, "_sha256", lambda _path: "d" * 64)

    result = create_numbered_installation(_numbered_options(tmp_path, reference, sha, client))

    assert result.identity.identifier == "1"
    assert adopted == [tmp_path / "checkouts" / "odcli-1"]
    manifest = read_alternate_manifest(result.identity)
    assert manifest is not None
    assert manifest.checkout_path == str(tmp_path / "checkouts" / "odcli-1")
    assert manifest.sha == "c" * 40
    assert manifest.hash_lock_sha256 == "d" * 64
    assert manifest.environment_id == str(environment.id)
    assert not (result.identity.manager_root / "reservation.json").exists()


def test_delete_failure_with_occupied_original_is_retryable_without_environment_repeat(
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
    assert manifest.checkout_path is None
    assert manifest.environment_id is None
    reservation = json.loads(
        (identity_value.manager_root / "reservation.json").read_text(encoding="utf-8")
    )
    assert reservation["environment_id"] == str(environment.id)
    checkout = tmp_path / "checkouts" / "odcli-1"
    assert checkout.is_dir()
    assert (identity_value.manager_root / "reservation.json").is_file()

    environment.worktree_path = str(checkout)
    environment.checkout_repository_root = str(checkout)
    environment.state = EnvironmentState.REMOVED.value
    (checkout / "owned.txt").write_text("owned\n", encoding="utf-8")
    owned_identity = agent_checkout._checkout_identity(checkout)
    real_unlink = os.unlink
    delete_failures = 1

    def fail_once(path: str, *, dir_fd: int | None = None) -> None:
        nonlocal delete_failures
        if path == "owned.txt" and delete_failures:
            delete_failures -= 1
            checkout.mkdir()
            (checkout / "replacement.txt").write_text("keep\n", encoding="utf-8")
            raise OSError("recursive deletion failed")
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", fail_once)
    monkeypatch.setattr(agent_checkout, "_validate_checkout", lambda *args, **kwargs: {})
    with pytest.raises(OSError, match="recursive deletion failed"):
        agent_checkout.remove_numbered_installation(
            "1", client=cast("OdooClient", client), home=tmp_path
        )

    assert len(remove_calls) == 1
    assert checkout.is_dir()
    assert (checkout / "replacement.txt").is_file()
    recovery = json.loads(
        (identity_value.manager_root / "reservation.json").read_text(encoding="utf-8")
    )
    quarantine = Path(recovery["quarantine"])
    assert quarantine == checkout.parent / ".odcli-deleting" / ".odcli-1.deleting"
    assert quarantine.is_dir()
    assert agent_checkout._checkout_identity(quarantine / "target") == owned_identity
    assert recovery["quarantine_identity"] == f"{owned_identity[0]}:{owned_identity[1]}"

    agent_checkout.remove_numbered_installation(
        "1", client=cast("OdooClient", client), home=tmp_path
    )

    assert len(remove_calls) == 1
    assert (checkout / "replacement.txt").is_file()
    assert not identity_value.manager_root.exists()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "https://GitHub.com/maximchikAlexandr/odoo-instance-sdk.git",
            "https://github.com/maximchikAlexandr/odoo-instance-sdk.git",
        ),
        (
            "ssh://git@GitHub.com/maximchikAlexandr/odoo-instance-sdk.git",
            "ssh://git@github.com/maximchikAlexandr/odoo-instance-sdk.git",
        ),
        (
            "git@GitHub.com:maximchikAlexandr/odoo-instance-sdk.git",
            "ssh://git@github.com/maximchikAlexandr/odoo-instance-sdk.git",
        ),
    ],
)
def test_repository_identity_is_canonical_and_secret_free(source: str, expected: str) -> None:
    assert agent_checkout._canonical_repository_identity(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        "https://github.com/org/repo.git?token=secret",
        "https://github.com/org/repo.git#secret",
        "ext::sh -c 'echo secret'",
        "alice@github.com:org/repo.git",
        "ssh://alice:secret@github.com/org/repo.git",
    ],
)
def test_repository_identity_rejects_secret_bearing_transports(source: str) -> None:
    with pytest.raises(AgentCheckoutError):
        agent_checkout._canonical_repository_identity(source)


def test_remove_execution_consumes_frozen_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkout = tmp_path / "checkouts" / "odcli-1"
    checkout.mkdir(parents=True)
    (checkout / "owned.txt").write_text("owned\n", encoding="utf-8")
    environment = _environment(tmp_path, checkout)
    identity_value = _published_identity(tmp_path, checkout, environment)

    def unexpected_get(_id: object) -> object:
        pytest.fail("remove execution used the pre-plan client")

    def unexpected_remove(_environment: object) -> None:
        pytest.fail("remove execution used the pre-plan client")

    client = SimpleNamespace(
        environments=SimpleNamespace(get=unexpected_get, remove=unexpected_remove)
    )
    plan_client = SimpleNamespace(
        environments=SimpleNamespace(
            get=lambda _id: environment,
            remove=lambda _environment: setattr(
                environment, "state", EnvironmentState.REMOVED.value
            ),
        )
    )
    real_plan = _RemoveCommandPlan

    def altered_plan(*args: object, **kwargs: object) -> _RemoveCommandPlan:
        plan = cast("Callable[..., _RemoveCommandPlan]", real_plan)(*args, **kwargs)
        return replace(plan, client=cast("OdooClient", plan_client))

    monkeypatch.setattr(numbered_command_plan, "_RemoveCommandPlan", altered_plan)
    monkeypatch.setattr(agent_checkout, "_validate_checkout", lambda *args, **kwargs: {})
    agent_checkout.remove_numbered_installation(
        "1", client=cast("OdooClient", client), home=tmp_path
    )

    assert environment.state == EnvironmentState.REMOVED.value
    assert not identity_value.manager_root.exists()


def test_successful_remove_is_ordered_once_and_preserves_neighbor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkout = tmp_path / "checkouts" / "odcli-1"
    checkout.mkdir(parents=True)
    (checkout / "owned.txt").write_text("owned\n", encoding="utf-8")
    neighbor = tmp_path / "checkouts" / "odcli-2"
    neighbor.mkdir()
    environment = _environment(tmp_path, checkout)
    identity_value = _published_identity(tmp_path, checkout, environment)
    environment_remove_order: list[bool] = []

    def remove(value: object) -> None:
        del value
        environment_remove_order.append(checkout.exists())
        environment.state = EnvironmentState.REMOVED.value

    client = SimpleNamespace(
        environments=SimpleNamespace(get=lambda _id: environment, remove=remove)
    )
    monkeypatch.setattr(agent_checkout, "_validate_checkout", lambda *args, **kwargs: {})
    real_rmdir = os.rmdir

    def replace_checkout() -> None:
        checkout.mkdir()
        (checkout / "replacement.txt").write_text("keep\n", encoding="utf-8")

    fault = _OneShotFault(
        target="target",
        real=real_rmdir,
        before_error=replace_checkout,
        message="recursive deletion failed",
    )
    monkeypatch.setattr(os, "rmdir", fault)
    with pytest.raises(OSError, match="recursive deletion failed"):
        agent_checkout.remove_numbered_installation(
            "1", client=cast("OdooClient", client), home=tmp_path
        )

    reservation = identity_value.manager_root / "reservation.json"
    recovery = json.loads(reservation.read_text(encoding="utf-8"))
    assert fault.fired
    assert Path(recovery["quarantine"]).is_dir()
    assert environment_remove_order == [True]
    assert (checkout / "replacement.txt").read_text(encoding="utf-8") == "keep\n"

    agent_checkout.remove_numbered_installation(
        "1", client=cast("OdooClient", client), home=tmp_path
    )

    assert environment_remove_order == [True]
    assert (checkout / "replacement.txt").is_file()
    assert neighbor.is_dir()


def test_private_command_plans_are_frozen_and_redacted(tmp_path: Path) -> None:
    reference, sha = _reference_repo(tmp_path)
    options = _numbered_options(tmp_path, reference, sha, object())
    plan = _CreateCommandPlan.from_options(
        client=options.client,
        project=options.project,
        environment_options=options.environment_options,
        home=tmp_path,
        number="1",
        identity=identity("numbered", "1", home=tmp_path),
        checkout=tmp_path / "checkouts" / "odcli-1",
        reference=reference,
        checkout_parent=tmp_path / "checkouts",
        repository=str(reference),
        reference_sha=sha,
        sdk_sha=options.sdk_sha,
    )

    assert plan.redacted() == {
        "operation": "numbered.create",
        "number": "1",
        "checkout": str(tmp_path / "checkouts" / "odcli-1"),
        "reference": str(reference),
        "checkout_parent": str(tmp_path / "checkouts"),
        "repository": str(reference),
        "reference_sha": sha,
        "sdk_sha": "b" * 40,
    }
    with pytest.raises(FrozenInstanceError):
        plan.home = tmp_path / "other"  # type: ignore[misc]

    manifest = AlternateManifest(
        identity("numbered", "1", home=tmp_path),
        "a" * 40,
        REPOSITORY_URL,
        tmp_path / "tool",
        "",
    )
    remove_plan = _RemoveCommandPlan(
        options.client, tmp_path, "1", manifest.identity, None, manifest
    )
    assert "client" not in remove_plan.redacted()
    assert "options" not in remove_plan.redacted()


def test_remove_rejects_checkout_inode_swap_after_environment_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkout = tmp_path / "checkouts" / "odcli-1"
    checkout.mkdir(parents=True)
    (checkout / "owned.txt").write_text("owned\n", encoding="utf-8")
    neighbor = tmp_path / "checkouts" / "odcli-2"
    neighbor.mkdir()
    (neighbor / "keep.txt").write_text("keep\n", encoding="utf-8")
    identity_value = identity("numbered", "1", home=tmp_path)
    environment = _environment(tmp_path, checkout)
    base = AlternateManifest(
        identity_value, "a" * 40, REPOSITORY_URL, identity_value.executable, ""
    )
    manifest = AlternateManifest(
        identity_value,
        base.sha,
        base.repository,
        base.executable,
        render_launcher(base),
        checkout_path=str(checkout),
        checkout_repository=REPOSITORY_URL,
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

    remove_calls: list[object] = []

    def swap_and_remove(value: object) -> None:
        remove_calls.append(value)
        moved = checkout.with_name("odcli-1-original")
        checkout.rename(moved)
        checkout.mkdir()
        marker = checkout / ".git" / CHECKOUT_MARKER
        marker.parent.mkdir()
        marker.write_text(
            json.dumps(agent_checkout._marker_payload("1", REPOSITORY_URL, "b" * 40)),
            encoding="utf-8",
        )
        environment.state = EnvironmentState.REMOVED.value

    get_calls: list[object] = []

    def get_environment(_id: object) -> SimpleNamespace:
        get_calls.append(_id)
        return environment

    client = SimpleNamespace(
        environments=SimpleNamespace(get=get_environment, remove=swap_and_remove)
    )
    monkeypatch.setattr(agent_checkout, "_validate_checkout", lambda *args, **kwargs: {})

    with pytest.raises(AgentCheckoutError, match="identity changed"):
        agent_checkout.remove_numbered_installation(
            "1", client=cast("OdooClient", client), home=tmp_path
        )

    assert (checkout / ".git" / CHECKOUT_MARKER).is_file()
    assert (checkout.with_name("odcli-1-original") / "owned.txt").is_file()
    assert (neighbor / "keep.txt").is_file()
    recovery = json.loads(
        (identity_value.manager_root / "reservation.json").read_text(encoding="utf-8")
    )
    assert recovery["checkout_identity"] != ":".join(
        str(value) for value in agent_checkout._checkout_identity(checkout)
    )

    with pytest.raises(AgentCheckoutError, match="identity changed before cleanup"):
        agent_checkout.remove_numbered_installation(
            "1", client=cast("OdooClient", client), home=tmp_path
        )

    assert len(remove_calls) == 1
    assert len(get_calls) == 1
    assert (checkout / ".git" / CHECKOUT_MARKER).is_file()
    assert (checkout.with_name("odcli-1-original") / "owned.txt").is_file()


def test_recovery_keeps_published_require_pushed_policy_after_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkout = tmp_path / "checkouts" / "odcli-1"
    checkout.mkdir(parents=True)
    (checkout / "owned.txt").write_text("owned\n", encoding="utf-8")
    environment = _environment(tmp_path, checkout)
    identity_value = _published_identity(tmp_path, checkout, environment)
    remove_calls: list[object] = []

    def remove(value: object) -> None:
        remove_calls.append(value)
        environment.state = EnvironmentState.REMOVED.value

    validation = _StepValidation({1: "unpushed checkout", 2: "unpushed checkout"})
    client = SimpleNamespace(
        environments=SimpleNamespace(get=lambda _id: environment, remove=remove)
    )
    monkeypatch.setattr(agent_checkout, "_validate_checkout", validation)

    with pytest.raises(AgentCheckoutError, match="unpushed checkout"):
        agent_checkout.remove_numbered_installation(
            "1", client=cast("OdooClient", client), home=tmp_path
        )

    with pytest.raises(AgentCheckoutError, match="unpushed checkout"):
        agent_checkout.remove_numbered_installation(
            "1", client=cast("OdooClient", client), home=tmp_path
        )

    recovery = json.loads(
        (identity_value.manager_root / "reservation.json").read_text(encoding="utf-8")
    )
    assert recovery["require_pushed"] is True
    assert validation.calls == [True, True, True]
    assert len(remove_calls) == 1
    assert checkout.is_dir()
    assert (checkout / "owned.txt").is_file()


def test_recovery_rejects_replacement_after_environment_callback_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkout = tmp_path / "checkouts" / "odcli-1"
    checkout.mkdir(parents=True)
    (checkout / "owned.txt").write_text("owned\n", encoding="utf-8")
    environment = _environment(tmp_path, checkout)
    identity_value = _published_identity(tmp_path, checkout, environment)
    original_identity = agent_checkout._checkout_identity(checkout)
    fault = _EnvironmentCleanupFault(checkout, environment)
    get_calls: list[object] = []

    def get_environment(value: object) -> SimpleNamespace:
        get_calls.append(value)
        return environment

    client = SimpleNamespace(environments=SimpleNamespace(get=get_environment, remove=fault))
    monkeypatch.setattr(agent_checkout, "_validate_checkout", lambda *args, **kwargs: {})

    with pytest.raises(RuntimeError, match="environment cleanup interrupted"):
        agent_checkout.remove_numbered_installation(
            "1", client=cast("OdooClient", client), home=tmp_path
        )

    recovery = json.loads(
        (identity_value.manager_root / "reservation.json").read_text(encoding="utf-8")
    )
    assert recovery["require_pushed"] is True
    assert recovery["checkout_identity"] == f"{original_identity[0]}:{original_identity[1]}"

    with pytest.raises(AgentCheckoutError, match="identity changed before cleanup"):
        agent_checkout.remove_numbered_installation(
            "1", client=cast("OdooClient", client), home=tmp_path
        )

    assert len(fault.calls) == 1
    assert len(get_calls) == 1
    assert environment.state == EnvironmentState.REMOVED.value
    assert (checkout / ".git" / CHECKOUT_MARKER).is_file()
    assert (checkout.with_name("odcli-1-original") / "owned.txt").is_file()


def test_quarantine_rejects_post_quarantine_entry_swap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkout = tmp_path / "checkouts" / "odcli-1"
    checkout.mkdir(parents=True)
    (checkout / "owned.txt").write_text("owned\n", encoding="utf-8")
    expected = agent_checkout._checkout_identity(checkout)
    swapped = False
    real_rmdir = os.rmdir

    def swap_after_final_identity_check(path: str, *, dir_fd: int | None = None) -> None:
        nonlocal swapped
        if path == "target" and not swapped:
            swapped = True
            checkout.mkdir()
        real_rmdir(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "rmdir", swap_after_final_identity_check)

    agent_checkout._remove_owned_checkout(checkout, expected)

    assert swapped
    assert checkout.is_dir()
    assert not (checkout / "owned.txt").exists()
    assert not (checkout.parent / ".odcli-deleting" / ".odcli-1.deleting").exists()


def test_quarantine_parent_swap_stays_bound_to_original_parent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parent = tmp_path / "checkouts"
    checkout = parent / "odcli-1"
    checkout.mkdir(parents=True)
    (checkout / "owned.txt").write_text("owned\n", encoding="utf-8")
    expected = agent_checkout._checkout_identity(checkout)
    real_fwalk = os.fwalk
    swapped_parent = tmp_path / "checkouts-original"

    def swap_parent(
        top: str, *, topdown: bool, dir_fd: int, follow_symlinks: bool
    ) -> Iterator[tuple[str, list[str], list[str], int]]:
        parent.rename(swapped_parent)
        parent.mkdir()
        (parent / "neighbor.txt").write_text("keep\n", encoding="utf-8")
        return real_fwalk(top, topdown=topdown, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "fwalk", swap_parent)

    agent_checkout._remove_owned_checkout(checkout, expected)

    assert (parent / "neighbor.txt").read_text(encoding="utf-8") == "keep\n"
    assert not (swapped_parent / "odcli-1").exists()


def test_recovery_rejects_marker_drift_before_environment_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reference, sha = _reference_repo(tmp_path)
    checkout = tmp_path / "checkouts" / "odcli-1"
    repository = str(reference.resolve())
    _clone_checkout(reference, checkout, number="1", repository=repository, sha=sha)
    environment = _environment(tmp_path, checkout)
    numbered = identity("numbered", "1", home=tmp_path)
    numbered.manager_root.mkdir(parents=True)
    numbered.lock.touch()
    base = AlternateManifest(numbered, "a" * 40, REPOSITORY_URL, numbered.executable, "")
    numbered.manifest.parent.mkdir(parents=True, exist_ok=True)
    numbered.manifest.write_text(json.dumps(base.to_json()), encoding="utf-8")
    reservation = numbered.manager_root / "reservation.json"
    reservation.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "numbered",
                "identifier": "1",
                "checkout": str(checkout),
                "repository": repository,
                "checkout_sha": sha,
                "reference_sha": sha,
                "sdk_sha": "b" * 40,
                "environment_id": str(environment.id),
                "environment_path": environment.python_environment_path,
                "environment_lock": environment.dependency_lock_path,
                "interpreter_path": str(
                    Path(environment.python_environment_path) / "bin" / "python"
                ),
                "hash_lock_sha256": hashlib.sha256(
                    Path(environment.dependency_lock_path).read_bytes()
                ).hexdigest(),
                "quarantine": "",
                "quarantine_identity": "",
                "checkout_identity": ":".join(
                    str(value) for value in agent_checkout._checkout_identity(checkout)
                ),
                "require_pushed": False,
            }
        ),
        encoding="utf-8",
    )
    marker = checkout / ".git" / CHECKOUT_MARKER
    marker_payload = json.loads(marker.read_text(encoding="utf-8"))
    marker_payload["sha"] = "f" * 40
    marker.write_text(json.dumps(marker_payload), encoding="utf-8")
    get_calls: list[str] = []

    def get_environment(value: str) -> None:
        get_calls.append(value)

    def refuse_cleanup(_environment: object) -> None:
        pytest.fail("cleanup must not run")

    client = SimpleNamespace(
        environments=SimpleNamespace(get=get_environment, remove=refuse_cleanup)
    )
    monkeypatch.setattr(agent_checkout, "read_alternate_manifest", lambda _identity: base)

    with pytest.raises(AgentCheckoutError, match="marker"):
        agent_checkout.remove_numbered_installation(
            "1", client=cast("OdooClient", client), home=tmp_path
        )

    assert get_calls == []
    assert checkout.is_dir()


def test_recovery_rejects_symlinked_reservation_without_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    numbered = identity("numbered", "1", home=tmp_path)
    numbered.manager_root.mkdir(parents=True)
    numbered.lock.touch()
    base = AlternateManifest(numbered, "a" * 40, REPOSITORY_URL, numbered.executable, "")
    numbered.manifest.parent.mkdir(parents=True, exist_ok=True)
    numbered.manifest.write_text(json.dumps(base.to_json()), encoding="utf-8")
    target = tmp_path / "reservation-target.json"
    target.write_text("{}", encoding="utf-8")
    reservation = numbered.manager_root / "reservation.json"
    reservation.symlink_to(target)
    get_calls: list[str] = []

    def get_environment(value: str) -> None:
        get_calls.append(value)

    def refuse_cleanup(_environment: object) -> None:
        pytest.fail("cleanup must not run")

    client = SimpleNamespace(
        environments=SimpleNamespace(get=get_environment, remove=refuse_cleanup)
    )
    monkeypatch.setattr(agent_checkout, "read_alternate_manifest", lambda _identity: base)

    with pytest.raises(AgentCheckoutError, match="symlinked"):
        agent_checkout.remove_numbered_installation(
            "1", client=cast("OdooClient", client), home=tmp_path
        )

    assert get_calls == []


def test_recovery_rejects_reservation_schema_drift_without_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    numbered = identity("numbered", "1", home=tmp_path)
    numbered.manager_root.mkdir(parents=True)
    numbered.lock.touch()
    base = AlternateManifest(numbered, "a" * 40, REPOSITORY_URL, numbered.executable, "")
    numbered.manifest.parent.mkdir(parents=True, exist_ok=True)
    numbered.manifest.write_text(json.dumps(base.to_json()), encoding="utf-8")
    (numbered.manager_root / "reservation.json").write_text(
        json.dumps({"version": 1, "kind": "numbered", "identifier": "1"}),
        encoding="utf-8",
    )
    get_calls: list[str] = []

    def get_environment(value: str) -> None:
        get_calls.append(value)

    def refuse_cleanup(_environment: object) -> None:
        pytest.fail("cleanup must not run")

    client = SimpleNamespace(
        environments=SimpleNamespace(get=get_environment, remove=refuse_cleanup)
    )
    monkeypatch.setattr(agent_checkout, "read_alternate_manifest", lambda _identity: base)

    with pytest.raises(AgentCheckoutError, match="schema drifted"):
        agent_checkout.remove_numbered_installation(
            "1", client=cast("OdooClient", client), home=tmp_path
        )

    assert get_calls == []


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
        sha="c" * 40,
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
        dependency_lock_path=str(tmp_path / "requirements.lock"),
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
