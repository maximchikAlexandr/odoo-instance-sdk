from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands.output import (
    OutputMode,
    build_envelope,
    emit_json_envelope,
    output_options,
    resolve_output_mode,
    rich_print,
)
from odoo_instance_sdk.internal.automation import (
    DepsVerifyResult,
    ModuleRecord,
    ModuleUpdatePlan,
    ShellOutcome,
    TranslationExportResult,
)
from odoo_instance_sdk.internal.automation import (
    TestRunResult as ModuleTestRunResult,
)
from odoo_instance_sdk.internal.doctor import CheckResult, DoctorReport
from odoo_instance_sdk.models import Snapshot
from odoo_instance_sdk.resources.environment import EnvironmentDatabaseMode
from odoo_instance_sdk.resources.postgres import PostgresCluster

BOUNDED_LEAVES = (
    ("init",),
    ("doctor",),
    ("env", "checkout"),
    ("env", "list"),
    ("env", "remove"),
    ("env", "sync"),
    ("eval",),
    ("exec",),
    ("module", "list"),
    ("module", "update"),
    ("module", "test"),
    ("translations", "export"),
    ("deps", "verify"),
    ("vscode", "generate"),
    ("postgres", "approve-image"),
    ("postgres", "status"),
    ("postgres", "up"),
    ("postgres", "stop"),
)


@dataclass(frozen=True)
class PublicLeafCase:
    path: tuple[str, ...]
    args: tuple[str, ...]


PUBLIC_LEAF_CASES = tuple(
    PublicLeafCase(path, args)
    for path, args in zip(
        BOUNDED_LEAVES,
        (
            ("init", "--no-input", "--odoo-bin", "/opt/odoo/odoo-bin", "--dry-run", "--project"),
            ("doctor",),
            ("env", "checkout", "main", "--dry-run"),
            ("env", "list", "--all-projects"),
            ("env", "remove", "env-1", "--yes"),
            ("env", "sync", "env-1"),
            ("eval", "1"),
            ("exec", "-"),
            ("module", "list", "sale"),
            ("module", "update", "sale", "--yes"),
            ("module", "test", "sale", "--test-tags", "/sale"),
            ("translations", "export", "--module", "sale", "--language", "fr_FR"),
            ("deps", "verify"),
            ("vscode", "generate"),
            (
                "postgres",
                "approve-image",
                "--image-digest",
                "docker.io/library/postgres@sha256:" + "a" * 64,
            ),
            ("postgres", "status"),
            ("postgres", "up"),
            ("postgres", "stop"),
        ),
    )
)


def _matrix_environment() -> SimpleNamespace:
    return SimpleNamespace(
        id="env-1",
        name="demo",
        state="ready",
        branch="main",
        db_mode=EnvironmentDatabaseMode.SHARED,
        http_interface="127.0.0.1",
        http_port=8069,
        worktree_path="/worktree",
        python_environment_path="/venv",
        generated_config_path="/worktree/odoo.conf",
        backup_id=None,
    )


def _patch_leaf_external(  # noqa: C901
    monkeypatch: pytest.MonkeyPatch,
    case: PublicLeafCase,
    *,
    failing: bool,
    tmp_path: Path,
) -> None:
    """Give one public leaf an isolated operation seam for the parity matrix."""

    def fail_operation(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("isolated external operation failed")

    path = case.path
    if path == ("init",):
        return

    if path == ("doctor",):
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.cli_context.resolve_project_path", lambda _ctx: tmp_path
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.run_doctor",
            fail_operation if failing else lambda *_args, **_kwargs: DoctorReport(),
        )
        return

    if path[:2] == ("env", "checkout"):
        client = MagicMock()
        client.environments._plan_checkout.side_effect = fail_operation if failing else None
        client.environments._plan_checkout.return_value = SimpleNamespace(
            db_mode=EnvironmentDatabaseMode.SHARED
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.env.resolve_project_path", lambda _ctx: tmp_path
        )
        monkeypatch.setattr("odoo_instance_sdk.commands.env.OdooClient", lambda **_kwargs: client)
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.env._checkout_plan_dict",
            lambda _plan: {"id": "env-1", "name": "demo", "state": "creating"},
        )
        return

    if path[:2] == ("env", "list"):
        snapshot = Snapshot(
            schema_version=2,
            generated_at=datetime(2020, 1, 1, tzinfo=UTC),
            projects=(),
            environments=(),
        )

        def snapshot_operation(*_args: object, **_kwargs: object) -> Snapshot:
            if failing:
                raise RuntimeError("isolated external operation failed")
            return snapshot

        monkeypatch.setattr(
            "odoo_instance_sdk.commands.env.EnvironmentMonitor.snapshot", snapshot_operation
        )
        return

    if path[:2] in {("env", "remove"), ("env", "sync")}:
        client = MagicMock()
        env = _matrix_environment()
        client.environments.get.return_value = env
        client.environments.sync_python.return_value = env
        if failing:
            if path[1] == "remove":
                client.environments.get.side_effect = fail_operation
            else:
                client.environments.sync_python.side_effect = fail_operation
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.env.resolve_project_path", lambda _ctx: tmp_path
        )
        monkeypatch.setattr("odoo_instance_sdk.commands.env.OdooClient", lambda **_kwargs: client)
        return

    if (
        path in {("eval",), ("exec",)}
        or path[:1] == ("module",)
        or path[:1]
        in {
            ("translations",),
            ("deps",),
            ("vscode",),
        }
    ):
        instance = MagicMock()
        env = _matrix_environment()
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.cli_context.ready_instance",
            lambda _ctx: (MagicMock(), env, instance),
        )

    if path == ("eval",):
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.eval_expression",
            fail_operation
            if failing
            else lambda *_args, **_kwargs: ShellOutcome(0, "", "", {"result": 42}),
        )
        return

    if path == ("exec",):
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.exec_script",
            fail_operation
            if failing
            else lambda *_args, **_kwargs: ShellOutcome(0, "", "", {"result": "ok"}),
        )
        return

    if path == ("module", "list"):
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.list_modules",
            fail_operation
            if failing
            else lambda *_args, **_kwargs: [ModuleRecord("sale", "installed")],
        )
        return

    if path == ("module", "update"):
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.plan_module_update",
            fail_operation
            if failing
            else lambda *_args, **_kwargs: ModuleUpdatePlan(modules=["sale"]),
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.update_modules",
            lambda *_args, **_kwargs: ShellOutcome(0, "", "", {"result": {"updated": ["sale"]}}),
        )
        return

    if path == ("module", "test"):
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.run_module_tests",
            fail_operation
            if failing
            else lambda *_args, **_kwargs: (
                ModuleTestRunResult(1, 1, 0, 0, 0, False, False),
                0,
            ),
        )
        return

    if path == ("translations", "export"):
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.export_translations",
            fail_operation
            if failing
            else lambda *_args, **_kwargs: [
                TranslationExportResult("sale", "fr_FR", "fr.po", tmp_path / "fr.po", 2)
            ],
        )
        return

    if path == ("deps", "verify"):
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.verify_deps",
            fail_operation if failing else lambda **_kwargs: DepsVerifyResult(),
        )
        return

    if path == ("vscode", "generate"):
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.build_launch_profile",
            fail_operation if failing else lambda *_args, **_kwargs: {"name": "demo"},
        )
        return

    if path[:1] == ("postgres",):

        class FakeCluster:
            mode = "external"
            owned = False
            endpoint = "127.0.0.1:5432"
            endpoint_host = "127.0.0.1"
            endpoint_port = 5432

            def approve_image(self, *_args: object, **_kwargs: object) -> None:
                if failing:
                    raise RuntimeError("isolated external operation failed")

            def status(self) -> object:
                if failing:
                    raise RuntimeError("isolated external operation failed")
                from odoo_instance_sdk.models import PostgresClusterState

                return PostgresClusterState.HEALTHY

            def ensure_running(self, *, timeout: float) -> None:
                _ = timeout
                if failing:
                    raise RuntimeError("isolated external operation failed")

            def stop(self, *, timeout: float) -> None:
                _ = timeout
                if failing:
                    raise RuntimeError("isolated external operation failed")

            def to_diagnostic_dict(self) -> dict[str, object]:
                return {
                    "mode": self.mode,
                    "owned": self.owned,
                    "endpoint": self.endpoint,
                    "image": "postgres:16",
                }

        cluster = FakeCluster()
        monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(lambda _path: cluster))
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.postgres_cli.resolve_project_path", lambda _ctx: tmp_path
        )
        return

    raise AssertionError(f"missing matrix setup for {path}")


def _decode_document(document: str, mode: str) -> object:
    if mode == "json":
        return json.loads(document)
    from toon import DecodeOptions, decode

    return decode(document, DecodeOptions(indent=2, strict=True))


@pytest.mark.parametrize("case", PUBLIC_LEAF_CASES, ids=lambda case: ".".join(case.path))
def test_public_cli_leaf_matrix_has_json_toon_parity(
    case: PublicLeafCase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exercise every bounded leaf through Click with only its operation mocked."""

    runner = CliRunner()
    success_documents: list[tuple[object, int, str]] = []
    failure_documents: list[tuple[object, int, str]] = []
    for mode in ("json", "toon"):
        with monkeypatch.context() as isolated:
            args = list(case.args)
            if case.path == ("init",):
                args.append(str(tmp_path))
            _patch_leaf_external(isolated, case, failing=False, tmp_path=tmp_path)
            success = runner.invoke(
                cli,
                [*args, "--format", mode],
                input="pass\n" if case.path == ("exec",) else None,
            )
            assert success.exit_code == 0, success.output
            assert success.stderr == ""
            assert success.stdout.strip()
            assert "\x1b" not in success.stdout
            assert "password" not in success.stdout.lower()
            assert "Would you like" not in success.stdout
            assert "Progress" not in success.stdout
            success_documents.append(
                (_decode_document(success.stdout, mode), success.exit_code, success.stderr)
            )

        with monkeypatch.context() as isolated:
            args = list(case.args)
            if case.path == ("init",):
                args = ["init", "--no-input"]
            _patch_leaf_external(isolated, case, failing=True, tmp_path=tmp_path)
            failure = runner.invoke(
                cli,
                [*args, "--format", mode],
                input="pass\n" if case.path == ("exec",) else None,
            )
            assert failure.exit_code == 1, failure.output
            assert failure.stderr == ""
            assert failure.stdout.strip()
            assert "\x1b" not in failure.stdout
            assert "password" not in failure.stdout.lower()
            assert "Would you like" not in failure.stdout
            assert "Progress" not in failure.stdout
            failure_documents.append(
                (_decode_document(failure.stdout, mode), failure.exit_code, failure.stderr)
            )

    assert success_documents[0] == success_documents[1]
    assert failure_documents[0] == failure_documents[1]
    assert success_documents[0][0]["ok"] is True  # type: ignore[index]
    assert failure_documents[0][0]["ok"] is False  # type: ignore[index]


def test_public_cli_leaf_matrix_rejects_env_list_watch_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.env.EnvironmentMonitor.snapshot",
        lambda *_args, **_kwargs: pytest.fail("watch rejection must precede collection"),
    )
    result = CliRunner().invoke(cli, ["env", "list", "--watch", "--json"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "--watch is only available with Rich output" in result.stderr


def _command(path: tuple[str, ...]) -> click.Command:
    command: click.Command = cli
    for name in path:
        assert isinstance(command, click.Group)
        command = command.commands[name]
    return command


def _option_names(command: click.Command) -> set[str]:
    return {option for param in command.params for option in param.opts}


def test_format_options_are_local_to_exactly_the_bounded_leaves() -> None:
    for path in BOUNDED_LEAVES:
        command = _command(path)
        options = _option_names(command)
        assert "--format" in options, path
        assert "--json" in options, path

    for path in (("run",), ("shell",), ("logs",), ("monitor",)):
        options = _option_names(_command(path))
        assert "--format" not in options, path
        assert "--json" not in options, path

    root_result = CliRunner().invoke(cli, ["--format", "json", "env", "list"])
    assert root_result.exit_code == 2
    assert root_result.stdout == ""
    assert "No such option" in root_result.stderr


def test_format_resolution_accepts_json_alias_and_rejects_conflicts_before_operation() -> None:
    assert resolve_output_mode(None, False) is OutputMode.RICH
    assert resolve_output_mode(None, True) is OutputMode.JSON
    assert resolve_output_mode("json", True) is OutputMode.JSON
    with pytest.raises(click.UsageError, match="conflicts"):
        resolve_output_mode("toon", True)


def test_invalid_format_uses_native_click_parse_failure() -> None:
    result = CliRunner().invoke(cli, ["env", "list", "--format", "invalid"])
    assert result.exit_code == 2
    assert "Invalid value for '--format'" in result.stderr


def test_json_and_toon_emit_the_same_sanitized_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    result = {"message": "secret=***", "items": [], "enabled": True, "value": None}
    emit_json_envelope(ok=True, command="test", result=result, mode=OutputMode.JSON)
    json_document = capsys.readouterr().out
    emit_json_envelope(ok=True, command="test", result=result, mode=OutputMode.TOON)
    toon_document = capsys.readouterr().out

    from toon import DecodeOptions, decode

    json_value = json.loads(json_document)
    toon_value = decode(toon_document, DecodeOptions(indent=2, strict=True))
    assert toon_value == json_value
    assert "\033[" not in json_document + toon_document
    assert (
        build_envelope(ok=False, command="test", error_message="token=hidden")["error"]["message"]
        == "<redacted>"
    )


@pytest.mark.parametrize("source", ["direct", "imported", "catalog"])
def test_public_success_result_sources_are_sanitized_before_json_and_toon(
    source: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = "\x00\x1f\n\x1b[2J\x7f\x80\x9b31m"

    def invoke(mode: str) -> object:
        with monkeypatch.context() as isolated:
            if source == "direct":
                args = [
                    "init",
                    "--no-input",
                    "--dry-run",
                    "--odoo-bin",
                    "/opt/odoo/odoo-bin",
                    "--python",
                    f"python-{payload}",
                    "--project",
                    str(tmp_path),
                ]
            elif source == "imported":
                launch = tmp_path / "launch.json"
                launch.write_text(
                    json.dumps(
                        {
                            "configurations": [
                                {
                                    "name": "Odoo malicious",
                                    "type": "debugpy",
                                    "request": "launch",
                                    "program": "${workspaceFolder}/odoo-bin",
                                    "python": f"python-{payload}",
                                    "args": [f"--dev={payload}"],
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                args = [
                    "init",
                    "--no-input",
                    "--dry-run",
                    "--from-vscode",
                    str(launch),
                    "--launch-name",
                    "Odoo malicious",
                    "--project",
                    str(tmp_path),
                ]
            else:
                snapshot = {
                    "catalog_value": payload,
                    "nested": [{"display_name": payload}],
                }
                isolated.setattr(
                    "odoo_instance_sdk.commands.env.EnvironmentMonitor.snapshot",
                    lambda *_args, **_kwargs: snapshot,
                )
                args = ["env", "list", "--all-projects"]
            result = CliRunner().invoke(cli, [*args, "--format", mode])
            assert result.exit_code == 0, result.output
            assert result.stderr == ""
            document = result.stdout
            assert document.strip()
            assert not any(
                (ord(char) < 0x20 and char not in "\n") or 0x7F <= ord(char) <= 0x9F
                for char in document
            )
            return _decode_document(document, mode)

    json_value = invoke("json")
    toon_value = invoke("toon")
    assert toon_value == json_value
    assert json_value["ok"] is True  # type: ignore[index]
    result_value = json_value["result"]  # type: ignore[index]
    assert payload not in json.dumps(result_value)
    assert any(
        escaped in json.dumps(result_value) for escaped in (r"\x00", r"\x1b", r"\x9b", r"\x7f")
    )


@pytest.mark.parametrize("mode", ["json", "toon"])
def test_doctor_machine_mode_outside_project_emits_one_failure_document(
    mode: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["doctor", "--format", mode])

    assert result.exit_code == 1
    assert result.stderr == ""
    assert result.stdout.count("\n") >= 1
    document = _decode_document(result.stdout, mode)
    assert document["ok"] is False  # type: ignore[index]
    assert document["command"] == "doctor"  # type: ignore[index]
    assert document["error"]["code"] == "doctor_failed"  # type: ignore[index]
    assert "Project" not in result.stdout


def test_output_options_is_a_click_option_composition_helper() -> None:
    @output_options
    @click.command()
    def command(output_format: str | None, json_output: bool) -> None:
        click.echo(resolve_output_mode(output_format, json_output).value)

    runner = CliRunner()
    assert runner.invoke(command, ["--json", "--format", "json"]).output == "json\n"
    conflict = runner.invoke(command, ["--json", "--format", "toon"])
    assert conflict.exit_code == 2
    assert "conflicts" in conflict.output


def test_env_list_toon_is_one_machine_document(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = Snapshot(
        schema_version=2,
        generated_at=datetime.now(UTC),
        projects=(),
        environments=(),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.env.EnvironmentMonitor.snapshot",
        lambda self, project_id=None, *, include_removed=False: snapshot,
    )
    result = CliRunner().invoke(cli, ["env", "list", "--all-projects", "--format", "toon"])
    assert result.exit_code == 0, result.output
    from toon import DecodeOptions, decode

    decoded = decode(result.stdout, DecodeOptions(indent=2, strict=True))
    assert decoded["schema_version"] == 1
    assert decoded["result"] == decoded["data"]


@pytest.mark.parametrize("args", [["--json"], ["--format", "json"]])
def test_env_list_json_aliases_have_identical_v1_envelopes(
    args: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = Snapshot(
        schema_version=2,
        generated_at=datetime.now(UTC),
        projects=(),
        environments=(),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.env.EnvironmentMonitor.snapshot",
        lambda self, project_id=None, *, include_removed=False: snapshot,
    )
    result = CliRunner().invoke(cli, ["env", "list", "--all-projects", *args])
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["schema_version"] == 1
    assert document["result"] == document["data"]
    if args == ["--json"]:
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.env.EnvironmentMonitor.snapshot",
            lambda self, project_id=None, *, include_removed=False: snapshot,
        )
        alias_result = CliRunner().invoke(
            cli, ["env", "list", "--all-projects", "--format", "json"]
        )
        assert json.loads(alias_result.stdout) == document


def test_conflicting_machine_alias_is_rejected_before_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def snapshot(*_args: object, **_kwargs: object) -> Snapshot:
        nonlocal called
        called = True
        raise AssertionError("conflicting mode must fail before operation")

    monkeypatch.setattr("odoo_instance_sdk.commands.env.EnvironmentMonitor.snapshot", snapshot)
    result = CliRunner().invoke(cli, ["env", "list", "--json", "--format", "toon"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "conflicts" in result.stderr
    assert not called


@pytest.mark.parametrize("args", [["--format", "json"], ["--format", "toon"], ["--json"]])
def test_machine_env_remove_requires_yes_without_prompt_or_operation(
    args: list[str], tmp_path: object
) -> None:
    env = SimpleNamespace(
        id="env-1",
        name="demo",
        state="ready",
        branch="main",
        db_mode="shared",
        http_port=8069,
        worktree_path="/worktree",
    )
    client = MagicMock()
    client.environments.get.return_value = env
    with (
        patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client),
        patch("odoo_instance_sdk.commands.env.resolve_project_path", return_value=tmp_path),
        patch("odoo_instance_sdk.commands.env.click.confirm") as confirm,
    ):
        result = CliRunner().invoke(cli, ["env", "remove", "env-1", *args])

    assert result.exit_code == 1, result.output
    assert result.stderr == ""
    assert result.output.count("schema_version") == 1
    assert "confirmation_required" in result.output
    assert "requires --yes" in result.output
    confirm.assert_not_called()
    client.environments.remove.assert_not_called()


@pytest.mark.parametrize("args", [["--format", "json"], ["--format", "toon"], ["--json"]])
def test_machine_env_remove_with_yes_calls_remove_once(args: list[str], tmp_path: object) -> None:
    env = SimpleNamespace(
        id="env-1",
        name="demo",
        state="removed",
        branch="main",
        db_mode="shared",
        http_port=8069,
        worktree_path="/worktree",
    )
    client = MagicMock()
    client.environments.get.return_value = env
    with (
        patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client),
        patch("odoo_instance_sdk.commands.env.resolve_project_path", return_value=tmp_path),
        patch("odoo_instance_sdk.commands.env.click.confirm") as confirm,
    ):
        result = CliRunner().invoke(cli, ["env", "remove", "env-1", "--yes", *args])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert result.output.count("schema_version") == 1
    client.environments.remove.assert_called_once_with(env)
    confirm.assert_not_called()


def test_rich_env_remove_retains_confirmation_prompt(tmp_path: object) -> None:
    env = SimpleNamespace(
        id="env-1",
        name="demo",
        state="ready",
        branch="main",
        db_mode="shared",
        http_port=8069,
        worktree_path="/worktree",
    )
    client = MagicMock()
    client.environments.get.return_value = env
    with (
        patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client),
        patch("odoo_instance_sdk.commands.env.resolve_project_path", return_value=tmp_path),
    ):
        result = CliRunner().invoke(cli, ["env", "remove", "env-1"], input="n\n")

    assert result.exit_code == 0, result.output
    assert "Aborted." in result.output
    client.environments.remove.assert_not_called()


def test_rich_print_sanitizes_by_default_and_preserves_document_line_feeds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rich_print("first\n\x1b[31msecond")
    safe_output = capsys.readouterr().out
    assert "\x1b" not in safe_output
    assert r"first\x0a\x1b[31msecond" in safe_output

    rich_print("first\nsecond", preserve_newlines=True)
    assert capsys.readouterr().out == "first\nsecond\n"


@pytest.mark.parametrize("command", ["checkout", "remove", "sync", "doctor"])
def test_public_human_callbacks_neutralize_terminal_controls(
    command: str,
    tmp_path: Path,
) -> None:
    c0 = "\x00"
    esc_csi = "\x1b[2J"
    c1_csi = "\x9b31m"
    delete = "\x7f"
    payload = f"{c0}{esc_csi}{c1_csi}{delete}"
    env = SimpleNamespace(
        id="env-1",
        name=f"evil-{payload}",
        state="ready",
        branch="main",
        db_mode=EnvironmentDatabaseMode.SHARED,
        http_port=8069,
        worktree_path="/worktree",
    )
    client = MagicMock()
    runner = CliRunner()

    if command == "doctor":
        report = DoctorReport(
            checks=[
                CheckResult(
                    name=f"check-{payload}",
                    status="ok",
                    detail=f"detail-{payload}",
                    environment_id=f"id-{payload}",
                    environment_name=f"name-{payload}",
                )
            ]
        )
        with (
            patch("odoo_instance_sdk.cli.cli_context.resolve_project_path", return_value=tmp_path),
            patch("odoo_instance_sdk.cli.OdooClient", return_value=client),
            patch("odoo_instance_sdk.cli.run_doctor", return_value=report),
        ):
            result = runner.invoke(cli, ["doctor"])
    else:
        client.environments.get.return_value = env
        client.environments.checkout.return_value = env
        client.environments.sync_python.return_value = env
        with (
            patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client),
            patch("odoo_instance_sdk.commands.env.resolve_project_path", return_value=tmp_path),
        ):
            args = {
                "checkout": ["env", "checkout", "main"],
                "remove": ["env", "remove", "env-1", "--yes"],
                "sync": ["env", "sync", "env-1"],
            }[command]
            result = runner.invoke(cli, args)

    assert result.exit_code == 0, result.output
    assert "\x00" not in result.output
    assert "\x1b" not in result.output
    assert "\x7f" not in result.output
    assert "\x9b" not in result.output
    assert r"\x00" in result.output
    assert r"\x1b[2J" in result.output
    assert r"\x9b31m" in result.output
    assert r"\x7f" in result.output
