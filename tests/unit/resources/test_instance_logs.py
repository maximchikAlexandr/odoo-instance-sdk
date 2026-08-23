from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner, Result

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import InstanceConfigurationError
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


@dataclass(frozen=True)
class TailCase:
    id: str
    text: str
    tail: int
    expected: list[str]


TAIL_CASES = [
    TailCase(id="last_three", text="1\n2\n3\n4\n5\n", tail=3, expected=["3\n", "4\n", "5\n"]),
    TailCase(id="exact_length", text="1\n2\n", tail=2, expected=["1\n", "2\n"]),
    TailCase(id="longer_than_file", text="1\n2\n", tail=10, expected=["1\n", "2\n"]),
    TailCase(id="empty_file", text="", tail=5, expected=[]),
    TailCase(id="no_trailing_newline", text="a\nb", tail=2, expected=["a\n", "b"]),
]


@pytest.mark.parametrize("case", [pytest.param(case, id=case.id) for case in TAIL_CASES])
def test_iter_logs_returns_trailing_lines(tmp_path: Path, case: TailCase) -> None:
    log = tmp_path / "odoo.log"
    log.write_text(case.text)
    inst = _instance_from_config(tmp_path, logfile=str(log))
    assert list(inst.iter_logs(tail=case.tail)) == case.expected


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


def _invoke_logs(*args: str, instance: MagicMock) -> Result:
    with patch(
        "odoo_instance_sdk.cli.cli_context.ready_instance",
        return_value=(MagicMock(), SimpleNamespace(), instance),
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
    instance.iter_logs.return_value = iter(["line\n"])
    result = _invoke_logs(*args, instance=instance)
    assert result.exit_code == 0, result.output
    assert result.output == "line\n"
    instance.iter_logs.assert_called_once_with(tail=tail, follow=follow)


@pytest.mark.parametrize("tail", ["0", "-1"], ids=["zero", "negative"])
def test_cli_logs_invalid_tail_is_nonzero(tail: str) -> None:
    result = CliRunner().invoke(cli, ["logs", "--tail", tail])
    assert result.exit_code != 0


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
        return_value=(client, SimpleNamespace(), instance),
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
    assert list(tmp_path.iterdir()) == []
