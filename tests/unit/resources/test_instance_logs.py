from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner, Result

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import InstanceConfigurationError, LogfileAccessError
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.resources.environment import (
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
)
from odoo_instance_sdk.resources.instance import OdooInstance


def _instance_from_config(tmp_path: Path, *, logfile: str | None) -> OdooInstance:
    conf = tmp_path / "odoo.conf"
    body = "[options]\nhttp_port = 8069\nhttp_interface = 127.0.0.1\n"
    if logfile is not None:
        body += f"logfile = {logfile}\n"
    conf.write_text(body)
    return OdooClient(config=OdooClientConfig(executable="python3")).instance.from_config(conf)


@pytest.mark.parametrize(
    ("text", "tail", "expected"),
    [
        pytest.param("1\n2\n3\n4\n5\n", 3, ["3\n", "4\n", "5\n"], id="last_three"),
        pytest.param("1\n2\n", 2, ["1\n", "2\n"], id="exact_length"),
        pytest.param("1\n2\n", 10, ["1\n", "2\n"], id="longer_than_file"),
        pytest.param("", 5, [], id="empty_file"),
        pytest.param("a\nb", 2, ["a\n", "b"], id="no_trailing_newline"),
    ],
)
def test_iter_logs_returns_trailing_lines(
    tmp_path: Path, text: str, tail: int, expected: list[str]
) -> None:
    log = tmp_path / "odoo.log"
    log.write_text(text)
    inst = _instance_from_config(tmp_path, logfile=str(log))
    assert list(inst.iter_logs(tail=tail)) == expected


def test_iter_logs_follow_append(tmp_path: Path) -> None:
    log = tmp_path / "odoo.log"
    log.write_text("old\n")
    inst = _instance_from_config(tmp_path, logfile=str(log))
    it = inst.iter_logs(tail=1, follow=True)
    assert next(it) == "old\n"
    with log.open("a") as handle:
        handle.write("new\n")
    assert next(it) == "new\n"


def test_iter_logs_follow_truncation(tmp_path: Path) -> None:
    log = tmp_path / "odoo.log"
    log.write_text("a\nb\n")
    inst = _instance_from_config(tmp_path, logfile=str(log))
    it = inst.iter_logs(tail=2, follow=True)
    assert next(it) == "a\n"
    assert next(it) == "b\n"
    log.write_text("x\n")
    assert next(it) == "x\n"


def test_iter_logs_follow_same_size_rewrite(tmp_path: Path) -> None:
    log = tmp_path / "odoo.log"
    log.write_text("old\n")
    inst = _instance_from_config(tmp_path, logfile=str(log))
    it = inst.iter_logs(tail=1, follow=True)
    assert next(it) == "old\n"
    log.write_text("new\n")
    assert next(it) == "new\n"


def test_iter_logs_follow_rapid_truncate_and_regrowth(tmp_path: Path) -> None:
    log = tmp_path / "odoo.log"
    log.write_text("old\n")
    inst = _instance_from_config(tmp_path, logfile=str(log))
    it = inst.iter_logs(tail=1, follow=True)
    assert next(it) == "old\n"
    log.write_text("new-first\nnew-second\n")
    assert next(it) == "new-first\n"
    assert next(it) == "new-second\n"


def test_iter_logs_follow_sentinel_read_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "odoo.log"
    log.write_bytes(b"x" * 100_000)
    inst = _instance_from_config(tmp_path, logfile=str(log))
    import odoo_instance_sdk.resources.instance as instance_module

    original_pread = os.pread
    read_lengths: list[int] = []

    def recording_pread(fd: int, length: int, offset: int) -> bytes:
        read_lengths.append(length)
        return original_pread(fd, length, offset)

    monkeypatch.setattr(os, "pread", recording_pread)
    it = inst.iter_logs(tail=1, follow=True)
    assert next(it) == "x" * 100_000
    with log.open("a") as handle:
        handle.write("\n")
    assert next(it) == "\n"
    assert read_lengths
    assert max(read_lengths) <= instance_module._LOGFILE_SENTINEL_BYTES


def test_iter_logs_follow_replacement(tmp_path: Path) -> None:
    log = tmp_path / "odoo.log"
    log.write_text("old\n")
    inst = _instance_from_config(tmp_path, logfile=str(log))
    it = inst.iter_logs(tail=1, follow=True)
    assert next(it) == "old\n"
    log.unlink()
    log.write_text("replaced\n")
    assert next(it) == "replaced\n"


def test_iter_logs_relative_logfile_uses_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "odoo.log").write_text("rel\n")
    inst = _instance_from_config(tmp_path, logfile="odoo.log")
    assert list(inst.iter_logs(tail=1)) == ["rel\n"]


def test_from_environment_reads_rewritten_logfile(
    env_client: OdooClient,
    project_manifest: Path,
    fake_python: Path,
    source_config: Path,
) -> None:
    source_config.write_text(source_config.read_text() + "logfile = /tmp/shared.log\n")
    env = env_client.environments.checkout(
        project_manifest,
        "feat/logs-read",
        options=EnvironmentCheckoutOptions(
            python=str(fake_python),
            db_mode=EnvironmentDatabaseMode.SHARED,
        ),
    )
    log_path = Path(env.generated_config_path).parent / "odoo.log"
    assert StartConfig.from_odoo_config(env.generated_config_path).logfile == str(
        log_path.resolve()
    )
    log_path.write_text("from-env\n")
    inst = env_client.instance.from_environment(env)
    assert list(inst.iter_logs(tail=1)) == ["from-env\n"]


def test_iter_logs_missing_file_includes_path_and_does_not_create(tmp_path: Path) -> None:
    missing = tmp_path / "missing.log"
    inst = _instance_from_config(tmp_path, logfile=str(missing))
    with pytest.raises(InstanceConfigurationError, match=str(missing)):
        list(inst.iter_logs())
    assert not missing.exists()


def test_iter_logs_unreadable_path_includes_path(tmp_path: Path) -> None:
    logfile = tmp_path / "odoo.log"
    logfile.mkdir()
    inst = _instance_from_config(tmp_path, logfile=str(logfile))
    with pytest.raises(InstanceConfigurationError, match=str(logfile)):
        list(inst.iter_logs())


@pytest.mark.parametrize("logfile", [None, "", "   "], ids=["absent", "empty", "whitespace"])
def test_iter_logs_rejects_unset_logfile(tmp_path: Path, logfile: str | None) -> None:
    inst = _instance_from_config(tmp_path, logfile=logfile)
    with pytest.raises(InstanceConfigurationError, match="absent or empty"):
        list(inst.iter_logs())


@pytest.mark.parametrize("tail", [0, -1], ids=["zero", "negative"])
def test_iter_logs_rejects_invalid_tail(tmp_path: Path, tail: int) -> None:
    (tmp_path / "odoo.log").write_text("x\n")
    inst = _instance_from_config(tmp_path, logfile=str(tmp_path / "odoo.log"))
    with pytest.raises(InstanceConfigurationError, match="tail must be >= 1"):
        list(inst.iter_logs(tail=tail))


def test_iter_logs_requires_start_config() -> None:
    inst = OdooInstance(
        config=InstanceConfig(base_url="http://127.0.0.1:8069"),
        _client=OdooClient(config=OdooClientConfig(executable="python3")),
    )
    with pytest.raises(InstanceConfigurationError, match="No StartConfig"):
        list(inst.iter_logs())


def _invoke_logs(*args: str, instance: OdooInstance | MagicMock) -> Result:
    with patch(
        "odoo_instance_sdk.cli.cli_context.ready_instance",
        return_value=(MagicMock(), MagicMock(), instance),
    ):
        return CliRunner().invoke(cli, ["logs", *args])


@pytest.mark.parametrize(
    ("args", "tail", "follow"),
    [
        ((), 100, False),
        (("-n", "5", "-f"), 5, True),
        (("--tail", "1"), 1, False),
        (("--follow",), 100, True),
    ],
    ids=["defaults", "short_flags", "tail_long", "follow_long"],
)
def test_cli_logs_forwards_options_and_raw_text(
    args: tuple[str, ...], tail: int, follow: bool
) -> None:
    instance = MagicMock()
    instance.iter_logs.return_value = iter(["\x1b[31mline\x1b[0m\nfragment"])
    result = _invoke_logs(*args, instance=instance)
    assert result.exit_code == 0, result.output
    assert result.stdout == "\x1b[31mline\x1b[0m\nfragment"
    assert result.stderr == ""
    instance.iter_logs.assert_called_once_with(tail=tail, follow=follow)


@pytest.mark.parametrize("tail", ["0", "-1"], ids=["zero", "negative"])
def test_cli_logs_invalid_tail_is_nonzero(tail: str) -> None:
    result = CliRunner().invoke(cli, ["logs", "--tail", tail])
    assert result.exit_code != 0
    assert result.stdout == ""
    assert "Invalid value" in result.stderr


@pytest.mark.parametrize("is_directory", [False, True], ids=["absent", "unreadable"])
def test_cli_logs_file_errors_write_only_stderr(tmp_path: Path, is_directory: bool) -> None:
    logfile = tmp_path / "odoo.log"
    if is_directory:
        logfile.mkdir()
    instance = _instance_from_config(tmp_path, logfile=str(logfile))
    result = _invoke_logs(instance=instance)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert str(logfile) not in result.stderr
    assert "<path>" in result.stderr
    assert "set logfile" in result.stderr


def test_cli_logs_file_error_neutralizes_terminal_controls() -> None:
    instance = MagicMock()
    instance.iter_logs.side_effect = LogfileAccessError(
        "evil\x00\x1b[31m\x9b2J\x7f.log", "not readable"
    )

    result = _invoke_logs(instance=instance)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "evil\\x00\\x1b[31m\\x9b2J\\x7f.log" in result.stderr
    assert "not readable" in result.stderr
    assert not any(ord(char) < 32 and char != "\n" for char in result.stderr)
    assert not any(127 <= ord(char) <= 159 for char in result.stderr)


def test_cli_logs_keyboard_interrupt_exits_130() -> None:
    instance = MagicMock()
    instance.iter_logs.side_effect = KeyboardInterrupt
    result = _invoke_logs("-f", instance=instance)
    assert result.exit_code == 130


def test_cli_logs_does_not_record_use() -> None:
    client = MagicMock()
    instance = MagicMock()
    instance.iter_logs.return_value = iter([])
    with patch(
        "odoo_instance_sdk.cli.cli_context.ready_instance",
        return_value=(client, MagicMock(), instance),
    ):
        result = CliRunner().invoke(cli, ["logs"])
    assert result.exit_code == 0, result.output
    client.environments.record_use.assert_not_called()


def test_cli_logs_resolution_error_is_nonzero_and_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with patch(
        "odoo_instance_sdk.cli.cli_context.ready_instance",
        side_effect=RuntimeError("Environment demo is not ready (state=creating)"),
    ):
        result = CliRunner().invoke(cli, ["logs"])
    assert result.exit_code != 0
    assert result.stdout == ""
    assert "not ready" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_cli_logs_resolves_registered_worktree_without_ready_instance_mock(
    env_client: OdooClient,
    project_manifest: Path,
    fake_python: Path,
    source_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_config.write_text(source_config.read_text() + "logfile = /tmp/shared.log\n")
    env = env_client.environments.checkout(
        project_manifest,
        "feat/logs-cwd",
        options=EnvironmentCheckoutOptions(
            python=str(fake_python), db_mode=EnvironmentDatabaseMode.SHARED
        ),
    )
    logfile = Path(env.generated_config_path).parent / "odoo.log"
    logfile.write_text("from-worktree\n")
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.context.OdooClient", lambda **_kwargs: env_client
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_worktree.rev_parse_toplevel",
        lambda _path: Path(env.worktree_path),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.git_worktree.rev_parse_git_common_dir",
        lambda _path: Path(env.git_common_dir),
    )
    monkeypatch.chdir(Path(env.worktree_path))

    result = CliRunner().invoke(cli, ["logs"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert result.stdout == "from-worktree\n"
    assert result.stderr == ""


def test_cli_logs_resolves_explicit_project_and_environment_outside_worktree(
    env_client: OdooClient,
    project_manifest: Path,
    fake_python: Path,
    source_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_config.write_text(source_config.read_text() + "logfile = /tmp/shared.log\n")
    env = env_client.environments.checkout(
        project_manifest,
        "feat/logs-explicit",
        options=EnvironmentCheckoutOptions(
            python=str(fake_python), db_mode=EnvironmentDatabaseMode.SHARED
        ),
    )
    logfile = Path(env.generated_config_path).parent / "odoo.log"
    logfile.write_text("from-explicit-context\n")
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.context.OdooClient", lambda **_kwargs: env_client
    )
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        cli,
        ["--project", str(project_manifest), "--env", str(env.id), "logs"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "from-explicit-context\n"
    assert result.stderr == ""
