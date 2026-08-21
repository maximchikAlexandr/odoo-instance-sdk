from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from odoo_instance_sdk.exceptions import LockConflictError
from odoo_instance_sdk.resources.environment import (
    DevelopmentEnvironment,
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
    EnvironmentState,
)

if TYPE_CHECKING:
    from odoo_instance_sdk import OdooClient


def _patch_subprocess(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []
    real_run = subprocess.run

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if not args or args[0] != "uv":
            return real_run(args, **cast("Any", kwargs))
        calls.append(args)
        if args[:2] == ["uv", "venv"]:
            venv = Path(args[2])
            (venv / "bin").mkdir(parents=True, exist_ok=True)
            (venv / "bin" / "python").write_text("#!/bin/sh\nexit 0\n")
            os.chmod(venv / "bin" / "python", 0o755)
        if "compile" in args and "-o" in args:
            idx = args.index("-o")
            Path(args[idx + 1]).parent.mkdir(parents=True, exist_ok=True)
            Path(args[idx + 1]).write_text("# compiled\n")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("odoo_instance_sdk.resources.environment.subprocess.run", fake_run)
    return calls


def _add_requirements(project_manifest: Path, fake_python: Path) -> None:
    (project_manifest / "requirements.txt").write_text("requests\n")
    subprocess.run(["git", "add", "requirements.txt"], cwd=project_manifest, check=True)
    subprocess.run(["git", "commit", "-m", "requirements"], cwd=project_manifest, check=True)
    manifest = project_manifest / ".odcli" / "project.toml"
    manifest.write_text(
        textwrap.dedent(f"""\
            [project]
            odoo_bin = "/usr/bin/odoo"
            python = "{fake_python}"
            source_config = "odoo.conf"
            default_source_database = "comerta"
            requirements = ["requirements.txt"]
        """)
    )


def _checkout_reuse(
    env_client: OdooClient, project_manifest: Path, fake_python: Path, branch: str
) -> DevelopmentEnvironment:
    opts = EnvironmentCheckoutOptions(
        python=str(fake_python), db_mode=EnvironmentDatabaseMode.SHARED
    )
    return env_client.environments.checkout(project_manifest, branch, options=opts)


def _checkout_reuse_reqs(
    env_client: OdooClient,
    project_manifest: Path,
    fake_python: Path,
    branch: str,
    monkeypatch: pytest.MonkeyPatch,
) -> DevelopmentEnvironment:
    _add_requirements(project_manifest, fake_python)
    _patch_subprocess(monkeypatch)
    opts = EnvironmentCheckoutOptions(
        python=str(fake_python), db_mode=EnvironmentDatabaseMode.SHARED
    )
    return env_client.environments.checkout(project_manifest, branch, options=opts)


class TestReuseVenv:
    def test_reuse_records_project_interpreter_owned_false(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        env = _checkout_reuse(env_client, project_manifest, fake_python, "feat/reuse1")
        assert env.python_environment_owned is False
        assert env.python_environment_path == str(fake_python)

    def test_reuse_venv_not_deleted_on_remove(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        env = _checkout_reuse(env_client, project_manifest, fake_python, "feat/reuse2")
        env_client.environments.remove(env)
        removed = env_client.environments.get(str(env.id))
        assert removed.state == EnvironmentState.REMOVED
        assert Path(fake_python).is_file()


class TestCreateVenv:
    def test_create_venv_invokes_uv_venv(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = _patch_subprocess(monkeypatch)
        opts = EnvironmentCheckoutOptions(
            python="3.12", create_venv=True, db_mode=EnvironmentDatabaseMode.SHARED
        )
        env = env_client.environments.checkout(project_manifest, "feat/create1", options=opts)
        assert env.python_environment_owned is True
        venv_calls = [c for c in calls if c[:2] == ["uv", "venv"]]
        assert len(venv_calls) == 1
        assert venv_calls[0][2] == str(Path(env.worktree_path).parent / "venv")
        assert "--python" in venv_calls[0]
        assert "3.12" in venv_calls[0]


class TestSyncUpgradePreserve:
    def test_sync_upgrade_passes_upgrade_flag(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env = _checkout_reuse_reqs(
            env_client, project_manifest, fake_python, "feat/sync-up", monkeypatch
        )
        calls = _patch_subprocess(monkeypatch)
        env_client.environments.sync_python(str(env.id), upgrade=True)
        compile_calls = [c for c in calls if "compile" in c]
        assert len(compile_calls) == 1
        assert "--upgrade" in compile_calls[0]

    def test_sync_preserve_no_upgrade_flag(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env = _checkout_reuse_reqs(
            env_client, project_manifest, fake_python, "feat/sync-no", monkeypatch
        )
        calls = _patch_subprocess(monkeypatch)
        env_client.environments.sync_python(str(env.id), upgrade=False)
        compile_calls = [c for c in calls if "compile" in c]
        assert len(compile_calls) == 1
        assert "--upgrade" not in compile_calls[0]

    def test_owned_venv_uses_pip_sync(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _add_requirements(project_manifest, fake_python)
        calls = _patch_subprocess(monkeypatch)
        opts = EnvironmentCheckoutOptions(
            python="3.12", create_venv=True, db_mode=EnvironmentDatabaseMode.SHARED
        )
        env = env_client.environments.checkout(project_manifest, "feat/syncowned", options=opts)
        calls.clear()
        env_client.environments.sync_python(str(env.id))
        install_calls = [c for c in calls if "sync" in c and "pip" in c]
        assert len(install_calls) == 1
        assert "--python" in install_calls[0]
        assert str(Path(env.python_environment_path) / "bin" / "python") in install_calls[0]

    def test_reused_venv_uses_pip_install(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env = _checkout_reuse_reqs(
            env_client, project_manifest, fake_python, "feat/sync-reuse", monkeypatch
        )
        calls = _patch_subprocess(monkeypatch)
        env_client.environments.sync_python(str(env.id))
        install_calls = [c for c in calls if "install" in c and "pip" in c]
        assert len(install_calls) == 1
        assert "--python" in install_calls[0]
        assert str(fake_python) in install_calls[0]
        assert "-r" in install_calls[0]


class TestFailedCompileKeepsLock:
    def test_failed_compile_does_not_overwrite_valid_lock(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env = _checkout_reuse_reqs(
            env_client, project_manifest, fake_python, "feat/lock-keep", monkeypatch
        )
        lock_file = Path(env.dependency_lock_path)
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.write_text("# original valid lock\n")
        original = lock_file.read_text()
        real_run = subprocess.run

        def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if not args or args[0] != "uv":
                return real_run(args, **cast("Any", kwargs))
            if "compile" in args:
                return subprocess.CompletedProcess(
                    args=args, returncode=1, stdout="", stderr="boom"
                )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("odoo_instance_sdk.resources.environment.subprocess.run", fake_run)
        result = env_client.environments.sync_python(str(env.id))
        assert lock_file.read_text() == original
        assert result.state == EnvironmentState.READY


class TestRunShellNoSync:
    def test_environment_resource_has_no_run_shell_methods(self) -> None:
        from odoo_instance_sdk.resources.environment import EnvironmentResource

        for method in ("run", "shell", "start", "stop"):
            assert not hasattr(EnvironmentResource, method)


class TestFlockSerialization:
    def test_concurrent_sync_same_python_path_serializes(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env = _checkout_reuse_reqs(
            env_client, project_manifest, fake_python, "feat/flock", monkeypatch
        )
        lock_file = Path(env.dependency_lock_path)
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.write_text("# lock\n")
        real_run = subprocess.run

        def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if not args or args[0] != "uv":
                return real_run(args, **cast("Any", kwargs))
            if "compile" in args and "-o" in args:
                idx = args.index("-o")
                Path(args[idx + 1]).write_text("# compiled\n")
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("odoo_instance_sdk.resources.environment.subprocess.run", fake_run)
        from odoo_instance_sdk.internal.locks import python_env_lock_path

        lock_path = python_env_lock_path(env.python_environment_path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        import fcntl

        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(LockConflictError):
                env_client.environments.sync_python(str(env.id))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
