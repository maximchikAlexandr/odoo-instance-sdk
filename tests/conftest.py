from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import platformdirs
import pytest

if TYPE_CHECKING:
    from odoo_instance_sdk import OdooClient


_DASHBOARD_MODULES = ("fastapi", "uvicorn")
_DOCKER_VOLUME_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _dashboard_extra_available() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in _DASHBOARD_MODULES)


@pytest.fixture
def env_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[OdooClient]:
    """Provide the environment client even when a mixed path skips nested conftest discovery."""
    from odoo_instance_sdk import OdooClient, OdooClientConfig

    fake_uv = tmp_path / "fakebin" / "uv"
    fake_uv.parent.mkdir()
    fake_uv.write_text("#!/bin/sh\nexit 0\n")
    os.chmod(fake_uv, 0o755)
    monkeypatch.setenv("PATH", str(fake_uv.parent) + os.pathsep + os.environ.get("PATH", ""))
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    cache_root = tmp_path / "cache"
    data_root.mkdir()
    state_root.mkdir()
    cache_root.mkdir()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_data_root", lambda **_kwargs: data_root
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_environments_root",
        lambda **_kwargs: data_root / "environments",
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_state_root", lambda **_kwargs: state_root
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_locks_dir", lambda **_kwargs: state_root / "locks"
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_cache_root", lambda **_kwargs: cache_root
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path",
        lambda **_kwargs: data_root / "catalog.sqlite3",
    )
    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    try:
        yield client
    finally:
        if client._catalog is not None:
            client._catalog.close()


@pytest.fixture
def production_catalogue_path() -> Path:
    """Capture the real catalogue before any test-local XDG redirection."""
    return (
        Path(platformdirs.user_data_dir("odoo-instance-sdk", ensure_exists=False))
        / "catalog.sqlite3"
    )


def _run_docker_volume_ls() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "volume", "ls", "--format", "{{.Name}}"],
        capture_output=True,
        check=False,
        timeout=60.0,
        text=True,
    )


def _run_docker_volume_inspect(name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "volume", "inspect", name],
        capture_output=True,
        check=False,
        timeout=60.0,
        text=True,
    )


def _docker_volume_labels(
    name: str, inspected: subprocess.CompletedProcess[str]
) -> tuple[tuple[str, str], ...]:
    if inspected.returncode != 0:
        detail = inspected.stderr.strip() or inspected.stdout.strip() or "no diagnostic output"
        raise AssertionError(f"cannot inspect generated volume {name}: {detail}")
    try:
        payload = json.loads(inspected.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"malformed Docker volume inspection for {name}") from exc
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
        or payload[0].get("Name") != name
    ):
        raise AssertionError(f"malformed Docker volume inspection for {name}")
    labels = payload[0].get("Labels")
    if labels is None:
        labels = {}
    if not isinstance(labels, dict):
        raise TypeError(f"malformed Docker volume labels for {name}")
    return tuple(sorted((str(key), str(value)) for key, value in labels.items()))


def _docker_project_volumes() -> dict[str, tuple[tuple[str, str], ...]] | None:
    """Snapshot every SDK-generated PostgreSQL volume and its ownership labels."""
    try:
        listed = _run_docker_volume_ls()
    except FileNotFoundError:
        return None
    if listed.returncode != 0:
        detail = listed.stderr.strip() or listed.stdout.strip() or "no diagnostic output"
        raise AssertionError(f"cannot list Docker volumes: {detail}")
    snapshot: dict[str, tuple[tuple[str, str], ...]] = {}
    for name in listed.stdout.splitlines():
        if not _DOCKER_VOLUME_NAME.fullmatch(name):
            raise AssertionError(f"malformed Docker volume listing: {name!r}")
        if not name.startswith("pgdata_"):
            continue
        snapshot[name] = _docker_volume_labels(name, _run_docker_volume_inspect(name))
    return snapshot


@pytest.fixture
def docker_visible_postgres_root() -> Iterator[Path]:
    """Provide a disposable bind source under the checkout for Docker Desktop.

    ``tmp_path`` is intentionally isolated for ordinary tests, but macOS Docker
    Desktop does not necessarily share pytest's ``/private/tmp`` tree.  The
    PostgreSQL integration tests bind-mount their generated password file, so
    keep only that disposable artifact under the already shared checkout.  The
    approval sentinel proves that the parent checkout policy is never touched.
    """
    volumes_before = _docker_project_volumes()
    if volumes_before is None:
        pytest.skip("Docker executable unavailable; cannot establish a resource baseline")
    root = Path(tempfile.mkdtemp(prefix=".odcli-postgres-", dir=Path.cwd()))
    checkout_sentinel = Path.cwd() / "approved-images.json"
    sentinel_existed = checkout_sentinel.exists()
    sentinel_contents = (
        checkout_sentinel.read_bytes() if sentinel_existed else b'{"sentinel": true}\n'
    )
    if not sentinel_existed:
        checkout_sentinel.write_bytes(sentinel_contents)
    try:
        yield root
    finally:
        shutil.rmtree(root)
        assert not root.exists()
        try:
            assert checkout_sentinel.read_bytes() == sentinel_contents
        finally:
            if not sentinel_existed:
                checkout_sentinel.unlink()
                assert not checkout_sentinel.exists()
        volumes_after = _docker_project_volumes()
        assert volumes_after is not None, "Docker volume cleanup probe became unavailable"
        assert volumes_after == volumes_before, (
            "generated PostgreSQL resource delta after disposable test: "
            f"before={volumes_before!r}, after={volumes_after!r}"
        )


@pytest.fixture(autouse=True)
def isolated_cli_catalogue(  # noqa: C901
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    request: pytest.FixtureRequest,
    production_catalogue_path: Path,
) -> Path:
    """Keep every init path in a per-test, per-worker catalogue root."""
    worker_id = str(getattr(request.config, "workerinput", {}).get("workerid", "master"))
    worker_root = tmp_path / f"catalogue-{worker_id}"
    data_root = worker_root / "data"
    state_root = worker_root / "state"
    cache_root = worker_root / "cache"
    catalog_path = data_root / "catalog.sqlite3"

    def data_root_path(*, ensure_exists: bool = True) -> Path:
        if ensure_exists:
            data_root.mkdir(parents=True, exist_ok=True)
        return data_root

    def state_root_path() -> Path:
        state_root.mkdir(parents=True, exist_ok=True)
        return state_root

    def cache_root_path(*, ensure_exists: bool = True) -> Path:
        if ensure_exists:
            cache_root.mkdir(parents=True, exist_ok=True)
        return cache_root

    def catalog_path_for(*, ensure_exists: bool = True) -> Path:
        if ensure_exists:
            data_root.mkdir(parents=True, exist_ok=True)
        return catalog_path

    def locks_dir_path(**_kwargs: object) -> Path:
        return state_root_path() / "locks"

    for method_name in (
        "open",
        "exists",
        "is_file",
        "stat",
        "read_text",
        "read_bytes",
        "write_text",
        "write_bytes",
        "replace",
        "unlink",
        "mkdir",
        "touch",
        "resolve",
    ):
        original = getattr(Path, method_name)

        def guarded(
            path: Path, *args: object, _original: object = original, **kwargs: object
        ) -> object:
            if path == production_catalogue_path:
                raise AssertionError(f"production catalogue accessed: {path}")
            return _original(path, *args, **kwargs)  # type: ignore[operator]

        monkeypatch.setattr(Path, method_name, guarded)

    if request.node.get_closest_marker("unpatched_xdg") is not None:
        return production_catalogue_path

    monkeypatch.setenv("XDG_DATA_HOME", str(data_root))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root))
    monkeypatch.setattr("odoo_instance_sdk.cli.get_catalog_path", catalog_path_for)
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_data_root", data_root_path)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_environments_root",
        lambda *, ensure_exists=True: data_root_path(ensure_exists=ensure_exists) / "environments",
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_state_root", state_root_path)
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_locks_dir", locks_dir_path)
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", cache_root_path)
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_catalog_path", catalog_path_for)
    return catalog_path


def _git_run(args: list[str], *, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, shell=False, capture_output=True, text=True, check=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git_run(["git", "init", "-b", "main"], cwd=repo)
    _git_run(["git", "config", "user.email", "test@test.com"], cwd=repo)
    _git_run(["git", "config", "user.name", "Test"], cwd=repo)
    (repo / "README.md").write_text("# Test\n")
    _git_run(["git", "add", "."], cwd=repo)
    _git_run(["git", "commit", "-m", "initial"], cwd=repo)
    return repo


@pytest.fixture
def fake_python(tmp_path: Path) -> Path:
    bindir = tmp_path / "fakebin"
    bindir.mkdir(parents=True, exist_ok=True)
    pybin = bindir / "fakepython"
    pybin.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import sys
            if len(sys.argv) > 1 and sys.argv[1] == "-c" and "sys.prefix" in sys.argv[2]:
                print("True")
                sys.exit(0)
            sys.exit(0)
        """
        )
    )
    os.chmod(pybin, 0o755)
    return pybin


@pytest.fixture
def source_config(git_repo: Path) -> Path:
    cfg = git_repo / "odoo.conf"
    cfg.write_text(
        textwrap.dedent(
            """\
            [options]
            db_name = comerta
            http_interface = 127.0.0.1
            http_port = 8069
            admin_passwd = admin
            db_host = localhost
            db_port = 5432
            db_user = odoo
            db_password = secret
            data_dir = /tmp/odoo_data
        """
        )
    )
    return cfg


@pytest.fixture
def project_manifest(git_repo: Path, fake_python: Path, source_config: Path) -> Path:
    manifest_dir = git_repo / ".odcli"
    manifest_dir.mkdir(exist_ok=True)
    manifest = manifest_dir / "project.toml"
    fake_odoo = fake_python.parent / "odoo-bin"
    fake_odoo.write_text("#!/bin/sh\nexit 0\n")
    os.chmod(fake_odoo, 0o755)
    rel_config = source_config.relative_to(git_repo)
    manifest.write_text(
        textwrap.dedent(
            f"""\
            [project]
            odoo_bin = "{fake_odoo}"
            python = "{fake_python}"
            source_config = "{rel_config}"
            default_source_database = "comerta"
        """
        )
    )
    return git_repo


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    root = Path(__file__).resolve().parent
    dashboard_extra_available = _dashboard_extra_available()
    for item in items:
        rel = Path(item.path).relative_to(root).as_posix()
        if rel.startswith("packaging/"):
            item.add_marker(pytest.mark.packaging)
        elif rel.startswith("integration/"):
            item.add_marker(pytest.mark.integration)
        else:
            item.add_marker(pytest.mark.unit)
        if not dashboard_extra_available and item.get_closest_marker("dashboard") is not None:
            item.add_marker(
                pytest.mark.skip(reason="dashboard extra unavailable; run `make dashboard`")
            )
