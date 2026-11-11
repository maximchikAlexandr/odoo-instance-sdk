from __future__ import annotations

import stat
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest

from scripts.real_odoo_pins import E2E_PINS
from tests.integration.real_odoo import conftest as e2e_fixtures


def _success(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _write_consumer_checkout(root: Path) -> Path:
    root.mkdir()
    package = root / "odoo"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "release.py").write_text("version_info = (19, 0)\n", encoding="utf-8")
    odoo_bin = root / "odoo-bin"
    odoo_bin.write_text(
        "from odoo.release import version_info\n"
        "print(f'Odoo Server {version_info[0]}.{version_info[1]}')\n",
        encoding="utf-8",
    )
    return odoo_bin


class BootstrapSubprocess:
    def __init__(self) -> None:
        self.uv_calls: list[tuple[str, ...]] = []
        self.import_probes: list[tuple[str, ...]] = []
        self.commands: list[list[str]] = []
        self.source_commit = E2E_PINS.odoo_source_commit
        self.real_run = subprocess.run

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.commands.append(argv)
        if argv[0] == "uv":
            self.uv_calls.append(tuple(argv[1:]))
            if argv[1] == "venv":
                environment = Path(argv[-1])
                (environment / "bin").mkdir(parents=True)
                (environment / "bin" / "python").symlink_to(sys.executable)
                return _success()
            if argv[1:3] == ["pip", "sync"]:
                return _success()
            return _success(f"uv {E2E_PINS.uv}\n")
        if len(argv) == 2 and argv[1] == "--version":
            return _success(f"Python {E2E_PINS.cpython}\n")
        if argv[0] == "git":
            return _success(f"{self.source_commit}\n")
        if len(argv) > 2 and argv[-1:] == ["--version"]:
            self.import_probes.append(tuple(argv))
        return self.real_run(argv, **kwargs)


@pytest.fixture
def bootstrap_subprocess(monkeypatch: pytest.MonkeyPatch) -> BootstrapSubprocess:
    platform_module = cast("ModuleType", getattr(e2e_fixtures, "platform"))
    monkeypatch.setattr(platform_module, "system", lambda: "Linux")
    monkeypatch.setattr(platform_module, "machine", lambda: "x86_64")
    subprocess_module = cast("ModuleType", getattr(e2e_fixtures, "subprocess"))
    fake = BootstrapSubprocess()
    monkeypatch.setattr(subprocess_module, "run", fake)
    return fake


@pytest.fixture
def pinned_bootstrap(
    tmp_path: Path, bootstrap_subprocess: BootstrapSubprocess
) -> Iterator[tuple[Path, e2e_fixtures.PinnedOdooBootstrap]]:
    root = tmp_path / "pinned"
    bootstrap = e2e_fixtures._build_pinned_odoo_bootstrap(root)
    bootstrap_subprocess.commands.clear()
    yield root, bootstrap
    e2e_fixtures._remove_bootstrap_root(root, tmp_path, bootstrap.filesystem_state)


def test_bootstrap_is_built_once_and_each_consumer_runs_its_own_pinned_import(
    tmp_path: Path,
    bootstrap_subprocess: BootstrapSubprocess,
    pinned_bootstrap: tuple[Path, e2e_fixtures.PinnedOdooBootstrap],
) -> None:
    root, bootstrap = pinned_bootstrap
    consumer_a = _write_consumer_checkout(tmp_path / "consumer-a")
    consumer_b = _write_consumer_checkout(tmp_path / "consumer-b")

    assert (
        e2e_fixtures.verify_odoo_runtime_python(bootstrap, consumer_b.parent, consumer_b)
        == bootstrap.python
    )
    assert Path(bootstrap_subprocess.import_probes[0][1]).parent == consumer_b.parent
    assert (
        e2e_fixtures.verify_odoo_runtime_python(bootstrap, consumer_a.parent, consumer_a)
        == bootstrap.python
    )
    assert Path(bootstrap_subprocess.import_probes[1][1]).parent == consumer_a.parent

    assert [call[0] for call in bootstrap_subprocess.uv_calls] == ["venv", "pip", "--version"]
    assert "--require-hashes" in bootstrap_subprocess.uv_calls[1]
    assert len(bootstrap_subprocess.import_probes) == 2
    assert all(
        not (path.lstat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        for path in root.rglob("*")
        if not path.is_symlink()
    )
    assert e2e_fixtures._bootstrap_filesystem_state(root) == bootstrap.filesystem_state
    assert root.exists()


def test_bootstrap_rejects_changed_lock_before_any_subprocess(
    bootstrap_subprocess: BootstrapSubprocess,
    pinned_bootstrap: tuple[Path, e2e_fixtures.PinnedOdooBootstrap],
) -> None:
    root, bootstrap = pinned_bootstrap
    changed = replace(bootstrap, lock_sha256="0" * 64)

    with pytest.raises(AssertionError, match="platform or lock binding changed"):
        e2e_fixtures.verify_odoo_runtime_python(changed, root, root / "odoo-bin")
    assert bootstrap_subprocess.commands == []


def test_bootstrap_rejects_changed_source_before_odoo_import(
    bootstrap_subprocess: BootstrapSubprocess,
    pinned_bootstrap: tuple[Path, e2e_fixtures.PinnedOdooBootstrap],
    tmp_path: Path,
) -> None:
    _root, bootstrap = pinned_bootstrap
    source = tmp_path / "source"
    source.mkdir()
    odoo_bin = source / "odoo-bin"
    odoo_bin.write_text("print('Odoo Server 19.0')\n", encoding="utf-8")
    bootstrap_subprocess.source_commit = "different-source-commit"

    with pytest.raises(AssertionError, match="pinned source commit"):
        e2e_fixtures.verify_odoo_runtime_python(bootstrap, source, odoo_bin)
    assert bootstrap_subprocess.commands == [
        ["git", "-C", str(source.resolve()), "rev-parse", "HEAD"]
    ]
    assert bootstrap_subprocess.import_probes == []


def test_bootstrap_cleanup_reports_mutation_and_removes_only_its_owned_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "owned-bootstrap"
    root.mkdir(mode=0o700)
    artifact = root / "artifact"
    artifact.write_text("original", encoding="utf-8")
    state = e2e_fixtures._bootstrap_filesystem_state(root)
    artifact.chmod(0o600)
    artifact.write_text("changed", encoding="utf-8")

    with pytest.raises(AssertionError, match="was modified by a consumer"):
        e2e_fixtures._remove_bootstrap_root(root, tmp_path, state)
    assert not root.exists()


def test_bootstrap_cleanup_refuses_to_touch_a_root_outside_session_owner(
    tmp_path: Path,
) -> None:
    foreign_parent = tmp_path / "foreign-owner"
    root = foreign_parent / "bootstrap"
    root.mkdir(parents=True)
    marker = root / "keep"
    marker.write_text("outside owner", encoding="utf-8")

    with pytest.raises(AssertionError, match="refusing to remove unowned"):
        e2e_fixtures._remove_bootstrap_root(root, tmp_path, None)

    assert marker.read_text(encoding="utf-8") == "outside owner"


def test_bootstrap_creator_failure_releases_partial_owned_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Request:
        finalizers: list[object]

        def __init__(self) -> None:
            self.finalizers = []

        def addfinalizer(self, finalizer: object) -> None:
            self.finalizers.append(finalizer)

    class TempPathFactory:
        def getbasetemp(self) -> Path:
            return tmp_path

    primary = RuntimeError("bootstrap creation failed after writing")

    def fail_after_partial_write(root: Path) -> e2e_fixtures.PinnedOdooBootstrap:
        root.mkdir(mode=0o700)
        (root / "partial").write_text("owned", encoding="utf-8")
        raise primary

    monkeypatch.setattr(e2e_fixtures, "_build_pinned_odoo_bootstrap", fail_after_partial_write)
    fixture_impl = cast(
        "Callable[..., e2e_fixtures.PinnedOdooBootstrap]",
        getattr(e2e_fixtures.pinned_odoo_bootstrap, "__wrapped__"),
    )
    request = Request()

    with pytest.raises(RuntimeError, match="creation failed after writing"):
        fixture_impl(request, TempPathFactory())

    assert len(request.finalizers) == 1
    assert list(tmp_path.glob("odcli-pinned-bootstrap-*")) == []
    cast("Callable[[], None]", request.finalizers[0])()


def test_bootstrap_setup_failure_and_cleanup_failure_are_both_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Request:
        finalizers: list[object]

        def __init__(self) -> None:
            self.finalizers = []

        def addfinalizer(self, finalizer: object) -> None:
            self.finalizers.append(finalizer)

    class TempPathFactory:
        def getbasetemp(self) -> Path:
            return tmp_path

    primary = RuntimeError("bootstrap creator failed")
    cleanup = RuntimeError("owned bootstrap cleanup failed")

    def fixed_uuid() -> SimpleNamespace:
        return SimpleNamespace(hex="owned")

    uuid_module = cast("ModuleType", getattr(e2e_fixtures, "uuid"))
    monkeypatch.setattr(uuid_module, "uuid4", fixed_uuid)
    monkeypatch.setattr(
        e2e_fixtures,
        "_build_pinned_odoo_bootstrap",
        lambda root: (_ for _ in ()).throw(primary),
    )
    monkeypatch.setattr(
        e2e_fixtures,
        "_remove_bootstrap_root",
        lambda *_args: (_ for _ in ()).throw(cleanup),
    )
    request = Request()

    fixture_impl = cast(
        "Callable[..., e2e_fixtures.PinnedOdooBootstrap]",
        getattr(e2e_fixtures.pinned_odoo_bootstrap, "__wrapped__"),
    )
    with pytest.raises(BaseExceptionGroup) as raised:
        fixture_impl(request, TempPathFactory())
    assert primary in raised.value.exceptions
    assert cleanup in raised.value.exceptions
    assert len(request.finalizers) == 1
