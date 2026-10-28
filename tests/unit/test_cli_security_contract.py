from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import ANY, MagicMock, call, patch

import pytest
from click.testing import CliRunner

from odoo_instance_sdk import OdooClient
from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands.cli_parts import run as run_callbacks
from odoo_instance_sdk.commands.context import ResolvedContext, RuntimeSource
from odoo_instance_sdk.exceptions import EnvironmentConflictError, InstanceConfigurationError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.address import AddressState
from odoo_instance_sdk.internal.project_manifest import write_manifest
from odoo_instance_sdk.internal.project_runtime import ProjectHttpPortSelection
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig
from odoo_instance_sdk.resources.environment import EnvironmentState
from odoo_instance_sdk.resources.instance import OdooInstance
from tests.unit.monitor_support import FakeProcessProvider


def _stub_command(callback: Callable[[], int]) -> Command[int]:
    return Command.create(ExecutionPlan(), lambda _context: callback(), ())


def _resolved_context(client: object, source: object, instance: object) -> ResolvedContext:
    return ResolvedContext(
        client=cast("OdooClient", client),
        source=cast("RuntimeSource", source),
        instance=cast("OdooInstance", instance),
        provenance="explicit",
    )


@pytest.fixture(autouse=True)
def _inject_monitor_process_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from odoo_instance_sdk.resources.monitor import EnvironmentMonitor

    original_init = EnvironmentMonitor.__init__

    def init(self: EnvironmentMonitor, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("process_provider", FakeProcessProvider())
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(EnvironmentMonitor, "__init__", init)


@pytest.mark.parametrize(
    "diagnostic, secret",
    [
        ("password='quoted secret' passwd=bare-secret", "quoted secret"),
        ('api_key="api-secret" token=token-secret', "api-secret"),
        ("postgresql://user:dsn-secret@db.example/app", "dsn-secret"),
        ("https://alice:auth-secret@example.test/callback", "auth-secret"),
        ("/private/runtime/secret-path\\n" + "x" * 3000, "secret-path"),
    ],
)
def test_eval_json_failure_is_single_sanitized_envelope(diagnostic: str, secret: str) -> None:
    runner = CliRunner()
    with patch(
        "odoo_instance_sdk.internal.context.resolve_environment",
        side_effect=RuntimeError(diagnostic),
    ):
        result = runner.invoke(cli, ["eval", "1", "--format", "json"])

    assert result.exit_code == 1, result.output
    assert result.stderr == ""
    assert result.output.count('"schema_version"') == 1
    envelope = json.loads(result.output)
    assert envelope["ok"] is False
    assert envelope["command"] == "eval"
    assert envelope["context"] == {}
    assert envelope["dry_run"] is False
    assert envelope["error"]["code"] == "eval_failed"
    assert secret not in result.output
    assert len(envelope["error"]["message"]) <= 2000


def test_init_json_failure_is_single_stdout_envelope(tmp_path: pytest.TempPathFactory) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli, ["init", "--no-input", "--format", "json", "--project", str(tmp_path)]
    )

    assert result.exit_code == 1
    assert result.stderr == ""
    assert result.output.count('"schema_version"') == 1
    envelope = json.loads(result.output)
    assert envelope == {
        "schema_version": 1,
        "ok": False,
        "command": "init",
        "context": {},
        "provenance": {},
        "dry_run": False,
        "warnings": [],
        "error": {"code": "init_failed", "message": "Missing required option --odoo-bin"},
    }


def test_init_json_success_has_stable_result_and_provenance(
    tmp_path: pytest.TempPathFactory,
) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "init",
            "--no-input",
            "--allow-partial",
            "--dry-run",
            "--format",
            "json",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--project",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert envelope["command"] == "init"
    assert envelope["dry_run"] is True
    assert envelope["provenance"]["option"] == ["odoo_bin"]
    assert envelope["result"] == envelope["data"]


@pytest.mark.parametrize(
    "command, method",
    [("run", "run_foreground"), ("shell", "shell"), ("logs", "iter_logs")],
)
def test_run_and_shell_sanitize_runtime_exception(command: str, method: str) -> None:
    runner = CliRunner()
    instance = MagicMock()
    getattr(instance, method).side_effect = RuntimeError("password=top-secret /private/path")
    client = MagicMock()
    client.instance.from_environment.return_value = instance
    env = SimpleNamespace(
        state=EnvironmentState.READY,
        http_interface="127.0.0.1",
        http_port=8069,
    )
    with (
        patch("odoo_instance_sdk.commands.context.OdooClient", return_value=client),
        patch("odoo_instance_sdk.internal.context.resolve_environment", return_value=env),
        patch("odoo_instance_sdk.internal.context._verify_env_runtime", return_value=None),
        patch("odoo_instance_sdk.internal.context._check_port_free", return_value=True),
    ):
        result = runner.invoke(cli, [command])

    assert result.exit_code == 1
    assert "top-secret" not in result.output
    assert "/private/path" not in result.output


def test_env_list_is_read_only_through_public_resources() -> None:
    runner = CliRunner()
    client = MagicMock()
    client.backups.list.return_value = []
    # New contract: env list delegates to EnvironmentMonitor.snapshot(); patch
    # it to avoid the real catalog and assert the CLI body itself does not
    # touch client.get_catalog() or write environment events.
    from datetime import UTC, datetime

    from odoo_instance_sdk.models import Snapshot

    empty_snapshot = Snapshot(
        schema_version=3, generated_at=datetime.now(UTC), projects=(), environments=()
    )
    with (
        patch("odoo_instance_sdk.client.OdooClient", return_value=client),
        patch(
            "odoo_instance_sdk.commands.env.checkout.EnvironmentMonitor.snapshot",
            return_value=empty_snapshot,
        ),
    ):
        result = runner.invoke(cli, ["env", "list", "--all-projects", "--format", "json"])

    assert result.exit_code == 0, result.output
    # The CLI body no longer calls client.environments.list / client.backups.list
    # for the JSON path (the monitor owns discovery). It must not open the
    # catalog through the client or write environment events.
    client.environments.list.assert_not_called()
    client.get_catalog.assert_not_called()
    client.environments.record_use.assert_not_called()


def test_run_records_use_once_before_foreground_start() -> None:
    client = MagicMock()
    instance = MagicMock()
    instance.run_foreground_command.return_value = _stub_command(lambda: 0)
    env = SimpleNamespace(http_interface="127.0.0.1", http_port=8069)
    calls = MagicMock()
    calls.attach_mock(client.environments.record_use, "record_use")
    calls.attach_mock(instance.run_foreground_command, "run_foreground_command")
    with (
        patch(
            "odoo_instance_sdk.cli.cli_context.ready_instance",
            return_value=_resolved_context(client, env, instance),
        ),
        patch(
            "odoo_instance_sdk.commands.context.ResolvedContext.check_port_free", return_value=True
        ),
    ):
        result = CliRunner().invoke(cli, ["run", "--", "--dev=reload"])

    assert result.exit_code == 0, result.output
    assert calls.mock_calls == [
        call.run_foreground_command(args=("--dev=reload",), env=ANY),
        call.record_use(env),
    ]


def test_project_run_never_records_environment_use(tmp_path: Path) -> None:
    client = MagicMock()
    instance = MagicMock()
    instance.run_foreground_command.return_value = _stub_command(lambda: 0)
    project = ProjectConfig(repository_root=tmp_path)
    with (
        patch(
            "odoo_instance_sdk.cli.cli_context.ready_instance",
            return_value=_resolved_context(client, project, instance),
        ),
        patch(
            "odoo_instance_sdk.commands.context.ResolvedContext.check_port_free", return_value=True
        ),
    ):
        result = CliRunner().invoke(cli, ["run", "--", "--dev=reload"])

    assert result.exit_code == 0, result.output
    instance.run_foreground_command.assert_called_once_with(args=("--dev=reload",), env=ANY)
    client.environments.record_use.assert_not_called()


def test_project_run_preview_uses_selected_endpoint_without_writes(tmp_path: Path) -> None:
    project = ProjectConfig(repository_root=tmp_path)
    write_manifest(tmp_path, project)
    client = MagicMock()
    original = MagicMock()
    original.run_foreground_command.return_value = _stub_command(lambda: 0)
    selected = MagicMock()
    selected.run_foreground_command.return_value = _stub_command(lambda: 0)
    selection = ProjectHttpPortSelection(
        project=project,
        port=8070,
        selection_kind="automatic_fallback",
        fallback_reason="occupied",
    )
    client.instance.from_project.return_value = selected
    with (
        patch(
            "odoo_instance_sdk.cli.cli_context.ready_instance",
            return_value=_resolved_context(client, project, original),
        ),
        patch(
            "odoo_instance_sdk.commands.cli_parts.run.select_project_http_port",
            return_value=selection,
        ) as select,
        patch(
            "odoo_instance_sdk.commands.cli_parts.run.apply_project_http_port_selection"
        ) as persist,
        patch(
            "odoo_instance_sdk.commands.context.ResolvedContext.check_port_free", return_value=True
        ),
    ):
        result = CliRunner().invoke(
            cli, ["run", "--dry-run", "--format", "json", "--", "--dev=reload"]
        )

    assert result.exit_code == 0, result.output
    select.assert_called_once()
    persist.assert_not_called()
    client.instance.from_project.assert_called_once_with(project)
    selected.run_foreground_command.assert_called_once_with(args=("--dev=reload",), env=ANY)


def test_project_run_executes_captured_endpoint_and_renders_fallback_notice(tmp_path: Path) -> None:
    project = ProjectConfig(repository_root=tmp_path)
    write_manifest(tmp_path, project)
    client = MagicMock()
    original = MagicMock()
    original.run_foreground_command.return_value = _stub_command(lambda: 0)
    selected = MagicMock()
    selected.run_foreground_command.return_value = _stub_command(lambda: 0)
    selection = ProjectHttpPortSelection(
        project=project,
        port=8070,
        selection_kind="automatic_fallback",
        fallback_reason="occupied",
        source_project=project,
    )
    client.instance.from_project.return_value = selected
    select_endpoint = MagicMock(return_value=selection)
    with (
        patch(
            "odoo_instance_sdk.cli.cli_context.ready_instance",
            return_value=_resolved_context(client, project, original),
        ),
        patch(
            "odoo_instance_sdk.commands.cli_parts.run.prepare_project_http_port_selection",
            side_effect=select_endpoint,
            create=True,
        ),
        patch(
            "odoo_instance_sdk.commands.cli_parts.run.select_project_http_port",
            side_effect=select_endpoint,
        ),
        patch(
            "odoo_instance_sdk.commands.cli_parts.run.apply_project_http_port_selection",
            return_value=selection,
        ) as persist,
        patch(
            "odoo_instance_sdk.commands.context.ResolvedContext.check_port_free", return_value=True
        ),
    ):
        result = CliRunner().invoke(cli, ["run", "--", "--dev=reload"])

    assert result.exit_code == 0, result.output
    select_endpoint.assert_called_once()
    persist.assert_called_once_with(selection, None)
    client.instance.from_project.assert_called_once_with(project)
    selected.run_foreground_command.assert_called_once_with(args=("--dev=reload",), env=ANY)
    assert "HTTP port 8069" in result.output
    assert "8070" in result.output
    assert "automatic fallback" in result.output


def _registered_main_projects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> tuple[list[Path], list[MagicMock], list[ResolvedContext], bytes]:
    from odoo_instance_sdk.internal.repo_key import repo_key
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    roots = [tmp_path / "first", tmp_path / "second"]
    clients: list[MagicMock] = []
    contexts: list[ResolvedContext] = []
    for root in roots:
        root.mkdir()
        (root / ".git").mkdir()
        (root / "odoo.conf").write_text("[options]\nhttp_port = 8069\n", encoding="utf-8")
        project = ProjectConfig(repository_root=root, source_config=Path("odoo.conf"))
        write_manifest(root, project)
        client = MagicMock()
        selected_instance = MagicMock()
        selected_instance.run_foreground_command.return_value = _stub_command(lambda: 0)
        client.instance.from_project.return_value = selected_instance
        clients.append(client)
        original_instance = MagicMock()
        original_instance.run_foreground_command.return_value = _stub_command(lambda: 0)
        contexts.append(_resolved_context(client, project, original_instance))

    catalog_path = tmp_path / "catalog.sqlite3"
    catalog = BackupCatalog(db_path=catalog_path)
    for root in roots:
        project_id = f"project_{repo_key(root.resolve(), (root / '.git').resolve())}"
        catalog._register_project(project_id, root, root / ".git")
    request.addfinalizer(catalog.close)
    for client in clients:
        client.get_catalog.return_value = catalog
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.port_allocation._paths.get_catalog_path",
        lambda **_kwargs: catalog_path,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_locks_dir",
        lambda **_kwargs: tmp_path / "locks",
    )

    def probe(_host: str, port: int) -> AddressState:
        return AddressState.OCCUPIED if port <= 8069 else AddressState.FREE

    monkeypatch.setattr("odoo_instance_sdk.internal.project_runtime.probe_address", probe)
    monkeypatch.setattr("odoo_instance_sdk.internal.port_allocation.probe_address", probe)
    original_manifest = (roots[0] / ".odcli" / "project.toml").read_bytes()
    return roots, clients, contexts, original_manifest


def test_public_run_preview_uses_captured_registered_project_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    roots, clients, contexts, original_manifest = _registered_main_projects(
        tmp_path, monkeypatch, request
    )
    original_config = (roots[0] / "odoo.conf").read_bytes()
    with (
        patch(
            "odoo_instance_sdk.commands.cli_parts.callbacks._ready_instance",
            side_effect=[contexts[0], contexts[0]],
        ),
        patch(
            "odoo_instance_sdk.commands.context.ResolvedContext.check_port_free",
            return_value=True,
        ),
    ):
        preview = CliRunner().invoke(cli, ["run", "--dry-run", "--format", "json"])
        assert preview.exit_code == 0, preview.output
        preview_project = clients[0].instance.from_project.call_args.args[0]
        assert preview_project.assigned_http_port == 8070
        preview_plan = json.loads(preview.stdout)["result"]
        endpoint_action = preview_plan["steps"][0]
        assert (roots[0] / ".odcli" / "project.toml").read_bytes() == original_manifest
        assert (roots[0] / "odoo.conf").read_bytes() == original_config
        first = CliRunner().invoke(cli, ["run"])
    assert first.exit_code == 0, first.output
    live_first = clients[0].instance.from_project.call_args.args[0]
    assert live_first.assigned_http_port == preview_project.assigned_http_port
    assert ProjectConfig.load(roots[0]).assigned_http_port == 8070
    assert endpoint_action == {
        "kind": "action",
        "step_id": "project.http_port.revalidate",
        "action": "project_http_port.revalidate_and_repair_owned_config",
        "description": "Revalidate the captured endpoint and repair owned config if needed",
        "details": {
            "port": 8070,
            "selection": "automatic_fallback",
            "live_assignment": "persisted_before_capture",
            "owned_config_repair": "if_drifted",
        },
        "read_only": False,
        "mutating": True,
    }


def test_public_run_registered_projects_choose_distinct_endpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    roots, clients, contexts, _original_manifest = _registered_main_projects(
        tmp_path, monkeypatch, request
    )
    with (
        patch(
            "odoo_instance_sdk.commands.cli_parts.callbacks._ready_instance",
            side_effect=contexts,
        ),
        patch(
            "odoo_instance_sdk.commands.context.ResolvedContext.check_port_free",
            return_value=True,
        ),
    ):
        first = CliRunner().invoke(cli, ["run"])
        second = CliRunner().invoke(cli, ["run"])
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    live_first = clients[0].instance.from_project.call_args.args[0]
    live_second = clients[1].instance.from_project.call_args.args[0]
    assert live_first.assigned_http_port == 8070
    assert live_second.assigned_http_port == 8071
    assert ProjectConfig.load(roots[0]).assigned_http_port == 8070
    assert ProjectConfig.load(roots[1]).assigned_http_port == 8071


def test_public_run_persists_project_endpoint_before_command_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    from msgspec.structs import replace

    roots, clients, contexts, _original_manifest = _registered_main_projects(
        tmp_path, monkeypatch, request
    )
    root = roots[0]
    project = ProjectConfig.load(root)
    project = replace(
        project,
        postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=5468),
    )
    write_manifest(root, project)
    generated = root / ".odcli" / "odoo.conf"
    generated.write_text(
        "[options]\nhttp_interface = 127.0.0.1\nhttp_port = 8069\n"
        "db_name = source\ndb_host = 127.0.0.1\ndb_port = 5468\n"
        "db_user = odoo\ndb_password = test-password\nadmin_passwd = test-admin\n",
        encoding="utf-8",
    )
    password_file = root / ".odcli" / "postgres-password"
    password_file.write_text("test-password\n", encoding="utf-8")
    cluster = SimpleNamespace(
        endpoint_host="127.0.0.1", endpoint_port=5468, password_file=password_file
    )
    selected_instance = MagicMock()

    def execute_after_capture() -> int:
        assert "http_port = 8070" in generated.read_text(encoding="utf-8")
        return 0

    def capture_command(*, args: tuple[str, ...], env: dict[str, str] | None) -> Command[int]:
        generated.write_text(
            generated.read_text(encoding="utf-8").replace("http_port = 8070", "http_port = 8069"),
            encoding="utf-8",
        )
        return _stub_command(execute_after_capture)

    selected_instance.run_foreground_command.side_effect = capture_command

    def capture_instance(captured_project: ProjectConfig) -> MagicMock:
        persisted = ProjectConfig.load(root)
        assert persisted.assigned_http_port == 8070
        assert captured_project.assigned_http_port == persisted.assigned_http_port
        assert "http_port = 8070" in generated.read_text(encoding="utf-8")
        return selected_instance

    clients[0].instance.from_project.side_effect = capture_instance
    with (
        patch(
            "odoo_instance_sdk.commands.cli_parts.callbacks._ready_instance",
            return_value=contexts[0],
        ),
        patch(
            "odoo_instance_sdk.commands.context.ResolvedContext.check_port_free",
            return_value=True,
        ),
        patch(
            "odoo_instance_sdk.internal.project_init.validate_generated_config_target",
            return_value=None,
        ),
        patch(
            "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project",
            return_value=cluster,
        ),
    ):
        result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code == 0, result.output
    clients[0].instance.from_project.assert_called_once()
    selected_instance.run_foreground_command.assert_called_once_with(args=(), env=ANY)


def test_project_run_preserves_explicit_port_conflict_code(tmp_path: Path) -> None:
    project = ProjectConfig(repository_root=tmp_path, preferred_http_port=8069)
    write_manifest(tmp_path, project)
    client = MagicMock()
    instance = MagicMock()
    conflict = EnvironmentConflictError(
        "port_in_use",
        "Explicit project HTTP endpoint 127.0.0.1:8069 is occupied; "
        "free the port or update/remove preferred_http_port and retry.",
        details={"host": "127.0.0.1", "port": 8069, "kind": "http"},
    )
    select_endpoint = MagicMock(side_effect=conflict)
    with (
        patch(
            "odoo_instance_sdk.cli.cli_context.ready_instance",
            return_value=_resolved_context(client, project, instance),
        ),
        patch(
            "odoo_instance_sdk.commands.cli_parts.run.prepare_project_http_port_selection",
            side_effect=select_endpoint,
            create=True,
        ),
        patch(
            "odoo_instance_sdk.commands.cli_parts.run.select_project_http_port",
            side_effect=select_endpoint,
        ),
    ):
        result = CliRunner().invoke(
            cli, ["run", "--detach", "--format", "json", "--", "--dev=reload"]
        )

    assert result.exit_code == 1, result.output
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "port_in_use"
    assert "preferred_http_port" in error["message"]
    select_endpoint.assert_called_once()
    client.instance.from_project.assert_not_called()


def test_project_run_renders_explicit_port_conflict_in_rich_output(tmp_path: Path) -> None:
    project = ProjectConfig(repository_root=tmp_path, preferred_http_port=8069)
    write_manifest(tmp_path, project)
    client = MagicMock()
    instance = MagicMock()
    conflict = EnvironmentConflictError(
        "port_in_use",
        "Explicit project HTTP endpoint 127.0.0.1:8069 is occupied; "
        "free the port or update/remove preferred_http_port and retry.",
        details={"host": "127.0.0.1", "port": 8069, "kind": "http"},
    )
    select_endpoint = MagicMock(side_effect=conflict)
    with (
        patch(
            "odoo_instance_sdk.cli.cli_context.ready_instance",
            return_value=_resolved_context(client, project, instance),
        ),
        patch(
            "odoo_instance_sdk.commands.cli_parts.run.prepare_project_http_port_selection",
            side_effect=select_endpoint,
            create=True,
        ),
        patch(
            "odoo_instance_sdk.commands.cli_parts.run.select_project_http_port",
            side_effect=select_endpoint,
        ),
    ):
        result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code == 1, result.output
    assert "Explicit project HTTP endpoint 127.0.0.1:8069 is occupied" in result.output
    assert "preferred_http_port" in result.output
    select_endpoint.assert_called_once()
    client.instance.from_project.assert_not_called()


def test_environment_owned_command_rejects_project_context(tmp_path: Path) -> None:
    client = MagicMock()
    instance = MagicMock()
    project = ProjectConfig(repository_root=tmp_path)
    with patch(
        "odoo_instance_sdk.cli.cli_context.ready_instance",
        return_value=_resolved_context(client, project, instance),
    ):
        result = CliRunner().invoke(cli, ["module", "update", "sale", "--yes"])

    assert result.exit_code == 1
    assert "requires a development environment" in result.output
    instance.run_shell_script_command.assert_not_called()
    client.environments.record_use.assert_not_called()


def test_run_captures_then_records_use_then_executes_the_same_command() -> None:
    events: list[str] = []
    client = MagicMock()
    instance = MagicMock()
    env = SimpleNamespace(http_interface="127.0.0.1", http_port=8069)

    def execute() -> int:
        events.append("execute")
        return 0

    command = _stub_command(execute)

    def capture(*, args: tuple[str, ...], env: dict[str, str] | None) -> Command[int]:
        assert args == ("--dev=reload", "--dev=xml")
        assert env is not None
        assert env["ODCLI_REAL_PG_DUMP"] == str(Path("/usr/bin/pg_dump").resolve())
        shim_dir = Path(run_callbacks.__file__).resolve().parents[2] / "internal" / "pg_dump_compat"
        assert env["PATH"].split(os.pathsep)[0] == str(shim_dir)
        events.append("capture")
        return command

    instance.run_foreground_command.side_effect = capture

    def record_use(_env: object) -> None:
        events.append("record")

    client.environments.record_use.side_effect = record_use
    with (
        patch(
            "odoo_instance_sdk.cli.cli_context.ready_instance",
            return_value=_resolved_context(client, env, instance),
        ),
        patch(
            "odoo_instance_sdk.commands.context.ResolvedContext.check_port_free", return_value=True
        ),
        patch(
            "odoo_instance_sdk.commands.cli_parts.run.shutil.which",
            return_value="/usr/bin/pg_dump",
        ),
    ):
        result = CliRunner().invoke(
            cli,
            ["run", "--", "--dev=reload", "--dev=xml"],
        )

    assert result.exit_code == 0, result.output
    assert events == ["capture", "record", "execute"]
    client.environments.record_use.assert_called_once_with(env)


def test_run_without_pg_dump_keeps_foreground_environment_unmodified(tmp_path: Path) -> None:
    client = MagicMock()
    instance = MagicMock()
    instance.run_foreground_command.return_value = _stub_command(lambda: 0)
    project = ProjectConfig(repository_root=tmp_path)
    with (
        patch(
            "odoo_instance_sdk.cli.cli_context.ready_instance",
            return_value=_resolved_context(client, project, instance),
        ),
        patch(
            "odoo_instance_sdk.commands.context.ResolvedContext.check_port_free", return_value=True
        ),
        patch("odoo_instance_sdk.commands.cli_parts.run.shutil.which", return_value=None),
    ):
        result = CliRunner().invoke(cli, ["run", "--", "--dev=reload"])

    assert result.exit_code == 0, result.output
    instance.run_foreground_command.assert_called_once_with(args=("--dev=reload",), env=None)


def test_ready_instance_resolves_environment_before_creating_instance() -> None:
    events: list[str] = []
    client = MagicMock()
    instance = MagicMock()
    instance.run_foreground_command.return_value = _stub_command(lambda: 0)
    env = SimpleNamespace(
        id="env-id",
        name="test",
        state=EnvironmentState.READY,
        repository_root="/repo",
        http_interface="127.0.0.1",
        http_port=8069,
    )

    def create_instance(_value: object) -> MagicMock:
        events.append("instance")
        return instance

    client.instance.from_environment.side_effect = create_instance

    def resolve_environment(*_args: object, **_kwargs: object) -> object:
        events.append("environment")
        return env

    with (
        patch("odoo_instance_sdk.commands.context._client_class", return_value=lambda **_: client),
        patch(
            "odoo_instance_sdk.commands.context._client_config_class",
            return_value=lambda **_: SimpleNamespace(executable="odoo"),
        ),
        patch(
            "odoo_instance_sdk.internal.context.resolve_environment",
            side_effect=resolve_environment,
        ),
        patch("odoo_instance_sdk.internal.context._verify_env_runtime"),
        patch(
            "odoo_instance_sdk.commands.context.ResolvedContext.check_port_free", return_value=True
        ),
    ):
        result = CliRunner().invoke(cli, ["run", "--", "--stop-after-init"])

    assert result.exit_code == 0, result.output
    assert events == ["environment", "instance"]
    instance.run_foreground_command.assert_called_once_with(args=("--stop-after-init",), env=ANY)


def test_port_conflict_does_not_record_use_or_start_foreground() -> None:
    client = MagicMock()
    instance = MagicMock()
    env = SimpleNamespace(http_interface="127.0.0.1", http_port=8069)
    with (
        patch(
            "odoo_instance_sdk.cli.cli_context.ready_instance",
            return_value=_resolved_context(client, env, instance),
        ),
        patch(
            "odoo_instance_sdk.commands.context.ResolvedContext.check_port_free", return_value=False
        ),
    ):
        result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code == 1
    client.environments.record_use.assert_not_called()
    instance.run_foreground_command.assert_not_called()


def test_port_conflict_after_instance_creation_skips_capture_use_and_launch() -> None:
    events: list[str] = []
    client = MagicMock()
    instance = MagicMock()
    env = SimpleNamespace(http_interface="127.0.0.1", http_port=8069)

    def ready(_ctx: object) -> ResolvedContext:
        events.append("ready")
        return _resolved_context(client, env, instance)

    def port_free() -> bool:
        events.append("port")
        return False

    with (
        patch("odoo_instance_sdk.cli.cli_context.ready_instance", side_effect=ready),
        patch(
            "odoo_instance_sdk.commands.context.ResolvedContext.check_port_free",
            side_effect=port_free,
        ),
    ):
        result = CliRunner().invoke(cli, ["run", "--", "--dev=reload"])

    assert result.exit_code == 1
    assert events == ["ready", "port"]
    instance.run_foreground_command.assert_not_called()
    client.environments.record_use.assert_not_called()


def test_protected_native_argument_is_rejected_at_sdk_boundary_without_machine_document() -> None:
    client = MagicMock()
    instance = MagicMock()
    instance.run_foreground_command.side_effect = InstanceConfigurationError(
        "protected runtime option '--database'"
    )
    env = SimpleNamespace(http_interface="127.0.0.1", http_port=8069)

    with (
        patch(
            "odoo_instance_sdk.cli.cli_context.ready_instance",
            return_value=_resolved_context(client, env, instance),
        ),
        patch(
            "odoo_instance_sdk.commands.context.ResolvedContext.check_port_free", return_value=True
        ),
    ):
        result = CliRunner().invoke(
            cli,
            ["run", "--dry-run", "--format", "json", "--", "--database", "other"],
        )

    assert result.exit_code == 1
    assert result.stderr == ""
    assert result.stdout.count('"schema_version"') == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "--database" in payload["error"]["message"]
    client.environments.record_use.assert_not_called()
    instance.run_foreground_command.assert_called_once_with(args=("--database", "other"), env=ANY)


def test_shell_does_not_record_use() -> None:
    client = MagicMock()
    instance = MagicMock()
    instance.shell_command.return_value = _stub_command(lambda: 0)
    with patch(
        "odoo_instance_sdk.cli.cli_context.ready_instance",
        return_value=_resolved_context(client, SimpleNamespace(), instance),
    ):
        result = CliRunner().invoke(cli, ["shell"])

    assert result.exit_code == 0, result.output
    client.environments.record_use.assert_not_called()
