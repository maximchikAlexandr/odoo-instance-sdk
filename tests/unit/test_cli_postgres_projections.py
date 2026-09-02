from __future__ import annotations

import json
import os
import pty
import select
import signal
import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands.output import (
    OutputMode,
    emit,
    model_to_dict,
    success_document,
)
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.models import (
    BloatCapabilities,
    DiagnosticWarning,
    IndexBloat,
    IndexStats,
    LockRow,
    LocksResult,
    PostgresBloatResult,
    PostgresStatsResult,
    StatsCapabilities,
    StatsSummary,
    TableBloat,
    TableStats,
)

if TYPE_CHECKING:
    import msgspec


CAPTURED_AT = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)


def _command(value: msgspec.Struct, error: BaseException | None = None) -> Command[msgspec.Struct]:
    def run(_context: object) -> msgspec.Struct:
        if error is not None:
            raise error
        return value

    return Command.create(ExecutionPlan(), run, ())


def _representative_results() -> tuple[tuple[str, msgspec.Struct], ...]:
    return (
        (
            "locks",
            LocksResult(
                database="feature_db",
                captured_at=CAPTURED_AT,
                rows=(
                    LockRow(
                        blocked_pid=42,
                        blocking_pids=(7, 8),
                        application_name="odoo",
                        user_name="odoo",
                        client_address="127.0.0.1",
                        wait_event_type="Lock",
                        wait_event="transactionid",
                        state="active",
                        transaction_age_seconds=4.5,
                        query_age_seconds=2.25,
                        query_preview="UPDATE res_partner SET name = 'changed'",
                    ),
                ),
                warnings=(
                    DiagnosticWarning(
                        code="cumulative_statistics",
                        message="statistics are cumulative",
                    ),
                ),
            ),
        ),
        (
            "stats",
            PostgresStatsResult(
                summary=StatsSummary(
                    database="feature_db",
                    server_version="16.4",
                    captured_at=CAPTURED_AT,
                    stats_since=None,
                    database_bytes=12_345,
                    block_size_bytes=8192,
                ),
                tables=(
                    TableStats(
                        schema="public",
                        table="res_partner",
                        estimated_live_rows=12,
                        heap_bytes=8192,
                        toast_bytes=0,
                        index_bytes=4096,
                        total_bytes=12_288,
                        index_count=1,
                        heap_blocks_read=3,
                        heap_blocks_hit=9,
                        index_blocks_read=1,
                        index_blocks_hit=4,
                        shared_buffer_bytes=0,
                        shared_buffer_ratio=0.0,
                        hot_page_ratio=0.0,
                    ),
                ),
                indexes=(
                    IndexStats(
                        schema="public",
                        index="res_partner_name_idx",
                        table="res_partner",
                        access_method="btree",
                        columns=("name",),
                        bytes=4096,
                        scans=3,
                    ),
                ),
                capabilities=StatsCapabilities(pg_buffercache=True),
                warnings=(
                    DiagnosticWarning(
                        code="pg_buffercache_privilege_denied",
                        message="pg_buffercache is unavailable",
                    ),
                    DiagnosticWarning(
                        code="cumulative_statistics",
                        message="statistics are cumulative",
                    ),
                ),
            ),
        ),
        (
            "bloat",
            PostgresBloatResult(
                database="feature_db",
                captured_at=CAPTURED_AT,
                tables=(
                    TableBloat(
                        schema="public",
                        table="res_partner",
                        total_bytes=16_384,
                        bloat_bytes=2048,
                        bloat_ratio=0.125,
                        live_tuples=10,
                        dead_tuples=2,
                        last_vacuum_at=None,
                        last_autovacuum_at=None,
                        last_analyze_at=CAPTURED_AT,
                        last_autoanalyze_at=None,
                        method="estimate",
                    ),
                ),
                indexes=(
                    IndexBloat(
                        schema="public",
                        index="res_partner_name_idx",
                        table="res_partner",
                        total_bytes=4096,
                        bloat_bytes=512,
                        bloat_ratio=0.125,
                        scans=3,
                        unused_candidate=False,
                        method="exact",
                    ),
                ),
                capabilities=BloatCapabilities(pgstattuple=True),
                warnings=(
                    DiagnosticWarning(
                        code="pgstattuple_query_failed",
                        message="exact bloat unavailable",
                    ),
                    DiagnosticWarning(
                        code="cumulative_statistics",
                        message="statistics are cumulative",
                    ),
                ),
            ),
        ),
    )


def _decode(document: str, mode: str) -> object:
    if mode == "json":
        return json.loads(document)
    from toon import DecodeOptions, decode

    return decode(document, DecodeOptions(indent=2, strict=True))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("command_name", "result"),
    _representative_results(),
    ids=("locks", "stats", "bloat"),
)
def test_diagnostic_cli_json_toon_preserve_frozen_results(
    command_name: str,
    result: msgspec.Struct,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = MagicMock()
    getattr(resource, f"{command_name}_command").return_value = _command(result)
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.pg._database_resource",
        lambda _ctx, _database: (None, resource, "feature_db"),
    )
    documents: list[dict[str, object]] = []
    for mode in ("json", "toon"):
        invoked = CliRunner().invoke(
            cli,
            ["db", command_name, "feature_db", "--format", mode],
        )
        assert invoked.exit_code == 0, invoked.output
        assert invoked.stderr == ""
        assert invoked.stdout.strip()
        decoded = _decode(invoked.stdout, mode)
        assert isinstance(decoded, dict)
        documents.append(decoded)
    assert documents[0] == documents[1]
    payload = documents[0]
    assert payload["ok"] is True
    assert payload["result"] == payload["data"]
    data = payload["data"]
    assert isinstance(data, dict)
    assert isinstance(data.get("warnings"), list)
    assert all(isinstance(item, dict) for item in data["warnings"])
    if command_name == "stats":
        summary = data["summary"]
        tables = data["tables"]
        indexes = data["indexes"]
        assert isinstance(summary, dict) and type(summary["database_bytes"]) is int
        assert isinstance(tables, list) and type(tables[0]["heap_bytes"]) is int
        assert type(tables[0]["shared_buffer_bytes"]) is int
        assert isinstance(indexes, list) and type(indexes[0]["bytes"]) is int
        assert {warning["code"] for warning in data["warnings"]} == {
            "pg_buffercache_privilege_denied",
            "cumulative_statistics",
        }
    elif command_name == "bloat":
        assert type(data["tables"][0]["total_bytes"]) is int
        assert type(data["indexes"][0]["total_bytes"]) is int
        assert {warning["code"] for warning in data["warnings"]} == {
            "pgstattuple_query_failed",
            "cumulative_statistics",
        }
    else:
        assert type(data["rows"][0]["blocked_pid"]) is int
        assert data["rows"][0]["blocking_pids"] == [7, 8]


@pytest.mark.unit
@pytest.mark.parametrize("mode", ("json", "toon"))
def test_diagnostic_cli_errors_keep_renderer_independent_exit_and_clean_streams(
    mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    resource = MagicMock()
    resource.locks_command.return_value = _command(
        _representative_results()[0][1], RuntimeError("password=secret")
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.pg._database_resource",
        lambda _ctx, _database: (None, resource, "feature_db"),
    )
    invoked = CliRunner().invoke(cli, ["db", "locks", "feature_db", "--format", mode])
    assert invoked.exit_code == 1
    assert invoked.stderr == ""
    document = _decode(invoked.stdout, mode)
    assert isinstance(document, dict)
    assert document["ok"] is False
    assert document["error"]["message"] == "<redacted>"


@pytest.mark.unit
def test_rich_diagnostics_keep_table_and_index_sections_separate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from odoo_instance_sdk.commands.pg import _bloat_rich, _stats_rich

    for command_name, result, renderer in (
        ("db.stats", _representative_results()[1][1], _stats_rich),
        ("db.bloat", _representative_results()[2][1], _bloat_rich),
    ):
        emit(
            success_document(command=command_name, result=model_to_dict(result)),
            OutputMode.RICH,
            rich=renderer,
        )
        rendered = capsys.readouterr().out
        assert "Tables" in rendered
        assert "Indexes" in rendered
        assert "res_partner" in rendered


def _run_pty_cli(  # noqa: C901
    # The helper intentionally keeps the real subprocess/PTY lifecycle in one
    # guard so signal delivery and cleanup are asserted at the same boundary.
    repo_root: Path,
    tmp_path: Path,
    args: tuple[str, ...],
    *,
    exit_code: int = 0,
    interactive_input: bytes = b"",
    signal_case: bool = False,
) -> tuple[int, str, bool]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_psql = fake_bin / "psql"
    fake_psql.write_text(
        textwrap.dedent(
            """
            #!/usr/bin/env python3
            import os
            import signal
            import sys

            if os.getenv("PSQL_HOLD"):
                def on_signal(_signum, _frame):
                    print("native-signal=SIGTERM", file=sys.stderr, flush=True)
                    os._exit(143)
                signal.signal(signal.SIGTERM, on_signal)
            print("tty=" + ",".join(str(stream.isatty()) for stream in (sys.stdin, sys.stdout, sys.stderr)), flush=True)
            print("native-stdout", flush=True)
            print("native-stderr", file=sys.stderr, flush=True)
            print("argv=" + repr(sys.argv[1:]), flush=True)
            print("native-ready", flush=True)
            if os.getenv("PSQL_INTERACTIVE"):
                print("input=" + sys.stdin.read(1), flush=True)
            if os.getenv("PSQL_HOLD"):
                signal.pause()
            raise SystemExit(int(os.getenv("PSQL_EXIT", "0")))
            """
        ).lstrip(),
        encoding="utf-8",
    )
    fake_psql.chmod(0o755)
    child = tmp_path / "invoke_odcli.py"
    child.write_text(
        textwrap.dedent(
            """
            import sys
            from pathlib import Path

            from odoo_instance_sdk.client import OdooClient, OdooClientConfig
            from odoo_instance_sdk.commands import pg
            from odoo_instance_sdk.config import InstanceConfig
            from odoo_instance_sdk.resources.instance import OdooInstance

            client = OdooClient(config=OdooClientConfig(executable="odoo"))
            instance = OdooInstance(
                config=InstanceConfig(
                    base_url="http://127.0.0.1:8069",
                    configured_database_names=("feature_db",),
                    db_host="127.0.0.1",
                    db_port=5432,
                    db_user="odoo",
                    db_password="private-password",
                    default_cwd=Path.cwd(),
                ),
                _client=client,
            )
            pg._database_resource = lambda _ctx, _database: (None, instance.databases, "feature_db")
            from odoo_instance_sdk.cli import cli

            cli.main(args=sys.argv[1:], prog_name="odcli", standalone_mode=True)
            """
        ),
        encoding="utf-8",
    )
    master, slave = pty.openpty()
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment.get('PATH', '')}"
    environment["PYTHONPATH"] = str(repo_root / "src")
    environment["PSQL_EXIT"] = str(exit_code)
    if interactive_input:
        environment["PSQL_INTERACTIVE"] = "1"
    if signal_case:
        environment["PSQL_HOLD"] = "1"
    process = subprocess.Popen(
        [sys.executable, str(child), *args],
        cwd=repo_root,
        env=environment,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave)
    os.set_blocking(master, False)
    if interactive_input:
        os.write(master, interactive_input)
    output = bytearray()
    signal_sent = False
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        ready, _, _ = select.select([master], [], [], 0.1)
        if ready:
            try:
                output.extend(os.read(master, 4096))
            except (BlockingIOError, OSError):
                break
        if signal_case and not signal_sent and b"native-ready" in output:
            os.killpg(process.pid, signal.SIGINT)
            signal_sent = True
        if process.poll() is not None:
            break
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=2.0)
        os.close(master)
        raise AssertionError(
            "native psql PTY child did not exit before the test deadline: "
            + output.decode(errors="replace")
        )
    return_code = process.wait(timeout=2.0)
    if signal_case and os.name != "nt":
        with pytest.raises(ProcessLookupError):
            os.killpg(process.pid, 0)
    drain_deadline = time.monotonic() + 1.0
    while time.monotonic() < drain_deadline:
        ready, _, _ = select.select([master], [], [], 0.05)
        if not ready:
            break
        try:
            output.extend(os.read(master, 4096))
        except (BlockingIOError, OSError):
            break
    os.close(master)
    return return_code, output.decode(errors="replace"), signal_sent


@pytest.mark.integration
@pytest.mark.skipif(not hasattr(pty, "openpty"), reason="PTY is unavailable on this platform")
@pytest.mark.parametrize(
    ("args", "exit_code", "interactive_input", "signal_case"),
    (
        (("psql",), 0, b"h\n", False),
        (("psql", "-c", "SELECT 1"), 0, b"", False),
        (("psql", "-f", "query.sql"), 0, b"", False),
        (("psql", "-c", "SELECT 1"), 7, b"", False),
        (("psql", "-c", "SELECT 1"), 0, b"", True),
    ),
    ids=("interactive", "command", "file", "nonzero", "signal"),
)
def test_psql_cli_native_pty_streams_arguments_exit_and_signal(
    tmp_path: Path,
    args: tuple[str, ...],
    exit_code: int,
    interactive_input: bytes,
    signal_case: bool,
) -> None:
    repo_root = Path(__file__).parents[2]
    actual_exit, output, signal_sent = _run_pty_cli(
        repo_root,
        tmp_path,
        args,
        exit_code=exit_code,
        interactive_input=interactive_input,
        signal_case=signal_case,
    )
    expected_exit = 130 if signal_case else exit_code
    assert actual_exit == expected_exit
    assert "tty=True,True,True" in output
    assert "native-stdout" in output
    assert "native-stderr" in output
    if interactive_input:
        assert "input=h" in output
    if "-c" in args:
        assert "'-c', 'SELECT 1'" in output
    if "-f" in args:
        assert "'-f', 'query.sql'" in output
    if signal_case:
        assert signal_sent
        assert "native-signal=SIGTERM" in output
