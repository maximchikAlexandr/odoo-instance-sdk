"""Stage A structured Odoo logs: manifest, query, launch, CLI, and cleanup."""

from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Literal, cast
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.commands.logs import present_log_follow
from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
from odoo_instance_sdk.exceptions import ConfigError, InstanceConfigurationError, LogQueryError
from odoo_instance_sdk.internal.doctor import DoctorReport
from odoo_instance_sdk.internal.doctor.manifest import _check_odoo_logging
from odoo_instance_sdk.internal.logs.cursor import decode_cursor
from odoo_instance_sdk.internal.logs.launch import (
    finish_structured_run,
    prepare_structured_run,
    structured_launch,
)
from odoo_instance_sdk.internal.logs.parser import parse_logfile
from odoo_instance_sdk.internal.logs.store import (
    LogFilters,
    LogScope,
    create_mark,
    search_page,
    write_run_meta,
)
from odoo_instance_sdk.internal.storage_footprint import _other_files_bytes
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.models.logs import LogEvent
from odoo_instance_sdk.project import OdooLoggingConfig, ProjectConfig
from odoo_instance_sdk.resources.environment.pgadmin import _PgadminMixin
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.instance.runtime import _RuntimeBinding


def _project(root: Path, logging: OdooLoggingConfig | None = None) -> ProjectConfig:
    config = ProjectConfig(
        repository_root=root,
        odoo_bin=Path(sys.executable),
        odoo_logging=logging,
    )
    manifest = root / ".odcli"
    manifest.mkdir(parents=True, exist_ok=True)
    (manifest / "project.toml").write_text(config.to_manifest(), encoding="utf-8")
    return config


def _instance(root: Path, *, owner_kind: str = "environment") -> OdooInstance:
    environment = root / "env"
    environment.mkdir(exist_ok=True)
    (environment / "odoo.conf").write_text("[options]\n", encoding="utf-8")
    return OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            start_config=StartConfig(
                http_port=8069,
                http_interface="127.0.0.1",
                config_path=str(environment / "odoo.conf"),
                db_name="db",
            ),
            command_prefix=(sys.executable, "-c", "pass"),
            default_cwd=environment,
        ),
        _client=OdooClient(config=OdooClientConfig(executable=sys.executable)),
        _environment_id="env-1",
        _runtime_binding=_RuntimeBinding(
            owner_kind=_owner_kind(owner_kind),
            owner_id="env-1",
            project_id="project-1",
            repository_root=root,
            git_common_dir=root,
        ),
    )


def _owner_kind(value: str) -> Literal["environment", "project"]:
    if value == "project":
        return "project"
    return "environment"


def _scope(root: Path) -> LogScope:
    return LogScope("project-1", "env-1", root)


def _write_run(root: Path, body: bytes, *, run_id: str = "run_a") -> Path:
    directory = root / "logs" / run_id
    directory.mkdir(parents=True)
    os.chmod(root / "logs", 0o700)
    os.chmod(directory, 0o700)
    logfile = directory / "odoo.log"
    logfile.write_bytes(body)
    os.chmod(logfile, 0o600)
    write_run_meta(
        directory,
        {
            "run_id": run_id,
            "state": "finished",
            "pid": None,
            "started_at": "2020-01-01T00:00:00.000Z",
            "process_create_time": None,
        },
    )
    return logfile


def _line(message: str) -> bytes:
    return f"2020-01-01 00:00:00,000 7 INFO db odoo.http: {message}\n".encode()


def test_logging_section_round_trips_and_blank_handler_is_rejected(tmp_path: Path) -> None:
    logging = OdooLoggingConfig(structured=True, level="debug", handlers=("odoo:INFO",))
    _project(tmp_path, logging)
    loaded = ProjectConfig.load(tmp_path)
    assert loaded.odoo_logging == logging

    omitted = ProjectConfig(repository_root=tmp_path, odoo_bin=Path(sys.executable))
    assert "[logging" not in omitted.to_manifest()
    with pytest.raises(ConfigError, match="logger:LEVEL"):
        OdooLoggingConfig(handlers=("",))


def test_doctor_logging_facts_are_present_when_the_section_is_absent(tmp_path: Path) -> None:
    _project(tmp_path)
    report = DoctorReport()
    _check_odoo_logging(report, tmp_path)
    assert report.checks[0].status == "ok"
    assert report.checks[0].facts == {"structured": False, "level": None, "handlers": []}

    configured = tmp_path / "configured"
    configured.mkdir()
    _project(
        configured, OdooLoggingConfig(structured=True, level="warning", handlers=("odoo:DEBUG",))
    )
    present = DoctorReport()
    _check_odoo_logging(present, configured)
    assert present.checks[0].facts["structured"] is True
    assert present.checks[0].facts["level"] == "warning"
    assert present.checks[0].facts["handlers"] == ["odoo:DEBUG"]
    assert json.loads(json.dumps(present.checks[0].facts))["structured"] is True


def test_parser_keeps_continuations_and_unparsed_lines() -> None:
    data = b"not a prefix\n" + _line("hello") + b"Traceback\n"
    records = parse_logfile(data)
    assert records[0].parsed is False
    assert records[0].message == "not a prefix"
    assert records[1].parsed is True
    assert records[1].message == "hello\nTraceback"


def test_cursor_errors_have_stable_codes_and_no_paths(tmp_path: Path) -> None:
    logfile = _write_run(tmp_path, _line("one") + _line("two"))
    scope = _scope(tmp_path)
    page = search_page(scope, None, LogFilters())
    with pytest.raises(LogQueryError, match="cursor is invalid") as invalid:
        decode_cursor("@@@", project_id="project-1", environment_id="env-1")
    with pytest.raises(LogQueryError, match="another instance") as wrong:
        decode_cursor(page.next_cursor, project_id="other", environment_id="env-1")
    logfile.write_bytes(_line("rewritten"))
    with pytest.raises(LogQueryError, match="reset") as reset:
        search_page(scope, None, LogFilters(), after=page.next_cursor)
    previous = logfile.stat()
    rotated = logfile.with_name(".odoo.log.next")
    rotated.write_bytes(_line("rotated"))
    logfile.unlink()
    os.replace(rotated, logfile)
    assert (logfile.stat().st_dev, logfile.stat().st_ino) != (previous.st_dev, previous.st_ino)
    with pytest.raises(LogQueryError, match="no longer retained") as expired:
        search_page(scope, None, LogFilters(), after=page.next_cursor)
    for error in (invalid, wrong, reset, expired):
        assert "/" not in str(error.value)
        assert "odoo.log" not in str(error.value)


def test_until_cursor_excludes_records_appended_after_the_window(tmp_path: Path) -> None:
    logfile = _write_run(tmp_path, _line("kept"))
    scope = _scope(tmp_path)
    first = search_page(scope, "run_a", LogFilters(), limit=1)
    logfile.write_bytes(logfile.read_bytes() + _line("appended"))
    frozen = search_page(scope, "run_a", LogFilters(), until=first.window_end_cursor)
    assert [event.message for event in frozen.events] == ["kept"]
    assert frozen.complete is True


def test_equal_timestamps_keep_file_order(tmp_path: Path) -> None:
    _write_run(tmp_path, _line("first") + _line("second"))
    page = search_page(_scope(tmp_path), None, LogFilters())
    assert [event.message for event in page.events] == ["first", "second"]


def test_structured_dry_run_does_not_create_the_run_directory(tmp_path: Path) -> None:
    _project(tmp_path, OdooLoggingConfig(structured=True, level="debug", handlers=("odoo:INFO",)))
    instance = _instance(tmp_path)
    command = instance.run_detached_command()
    argv = command.plan.process_steps[0].argv
    logfile = argv[argv.index("--logfile") + 1]
    assert "/logs/run_" in logfile.replace("\\", "/")
    assert argv[argv.index("--log-level") + 1] == "debug"
    assert "odoo:INFO" in argv
    assert not (tmp_path / "env" / "logs").exists()
    assert any(step.step_id == "instance.detached.runlog" for step in command.plan.steps)


def test_prepare_structured_run_uses_private_modes_and_secret_free_metadata(tmp_path: Path) -> None:
    _project(tmp_path, OdooLoggingConfig(structured=True))
    launch = structured_launch(_instance(tmp_path), action_id="instance.detached.runlog")
    assert launch is not None
    prepare_structured_run(launch)
    assert stat.S_IMODE(launch.logfile.parent.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(launch.logfile.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(launch.logfile.stat().st_mode) == 0o600
    meta = json.loads((launch.logfile.parent / "meta.json").read_text(encoding="utf-8"))
    assert meta["state"] == "running"
    assert meta["pid"] is None
    assert "password" not in json.dumps(meta)


def test_prepare_refuses_a_symlinked_logs_directory(tmp_path: Path) -> None:
    _project(tmp_path, OdooLoggingConfig(structured=True))
    outside = tmp_path / "outside"
    outside.mkdir()
    environment = tmp_path / "env"
    environment.mkdir()
    (environment / "logs").symlink_to(outside, target_is_directory=True)
    launch = structured_launch(_instance(tmp_path), action_id="instance.detached.runlog")
    assert launch is not None
    with pytest.raises(InstanceConfigurationError, match="symlink"):
        prepare_structured_run(launch)
    assert list(outside.iterdir()) == []


def test_finish_keeps_an_existing_terminal_state(tmp_path: Path) -> None:
    _project(tmp_path, OdooLoggingConfig(structured=True))
    launch = structured_launch(_instance(tmp_path), action_id="instance.detached.runlog")
    assert launch is not None
    prepare_structured_run(launch)
    finish_structured_run(launch, state="interrupted", exit_code=None)
    finish_structured_run(launch, state="finished", exit_code=0)
    meta = json.loads((launch.logfile.parent / "meta.json").read_text(encoding="utf-8"))
    assert meta["state"] == "interrupted"
    assert meta["exit_code"] is None


def test_unreadable_logfile_error_omits_the_path(tmp_path: Path) -> None:
    logfile = _write_run(tmp_path, _line("hidden"))
    logfile.chmod(0)
    try:
        with pytest.raises(LogQueryError, match="reset") as error:
            search_page(_scope(tmp_path), None, LogFilters())
    finally:
        logfile.chmod(0o600)
    assert str(tmp_path) not in str(error.value)
    assert "odoo.log" not in str(error.value)


def test_broken_manifest_does_not_fail_the_logging_check(tmp_path: Path) -> None:
    manifest = tmp_path / ".odcli"
    manifest.mkdir()
    (manifest / "project.toml").write_text("not = [", encoding="utf-8")
    report = DoctorReport()
    _check_odoo_logging(report, tmp_path)
    assert report.checks == []


def test_project_owner_cannot_enable_structured_launch(tmp_path: Path) -> None:
    _project(tmp_path, OdooLoggingConfig(structured=True))
    with pytest.raises(InstanceConfigurationError, match="ready environment"):
        structured_launch(
            _instance(tmp_path, owner_kind="project"), action_id="instance.foreground.runlog"
        )


def test_marks_are_private_atomic_and_dry_run_writes_nothing(tmp_path: Path) -> None:
    environment = tmp_path / "env"
    _write_run(environment, _line("before"))
    scope = LogScope("project-1", "env-1", environment)
    create_mark(scope, "checkpoint", None, replace=False)
    assert (
        stat.S_IMODE((environment / "logs" / "marks" / "checkpoint.json").stat().st_mode) == 0o600
    )
    with pytest.raises(LogQueryError, match="already exists") as exists:
        create_mark(scope, "checkpoint", None, replace=False)
    with pytest.raises(LogQueryError, match="not found") as missing:
        create_mark(scope, "../outside", None, replace=False)
    for error in (exists, missing):
        assert "/" not in str(error.value)
    _project(tmp_path, OdooLoggingConfig(structured=True))
    instance = _instance(tmp_path)
    command = instance.create_log_mark_command("other")
    assert command.plan.steps
    assert not (environment / "logs" / "marks" / "other.json").exists()
    created = command.run()
    assert created.name == "other"
    assert (environment / "logs" / "marks" / "other.json").is_file()


def test_search_json_page_and_jsonl_interrupt(tmp_path: Path) -> None:
    event = LogEvent(
        schema_version=1,
        source="odoo",
        timestamp=None,
        level="ERROR",
        message="deadlock detected",
        project_id="project",
        environment_id="env",
        run_id="run",
        pid=None,
        database=None,
        logger=None,
        parsed=True,
        attributes={},
        cursor="cursor-token",
    )

    def follow(**_kwargs: object) -> Iterator[LogEvent]:
        yield event
        raise KeyboardInterrupt

    instance = MagicMock()
    instance.search_log_events.return_value = search_page(
        _scope(_write_run(tmp_path, _line("kept")).parents[2]),
        None,
        LogFilters(),
    )
    instance.iter_log_events.side_effect = follow
    with patch(
        "odoo_instance_sdk.cli.cli_context.ready_instance",
        return_value=MagicMock(instance=instance),
    ):
        page = CliRunner().invoke(cli, ["logs", "search", "--format", "json"])
        followed = CliRunner().invoke(cli, ["logs", "search", "--follow", "--format", "jsonl"])
    assert page.exit_code == 0, page.output
    document = json.loads(page.stdout)
    assert document["result"].keys() >= {
        "events",
        "next_cursor",
        "has_more",
        "window_end_cursor",
        "complete",
    }
    assert followed.exit_code == 130
    rows = [json.loads(line) for line in followed.stdout.splitlines()]
    assert rows[0]["type"] == "checkpoint"
    assert rows[1]["type"] == "event"
    assert rows[-1]["type"] == "checkpoint"


def test_present_log_follow_checkpoint_exits_130() -> None:
    event = LogEvent(
        schema_version=1,
        source="odoo",
        timestamp=None,
        level="INFO",
        message="hello",
        project_id="project",
        environment_id="env",
        run_id="run",
        pid=None,
        database=None,
        logger=None,
        parsed=True,
        attributes={},
        cursor="cursor-token",
    )

    def events() -> Iterator[LogEvent]:
        yield event
        raise KeyboardInterrupt

    with pytest.raises(SystemExit) as raised:
        present_log_follow(events(), cursor="cursor-token")
    assert raised.value.code == 130


def test_environment_removal_deletes_owned_logs_and_keeps_unrelated_directories(
    tmp_path: Path,
) -> None:
    environment = tmp_path / "env"
    generated = environment / "odoo.conf"
    generated.parent.mkdir()
    generated.write_text("[options]\n", encoding="utf-8")
    (environment / "logs" / "run_a").mkdir(parents=True)
    (environment / "logs" / "run_a" / "odoo.log").write_text("owned\n", encoding="utf-8")
    (environment / "keep").mkdir()
    unrelated = tmp_path / "unrelated-logs"
    unrelated.mkdir()
    failures: list[str] = []
    assert (
        _PgadminMixin._remove_files(
            cast("_PgadminMixin", None), generated, environment / "lock", failures
        )
        is False
    )
    assert not (environment / "logs").exists()
    assert (environment / "keep").is_dir()
    assert unrelated.is_dir()
    assert failures == []


def test_other_files_bytes_includes_run_logs(tmp_path: Path) -> None:
    environment = tmp_path / "env"
    generated = environment / "odoo.conf"
    environment.mkdir()
    generated.write_bytes(b"abc")
    lock = environment / "lock"
    lock.write_bytes(b"d")
    logfile = environment / "logs" / "run_a" / "odoo.log"
    logfile.parent.mkdir(parents=True)
    logfile.write_bytes(b"hello")
    measured = _other_files_bytes(generated, lock, environment)
    assert measured == len(b"abc") + len(b"d") + len(b"hello")
