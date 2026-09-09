from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands.context import ResolvedContext
from odoo_instance_sdk.commands.db import _run_rich_restore
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.database_preparation import DatabasePreparationFailureContext
from odoo_instance_sdk.internal.database_replacement import (
    CopyReplacementFailureContext,
    CopyReplacementResult,
)
from odoo_instance_sdk.internal.proc import StepEvent, StepObserver
from odoo_instance_sdk.models import (
    AdminPasswordResetResult,
    DatabasePreparationAction,
    DatabasePreparationResult,
    DevelopmentEnvironment,
    EnvironmentDatabaseMode,
    EnvironmentState,
)
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


def _resolved_context(client: object, source: object, instance: object) -> ResolvedContext:
    return ResolvedContext(client=client, source=source, instance=instance, provenance="explicit")  # type: ignore[arg-type]


def _command(value: object = None, *, error: BaseException | None = None) -> Command[object]:
    def run(_context: object) -> object:
        if error is not None:
            raise error
        return value

    return Command.create(ExecutionPlan(), run)


def test_db_help_registers_both_commands_without_password_option() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["db", "--help"])
    refresh_help = runner.invoke(cli, ["db", "refresh", "--help"])
    reset_help = runner.invoke(cli, ["db", "reset-admin-password", "--help"])

    assert result.exit_code == 0
    assert "refresh" in result.output
    assert "reset-admin-password" in result.output
    assert refresh_help.exit_code == 0
    assert reset_help.exit_code == 0
    assert "--password" not in refresh_help.output
    assert "--show-command-output" in refresh_help.output
    assert "--password" not in reset_help.output
    assert "[y/n]" not in refresh_help.output.lower()
    assert "[y/n]" not in reset_help.output.lower()


def test_restore_is_registered_with_exact_uuid_and_target_options() -> None:
    result = CliRunner().invoke(cli, ["db", "restore", "--help"])

    assert result.exit_code == 0
    assert "--target" in result.output
    assert "--reset-admin-password" in result.output
    assert "--dry-run" in result.output
    assert "--yes" in result.output
    assert "--replace" in result.output


def test_restore_replace_rejects_target_before_environment_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_environment",
        lambda *_args, **_kwargs: pytest.fail("conflicting target must fail before context access"),
    )

    result = CliRunner().invoke(
        cli,
        [
            "db",
            "restore",
            "00000000-0000-0000-0000-000000000007",
            "--replace",
            "--target",
            "recorded_copy",
            "--yes",
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert "cannot be combined" in result.stderr


def test_restore_machine_confirmation_precedes_project_or_catalogue_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_project_path",
        lambda _ctx: pytest.fail("confirmation must precede project resolution"),
    )

    result = CliRunner().invoke(
        cli,
        ["db", "restore", "00000000-0000-0000-0000-000000000007", "--json"],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "confirmation_required"
    client.environments.refresh_database_command.assert_not_called()


def test_restore_dry_run_uses_registered_source_and_emits_one_document(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backup_id = "00000000-0000-0000-0000-000000000007"
    client = MagicMock()
    client.environments.refresh_database_command.return_value = _command(
        DatabasePreparationResult(
            mode=DatabasePreparationAction.RESTORE,
            restored_database="demo_copy",
        )
    )
    monkeypatch.setattr("odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)

    result = CliRunner().invoke(
        cli,
        ["db", "restore", backup_id, "--target", "demo_copy", "--dry-run", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["dry_run"] is True
    call = client.environments.refresh_database_command.call_args
    assert str(call.kwargs["restore_source"].backup_id) == backup_id
    assert call.kwargs["target_database"] == "demo_copy"


def test_restore_interrupt_emits_sanitized_context_and_exit_130(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backup_id = uuid.UUID("00000000-0000-0000-0000-000000000007")
    interrupted = KeyboardInterrupt()
    interrupted.failure_context = DatabasePreparationFailureContext(  # type: ignore[attr-defined]
        backup_id=backup_id,
        retained_backup_id=backup_id,
        retained_database="demo_copy",
        database_confirmed=True,
        default_switch_confirmed=False,
    )
    client = MagicMock()
    client.environments.refresh_database_command.return_value = _command(error=interrupted)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)

    result = CliRunner().invoke(
        cli,
        ["db", "restore", str(backup_id), "--yes", "--json"],
    )

    assert result.exit_code == 130
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "db_restore_interrupted"
    assert payload["context"]["backup_id"] == str(backup_id)
    assert payload["context"]["retained_database"] == "demo_copy"
    assert payload["context"]["database_confirmed"] is True
    assert payload["context"]["default_switch_confirmed"] is False


@pytest.mark.parametrize(
    ("root_args", "format_args", "dry_run"),
    [
        (
            root_args,
            format_args,
            dry_run,
        )
        for root_args in ([], ["--env", "repo:PROJ-1"])
        for format_args in (["--json"], ["--format", "toon"], [])
        for dry_run in (True, False)
    ],
)
def test_restore_replace_click_path_has_one_machine_envelope_for_both_context_spellings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    root_args: list[str],
    format_args: list[str],
    dry_run: bool,
) -> None:
    backup_id = uuid.UUID("00000000-0000-0000-0000-000000000007")
    client = MagicMock()
    environment = MagicMock()
    command = _command(
        CopyReplacementResult(
            backup_id=backup_id,
            environment_id=uuid.UUID("00000000-0000-0000-0000-000000000008"),
            database="copy_target",
            filestore="/owned/filestore/copy_target",
        )
    )
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_environment",
        lambda *_args, **_kwargs: environment,
    )
    builder = MagicMock(return_value=command)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.database_replacement.build_copy_replacement_command",
        builder,
    )
    result = CliRunner().invoke(
        cli,
        [
            *root_args,
            "db",
            "restore",
            str(backup_id),
            "--replace",
            "--yes",
            *(["--dry-run"] if dry_run else []),
            *format_args,
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert builder.call_args.args[:3] == (client, environment, backup_id)
    if format_args == ["--json"]:
        payload = json.loads(result.stdout)
        assert payload["dry_run"] is dry_run
        assert payload["ok"] is True
        assert result.stdout.count('"ok"') == 1
    elif format_args == ["--format", "toon"]:
        from toon import DecodeOptions, decode

        payload = decode(result.stdout, DecodeOptions(indent=2, strict=True))
        assert payload["dry_run"] is dry_run
        assert payload["ok"] is True
        assert result.stdout.count("ok:") == 1
    else:
        assert (
            ("Plan: db.restore" in result.stdout)
            if dry_run
            else ("Database restore" in result.stdout)
        )
        if not dry_run:
            assert "copy_target" in result.stdout
    assert str(tmp_path) not in result.stdout


def test_refresh_reset_option_is_click_usage_error_before_sdk_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)

    result = CliRunner().invoke(cli, ["db", "refresh", "--reset-admin-password"])

    assert result.exit_code == 2
    assert "requires --restore" in result.stderr
    client.environments.refresh_database.assert_not_called()


@pytest.mark.parametrize("format_args", [["--json"], ["--format", "json"], ["--format", "toon"]])
def test_refresh_show_command_output_is_rich_only_before_sdk_work(
    monkeypatch: pytest.MonkeyPatch, format_args: list[str]
) -> None:
    client = MagicMock()
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_project_path",
        lambda _ctx: pytest.fail("machine stream validation must precede project resolution"),
    )

    result = CliRunner().invoke(
        cli, ["db", "refresh", "--restore", "--show-command-output", *format_args]
    )

    assert result.exit_code == 2
    assert "only available with Rich output" in result.stderr
    client.environments.refresh_database_command.assert_not_called()


def test_refresh_uses_project_context_options_and_typed_machine_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = MagicMock()
    client.environments.refresh_database_command.return_value = _command(
        DatabasePreparationResult(
            mode=DatabasePreparationAction.RESTORE,
            restored_database="demo_copy",
            retained_artifacts=("backup.zip",),
        )
    )
    monkeypatch.setattr("odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)

    result = CliRunner().invoke(
        cli,
        [
            "db",
            "refresh",
            "--restore",
            "--reset-admin-password",
            "--source-branch",
            "release/19",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["ok"] is True
    assert document["result"]["restored_database"] == "demo_copy"
    assert document["result"]["retained_artifacts"] == ["backup.zip"]
    client.environments.refresh_database_command.assert_called_once()
    options = client.environments.refresh_database_command.call_args.kwargs["options"]
    assert options.restore is True
    assert options.reset_admin_password is True
    assert options.source_branch == "release/19"


def test_rich_restore_wires_step_observer_without_changing_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class ObservedCommand:
        plan = ExecutionPlan()

        def __init__(self) -> None:
            self.observer: StepObserver | None = None
            self.observe_output = False

        def run(
            self,
            *,
            observer: StepObserver | None = None,
            observe_output: bool = False,
        ) -> DatabasePreparationResult:
            self.observer = observer
            self.observe_output = observe_output
            assert observer is not None
            observer(StepEvent(step_id="restore.copy", kind="started"))
            observer(StepEvent(step_id="restore.copy", kind="completed", returncode=0))
            return DatabasePreparationResult(
                mode=DatabasePreparationAction.RESTORE,
                restored_database="demo_copy",
                retained_artifacts=(),
            )

    client = MagicMock()
    command = ObservedCommand()
    client.environments.refresh_database_command.return_value = command
    monkeypatch.setattr("odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)

    result = CliRunner().invoke(cli, ["db", "refresh", "--restore"])

    assert result.exit_code == 0, result.output
    assert command.observer is not None
    assert command.observe_output is False
    assert "[restore.copy] started" in result.output
    assert "[restore.copy] completed (exit 0)" in result.output
    assert "demo_copy" in result.output


def test_rich_restore_uses_live_only_for_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeConsole:
        is_terminal = True

    instances: list[object] = []

    class FakeLive:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.updates: list[str] = []
            instances.append(self)

        def __enter__(self) -> FakeLive:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def update(self, value: object, *, refresh: bool = False) -> None:
            assert refresh is True
            self.updates.append(str(value))

    monkeypatch.setattr("odoo_instance_sdk.commands.db.Console", FakeConsole)
    monkeypatch.setattr("rich.live.Live", FakeLive)

    def run(observer: StepObserver) -> tuple[int, DatabasePreparationResult | None]:
        observer(StepEvent(step_id="restore.plan", kind="started"))
        observer(StepEvent(step_id="restore.plan", kind="completed", returncode=0))
        return 0, None

    status, result = _run_rich_restore(run, show_command_output=False)

    assert status == 0
    assert result is None
    live = instances[0]
    assert isinstance(live, FakeLive)
    assert live.kwargs["transient"] is True
    assert live.updates[-1].splitlines() == [
        "[restore.plan] started",
        "[restore.plan] completed (exit 0)",
    ]


def test_reset_delegates_only_for_exact_recorded_local_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = MagicMock()
    instance.config.configured_database_names = ("demo_copy",)
    instance.databases.reset_admin_password_command.return_value = _command(
        AdminPasswordResetResult(database="demo_copy", completed=True, xml_id="base.user_admin")
    )
    environment = SimpleNamespace(id="env-1", source_db_name=None, target_db_name="demo_copy")
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.ready_instance",
        lambda _ctx: _resolved_context(MagicMock(), environment, instance),
    )

    result = CliRunner().invoke(cli, ["db", "reset-admin-password", "--json"])

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["context"] == {"environment_id": "env-1"}
    assert document["result"]["database"] == "demo_copy"
    assert "password" not in document["result"]
    instance.databases.reset_admin_password_command.assert_called_once_with()

    instance.config.configured_database_names = ("other",)
    rejected = CliRunner().invoke(cli, ["db", "reset-admin-password", "--json"])
    assert rejected.exit_code == 1
    assert "demo_copy" not in rejected.stdout
    assert "other" not in rejected.stdout


@pytest.mark.parametrize("output_format", ["json", "toon", None])
def test_refresh_failure_renders_typed_retained_context_without_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    output_format: str | None,
) -> None:
    client = MagicMock()
    failure = RuntimeError("reset failed: master_pwd=remote-password-sentinel")
    failure.failure_context = DatabasePreparationFailureContext(  # type: ignore[attr-defined]
        retained_backup_id=uuid.UUID("00000000-0000-0000-0000-000000000042"),
        retained_database="demo_refresh_42",
    )
    client.environments.refresh_database_command.return_value = _command(error=failure)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)

    args = ["db", "refresh"]
    if output_format is not None:
        args.extend(["--format", output_format])
    result = CliRunner().invoke(cli, args)

    assert result.exit_code == 1
    assert "remote-password-sentinel" not in (result.stdout + result.stderr)
    assert "00000000-0000-0000-0000-000000000042" in (result.stdout + result.stderr)
    assert "demo_refresh_42" in (result.stdout + result.stderr)
    if output_format == "json":
        document = json.loads(result.stdout)
        assert document["context"] == {
            "retained_backup_id": "00000000-0000-0000-0000-000000000042",
            "retained_database": "demo_refresh_42",
        }
    elif output_format == "toon":
        from toon import DecodeOptions, decode

        document = decode(result.stdout, DecodeOptions(indent=2, strict=True))
        assert document["context"]["retained_backup_id"] == ("00000000-0000-0000-0000-000000000042")
        assert document["context"]["retained_database"] == "demo_refresh_42"


def test_restore_replace_rich_confirmation_refusal_and_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    environment = MagicMock()
    ran = MagicMock()

    def callback(_context: object) -> object:
        ran()
        return CopyReplacementResult(
            backup_id=uuid.uuid4(),
            environment_id=uuid.uuid4(),
            database="copy_target",
            filestore="/owned/filestore/copy_target",
        )

    command = Command.create(ExecutionPlan(), callback)
    builder = MagicMock(return_value=command)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_environment",
        lambda *_args, **_kwargs: environment,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.database_replacement.build_copy_replacement_command",
        builder,
    )

    refused = CliRunner().invoke(
        cli, ["db", "restore", str(uuid.uuid4()), "--replace"], input="n\n"
    )
    assert refused.exit_code == 1
    assert "operation failed" in refused.output
    ran.assert_not_called()

    accepted = CliRunner().invoke(
        cli, ["db", "restore", str(uuid.uuid4()), "--replace"], input="y\n"
    )
    assert accepted.exit_code == 0, accepted.output
    ran.assert_called_once()


def test_restore_replace_machine_output_requires_yes_before_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = MagicMock()
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", MagicMock)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.database_replacement.build_copy_replacement_command",
        builder,
    )

    result = CliRunner().invoke(cli, ["db", "restore", str(uuid.uuid4()), "--replace", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "confirmation_required"
    builder.assert_not_called()


@pytest.mark.parametrize("mode_args", [["--json"], ["--format", "toon"]])
def test_restore_replace_execution_failure_is_one_machine_envelope(
    monkeypatch: pytest.MonkeyPatch, mode_args: list[str]
) -> None:
    client = MagicMock()
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_environment",
        lambda *_args, **_kwargs: MagicMock(),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.database_replacement.build_copy_replacement_command",
        MagicMock(return_value=_command(error=RuntimeError("restore failed"))),
    )

    result = CliRunner().invoke(
        cli, ["db", "restore", str(uuid.uuid4()), "--replace", "--yes", *mode_args]
    )

    assert result.exit_code == 1
    if mode_args == ["--json"]:
        document = json.loads(result.stdout)
    else:
        from toon import DecodeOptions, decode

        document = decode(result.stdout, DecodeOptions(indent=2, strict=True))
    assert document["ok"] is False
    assert document["command"] == "db.restore"
    assert result.stdout.count("db.restore") == 1


def test_restore_replace_reset_failure_preserves_replacement_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = RuntimeError("reset failed: master_pwd=reset-secret")
    failure.failure_context = CopyReplacementFailureContext(  # type: ignore[attr-defined]
        backup_id=uuid.UUID("00000000-0000-0000-0000-000000000007"),
        previous_backup_id=uuid.UUID("00000000-0000-0000-0000-000000000006"),
        target_database="copy_target",
        rollback_database="copy_target_odcli_rb_7",
        stage="restore",
        cleanup_failed=False,
        published=False,
    )
    client = MagicMock()
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_environment", lambda *_args, **_kwargs: MagicMock()
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.database_replacement.build_copy_replacement_command",
        MagicMock(return_value=_command(error=failure)),
    )

    result = CliRunner().invoke(
        cli,
        [
            "db",
            "restore",
            "00000000-0000-0000-0000-000000000007",
            "--replace",
            "--reset-admin-password",
            "--yes",
            "--json",
        ],
    )

    assert result.exit_code == 1
    document = json.loads(result.stdout)
    assert document["ok"] is False
    assert document["context"]["stage"] == "restore"
    assert "reset-secret" not in result.stdout


def _cli_replace_environment(
    *,
    mode: EnvironmentDatabaseMode = EnvironmentDatabaseMode.COPY,
    state: EnvironmentState = EnvironmentState.READY,
    removed: bool = False,
) -> DevelopmentEnvironment:
    return DevelopmentEnvironment(
        id=uuid.UUID("00000000-0000-0000-0000-000000000099"),
        name="repo:PROJ-1",
        repository_root="/repo",
        git_common_dir="/repo/.git",
        branch="PROJ-1",
        base_ref="main",
        worktree_path="/repo/worktree",
        generated_config_path="/repo/odoo.conf",
        python_environment_path="/repo/venv",
        python_environment_owned=False,
        dependency_lock_path="/repo/requirements.lock",
        http_interface="127.0.0.1",
        http_port=18069,
        db_mode=mode,
        source_db_name="source",
        target_db_name="copy_target" if mode is EnvironmentDatabaseMode.COPY else None,
        state=state,
        created_at=datetime.now(UTC),
        removed_at=datetime.now(UTC) if removed else None,
    )


@pytest.mark.parametrize(
    ("label", "environment"),
    [
        ("shared", _cli_replace_environment(mode=EnvironmentDatabaseMode.SHARED)),
        ("removed", _cli_replace_environment(removed=True)),
        ("live-runtime", _cli_replace_environment()),
    ],
)
def test_restore_replace_rejects_unsafe_contexts_before_builder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    label: str,
    environment: DevelopmentEnvironment,
) -> None:
    builder = MagicMock()
    client = MagicMock()
    if label == "live-runtime":
        catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
        monkeypatch.setattr(BackupCatalog, "get_environment_runtime", lambda _self, _id: object())
        client.get_catalog.return_value = catalog
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_environment",
        lambda *_args, **_kwargs: environment,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.database_replacement.build_copy_replacement_command",
        builder,
    )

    result = CliRunner().invoke(
        cli,
        [
            "--env",
            "repo:PROJ-1",
            "db",
            "restore",
            str(uuid.uuid4()),
            "--replace",
            "--yes",
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert label in result.stdout or label in result.stderr or "replacement" in result.stdout
    builder.assert_not_called()
    if label == "live-runtime":
        catalog.close()


def test_restore_replace_project_selector_rejects_before_builder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    builder = MagicMock()
    client = MagicMock()
    client.environments.list.return_value = []
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.database_replacement.build_copy_replacement_command",
        builder,
    )

    result = CliRunner().invoke(
        cli,
        [
            "--project",
            str(tmp_path),
            "db",
            "restore",
            str(uuid.uuid4()),
            "--replace",
            "--yes",
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert "No environment resolved" in result.stdout
    builder.assert_not_called()


def test_restore_replace_ambiguous_selector_rejects_before_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = MagicMock()
    environment = _cli_replace_environment()
    client = MagicMock()
    client.environments.list.return_value = [environment, environment]
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.database_replacement.build_copy_replacement_command",
        builder,
    )

    result = CliRunner().invoke(
        cli,
        [
            "--env",
            environment.name,
            "db",
            "restore",
            str(uuid.uuid4()),
            "--replace",
            "--yes",
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert "Ambiguous environment selector" in result.stdout
    builder.assert_not_called()


@pytest.mark.parametrize("mode_args", [["--json"], ["--format", "toon"], []])
def test_restore_replace_reset_option_reaches_builder_for_each_output_mode(
    monkeypatch: pytest.MonkeyPatch, mode_args: list[str]
) -> None:
    client = MagicMock()
    environment = MagicMock()
    backup_id = uuid.uuid4()
    command = _command(
        CopyReplacementResult(
            backup_id=backup_id,
            environment_id=uuid.uuid4(),
            database="copy_target",
            filestore="/owned/filestore/copy_target",
        )
    )
    builder = MagicMock(return_value=command)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_environment",
        lambda *_args, **_kwargs: environment,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.database_replacement.build_copy_replacement_command",
        builder,
    )

    result = CliRunner().invoke(
        cli,
        [
            "db",
            "restore",
            str(backup_id),
            "--replace",
            "--yes",
            "--reset-admin-password",
            *mode_args,
        ],
    )

    assert result.exit_code == 0, result.output
    assert builder.call_args.kwargs["reset_admin_password"] is True
