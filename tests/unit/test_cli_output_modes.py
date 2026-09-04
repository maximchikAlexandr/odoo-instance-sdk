from __future__ import annotations

import inspect
import json
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, TypeVar, cast
from unittest.mock import MagicMock, patch

import click
import msgspec
import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands.context import ResolvedContext
from odoo_instance_sdk.commands.output import (
    JsonValue,
    OutputDocument,
    OutputError,
    OutputMode,
    build_envelope,
    emit,
    emit_json_envelope,
    failure_document,
    model_to_dict,
    output_options,
    resolve_output_mode,
    rich_print,
    run_or_preview,
    success_document,
)
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.automation import (
    DepsVerifyResult,
)
from odoo_instance_sdk.internal.doctor import CheckResult, DoctorReport
from odoo_instance_sdk.models import (
    AdminPasswordResetResult,
    BackupFreshness,
    BackupProvenanceComparison,
    BackupProvenanceStatus,
    ClusterEndpoint,
    ClusterSnapshot,
    CommandResult,
    DatabasePreparationAction,
    DatabasePreparationResult,
    DevelopmentEnvironment,
    EnvironmentCheckoutPlan,
    EnvironmentPythonMode,
    OdooTestResult,
    PostgresClusterState,
    Snapshot,
)
from odoo_instance_sdk.resources.environment import EnvironmentDatabaseMode, EnvironmentState
from odoo_instance_sdk.resources.postgres import PostgresCluster

T = TypeVar("T")


def _resolved_context(client: object, source: object, instance: object) -> ResolvedContext:
    return ResolvedContext(
        client=cast("Any", client),
        source=cast("DevelopmentEnvironment", source),
        instance=cast("Any", instance),
        provenance="explicit",
    )


def _emit_plan(command: Command[T], *, command_name: str, mode: OutputMode) -> int:
    return emit(
        success_document(
            command=command_name,
            result=model_to_dict(command.plan),
            dry_run=True,
        ),
        mode,
    )


CliLeafClass = Literal[
    "bounded-read-only",
    "process-previewable-read-only",
    "mutating-or-spawning",
    "native-passthrough",
    "rich-live",
    "jsonl-stream",
]


@dataclass(frozen=True)
class PublicLeafCase:
    path: tuple[str, ...]
    args: tuple[str, ...]
    classification: CliLeafClass
    requires_dry_run: bool
    exception_reason: str | None = None
    variants: tuple[CliLeafClass, ...] = ()

    @property
    def is_bounded(self) -> bool:
        return self.classification in {
            "bounded-read-only",
            "process-previewable-read-only",
            "mutating-or-spawning",
        }


# This is the one CLI leaf inventory.  The output parity matrix below filters
# this data by ``is_bounded``; native, Rich-live, and JSONL leaves remain here
# with their explicit policy so a new leaf cannot avoid classification.
_PUBLIC_LEAF_DATA: tuple[PublicLeafCase, ...] = (
    PublicLeafCase(
        ("init",),
        ("init", "--no-input", "--odoo-bin", "/opt/odoo/odoo-bin", "--dry-run", "--project"),
        "mutating-or-spawning",
        True,
    ),
    PublicLeafCase(("doctor",), ("doctor",), "bounded-read-only", False),
    PublicLeafCase(
        ("env", "checkout"), ("env", "checkout", "main", "--dry-run"), "mutating-or-spawning", True
    ),
    PublicLeafCase(
        ("env", "list"),
        ("env", "list", "--all-projects"),
        "bounded-read-only",
        False,
        variants=("rich-live",),
    ),
    PublicLeafCase(
        ("env", "remove"), ("env", "remove", "env-1", "--yes"), "mutating-or-spawning", True
    ),
    PublicLeafCase(("env", "sync"), ("env", "sync", "env-1"), "mutating-or-spawning", True),
    PublicLeafCase(("db", "refresh"), ("db", "refresh"), "mutating-or-spawning", True),
    PublicLeafCase(
        ("db", "reset-admin-password"), ("db", "reset-admin-password"), "mutating-or-spawning", True
    ),
    PublicLeafCase(("eval",), ("eval", "1"), "process-previewable-read-only", True),
    PublicLeafCase(("exec",), ("exec", "-"), "mutating-or-spawning", True),
    PublicLeafCase(
        ("test",), ("test", "--changed", "--dry-run"), "process-previewable-read-only", True
    ),
    PublicLeafCase(
        ("module", "list"), ("module", "list", "sale"), "process-previewable-read-only", True
    ),
    PublicLeafCase(
        ("module", "update"), ("module", "update", "sale", "--yes"), "mutating-or-spawning", True
    ),
    PublicLeafCase(
        ("module", "test"),
        ("module", "test", "sale", "--test-tags", "/sale"),
        "mutating-or-spawning",
        True,
    ),
    PublicLeafCase(
        ("translations", "export"),
        ("translations", "export", "--module", "sale", "--language", "fr_FR"),
        "mutating-or-spawning",
        True,
    ),
    PublicLeafCase(("deps", "verify"), ("deps", "verify"), "process-previewable-read-only", True),
    PublicLeafCase(("vscode", "generate"), ("vscode", "generate"), "mutating-or-spawning", True),
    PublicLeafCase(
        ("postgres", "approve-image"),
        (
            "postgres",
            "approve-image",
            "--image-digest",
            "docker.io/library/postgres@sha256:" + "a" * 64,
        ),
        "mutating-or-spawning",
        True,
    ),
    PublicLeafCase(("postgres", "status"), ("postgres", "status"), "bounded-read-only", False),
    PublicLeafCase(("postgres", "up"), ("postgres", "up"), "mutating-or-spawning", True),
    PublicLeafCase(("postgres", "stop"), ("postgres", "stop"), "mutating-or-spawning", True),
    PublicLeafCase(("db", "locks"), ("db", "locks", "demo"), "bounded-read-only", False),
    PublicLeafCase(("db", "stats"), ("db", "stats", "demo"), "bounded-read-only", False),
    PublicLeafCase(("db", "bloat"), ("db", "bloat", "demo"), "bounded-read-only", False),
    PublicLeafCase(
        ("db", "init-monitoring"),
        ("db", "init-monitoring", "demo", "--yes", "--dry-run"),
        "mutating-or-spawning",
        True,
    ),
    PublicLeafCase(
        ("psql",),
        ("psql", "--dry-run", "-c", "SELECT 1"),
        "native-passthrough",
        True,
        "normal execution owns inherited native psql streams; dry-run uses the shared plan",
    ),
    PublicLeafCase(
        ("run",),
        ("run",),
        "native-passthrough",
        True,
        "normal execution owns inherited Odoo TTY streams; dry-run is still required",
    ),
    PublicLeafCase(
        ("logs",),
        ("logs", "--follow"),
        "jsonl-stream",
        False,
        "read-only logfile subscription has no finite child-process or mutation plan",
    ),
    PublicLeafCase(
        ("shell",),
        ("shell",),
        "native-passthrough",
        True,
        "normal execution owns interactive Odoo streams; dry-run is still required",
    ),
    PublicLeafCase(
        ("monitor",),
        ("monitor",),
        "native-passthrough",
        False,
        "long-running monitor server has no finite bounded output plan",
    ),
)

PUBLIC_LEAF_CASES = tuple(_PUBLIC_LEAF_DATA)


def _matrix_environment() -> SimpleNamespace:
    return SimpleNamespace(
        id="env-1",
        name="demo",
        state="ready",
        branch="main",
        base_ref="main",
        db_mode=EnvironmentDatabaseMode.SHARED,
        http_interface="127.0.0.1",
        http_port=8069,
        worktree_path="/worktree",
        python_environment_path="/venv",
        generated_config_path="/worktree/odoo.conf",
        backup_id=None,
        source_db_name=None,
        target_db_name="demo",
    )


def _cli_leaf_paths(command: click.Command, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    if not isinstance(command, click.Group):
        return {prefix}
    return {
        leaf_path
        for name, child in command.commands.items()
        for leaf_path in _cli_leaf_paths(child, (*prefix, name))
    }


def test_public_leaf_inventory_is_complete_and_classified() -> None:
    paths = [case.path for case in PUBLIC_LEAF_CASES]
    assert len(paths) == len(set(paths))
    assert set(paths) == _cli_leaf_paths(cli)
    valid_classes = {
        "bounded-read-only",
        "process-previewable-read-only",
        "mutating-or-spawning",
        "native-passthrough",
        "rich-live",
        "jsonl-stream",
    }
    assert all(case.classification in valid_classes for case in PUBLIC_LEAF_CASES)
    assert all(variant in valid_classes for case in PUBLIC_LEAF_CASES for variant in case.variants)
    assert all(
        case.exception_reason is not None
        for case in PUBLIC_LEAF_CASES
        if not case.is_bounded and not case.requires_dry_run
    )
    assert all(
        case.requires_dry_run or case.exception_reason is not None
        for case in PUBLIC_LEAF_CASES
        if case.classification in {"mutating-or-spawning", "process-previewable-read-only"}
    )


def test_every_eligible_leaf_uses_the_shared_preview_or_run_helper() -> None:
    """Keep the canonical inventory coupled to the executable composition path."""
    for case in PUBLIC_LEAF_CASES:
        if not case.requires_dry_run:
            continue
        callback = _command(case.path).callback
        assert callback is not None
        callback = inspect.unwrap(callback)
        assert "run_or_preview" in callback.__code__.co_names, case.path


def _matrix_checkout_plan(*, name: str = "demo") -> EnvironmentCheckoutPlan:
    return EnvironmentCheckoutPlan(
        name=name,
        branch="main",
        effective_base_ref="HEAD",
        db_mode=EnvironmentDatabaseMode.SHARED,
        source_database=None,
        target_database=None,
        python_mode=EnvironmentPythonMode.REUSE,
        provenance=BackupProvenanceComparison(
            status=BackupProvenanceStatus.UNKNOWN,
            expected_base_ref="HEAD",
            recorded_branch=None,
        ),
        freshness=BackupFreshness.MISSING,
        preparation_actions=(),
        warnings=(),
    )


def _matrix_public_environment(*, name: str = "demo") -> DevelopmentEnvironment:
    return DevelopmentEnvironment(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        name=name,
        repository_root="/project",
        git_common_dir="/project/.git",
        branch="main",
        base_ref="HEAD",
        worktree_path="/worktree",
        generated_config_path="/worktree/odoo.conf",
        python_environment_path="/venv",
        python_environment_owned=False,
        dependency_lock_path="/project/uv.lock",
        http_interface="127.0.0.1",
        http_port=8069,
        db_mode=EnvironmentDatabaseMode.SHARED,
        state=EnvironmentState.READY,
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )


def _payload_stdout(payload: dict[str, Any], nonce: str = "deadbeefdeadbeef") -> str:
    return f"__ODCLI_PAYLOAD__{nonce}__ {json.dumps(payload)} __END_PAYLOAD__{nonce}__\n"


def _command_result(returncode: int, payload: dict[str, Any]) -> CommandResult:
    return CommandResult(
        args=[],
        returncode=returncode,
        stdout=_payload_stdout(payload),
        stderr="",
        duration=0.0,
    )


def _matrix_command(
    value: T,
    *,
    error: BaseException | None = None,
    private_projection: EnvironmentCheckoutPlan | None = None,
) -> Command[T]:
    def run(_context: object) -> T:
        if error is not None:
            raise error
        return value

    return Command.create(
        ExecutionPlan(),
        run,
        private_projection=private_projection,
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
        plan = _matrix_checkout_plan()
        if failing:
            client.environments.checkout_command.side_effect = fail_operation
        else:
            client.environments.checkout_command.return_value = _matrix_command(
                _matrix_public_environment(), private_projection=plan
            )
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.env.resolve_project_path", lambda _ctx: tmp_path
        )
        monkeypatch.setattr("odoo_instance_sdk.commands.env.OdooClient", lambda **_kwargs: client)
        return

    if path == ("db", "refresh"):
        client = MagicMock()
        client.environments.refresh_database_command.return_value = _matrix_command(
            DatabasePreparationResult(mode=DatabasePreparationAction.DOWNLOAD),
            error=RuntimeError("isolated external operation failed") if failing else None,
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path
        )
        monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_kwargs: client)
        return

    if path == ("db", "reset-admin-password"):
        instance = MagicMock()
        instance.config.configured_database_names = ("demo",)
        instance.databases.reset_admin_password_command.return_value = _matrix_command(
            AdminPasswordResetResult(database="demo", completed=True, xml_id="base.user_admin"),
            error=RuntimeError("isolated external operation failed") if failing else None,
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.db.ready_instance",
            lambda _ctx: _resolved_context(MagicMock(), _matrix_environment(), instance),
        )
        return

    if path[:2] == ("env", "list"):
        snapshot = Snapshot(
            schema_version=3,
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
        client.environments.remove_command.return_value = _matrix_command(
            None,
            error=RuntimeError("isolated external operation failed") if failing else None,
        )
        client.environments.sync_python_command.return_value = _matrix_command(
            env,
            error=RuntimeError("isolated external operation failed") if failing else None,
        )
        if failing and path[1] == "remove":
            client.environments.get.side_effect = fail_operation
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.env.resolve_project_path", lambda _ctx: tmp_path
        )
        monkeypatch.setattr("odoo_instance_sdk.commands.env.OdooClient", lambda **_kwargs: client)
        return

    if (
        path in {("eval",), ("exec",)}
        or path[:1] == ("module",)
        or path[:1] == ("test",)
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
            lambda _ctx: _resolved_context(MagicMock(), env, instance),
        )

    if path == ("eval",):
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.eval_expression_command",
            fail_operation
            if failing
            else lambda *_args, **_kwargs: _matrix_command(_command_result(0, {"result": 42})),
        )
        return

    if path == ("exec",):
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.exec_script_command",
            fail_operation
            if failing
            else lambda *_args, **_kwargs: _matrix_command(
                _command_result(0, {"returncode": 0, "stdout": "", "stderr": ""})
            ),
        )
        return

    if path == ("module", "list"):
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.list_modules_command",
            fail_operation
            if failing
            else lambda *_args, **_kwargs: _matrix_command(
                _command_result(
                    0,
                    {"result": [{"name": "sale", "state": "installed"}]},
                )
            ),
        )
        return

    if path == ("test",):
        selection_plan = SimpleNamespace(
            base_source="explicit",
            requested_base="main",
            resolved_base="base-sha",
            merge_base="merge-sha",
            head="head-sha",
            changed_files=("addons/sale/tests/test_sale.py",),
            modules=("sale",),
            ignored_paths=(),
            unmapped_paths=(),
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.test.resolve_changed_selection",
            fail_operation if failing else lambda *_args, **_kwargs: selection_plan,
        )
        if not failing:
            monkeypatch.setattr(
                "odoo_instance_sdk.commands.test.run_odoo_tests_command",
                lambda *_args, **_kwargs: _matrix_command(
                    (
                        OdooTestResult(
                            counts={
                                "tests": 1,
                                "successful": 1,
                                "failed": 0,
                                "errors": 0,
                                "skipped": 0,
                            },
                            failures=False,
                            zero_tests=False,
                            exit_code=0,
                        ),
                        None,
                    )
                ),
            )
        return

    if path == ("module", "update"):
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.update_modules_command",
            fail_operation
            if failing
            else lambda *_args, **_kwargs: _matrix_command(
                _command_result(0, {"result": {"updated": ["sale"]}})
            ),
        )
        return

    if path == ("module", "test"):
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.resolve_module_test_selection",
            lambda *_args, **_kwargs: (
                SimpleNamespace(
                    modules=("sale",),
                    provenance=SimpleNamespace(
                        kind="module",
                        value="sale",
                        module_path=tmp_path / "sale",
                        file_path=None,
                    ),
                ),
            ),
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.module_tests_command",
            fail_operation
            if failing
            else lambda *_args, **_kwargs: _matrix_command(
                (
                    OdooTestResult(
                        counts={
                            "tests": 1,
                            "successful": 1,
                            "failed": 0,
                            "errors": 0,
                            "skipped": 0,
                        },
                        failures=False,
                        zero_tests=False,
                        exit_code=0,
                    ),
                    None,
                )
            ),
        )
        return

    if path == ("translations", "export"):
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.export_translations_command",
            fail_operation if failing else lambda *_args, **_kwargs: _matrix_command([]),
        )
        return

    if path == ("deps", "verify"):
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.verify_deps_command",
            fail_operation if failing else lambda **_kwargs: _matrix_command(DepsVerifyResult()),
        )
        return

    if path == ("vscode", "generate"):
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.build_launch_profile",
            fail_operation if failing else lambda *_args, **_kwargs: {"name": "demo"},
        )
        return

    if path[:2] in {("db", "locks"), ("db", "stats"), ("db", "bloat"), ("db", "init-monitoring")}:
        resource = MagicMock()
        result = CommandResult(args=[], returncode=0, stdout="", stderr="", duration=0.0)
        command = _matrix_command(
            result,
            error=RuntimeError("isolated external operation failed") if failing else None,
        )
        resource.locks_command.return_value = command
        resource.stats_command.return_value = command
        resource.bloat_command.return_value = command
        resource.init_monitoring_command.return_value = command
        if failing and path == ("db", "init-monitoring"):
            resource.init_monitoring_command.side_effect = fail_operation
        environment = _matrix_public_environment()
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.pg._database_resource",
            lambda _ctx, _database: (environment, resource, "demo"),
        )
        return

    if path == ("psql",):
        resource = MagicMock()
        resource.psql_command.return_value = _matrix_command(
            0,
            error=RuntimeError("isolated external operation failed") if failing else None,
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.pg._database_resource",
            lambda _ctx, _database: (_matrix_public_environment(), resource, "demo"),
        )
        return

    if path[:1] == ("postgres",):

        class FakeCluster:
            mode = "external"
            owned = False
            endpoint = "127.0.0.1:5432"
            endpoint_host = "127.0.0.1"
            endpoint_port = 5432

            @staticmethod
            def _command(operation: Callable[[], T]) -> Command[T]:
                return Command.create(ExecutionPlan(), lambda _context: operation(), ())

            def approve_image_command(self, *_args: object, **_kwargs: object) -> Command[None]:
                def operation() -> None:
                    if failing:
                        raise RuntimeError("isolated external operation failed")

                return self._command(operation)

            def status_command(self) -> Command[PostgresClusterState]:
                def operation() -> PostgresClusterState:
                    if failing:
                        raise RuntimeError("isolated external operation failed")
                    return PostgresClusterState.HEALTHY

                return self._command(operation)

            def ensure_running_command(self, *, timeout: float) -> Command[None]:
                _ = timeout

                def operation() -> None:
                    if failing:
                        raise RuntimeError("isolated external operation failed")

                return self._command(operation)

            def stop_command(self, *, timeout: float) -> Command[None]:
                _ = timeout

                def operation() -> None:
                    if failing:
                        raise RuntimeError("isolated external operation failed")

                return self._command(operation)

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
            "odoo_instance_sdk.internal.postgres_cli.cluster_snapshot",
            lambda _cluster, state: ClusterSnapshot(
                mode="external",
                owned=False,
                state=state,
                endpoint=ClusterEndpoint(host="127.0.0.1", port=5432),
                container=None,
                metrics=None,
                unavailability_reason="external_not_owned",
                sampled_at=None,
            ),
        )
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


@pytest.mark.parametrize(
    "case",
    [case for case in PUBLIC_LEAF_CASES if case.is_bounded],
    ids=lambda case: ".".join(case.path),
)
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
            assert "odcli_test_master_password" not in success.stdout.lower()
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
            assert "odcli_test_master_password" not in failure.stdout.lower()
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


def test_postgres_cli_diagnostic_formats_share_one_typed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = MagicMock()
    result = CommandResult(
        args=[],
        returncode=0,
        stdout="",
        stderr="",
        duration=0.0,
    )
    resource.stats_command.return_value = _matrix_command(result)
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.pg._database_resource",
        lambda _ctx, _database: (_matrix_public_environment(), resource, "demo"),
    )
    documents: list[object] = []
    for mode in ("json", "toon"):
        invoked = CliRunner().invoke(cli, ["db", "stats", "demo", "--format", mode])
        assert invoked.exit_code == 0, invoked.output
        documents.append(_decode_document(invoked.stdout, mode))
    assert documents[0] == documents[1]
    assert resource.stats_command.call_count == 2


def test_init_monitoring_machine_mode_requires_yes_before_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.pg._database_resource",
        lambda *_args, **_kwargs: pytest.fail("confirmation must precede resolution"),
    )
    result = CliRunner().invoke(cli, ["db", "init-monitoring", "--format", "json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "confirmation_required"


def test_psql_cli_keeps_native_args_and_rejects_document_mode_without_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = MagicMock()
    resource.psql_command.return_value = _matrix_command(17)
    resolve_resource = MagicMock(return_value=(_matrix_public_environment(), resource, "demo"))
    monkeypatch.setattr("odoo_instance_sdk.commands.pg._database_resource", resolve_resource)
    invoked = CliRunner().invoke(cli, ["psql", "-c", "SELECT 1"])
    assert invoked.exit_code == 17
    assert invoked.stdout == ""
    resource.psql_command.assert_called_once_with(("-c", "SELECT 1"))

    resolved_before_rejection = resolve_resource.call_count
    for args in (("--format", "json"), ("--json",)):
        rejected = CliRunner().invoke(cli, ["psql", *args])
        assert rejected.exit_code == 2
        assert "No such option" in rejected.stderr
        assert "Usage: cli psql" in rejected.stderr
    assert resolve_resource.call_count == resolved_before_rejection
    assert resource.psql_command.call_count == 1

    help_result = CliRunner().invoke(cli, ["psql", "--help"])
    assert help_result.exit_code == 0
    assert "--dry-run" in help_result.stdout
    assert "--format" not in help_result.stdout
    assert "--json" not in help_result.stdout


def _command(path: tuple[str, ...]) -> click.Command:
    command: click.Command = cli
    for name in path:
        assert isinstance(command, click.Group)
        command = command.commands[name]
    return command


def _option_names(command: click.Command) -> set[str]:
    return {option for param in command.params for option in param.opts}


def test_format_options_are_local_to_exactly_the_bounded_leaves() -> None:
    for case in PUBLIC_LEAF_CASES:
        if not case.is_bounded:
            continue
        path = case.path
        command = _command(path)
        options = _option_names(command)
        assert "--format" in options, path
        assert "--json" in options, path

    for path in (("logs",), ("monitor",)):
        options = _option_names(_command(path))
        assert "--format" not in options, path
        assert "--json" not in options, path
    for path in (
        ("db", "locks"),
        ("db", "stats"),
        ("db", "bloat"),
        ("postgres", "status"),
    ):
        assert "--dry-run" not in _option_names(_command(path)), path
    assert "--dry-run" in _option_names(_command(("db", "init-monitoring")))
    assert "--dry-run" in _option_names(_command(("psql",)))
    for path in (("run",), ("shell",)):
        options = _option_names(_command(path))
        assert "--dry-run" in options, path
        assert "--format" in options, path
        assert "--json" in options, path

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
    result: dict[str, JsonValue] = {
        "message": "secret=***",
        "items": [],
        "enabled": True,
        "value": None,
    }
    emit_json_envelope(ok=True, command="test", result=result, mode=OutputMode.JSON)
    json_document = capsys.readouterr().out
    emit_json_envelope(ok=True, command="test", result=result, mode=OutputMode.TOON)
    toon_document = capsys.readouterr().out

    from toon import DecodeOptions, decode

    json_value = json.loads(json_document)
    toon_value = decode(toon_document, DecodeOptions(indent=2, strict=True))
    assert toon_value == json_value
    assert "\033[" not in json_document + toon_document
    failure = cast(
        "dict[str, dict[str, JsonValue]]",
        build_envelope(ok=False, command="test", error_message="token=hidden"),
    )
    assert failure["error"]["message"] == "<redacted>"


def test_typed_output_documents_are_frozen_and_keep_v1_shape() -> None:
    success = success_document(
        command="typed",
        result={"secret": "password=hidden\x00", "items": [1, True]},
        dry_run=True,
    )
    failure = failure_document(
        command="typed",
        error_code="stale_plan",
        error_message="token=hidden",
    )
    assert isinstance(success, OutputDocument)
    assert isinstance(failure.error, OutputError)
    success_builtins = cast("dict[str, dict[str, JsonValue]]", msgspec.to_builtins(success))
    failure_builtins = cast("dict[str, dict[str, JsonValue]]", msgspec.to_builtins(failure))
    assert success_builtins["result"]["secret"] == r"password=hidden\x00"
    assert failure_builtins["error"]["code"] == "stale_plan"
    with pytest.raises(AttributeError):
        success.ok = False  # type: ignore[misc]


def test_run_or_preview_builds_once_and_runs_only_the_normal_path(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from odoo_instance_sdk.execution import ActionStep, Command, ExecutionPlan
    from odoo_instance_sdk.internal.proc import PreparedAction, RecordingExecutor, RunContext

    executor = RecordingExecutor()
    builds = 0
    confirmations: list[str] = []

    def build() -> Command[str]:
        nonlocal builds
        builds += 1
        action = PreparedAction("typed.action")

        def callback(context: RunContext[str]) -> str:
            context.action("typed.action")
            return "done"

        return Command.create(
            ExecutionPlan(
                steps=(ActionStep(step_id="typed.action", action="inspect", description="inspect"),)
            ),
            callback,
            (action,),
            executor=executor,
        )

    def result_payload(item: str | None) -> dict[str, JsonValue]:
        return {"value": item}

    status, value = run_or_preview(
        build,
        command_name="typed",
        mode=OutputMode.JSON,
        dry_run=True,
        result=result_payload,
        confirm=lambda: confirmations.append("confirmed"),
    )
    assert (status, value, builds, confirmations, executor.executed) == (0, None, 1, [], [])
    assert json.loads(capsys.readouterr().out)["dry_run"] is True

    status, value = run_or_preview(
        build,
        command_name="typed",
        mode=OutputMode.JSON,
        dry_run=False,
        result=lambda item: {"value": item},
        confirm=lambda: confirmations.append("confirmed"),
    )
    assert status == 0
    assert value == "done"
    assert builds == 2
    assert confirmations == ["confirmed"]


def test_rich_plan_projection_preserves_ordered_steps_and_multiline_input(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Rich is a pure, readable projection of the same redacted plan."""
    from odoo_instance_sdk.execution import Command, ExecutionPlan
    from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep, RunContext

    private_process = PreparedStep(
        step_id="instance.shell_script",
        argv=("odoo", "--config", "password=top secret"),
        stdin=b"password=top secret\nprint('ready')\n",
        public_input_preview="password=top secret\nprint('ready')\n",
        secret_values=("top secret",),
        cwd="/private/worktree",
        environment=(("DB_PASSWORD", "top secret"),),
        mutating=True,
    )
    private_action = PreparedAction(
        "instance.commit",
        action="commit",
        description="Commit transaction",
        mutating=True,
    )
    plan = ExecutionPlan(
        steps=(private_process.public_projection(), private_action.public_projection()),
        observations=({"probe": "git", "read_only": True, "executed_during_planning": True},),
        warnings=("rollback remains available",),
    ).with_fingerprint(secrets=("top secret",))

    def callback(context: RunContext[None]) -> None:
        context.process("instance.shell_script")
        context.action("instance.commit")

    command = Command.create(plan, callback, steps=(private_process, private_action))
    assert _emit_plan(command, command_name="instance.shell", mode=OutputMode.RICH) == 0
    rendered = capsys.readouterr().out
    assert "1. process instance.shell_script [mutating]" in rendered
    assert "2. action instance.commit [mutating]" in rendered
    assert "stdin: |" in rendered
    assert "print('ready')" in rendered
    assert "classification: mutating" in rendered
    assert "top secret" not in rendered
    assert "observations:" in rendered
    assert "warnings:" in rendered
    assert plan.fingerprint in rendered


def test_plan_machine_transports_are_equal_for_one_frozen_redacted_plan(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from odoo_instance_sdk.execution import Command, ExecutionPlan

    plan = ExecutionPlan(
        observations=({"token": "<redacted>", "read_only": True},),
        warnings=("secret remains redacted",),
    ).with_fingerprint(secrets=("token-value",))
    command = Command.create(plan, lambda _context: None)

    _emit_plan(command, command_name="probe", mode=OutputMode.JSON)
    json_document = capsys.readouterr().out
    _emit_plan(command, command_name="probe", mode=OutputMode.TOON)
    toon_document = capsys.readouterr().out
    from toon import DecodeOptions, decode

    assert decode(toon_document, DecodeOptions(indent=2, strict=True)) == json.loads(json_document)


def test_capture_boundary_corpus_is_secret_free_in_all_public_surfaces(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One captured private step must stay safe in plans, results, and failures."""
    from odoo_instance_sdk.internal.proc import (
        PreparedStep,
        ProcessResult,
        ProcessSpawnError,
        ProcessTimeoutError,
        SubprocessExecutor,
    )
    from odoo_instance_sdk.resources.instance import _command_result

    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.signature"
    cookie = "session=oauth-cookie-value"
    bearer = "bearer-oauth-value"
    uri_password = "uri-password-value"
    client_secret = "client-secret-value"
    refresh_token = "refresh-token-value"
    database_url = "postgresql://db-user:database-url-password@example.test/app"
    private = PreparedStep(
        step_id="security.corpus",
        argv=(
            "tool",
            "--profile=staging",
            "--client-secret",
            client_secret,
            f"--refresh-token={refresh_token}",
            "--header",
            f"Authorization: Bearer {bearer}",
            "--cookie",
            cookie,
            f"https://oauth:{uri_password}@example.test/callback",
            jwt,
        ),
        environment=(
            ("DATABASE_URL", database_url),
            ("OAUTH_COOKIE", cookie),
            ("LANG", "C"),
        ),
        environment_snapshot=(
            ("DATABASE_URL", database_url),
            ("OAUTH_COOKIE", cookie),
            ("INHERITED_PRIVATE", "inherited-secret"),
        ),
        environment_overrides=(
            ("DATABASE_URL", database_url),
            ("OAUTH_COOKIE", cookie),
            ("LANG", "C"),
        ),
        stdin=b'password = "quoted\nmultiline-secret"\n',
        public_input_preview=None,
        secret_values=(
            client_secret,
            refresh_token,
            bearer,
            cookie,
            uri_password,
            jwt,
            database_url,
            "inherited-secret",
            "multiline-secret",
        ),
        timeout=0.01,
    )
    command: Command[ProcessResult] = Command.create(
        ExecutionPlan(steps=(private.public_projection(),)).with_fingerprint(
            secrets=private.secret_values
        ),
        lambda context: context.process(private.step_id),
        steps=(private,),
    )
    raw_values = (*private.secret_values, "quoted")

    for mode in (OutputMode.RICH, OutputMode.JSON, OutputMode.TOON):
        _emit_plan(command, command_name="security.corpus", mode=mode)
        rendered = capsys.readouterr().out
        for value in raw_values:
            assert value not in rendered
    assert "--profile=staging" in private.public_projection().argv

    result = _command_result(
        ProcessResult(
            argv=private.argv,
            returncode=9,
            stdout=f"jwt={jwt}\n{database_url}\n{cookie}\n",
            stderr=f"Authorization: Bearer {bearer}; uri={uri_password}\n",
            duration=0.01,
            cwd=private.cwd,
            environment=private.environment,
        ),
        private.timeout,
        private,
    )
    result_text = repr(result)
    for value in raw_values:
        assert value not in result_text

    missing = PreparedStep(
        step_id="security.spawn",
        argv=("/definitely/missing", "--client-secret", client_secret),
        secret_values=(client_secret,),
    )
    with pytest.raises(ProcessSpawnError) as spawn:
        SubprocessExecutor().execute(missing)
    assert client_secret not in str(spawn.value)

    timeout_step = PreparedStep(
        step_id="security.timeout",
        argv=(sys.executable, "-c", "import time; time.sleep(1)", "--token", jwt),
        secret_values=(jwt,),
        timeout=0.01,
    )
    with pytest.raises(ProcessTimeoutError) as timed_out:
        SubprocessExecutor().execute(timeout_step)
    assert jwt not in str(timed_out.value)


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
        schema_version=3,
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
        schema_version=3,
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
    client.environments.remove_command.return_value = _matrix_command(None)
    with (
        patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client),
        patch("odoo_instance_sdk.commands.env.resolve_project_path", return_value=tmp_path),
        patch("odoo_instance_sdk.commands.env.click.confirm") as confirm,
    ):
        result = CliRunner().invoke(cli, ["env", "remove", "env-1", "--yes", *args])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert result.output.count("schema_version") == 1
    client.environments.remove_command.assert_called_once_with(env)
    client.environments.remove.assert_not_called()
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


def test_rich_env_checkout_execution_projects_final_public_plan(tmp_path: Path) -> None:
    plan = EnvironmentCheckoutPlan(
        name="demo",
        branch="feature",
        effective_base_ref="main",
        db_mode=EnvironmentDatabaseMode.SHARED,
        source_database="comerta",
        target_database="comerta",
        python_mode=EnvironmentPythonMode.CREATE,
        provenance=BackupProvenanceComparison(
            status=BackupProvenanceStatus.MATCHED,
            expected_base_ref="main",
            recorded_branch="main",
        ),
        freshness=BackupFreshness.STALE,
        preparation_actions=(
            DatabasePreparationAction.DOWNLOAD,
            DatabasePreparationAction.RESTORE,
            DatabasePreparationAction.SWITCH_DEFAULT,
        ),
        warnings=("backup is stale and will be refreshed",),
    )
    client = MagicMock()
    client.environments.checkout_command.return_value = _matrix_command(
        _matrix_public_environment(), private_projection=plan
    )

    with (
        patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client),
        patch("odoo_instance_sdk.commands.env.resolve_project_path", return_value=tmp_path),
    ):
        result = CliRunner().invoke(cli, ["env", "checkout", "feature"])

    assert result.exit_code == 0, result.output
    assert "Environment demo" in result.output
    assert "Checkout plan" in result.output
    assert 'provenance: {"expected_base_ref": "main"' in result.output
    assert 'freshness: "stale"' in result.output
    assert 'preparation_actions: ["download", "restore", "switch_default"]' in result.output
    assert "backup is stale and will be refreshed" in result.output
    for private_field in (
        "project",
        "options",
        "worktree_argv",
        "config_values",
        "python_selector",
    ):
        assert f"{private_field}:" not in result.output


def test_env_checkout_cli_inspects_one_command_for_dry_run_and_execution(
    tmp_path: Path,
) -> None:
    from odoo_instance_sdk.execution import ActionStep, Command, ExecutionPlan
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        RecordingExecutor,
        RunContext,
    )

    domain_plan = _matrix_checkout_plan()
    dry_effects: list[str] = []
    dry_executor = RecordingExecutor()
    dry_private_process = PreparedStep(
        step_id="checkout.worktree",
        argv=("git", "-C", "/project", "worktree", "add", "secret-target"),
        secret_values=("secret-target",),
        mutating=True,
    )
    dry_private_action = PreparedAction("checkout.cleanup")
    dry_public_process = dry_private_process.public_projection()
    dry_public_plan = ExecutionPlan(
        steps=(
            dry_public_process,
            ActionStep(
                step_id="checkout.cleanup",
                action="cleanup_on_failure",
                description="Remove owned checkout artifacts if execution fails",
                mutating=True,
            ),
        ),
        observations=(
            {
                "argv": ["git", "--version"],
                "returncode": 0,
                "read_only": True,
                "executed_during_planning": True,
            },
        ),
        warnings=("secret-target will remain redacted",),
    )
    dry_public_plan = dry_public_plan.with_fingerprint(secrets=("secret-target",))

    def dry_callback(_context: object) -> DevelopmentEnvironment:
        dry_effects.append("run")
        return _matrix_public_environment()

    dry_command = Command.create(
        dry_public_plan,
        dry_callback,
        steps=(dry_private_process, dry_private_action),
        executor=dry_executor,
        private_projection=domain_plan,
    )
    client = MagicMock()
    client.environments.checkout_command.return_value = dry_command

    with (
        patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client),
        patch("odoo_instance_sdk.commands.env.resolve_project_path", return_value=tmp_path),
    ):
        dry_result = CliRunner().invoke(cli, ["env", "checkout", "feature", "--dry-run", "--json"])

    assert dry_result.exit_code == 0, dry_result.output
    dry_payload = json.loads(dry_result.stdout)["result"]
    assert dry_payload == json.loads(json.dumps(model_to_dict(dry_command.plan)))
    assert dry_payload["steps"][0]["argv"][-1] == "<redacted>"
    assert dry_payload["observations"][0]["executed_during_planning"] is True
    assert dry_payload["fingerprint"] == dry_command.plan.fingerprint
    assert dry_effects == []
    assert dry_executor.executed == []
    client.environments.checkout_command.assert_called_once()
    client.environments.checkout_with_plan.assert_not_called()

    run_executor = RecordingExecutor()
    run_effects: list[str] = []

    def run_callback_with_steps(
        context: RunContext[DevelopmentEnvironment],
    ) -> DevelopmentEnvironment:
        context.process("checkout.worktree")
        context.action("checkout.cleanup")
        run_effects.append("run")
        return _matrix_public_environment()

    run_command = Command.create(
        dry_public_plan,
        run_callback_with_steps,
        steps=(dry_private_process, dry_private_action),
        executor=run_executor,
        private_projection=domain_plan,
    )
    client.environments.checkout_command.reset_mock()
    client.environments.checkout_command.return_value = run_command
    with (
        patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client),
        patch("odoo_instance_sdk.commands.env.resolve_project_path", return_value=tmp_path),
    ):
        run_result = CliRunner().invoke(cli, ["env", "checkout", "feature"])

    assert run_result.exit_code == 0, run_result.output
    assert run_effects == ["run"]
    assert run_executor.executed == [dry_private_process]
    assert msgspec.to_builtins(run_command.plan.steps[0]) == msgspec.to_builtins(
        dry_private_process.public_projection()
    )
    client.environments.checkout_command.assert_called_once()
    client.environments.checkout_with_plan.assert_not_called()


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
        if command == "checkout":
            plan = _matrix_checkout_plan(name=f"evil-{payload}")
            client.environments.checkout_command.return_value = _matrix_command(
                _matrix_public_environment(name=f"evil-{payload}"), private_projection=plan
            )
        elif command == "remove":
            client.environments.remove_command.return_value = _matrix_command(None)
        else:
            client.environments.sync_python_command.return_value = _matrix_command(env)
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
