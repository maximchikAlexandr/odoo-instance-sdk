from __future__ import annotations

import hashlib
import os
import subprocess
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from odoo_instance_sdk.exceptions import ConfigError, LockConflictError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.applied_settings import (
    decode_applied_settings,
    encode_applied_settings,
)
from odoo_instance_sdk.internal.proc import PreparedStep, StepObserver
from odoo_instance_sdk.resources.environment import (
    DevelopmentEnvironment,
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
    EnvironmentState,
    _dependency_evidence,
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

    def fake_pump(step: object, **kwargs: object) -> tuple[int, bytes, bytes, float]:
        prepared = cast("Any", step)
        completed = fake_run(
            list(prepared.argv),
            env=dict(prepared.environment),
            timeout=kwargs.get("timeout"),
            capture_output=True,
            text=True,
        )
        return (
            completed.returncode,
            completed.stdout.encode(),
            completed.stderr.encode(),
            0.0,
        )

    monkeypatch.setattr("odoo_instance_sdk.internal.proc.executor._run_pump", fake_pump)
    return calls


def _add_requirements(project_manifest: Path, fake_python: Path) -> None:
    (project_manifest / "requirements.txt").write_text("requests\n")
    subprocess.run(["git", "add", "requirements.txt"], cwd=project_manifest, check=True)
    subprocess.run(["git", "commit", "-m", "requirements"], cwd=project_manifest, check=True)
    manifest = project_manifest / ".odcli" / "project.toml"
    manifest.write_text(
        textwrap.dedent(f"""\
            [project]
            odoo_bin = "{fake_python.parent / "odoo-bin"}"
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
        python=str(fake_python),
        db_mode=EnvironmentDatabaseMode.SHARED,
        source_database="comerta",
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
        python=str(fake_python),
        db_mode=EnvironmentDatabaseMode.SHARED,
        source_database="comerta",
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
            python="3.12",
            create_venv=True,
            db_mode=EnvironmentDatabaseMode.SHARED,
            source_database="comerta",
        )
        env = env_client.environments.checkout(project_manifest, "feat/create1", options=opts)
        assert env.python_environment_owned is True
        venv_calls = [c for c in calls if c[:2] == ["uv", "venv"]]
        assert len(venv_calls) == 1
        assert venv_calls[0][2] == str(Path(env.worktree_path).parent / "venv")
        assert "--python" in venv_calls[0]
        assert "3.12" in venv_calls[0]

    def test_hash_locked_checkout_skips_discovery_and_compile(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        lock = tmp_path / "audited.lock"
        lock.write_text("requests==2.32.5 --hash=sha256:" + "0" * 64 + "\n", encoding="utf-8")
        digest = hashlib.sha256(lock.read_bytes()).hexdigest()
        calls = _patch_subprocess(monkeypatch)
        env_client.environments.checkout(
            project_manifest,
            "feat/hash-lock",
            options=EnvironmentCheckoutOptions(
                python="3.12",
                create_venv=True,
                hash_lock=lock,
                hash_lock_sha256=digest,
                db_mode=EnvironmentDatabaseMode.SHARED,
                source_database="comerta",
            ),
        )
        dependency_calls = [call for call in calls if call[:2] == ["uv", "pip"]]
        assert [call[:3] for call in dependency_calls] == [["uv", "pip", "sync"]]
        assert dependency_calls[0][3:] == [
            "--python",
            dependency_calls[0][4],
            "--require-hashes",
            str(lock.resolve()),
        ]

    def test_hash_locked_checkout_requires_owned_environment(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        tmp_path: Path,
    ) -> None:
        lock = tmp_path / "audited.lock"
        lock.write_text("requests==2.32.5\n", encoding="utf-8")
        digest = hashlib.sha256(lock.read_bytes()).hexdigest()
        with pytest.raises(ConfigError, match="owned environment"):
            env_client.environments.checkout(
                project_manifest,
                "feat/hash-lock-reuse",
                options=EnvironmentCheckoutOptions(
                    python="python",
                    hash_lock=lock,
                    hash_lock_sha256=digest,
                    db_mode=EnvironmentDatabaseMode.SHARED,
                    source_database="comerta",
                ),
            )

    def test_hash_locked_checkout_revalidates_before_install(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        lock = tmp_path / "audited.lock"
        lock.write_text("requests==2.32.5\n", encoding="utf-8")
        digest = hashlib.sha256(lock.read_bytes()).hexdigest()
        calls = _patch_subprocess(monkeypatch)
        from odoo_instance_sdk.internal.proc import executor as executor_module

        original_pump = executor_module._run_pump

        def tamper_after_venv(
            step: PreparedStep,
            *,
            timeout: float | None,
            environment_snapshot: tuple[tuple[str, str], ...],
            observer: StepObserver | None,
            observe_output: bool,
            max_output_bytes: int | None = None,
        ) -> tuple[int, bytes, bytes, float]:
            result = original_pump(
                step,
                timeout=timeout,
                environment_snapshot=environment_snapshot,
                observer=observer,
                observe_output=observe_output,
                max_output_bytes=max_output_bytes,
            )
            if step.step_id == "checkout.venv":
                lock.write_text("requests==2.32.6\n", encoding="utf-8")
            return result

        monkeypatch.setattr(executor_module, "_run_pump", tamper_after_venv)
        with pytest.raises(ConfigError, match="digest mismatch"):
            env_client.environments.checkout(
                project_manifest,
                "feat/hash-lock-revalidate",
                options=EnvironmentCheckoutOptions(
                    python="3.12",
                    create_venv=True,
                    hash_lock=lock,
                    hash_lock_sha256=digest,
                    db_mode=EnvironmentDatabaseMode.SHARED,
                    source_database="comerta",
                ),
            )
        assert not any(call[:2] == ["uv", "pip"] for call in calls)


class TestSyncUpgradePreserve:
    def test_hash_locked_sync_revalidates_captured_lock_before_install(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        lock = tmp_path / "audited.lock"
        lock.write_text("requests==2.32.5\n", encoding="utf-8")
        digest = hashlib.sha256(lock.read_bytes()).hexdigest()
        calls = _patch_subprocess(monkeypatch)
        env = env_client.environments.checkout(
            project_manifest,
            "feat/hash-lock-sync-revalidate",
            options=EnvironmentCheckoutOptions(
                python="3.12",
                create_venv=True,
                hash_lock=lock,
                hash_lock_sha256=digest,
                db_mode=EnvironmentDatabaseMode.SHARED,
                source_database="comerta",
            ),
        )

        catalog = env_client.get_catalog()
        row = catalog.get_environment(str(env.id))
        assert row is not None
        before = row["applied_settings_json"]
        calls.clear()
        command = env_client.environments.sync_python_command(
            str(env.id), hash_lock=lock, hash_lock_sha256=digest
        )
        lock.write_text("requests==2.32.6\n", encoding="utf-8")

        with pytest.raises(ConfigError, match="digest mismatch"):
            command.run()

        assert not any(call[:2] == ["uv", "pip"] for call in calls)
        row = catalog.get_environment(str(env.id))
        assert row is not None
        assert row["applied_settings_json"] == before

    def test_dependency_evidence_uses_normalized_meaningful_entries(self, tmp_path: Path) -> None:
        requirements = tmp_path / "requirements.txt"
        requirements.write_text(
            "# ignored\n requests >= 1  # comment\n\nhttpx== 2\n",
            encoding="utf-8",
        )
        first = _dependency_evidence([str(requirements)])
        requirements.write_text("httpx==2\nrequests>=1\n", encoding="utf-8")

        assert first == _dependency_evidence([str(requirements)])

    def test_dependency_evidence_fails_closed_when_input_disappears(self, tmp_path: Path) -> None:
        requirements = tmp_path / "requirements.txt"
        requirements.write_text("requests\n", encoding="utf-8")
        requirements.unlink()

        with pytest.raises(ConfigError, match="dependency input is unavailable"):
            _dependency_evidence([str(requirements)])

    def test_successful_sync_updates_python_dependencies_only(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env = _checkout_reuse_reqs(
            env_client, project_manifest, fake_python, "feat/sync-evidence", monkeypatch
        )
        catalog = env_client.get_catalog()
        original = encode_applied_settings(
            python={
                "selector": str(fake_python),
                "path": env.python_environment_path,
                "owned": False,
            },
            dependencies={"requirements.txt": "old"},
            managed_config={"http_port": "8069", "custom": "keep"},
            addons=[project_manifest / "addons"],
            git={"ticket": "PROJ-1", "branch": "PROJ-1", "base": "main"},
        )
        catalog.update_environment(str(env.id), {"applied_settings_json": original})

        _patch_subprocess(monkeypatch)
        env_client.environments.sync_python(str(env.id))

        row = catalog.get_environment(str(env.id))
        assert row is not None
        updated = decode_applied_settings(row["applied_settings_json"])
        before_components = cast(
            "dict[str, dict[str, JsonValue]]", decode_applied_settings(original)["components"]
        )
        after_components = cast("dict[str, dict[str, JsonValue]]", updated["components"])
        assert after_components["odoo"] == before_components["odoo"]
        assert after_components["addons"] == before_components["addons"]
        assert after_components["git"] == before_components["git"]
        assert after_components["python"]["path"] == env.python_environment_path
        assert after_components["dependencies"]["status"] == "known"

    def test_sync_dry_run_does_not_publish_evidence(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env = _checkout_reuse_reqs(
            env_client, project_manifest, fake_python, "feat/sync-preview", monkeypatch
        )
        catalog = env_client.get_catalog()
        row = catalog.get_environment(str(env.id))
        assert row is not None
        before = row["applied_settings_json"]
        env_client.environments.sync_python_command(str(env.id))
        row = catalog.get_environment(str(env.id))
        assert row is not None
        after = row["applied_settings_json"]
        assert after == before

    def test_failed_sync_does_not_publish_evidence(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env = _checkout_reuse_reqs(
            env_client, project_manifest, fake_python, "feat/sync-failure", monkeypatch
        )
        catalog = env_client.get_catalog()
        row = catalog.get_environment(str(env.id))
        assert row is not None
        before = row["applied_settings_json"]

        def fail_compile(step: object, **_kwargs: object) -> tuple[int, bytes, bytes, float]:
            prepared = cast("Any", step)
            if "compile" in prepared.argv:
                return 1, b"", b"compile failed", 0.0
            return 0, b"", b"", 0.0

        monkeypatch.setattr("odoo_instance_sdk.internal.proc.executor._run_pump", fail_compile)
        env_client.environments.sync_python(str(env.id))
        row = catalog.get_environment(str(env.id))
        assert row is not None
        after = row["applied_settings_json"]
        assert after == before

    def test_unreadable_sync_input_preserves_previous_evidence(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env = _checkout_reuse_reqs(
            env_client, project_manifest, fake_python, "feat/sync-missing-input", monkeypatch
        )
        catalog = env_client.get_catalog()
        row = catalog.get_environment(str(env.id))
        assert row is not None
        before = row["applied_settings_json"]

        _patch_subprocess(monkeypatch)
        from odoo_instance_sdk.internal.proc import executor as executor_module

        original_pump = executor_module._run_pump

        def disappear_after_apply(
            step: PreparedStep,
            *,
            timeout: float | None,
            environment_snapshot: tuple[tuple[str, str], ...],
            observer: StepObserver | None,
            observe_output: bool,
            max_output_bytes: int | None = None,
        ) -> tuple[int, bytes, bytes, float]:
            result = original_pump(
                step,
                timeout=timeout,
                environment_snapshot=environment_snapshot,
                observer=observer,
                observe_output=observe_output,
                max_output_bytes=max_output_bytes,
            )
            if "install" in step.argv:
                Path(env.worktree_path, "requirements.txt").unlink()
            return result

        monkeypatch.setattr(executor_module, "_run_pump", disappear_after_apply)
        with pytest.raises(ConfigError, match="dependency input is unavailable"):
            env_client.environments.sync_python(str(env.id))

        row = catalog.get_environment(str(env.id))
        assert row is not None
        assert row["applied_settings_json"] == before

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
            python="3.12",
            create_venv=True,
            db_mode=EnvironmentDatabaseMode.SHARED,
            source_database="comerta",
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

        def fake_pump(step: object, **kwargs: object) -> tuple[int, bytes, bytes, float]:
            prepared = cast("Any", step)
            completed = fake_run(
                list(prepared.argv),
                env=dict(prepared.environment),
                timeout=kwargs.get("timeout"),
                capture_output=True,
                text=True,
            )
            return completed.returncode, completed.stdout.encode(), completed.stderr.encode(), 0.0

        monkeypatch.setattr("odoo_instance_sdk.internal.proc.executor._run_pump", fake_pump)
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

        def fake_pump(step: object, **kwargs: object) -> tuple[int, bytes, bytes, float]:
            prepared = cast("Any", step)
            completed = fake_run(
                list(prepared.argv),
                env=dict(prepared.environment),
                timeout=kwargs.get("timeout"),
                capture_output=True,
                text=True,
            )
            return completed.returncode, completed.stdout.encode(), completed.stderr.encode(), 0.0

        monkeypatch.setattr("odoo_instance_sdk.internal.proc.executor._run_pump", fake_pump)
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
