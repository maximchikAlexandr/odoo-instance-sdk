from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import (
    InstanceConfigurationError,
    MasterPasswordRequiredError,
)
from odoo_instance_sdk.internal.proc import ProcessHandle, ProcessResult, RecordingExecutor
from odoo_instance_sdk.internal.server import _build_cli_args
from odoo_instance_sdk.models import CommandResult, OdooProcess, StartConfig
from odoo_instance_sdk.resources.instance import OdooInstance


def _make_client() -> OdooClient:
    return OdooClient(config=OdooClientConfig(executable="python3"))


def _recording_handle(pid: int = 4242) -> ProcessHandle:
    process = MagicMock()
    process.pid = pid
    process.poll.return_value = 0
    process.wait.return_value = 0
    return ProcessHandle(process, (), pid, pid, True)


class TestFromConfigNoPassword:
    def test_from_config_without_admin_passwd(self, tmp_path: Path) -> None:
        path = tmp_path / "odoo.conf"
        path.write_text("[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\n")
        client = _make_client()
        inst = client.instance.from_config(path)
        assert inst.config.master_password is None
        assert inst.config.command_prefix is None

    def test_from_config_backup_raises_without_password(self, tmp_path: Path) -> None:
        path = tmp_path / "odoo.conf"
        path.write_text("[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\n")
        client = _make_client()
        inst = client.instance.from_config(path)
        with pytest.raises(MasterPasswordRequiredError):
            inst.databases.backup("test_db")

    def test_from_config_list_works_without_password(self, tmp_path: Path) -> None:
        path = tmp_path / "odoo.conf"
        path.write_text("[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\n")
        client = _make_client()
        inst = client.instance.from_config(path)
        with patch("odoo_instance_sdk.resources.database.httpx.Client") as mock_http_cls:
            mock_http = mock_http_cls.return_value.__enter__.return_value
            mock_http.post.return_value.json.return_value = {"result": ["db1"]}
            mock_http.post.return_value.raise_for_status.return_value = None
            dbs = inst.databases.list()
        assert any(db.name == "db1" for db in dbs)

    def test_from_config_with_explicit_password(self, tmp_path: Path) -> None:
        path = tmp_path / "odoo.conf"
        path.write_text("[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\n")
        client = _make_client()
        inst = client.instance.from_config(path, master_password="secret")
        assert inst.config.master_password == "secret"


class TestInstancePrefix:
    def test_factory_bound_cluster_preflights_before_spawn(
        self,
        env_client: OdooClient,
        project_manifest: Path,
        fake_python: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from odoo_instance_sdk.resources.environment import (
            EnvironmentCheckoutOptions,
            EnvironmentDatabaseMode,
        )

        events: list[str] = []

        class StoppedCluster:
            def ensure_running(self, timeout: float = 60.0) -> None:
                events.append("healthy")

        options = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
            odoo_bin=fake_python.parent / "odoo-bin",
            source_database="comerta",
        )
        env = env_client.environments.checkout(
            project_manifest, "feat/postgres-preflight", options=options
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project",
            staticmethod(lambda path: StoppedCluster()),
        )
        instance = env_client.instance.from_environment(env)

        class EventExecutor(RecordingExecutor):
            def spawn(self, step: object) -> ProcessHandle:
                events.append("spawn")
                return super().spawn(step)  # type: ignore[arg-type]

        executor = EventExecutor(handles={"instance.foreground": _recording_handle()})
        with (
            patch("odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor),
            patch("odoo_instance_sdk.resources.instance._process_create_time", return_value=1.0),
        ):
            instance.run_foreground()
        assert events == ["healthy", "spawn"]

    def test_manual_instance_no_prefix(self) -> None:
        client = _make_client()
        inst = client.instance(base_url="http://localhost:8069")
        assert inst.config.command_prefix is None

    def test_run_uses_client_fallback(self) -> None:
        client = _make_client()
        inst = client.instance(base_url="http://localhost:8069")
        result = inst.run(["-c", "import sys; sys.exit(0)"])
        assert isinstance(result, CommandResult)
        assert result.returncode == 0
        assert result.args[0] == client.config.executable

    def test_from_environment_sets_prefix(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        from odoo_instance_sdk.resources.environment import (
            EnvironmentCheckoutOptions,
            EnvironmentDatabaseMode,
            EnvironmentState,
        )

        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
            odoo_bin=fake_python.parent / "odoo-bin",
            source_database="comerta",
        )
        env = env_client.environments.checkout(project_manifest, "feat/prefix", options=opts)
        assert env.state == EnvironmentState.READY
        inst = env_client.instance.from_environment(env)
        assert inst.config.command_prefix is not None
        assert len(inst.config.command_prefix) == 2
        assert inst.config.command_prefix[0] == str(fake_python)
        assert inst.config.command_prefix[1] == str(fake_python.parent / "odoo-bin")
        assert inst.config.master_password is None

    def test_from_environment_non_ready_raises(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        from odoo_instance_sdk.resources.environment import (
            EnvironmentCheckoutOptions,
            EnvironmentDatabaseMode,
        )

        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
            odoo_bin=fake_python.parent / "odoo-bin",
            source_database="comerta",
        )
        env = env_client.environments._plan_checkout(
            project_manifest, "feat/notready", options=opts
        )
        with pytest.raises(AttributeError):
            env_client.instance.from_environment(env)  # type: ignore[arg-type]

    def test_run_uses_instance_prefix(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        from odoo_instance_sdk.resources.environment import (
            EnvironmentCheckoutOptions,
            EnvironmentDatabaseMode,
        )

        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
            odoo_bin=fake_python.parent / "odoo-bin",
            source_database="comerta",
        )
        env = env_client.environments.checkout(project_manifest, "feat/runprefix", options=opts)
        inst = env_client.instance.from_environment(env)
        executor = RecordingExecutor()
        with patch(
            "odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor
        ):
            inst.run(["--help"])
        assert executor.executed[0].argv[0] == str(fake_python)


class TestStartConfigFromOdooConfig:
    def test_config_path_set_to_actual_path(self, tmp_path: Path) -> None:
        path = tmp_path / "odoo.conf"
        path.write_text(
            "[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\nconfig_path = /other/path\n"
        )
        sc = StartConfig.from_odoo_config(path)
        assert sc.config_path == str(path)
        assert sc.config_path != "/other/path"

    def test_config_path_without_file_option(self, tmp_path: Path) -> None:
        path = tmp_path / "odoo.conf"
        path.write_text("[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\n")
        sc = StartConfig.from_odoo_config(path)
        assert sc.config_path == str(path)


class TestBuildCliArgsSingleConfig:
    def test_single_config_when_config_path_set(self, tmp_path: Path) -> None:
        cfg = StartConfig(
            http_port=8069,
            http_interface="127.0.0.1",
            config_path=str(tmp_path / "odoo.conf"),
            db_password="secret",
        )
        args = _build_cli_args(cfg)
        config_indices = [i for i, a in enumerate(args) if a == "--config"]
        assert len(config_indices) == 1

    def test_logfile_not_emitted_as_argv(self, tmp_path: Path) -> None:
        cfg = StartConfig(
            http_port=8069,
            http_interface="127.0.0.1",
            config_path=str(tmp_path / "odoo.conf"),
            logfile=str(tmp_path / "odoo.log"),
        )
        args = _build_cli_args(cfg)
        assert "--logfile" not in args
        assert args.count("--config") == 1

    def test_no_secret_config_when_config_path_set(self, tmp_path: Path) -> None:
        cfg = StartConfig(
            http_port=8069,
            http_interface="127.0.0.1",
            config_path=str(tmp_path / "odoo.conf"),
            db_password="secret",
        )
        args = _build_cli_args(cfg)
        assert args.count("--config") == 1

    def test_secret_config_when_no_config_path(self) -> None:
        cfg = StartConfig(
            http_port=8069,
            http_interface="127.0.0.1",
            config_path=None,
            db_password="secret",
        )
        from odoo_instance_sdk.internal.server import _write_secret_config

        secret = _write_secret_config(cfg)
        assert secret is not None
        Path(secret).unlink(missing_ok=True)


class TestRunForeground:
    def test_run_foreground_returns_exit_code(self) -> None:
        client = _make_client()
        inst = client.instance(base_url="http://localhost:8069")
        cfg = StartConfig(http_port=9999, http_interface="127.0.0.1")
        executor = RecordingExecutor(handles={"instance.foreground": _recording_handle()})
        with patch(
            "odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor
        ):
            result = inst.run_foreground(cfg)
        assert result == 0

    def test_run_foreground_no_start_config_raises(self) -> None:
        client = _make_client()
        inst = client.instance(base_url="http://localhost:8069")
        with pytest.raises(InstanceConfigurationError):
            inst.run_foreground()

    def test_run_foreground_uses_prefix(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        from odoo_instance_sdk.resources.environment import (
            EnvironmentCheckoutOptions,
            EnvironmentDatabaseMode,
        )

        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
            odoo_bin=fake_python.parent / "odoo-bin",
            source_database="comerta",
        )
        env = env_client.environments.checkout(project_manifest, "feat/fgprefix", options=opts)
        inst = env_client.instance.from_environment(env)
        executor = RecordingExecutor(handles={"instance.foreground": _recording_handle()})
        with (
            patch.object(OdooInstance, "_ensure_dependencies_ready"),
            patch("odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor),
            patch("odoo_instance_sdk.resources.instance._process_create_time", return_value=1.0),
        ):
            inst.run_foreground()
            assert executor.spawned[0].argv[0] == str(fake_python)

    def test_run_foreground_real_exit_code(self) -> None:
        client = _make_client()
        inst = client.instance(base_url="http://localhost:8069")
        cfg = StartConfig(http_port=9999, http_interface="127.0.0.1")
        with patch("odoo_instance_sdk.resources.instance._build_cli_args", return_value=[]):
            result = inst.run_foreground(cfg)
        assert result == 0

    def test_foreground_plan_appends_native_argv_after_generated_config(
        self, tmp_path: Path
    ) -> None:
        client = _make_client()
        config = StartConfig(
            http_port=9999,
            http_interface="127.0.0.1",
            config_path=str(tmp_path / "generated.conf"),
        )
        native_args = (
            "--dev=reload",
            "--log-level",
            "debug",
            "--dev=xml",
            "space value",
            "meta;$(touch should-not-run)",
        )
        executor = RecordingExecutor(handles={"instance.foreground": _recording_handle()})

        with patch(
            "odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor
        ):
            command = client.instance(base_url="http://localhost:8069").run_foreground_command(
                config, args=native_args
            )
            process = command.plan.process_steps[0]
            assert process.argv == (
                "python3",
                *_build_cli_args(config),
                *native_args,
            )
            assert command.run() == 0

        assert executor.spawned[0].argv == process.argv
        assert executor.spawned[0].inherit_stdio is True
        assert executor.spawned[0].start_new_session is True

    def test_foreground_command_freezes_mutable_args_and_recording_executor_replays_them(
        self, tmp_path: Path
    ) -> None:
        client = _make_client()
        config = StartConfig(
            http_port=9999,
            http_interface="127.0.0.1",
            config_path=str(tmp_path / "generated.conf"),
        )
        native_args = ["--dev=reload", "space value", "meta;echo no-shell"]
        expected = tuple(native_args)
        executor = RecordingExecutor(handles={"instance.foreground": _recording_handle()})

        with patch(
            "odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor
        ):
            command = client.instance(base_url="http://localhost:8069").run_foreground_command(
                config, args=native_args
            )
            native_args[:] = ["--database", "wrong"]
            planned = command.plan.process_steps[0].argv
            assert planned[-len(expected) :] == expected
            assert command.commands[0].argv == planned
            assert command.run() == 0

        assert executor.spawned[0].argv == planned

    def test_foreground_returns_native_process_exit_code(self, tmp_path: Path) -> None:
        client = _make_client()
        config = StartConfig(
            http_port=9999,
            http_interface="127.0.0.1",
            config_path=str(tmp_path / "generated.conf"),
        )
        instance = OdooInstance(
            config=InstanceConfig(
                base_url="http://localhost:8069",
                start_config=config,
                command_prefix=("python3", "-c", "import sys; sys.exit(23)"),
            ),
            _client=client,
        )

        assert instance.run_foreground(args=("--stop-after-init",)) == 23


class TestShell:
    def test_shell_no_start_config_raises(self) -> None:
        client = _make_client()
        inst = client.instance(base_url="http://localhost:8069")
        with pytest.raises(InstanceConfigurationError):
            inst.shell()

    def test_shell_forbids_config_override_attached(self) -> None:
        client = _make_client()
        path = Path("/tmp/test.conf")
        inst = client.instance.from_config(_write_loopback_config(path))
        with pytest.raises(InstanceConfigurationError):
            inst.shell(args=["-c/tmp/evil.conf"])

    def test_shell_forbids_config_override_spaced(self, tmp_path: Path) -> None:
        client = _make_client()
        cfg_path = tmp_path / "odoo.conf"
        cfg_path.write_text(
            "[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\nadmin_passwd = x\n"
        )
        inst = client.instance.from_config(cfg_path)
        with pytest.raises(InstanceConfigurationError):
            inst.shell(args=["-c", "/tmp/evil.conf"])

    def test_shell_forbids_database_override_spaced(self, tmp_path: Path) -> None:
        client = _make_client()
        cfg_path = tmp_path / "odoo.conf"
        cfg_path.write_text(
            "[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\nadmin_passwd = x\n"
        )
        inst = client.instance.from_config(cfg_path)
        with pytest.raises(InstanceConfigurationError):
            inst.shell(args=["--database", "evil"])

    def test_shell_forbids_database_override_attached(self, tmp_path: Path) -> None:
        client = _make_client()
        cfg_path = tmp_path / "odoo.conf"
        cfg_path.write_text(
            "[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\nadmin_passwd = x\n"
        )
        inst = client.instance.from_config(cfg_path)
        with pytest.raises(InstanceConfigurationError):
            inst.shell(args=["-devil"])

    def test_shell_returns_exit_code(self, tmp_path: Path) -> None:
        client = _make_client()
        cfg_path = tmp_path / "odoo.conf"
        cfg_path.write_text(
            "[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\nadmin_passwd = x\n"
        )
        inst = client.instance.from_config(cfg_path)
        executor = RecordingExecutor(handles={"instance.shell": _recording_handle()})
        with patch(
            "odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor
        ):
            result = inst.shell()
        assert result == 0


class TestRunShellScript:
    def test_run_shell_script_returns_command_result(self, tmp_path: Path) -> None:
        client = _make_client()
        cfg_path = tmp_path / "odoo.conf"
        cfg_path.write_text(
            "[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\nadmin_passwd = x\n"
        )
        inst = client.instance.from_config(cfg_path)
        executor = RecordingExecutor(
            results={
                "instance.shell_script": ProcessResult(
                    argv=("python3", "shell"),
                    returncode=0,
                    stdout="2\n",
                    stderr="",
                    duration=0.1,
                    cwd=None,
                    environment=(),
                )
            }
        )
        with patch(
            "odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor
        ):
            result = inst.run_shell_script("print(1+1)")
        assert isinstance(result, CommandResult)
        assert result.returncode == 0
        assert executor.executed[0].stdin is not None
        assert "print(1+1)" in executor.executed[0].stdin.decode()

    def test_run_shell_script_no_start_config_raises(self) -> None:
        client = _make_client()
        inst = client.instance(base_url="http://localhost:8069")
        with pytest.raises(InstanceConfigurationError):
            inst.run_shell_script("print(1)")

    def test_run_shell_script_commit_flag(self, tmp_path: Path) -> None:
        client = _make_client()
        cfg_path = tmp_path / "odoo.conf"
        cfg_path.write_text(
            "[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\nadmin_passwd = x\n"
        )
        inst = client.instance.from_config(cfg_path)
        executor = RecordingExecutor()
        with patch(
            "odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor
        ):
            inst.run_shell_script("print(1)", commit=True)
        assert executor.executed[0].mutating is True

    def test_run_shell_script_argv_injected(self, tmp_path: Path) -> None:
        client = _make_client()
        cfg_path = tmp_path / "odoo.conf"
        cfg_path.write_text(
            "[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\nadmin_passwd = x\n"
        )
        inst = client.instance.from_config(cfg_path)
        executor = RecordingExecutor()
        with patch(
            "odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor
        ):
            inst.run_shell_script("print(1)", argv=["--flag", "val"])
        assert '"--flag", "val"' in (executor.executed[0].stdin or b"").decode()

    def test_shell_wrapper_contains_nonce_payload(self) -> None:
        from odoo_instance_sdk.internal.server import _build_shell_wrapper

        wrapper = _build_shell_wrapper("print(1)", ["--x"], commit=False, nonce="abc123")
        assert "__ODCLI_PAYLOAD__abc123__" in wrapper
        assert "__END_PAYLOAD__abc123__" in wrapper
        assert "print(1)" in wrapper
        assert '"--x"' in wrapper


class TestInspectableInstanceCommands:
    def test_shell_script_plan_captures_source_and_transaction_intent(self) -> None:
        client = _make_client()
        inst = client.instance.from_config(_write_loopback_config(Path("/tmp/command-plan.conf")))
        source = "line_one()\nline_two()\n"
        command = inst.run_shell_script_command(source, argv=["--flag"], commit=True)

        assert tuple(step.step_id for step in command.plan.steps) == (
            "instance.shell_script",
            "instance.shell_script.transaction",
        )
        process = command.plan.process_steps[0]
        assert process.input_preview == "<redacted>"
        action = command.plan.steps[1]
        assert getattr(action, "action") == "commit"
        assert command.plan.fingerprint

    def test_run_command_result_preserves_proc_metadata(self) -> None:
        client = _make_client()
        inst = client.instance(base_url="http://localhost:8069")
        executor = RecordingExecutor(
            results={
                "instance.run": ProcessResult(
                    argv=("python3", "--version"),
                    returncode=0,
                    stdout="ok",
                    stderr="",
                    duration=0.25,
                    cwd="/work",
                    environment=(("SAFE", "yes"),),
                )
            }
        )
        with patch(
            "odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor
        ):
            result = inst.run(["--version"], cwd="/work", timeout=3.0, env={"SAFE": "yes"})

        assert result.argv == ["python3", "--version"]
        assert result.cwd == "/work"
        assert result.env == (("SAFE", "<redacted>"),)
        assert result.timeout == 3.0
        assert executor.executed[0].argv == ("python3", "--version")

    def test_start_plan_redacts_private_config_path_without_writing(self, tmp_path: Path) -> None:
        client = _make_client()
        inst = client.instance(base_url="http://localhost:8069")
        command = inst.start_command(StartConfig(db_password="private"))

        step = command.plan.process_steps[0]
        assert "private" not in repr(command)
        assert step.mode == "long-running"
        assert step.long_running is True
        assert not list(tmp_path.iterdir())

    def test_stop_plan_has_platform_specific_shared_boundary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _make_client()
        inst = client.instance(base_url="http://localhost:8069")
        raw = MagicMock()
        raw.pid = 4242
        proc = OdooProcess(id="proc", pid=4242, args=["odoo"], started_at=0.0)
        client.register_process(proc, raw, None)

        monkeypatch.setattr("odoo_instance_sdk.resources.instance.sys.platform", "linux")
        posix = inst.stop_command(proc)
        assert getattr(posix.plan.steps[0], "action") == "terminate_process_group"

        monkeypatch.setattr("odoo_instance_sdk.resources.instance.sys.platform", "win32")
        windows = inst.stop_command(proc)
        assert windows.plan.process_steps[0].argv[:2] == ("taskkill", "/T")


class TestEnvironmentResourceNoRuntimeMethods:
    def test_no_run_method(self) -> None:
        from odoo_instance_sdk.resources.environment import EnvironmentResource

        assert not hasattr(EnvironmentResource, "run")

    def test_no_shell_method(self) -> None:
        from odoo_instance_sdk.resources.environment import EnvironmentResource

        assert not hasattr(EnvironmentResource, "shell")

    def test_no_start_method(self) -> None:
        from odoo_instance_sdk.resources.environment import EnvironmentResource

        assert not hasattr(EnvironmentResource, "start")

    def test_no_stop_method(self) -> None:
        from odoo_instance_sdk.resources.environment import EnvironmentResource

        assert not hasattr(EnvironmentResource, "stop")


class TestPortConflictCli:
    def test_port_conflict_exit_1(
        self, env_client: OdooClient, project_manifest: Path, fake_python: Path
    ) -> None:
        from click.testing import CliRunner

        from odoo_instance_sdk.cli import cli
        from odoo_instance_sdk.resources.environment import (
            EnvironmentCheckoutOptions,
            EnvironmentDatabaseMode,
        )

        opts = EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
            odoo_bin=fake_python.parent / "odoo-bin",
            source_database="comerta",
        )
        env = env_client.environments.checkout(project_manifest, "feat/portconf", options=opts)

        runner = CliRunner()
        with (
            patch("odoo_instance_sdk.commands.context.OdooClient", return_value=env_client),
            patch("odoo_instance_sdk.internal.context._check_port_free", return_value=False),
        ):
            result = runner.invoke(cli, ["--env", str(env.id), "run"])
        assert result.exit_code == 1
        assert "port-conflict" in result.output


def _write_loopback_config(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\nadmin_passwd = x\n")
    return path


if __name__ == "__main__":
    pytest.main([__file__])
