from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from click.testing import CliRunner

from odoo_instance_sdk import bug_report as bug_report_module
from odoo_instance_sdk.bug_report import bug_report_init_command
from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.internal.bug_report import (
    _normalize_version,
    _parse_odoo_version,
    _VersionProbePlan,
    bug_reports_root,
)
from odoo_instance_sdk.internal.proc import (
    PreparedStep,
    ProcessResult,
    RecordingExecutor,
    SubprocessExecutor,
)
from odoo_instance_sdk.resources.postgres import PostgresCluster


def _configured_probe_command(
    monkeypatch: pytest.MonkeyPatch,
    *,
    odoo_stdout: str,
    odoo_returncode: int,
) -> tuple[Any, RecordingExecutor, PreparedStep, PreparedStep]:
    odoo_step = PreparedStep(
        step_id="bug-report.version.odoo",
        argv=("python", "odoo-bin", "--version"),
        timeout=5.0,
        max_combined_output_bytes=16 * 1024,
        read_only=True,
        text=True,
    )
    postgres_step = PreparedStep(
        step_id="postgres.status.server-summary.0",
        argv=("psql", "-X", "-d", "<redacted>"),
        timeout=10.0,
        read_only=True,
        text=True,
    )
    probe_plan = _VersionProbePlan(
        odoo_step=odoo_step,
        postgres_steps=(postgres_step,),
        postgres_cluster=cast("PostgresCluster", object()),
        postgres_eligibility=None,
    )

    def result_for(step: object) -> ProcessResult:
        prepared = cast("PreparedStep", step)
        return ProcessResult(
            argv=prepared.argv,
            returncode=odoo_returncode if prepared.step_id == odoo_step.step_id else 0,
            stdout=odoo_stdout,
            stderr="",
            duration=0.0,
            cwd=prepared.cwd,
            environment=prepared.environment,
        )

    executor = RecordingExecutor(result_factory=result_for)

    def collect_summary(**kwargs: Any) -> Any:
        context = kwargs["context"]
        context.process_prepared(kwargs["steps"][0])
        return SimpleNamespace(server=SimpleNamespace(version="16.2"))

    def snapshot() -> object:
        return object()

    monkeypatch.setattr(bug_report_module, "resolve_project_snapshot", snapshot)
    monkeypatch.setattr(
        bug_report_module, "_build_version_probe_plan", lambda _snapshot: probe_plan
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.server.collect_server_summary", collect_summary
    )

    return bug_report_init_command(title="runtime versions"), executor, odoo_step, postgres_step


@pytest.mark.parametrize(
    ("odoo_stdout", "odoo_returncode", "expected_odoo"),
    (
        ("Odoo Server 17.0\n", 0, "17.0"),
        ("Odoo Server 17.0\nextra\n", 0, "unknown"),
        ("not a version\n", 0, "unknown"),
        ("Odoo Server 17.0\n", 1, "unknown"),
    ),
)
def test_init_odoo_result_matrix(
    odoo_stdout: str,
    odoo_returncode: int,
    expected_odoo: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command, executor, _odoo_step, _postgres_step = _configured_probe_command(
        monkeypatch, odoo_stdout=odoo_stdout, odoo_returncode=odoo_returncode
    )
    result = command.run()
    report = Path(result.report_path).read_text(encoding="utf-8")
    assert f"- Odoo version: {expected_odoo}" in report
    assert all(step.read_only for step in executor.executed)


def test_init_plan_captures_independent_provider_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command, executor, odoo_step, postgres_step = _configured_probe_command(
        monkeypatch, odoo_stdout="Odoo Server 17.0\n", odoo_returncode=0
    )
    assert [step.step_id for step in command.plan.steps] == [
        odoo_step.step_id,
        postgres_step.step_id,
        "bug-report.init",
    ]
    assert executor.executed == []


def test_init_postgres_result_stays_concrete_when_odoo_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command, _executor, _odoo_step, _postgres_step = _configured_probe_command(
        monkeypatch, odoo_stdout="not a version\n", odoo_returncode=0
    )
    result = command.run()
    report = Path(result.report_path).read_text(encoding="utf-8")
    assert "- PostgreSQL version: 16.2" in report
    assert "- Odoo version: unknown" in report


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("Odoo Server 17.0", "17.0"),
        ("Odoo Server 17.0\nextra", "unknown"),
        ("Odoo Server 17.0; rm -rf", "unknown"),
        ("Odoo Server " + "x" * 16_385, "unknown"),
    ),
)
def test_odoo_version_parser_is_strict_and_bounded(raw: str, expected: str) -> None:
    assert _parse_odoo_version(raw) == expected


def test_version_providers_require_digit_first_safe_values() -> None:
    assert _normalize_version("release") == "unknown"
    assert _parse_odoo_version("Odoo Server release") == "unknown"


def test_init_timeout_keeps_draft_available(monkeypatch: pytest.MonkeyPatch) -> None:
    step = PreparedStep(
        step_id="bug-report.version.odoo",
        argv=("python", "odoo-bin", "--version"),
        timeout=5.0,
        max_combined_output_bytes=16 * 1024,
        read_only=True,
    )
    plan = _VersionProbePlan(
        odoo_step=step,
        postgres_steps=(),
        postgres_cluster=None,
        postgres_eligibility=None,
    )
    executor = RecordingExecutor(result_factory=lambda _step: (_ for _ in ()).throw(TimeoutError()))

    def snapshot() -> object:
        return object()

    monkeypatch.setattr(bug_report_module, "resolve_project_snapshot", snapshot)
    monkeypatch.setattr(bug_report_module, "_build_version_probe_plan", lambda _snapshot: plan)
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    result = bug_report_init_command(title="timeout fallback").run()
    report = Path(result.report_path).read_text(encoding="utf-8")
    assert "- Odoo version: unknown" in report


def _server_summary_json(version: str = "16.4") -> str:
    return json.dumps(
        {
            "version": version,
            "postmaster_started_at": "2025-01-01T00:00:00Z",
            "uptime_seconds": 1,
            "connections_total": 1,
            "connections_active": 1,
            "connections_idle": 1,
            "max_connections": 100,
            "connectable_databases": 1,
        }
    )


def _configured_postgres_failure_probe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    title: str,
    postgres_stdout: str,
    postgres_returncode: int,
    postgres_error: BaseException | None,
    eligibility: str | None,
) -> tuple[Any, RecordingExecutor]:
    odoo_step = PreparedStep(
        step_id="bug-report.version.odoo",
        argv=("python", "odoo-bin", "--version"),
        timeout=5.0,
        max_combined_output_bytes=16 * 1024,
        read_only=True,
    )
    postgres_steps = tuple(
        PreparedStep(
            step_id=f"postgres.status.server-summary.{index}",
            argv=("psql", str(index)),
            timeout=10.0,
            read_only=True,
        )
        for index in range(3)
    )
    plan = _VersionProbePlan(
        odoo_step=odoo_step,
        postgres_steps=postgres_steps,
        postgres_cluster=cast("PostgresCluster", object()),
        postgres_eligibility=cast("Any", eligibility),
    )

    def result_for(step: object) -> ProcessResult:
        prepared = cast("PreparedStep", step)
        return ProcessResult(
            argv=prepared.argv,
            returncode=(0 if prepared.step_id == odoo_step.step_id else postgres_returncode),
            stdout=(
                "Odoo Server 17.0\n" if prepared.step_id == odoo_step.step_id else postgres_stdout
            ),
            stderr="postgres error" if prepared.step_id != odoo_step.step_id else "",
            duration=0.0,
            cwd=prepared.cwd,
            environment=prepared.environment,
        )

    def result_or_error(step: object) -> ProcessResult:
        prepared = cast("PreparedStep", step)
        if prepared.step_id != odoo_step.step_id and postgres_error is not None:
            raise postgres_error
        return result_for(step)

    executor = RecordingExecutor(result_factory=result_or_error)

    def snapshot() -> object:
        return object()

    monkeypatch.setattr(bug_report_module, "resolve_project_snapshot", snapshot)
    monkeypatch.setattr(bug_report_module, "_build_version_probe_plan", lambda _snapshot: plan)
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)
    return bug_report_init_command(title=title), executor


@pytest.mark.parametrize(
    (
        "case",
        "postgres_stdout",
        "postgres_returncode",
        "postgres_error",
        "eligibility",
        "expected_postgres_ids",
    ),
    (
        ("unavailable", "", 0, None, "psql_missing", ()),
        ("non-zero", "", 1, None, None, ("postgres.status.server-summary.0",)),
        ("timeout", "", 0, TimeoutError(), None, ("postgres.status.server-summary.0",)),
        (
            "exception",
            "",
            0,
            RuntimeError("query failed"),
            None,
            ("postgres.status.server-summary.0",),
        ),
        (
            "oversized",
            _server_summary_json("1" * 65),
            0,
            None,
            None,
            ("postgres.status.server-summary.0",),
        ),
        (
            "multiline",
            _server_summary_json("16.4\nsecret"),
            0,
            None,
            None,
            ("postgres.status.server-summary.0",),
        ),
        (
            "unsafe",
            _server_summary_json("release"),
            0,
            None,
            None,
            ("postgres.status.server-summary.0",),
        ),
    ),
)
def test_postgres_failures_preserve_odoo_and_account_draft(
    case: str,
    postgres_stdout: str,
    postgres_returncode: int,
    postgres_error: BaseException | None,
    eligibility: str | None,
    expected_postgres_ids: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command, executor = _configured_postgres_failure_probe(
        monkeypatch,
        title=f"postgres {case}",
        postgres_stdout=postgres_stdout,
        postgres_returncode=postgres_returncode,
        postgres_error=postgres_error,
        eligibility=eligibility,
    )
    result = command.run()
    report = Path(result.report_path).read_text(encoding="utf-8")
    assert "- Odoo version: 17.0" in report
    assert "- PostgreSQL version: unknown" in report
    pg_executed = tuple(
        step.step_id
        for step in executor.executed
        if step.step_id.startswith("postgres.status.server-summary.")
    )
    assert pg_executed == expected_postgres_ids
    assert result.report_id


def test_init_dry_run_previews_probes_without_spawning(monkeypatch: pytest.MonkeyPatch) -> None:
    step = PreparedStep(
        step_id="bug-report.version.odoo",
        argv=("python", "odoo-bin", "--version"),
        timeout=5.0,
        max_combined_output_bytes=16 * 1024,
        read_only=True,
    )
    plan = _VersionProbePlan(
        odoo_step=step,
        postgres_steps=(),
        postgres_cluster=None,
        postgres_eligibility=None,
    )

    class TrapExecutor:
        def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("dry-run executed a process")

        def execute_with_deadline(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("dry-run executed a process")

        def spawn(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("dry-run spawned a process")

    root = bug_reports_root(ensure_exists=False)
    before = set(root.iterdir()) if root.is_dir() else set()

    def snapshot() -> object:
        return object()

    monkeypatch.setattr(bug_report_module, "resolve_project_snapshot", snapshot)
    monkeypatch.setattr(bug_report_module, "_build_version_probe_plan", lambda _snapshot: plan)
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", TrapExecutor)

    result = CliRunner().invoke(
        cli,
        [
            "bug-report",
            "init",
            "--title",
            "preview versions",
            "--kind",
            "bug",
            "--dry-run",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    step_ids = [step["step_id"] for step in payload["result"]["steps"]]
    assert step.step_id in step_ids
    after = set(root.iterdir()) if root.is_dir() else set()
    assert after == before


def test_real_snapshot_and_plan_are_process_free_and_complete(
    project_manifest: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class TrapExecutor:
        def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("construction executed a process")

        def execute_with_deadline(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("construction executed a deadline process")

        def spawn(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("construction spawned a process")

    monkeypatch.chdir(project_manifest)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.server.resolve_psql_executable",
        lambda: Path("/usr/bin/psql"),
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", TrapExecutor)

    command = bug_report_init_command(title="real managed project plan")
    prepared = command._prepared()
    process_steps = tuple(step for step in prepared.steps if isinstance(step, PreparedStep))
    process_ids = tuple(step.step_id for step in process_steps)

    assert process_ids[0] == "bug-report.version.odoo"
    assert process_ids[1:] == tuple(
        f"postgres.status.server-summary.{index}" for index in range(len(process_ids) - 1)
    )
    assert tuple(step.step_id for step in command.plan.steps) == tuple(
        step.step_id for step in prepared.steps
    )
    assert all(step.read_only and step.mode == "captured" for step in process_steps)
    assert all(step.environment_policy == "explicit" for step in process_steps)
    assert all(
        set(dict(step.environment_snapshot))
        <= {"LANG", "LC_ALL", "PATH", "PGPASSWORD", "PGOPTIONS"}
        for step in process_steps
    )
    assert process_steps[0].timeout == 5.0
    assert process_steps[0].max_combined_output_bytes == 16 * 1024
    assert all(step.timeout == 10.0 for step in process_steps[1:])
    assert "shell=False" in inspect.getsource(SubprocessExecutor._execute)
    public_plan = repr(command.plan)
    assert "secret" not in public_plan.lower()
    assert "db_password" not in public_plan


def test_unregistered_manifest_cannot_spawn_runtime_or_receive_secret(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = git_repo / "untrusted-runtime"
    sentinel = git_repo / "spawned"
    script.write_text(f"#!/bin/sh\nprintf '%s' \"$ODCLI_SENTINEL_SECRET\" > {sentinel}\n")
    script.chmod(0o700)
    manifest_dir = git_repo / ".odcli"
    manifest_dir.mkdir()
    (manifest_dir / "project.toml").write_text(
        f'[project]\npython = "{script}"\nodoo_bin = "{script}"\n'
    )
    monkeypatch.setenv("ODCLI_SENTINEL_SECRET", "do-not-pass")
    monkeypatch.chdir(git_repo)

    command = bug_report_init_command(title="unregistered manifest")
    prepared = command._prepared()

    assert [step.step_id for step in prepared.steps] == ["bug-report.init"]
    assert "do-not-pass" not in repr(command.plan)
    result = command.run()
    assert Path(result.report_path).is_file()
    assert not sentinel.exists()


def _run_managed_project_cli(
    project_manifest: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, RecordingExecutor, tuple[str, ...]]:
    def result_for(step: object) -> ProcessResult:
        prepared = cast("PreparedStep", step)
        stdout = (
            "Odoo Server 17.0\n"
            if prepared.step_id == "bug-report.version.odoo"
            else _server_summary_json()
        )
        return ProcessResult(
            argv=prepared.argv,
            returncode=0,
            stdout=stdout,
            stderr="raw probe stderr secret=should-not-leak",
            duration=0.0,
            cwd=prepared.cwd,
            environment=prepared.environment,
        )

    executor = RecordingExecutor(result_factory=result_for)
    monkeypatch.chdir(project_manifest)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.server.resolve_psql_executable",
        lambda: Path("/usr/bin/psql"),
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", lambda: executor)

    command = bug_report_init_command(title="managed project versions")
    prepared = command._prepared()
    postgres_ids = tuple(
        step.step_id
        for step in prepared.steps
        if isinstance(step, PreparedStep)
        and step.step_id.startswith("postgres.status.server-summary.")
    )
    result = CliRunner().invoke(
        cli,
        [
            "bug-report",
            "init",
            "--title",
            "managed project versions",
            "--kind",
            "bug",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    report = Path(payload["result"]["report_path"]).read_text(encoding="utf-8")
    return report, executor, postgres_ids


def test_real_managed_project_cli_renders_safe_versions(
    project_manifest: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report, _executor, _postgres_ids = _run_managed_project_cli(project_manifest, monkeypatch)
    assert "- Odoo version: 17.0" in report
    assert "- PostgreSQL version: 16.4" in report
    assert "secret" not in report


def test_real_managed_project_cli_accounts_candidates(
    project_manifest: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _report, executor, postgres_ids = _run_managed_project_cli(project_manifest, monkeypatch)
    assert tuple(step.step_id for step in executor.executed) == (
        "bug-report.version.odoo",
        postgres_ids[0],
    )
    assert set(postgres_ids[1:]).isdisjoint({step.step_id for step in executor.executed})
    assert len(executor.effective_timeouts) == 1
    assert executor.effective_timeouts[0] <= 10.0


def test_real_outside_project_cli_keeps_both_versions_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    result = CliRunner().invoke(
        cli,
        ["bug-report", "init", "--title", "outside project", "--kind", "bug", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    report = Path(payload["result"]["report_path"]).read_text(encoding="utf-8")
    assert "- Odoo version: unknown" in report
    assert "- PostgreSQL version: unknown" in report


def test_real_managed_project_cli_dry_run_uses_spawn_trap_without_mutation(
    project_manifest: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class TrapExecutor:
        def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("dry-run executed a process")

        def execute_with_deadline(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("dry-run executed a deadline process")

        def spawn(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("dry-run spawned a process")

    monkeypatch.chdir(project_manifest)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.server.resolve_psql_executable",
        lambda: Path("/usr/bin/psql"),
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.proc.SubprocessExecutor", TrapExecutor)
    root = bug_reports_root(ensure_exists=False)
    before = set(root.iterdir()) if root.is_dir() else set()

    result = CliRunner().invoke(
        cli,
        [
            "bug-report",
            "init",
            "--title",
            "managed preview",
            "--kind",
            "bug",
            "--dry-run",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    step_ids = [step["step_id"] for step in payload["result"]["steps"]]
    assert "bug-report.version.odoo" in step_ids
    assert any(step_id.startswith("postgres.status.server-summary.") for step_id in step_ids)
    after = set(root.iterdir()) if root.is_dir() else set()
    assert after == before
