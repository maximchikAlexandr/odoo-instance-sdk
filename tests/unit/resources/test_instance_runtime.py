from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.exceptions import (
    InstanceConfigurationError,
    MasterPasswordRequiredError,
)
from odoo_instance_sdk.internal.server import _build_cli_args
from odoo_instance_sdk.models import CommandResult, StartConfig
from odoo_instance_sdk.resources.instance import OdooInstance


def _make_client() -> OdooClient:
    return OdooClient(config=OdooClientConfig(executable="python3"))


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
        )
        env = env_client.environments.checkout(
            project_manifest, "feat/postgres-preflight", options=options
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project",
            staticmethod(lambda path: StoppedCluster()),
        )
        instance = env_client.instance.from_environment(env)
        with patch(
            "odoo_instance_sdk.resources.instance.run_foreground_process",
            side_effect=lambda *args, **kwargs: events.append("spawn") or 0,
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
        )
        env = env_client.environments.checkout(project_manifest, "feat/runprefix", options=opts)
        inst = env_client.instance.from_environment(env)
        with patch("odoo_instance_sdk.resources.instance.run_command") as mock_run:
            mock_run.return_value = CommandResult(
                args=[], returncode=0, stdout="", stderr="", duration=0.0
            )
            inst.run(["--help"])
            mock_run.assert_called_once()
            called_exec = mock_run.call_args.args[0]
            assert isinstance(called_exec, tuple)
            assert called_exec[0] == str(fake_python)


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

    def test_no_secret_config_when_config_path_set(self, tmp_path: Path) -> None:
        cfg = StartConfig(
            http_port=8069,
            http_interface="127.0.0.1",
            config_path=str(tmp_path / "odoo.conf"),
            db_password="secret",
        )
        from odoo_instance_sdk.internal.server import start_process

        with patch("odoo_instance_sdk.internal.server.subprocess.Popen") as mock_popen:
            mock_handle = mock_popen.return_value
            mock_handle.pid = 12345
            _proc, _handle, secret = start_process("odoo", cfg)
        assert secret is None

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
        with patch("odoo_instance_sdk.resources.instance.run_foreground_process") as mock_fg:
            mock_fg.return_value = 0
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
        )
        env = env_client.environments.checkout(project_manifest, "feat/fgprefix", options=opts)
        inst = env_client.instance.from_environment(env)
        with (
            patch.object(OdooInstance, "_ensure_dependencies_ready"),
            patch("odoo_instance_sdk.resources.instance.run_foreground_process") as mock_fg,
        ):
            mock_fg.return_value = 0
            inst.run_foreground()
            mock_fg.assert_called_once()
            called_exec = mock_fg.call_args.args[0]
            assert isinstance(called_exec, tuple)
            assert called_exec[0] == str(fake_python)

    def test_run_foreground_real_exit_code(self) -> None:
        client = _make_client()
        inst = client.instance(base_url="http://localhost:8069")
        cfg = StartConfig(http_port=9999, http_interface="127.0.0.1")
        with patch("odoo_instance_sdk.internal.server._build_cli_args", return_value=[]):
            result = inst.run_foreground(cfg)
        assert result == 0


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
        with patch("odoo_instance_sdk.resources.instance.run_foreground_process") as mock_fg:
            mock_fg.return_value = 0
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
        with patch("odoo_instance_sdk.internal.server._run_captured_shell") as mock_run:
            mock_run.return_value = CommandResult(
                args=["python3", "shell"], returncode=0, stdout="2\n", stderr="", duration=0.1
            )
            result = inst.run_shell_script("print(1+1)")
        assert isinstance(result, CommandResult)
        assert result.returncode == 0
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["source"] == "print(1+1)"

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
        with patch("odoo_instance_sdk.internal.server._run_captured_shell") as mock_run:
            mock_run.return_value = CommandResult(
                args=[], returncode=0, stdout="", stderr="", duration=0.0
            )
            inst.run_shell_script("print(1)", commit=True)
            assert mock_run.call_args.kwargs["commit"] is True

    def test_run_shell_script_argv_injected(self, tmp_path: Path) -> None:
        client = _make_client()
        cfg_path = tmp_path / "odoo.conf"
        cfg_path.write_text(
            "[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\nadmin_passwd = x\n"
        )
        inst = client.instance.from_config(cfg_path)
        with patch("odoo_instance_sdk.internal.server._run_captured_shell") as mock_run:
            mock_run.return_value = CommandResult(
                args=[], returncode=0, stdout="", stderr="", duration=0.0
            )
            inst.run_shell_script("print(1)", argv=["--flag", "val"])
            assert mock_run.call_args.kwargs["argv"] == ["--flag", "val"]

    def test_shell_wrapper_contains_nonce_payload(self) -> None:
        from odoo_instance_sdk.internal.server import _build_shell_wrapper

        wrapper = _build_shell_wrapper("print(1)", ["--x"], commit=False, nonce="abc123")
        assert "__ODCLI_PAYLOAD__abc123__" in wrapper
        assert "__END_PAYLOAD__abc123__" in wrapper
        assert "print(1)" in wrapper
        assert '"--x"' in wrapper


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
        )
        env = env_client.environments.checkout(project_manifest, "feat/portconf", options=opts)

        runner = CliRunner()
        with (
            patch("odoo_instance_sdk.internal.context.OdooClient", return_value=env_client),
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
