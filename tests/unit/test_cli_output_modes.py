from __future__ import annotations

import inspect
import json
import re
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast
from unittest.mock import MagicMock, patch

import click
import msgspec
import pytest
from click.testing import CliRunner

if TYPE_CHECKING:
    from rich.console import Console

from odoo_instance_sdk.cli import _rich_shell_projection, cli
from odoo_instance_sdk.commands import output as output_commands
from odoo_instance_sdk.commands.context import ResolvedContext
from odoo_instance_sdk.commands.output import (
    JsonValue,
    OutputDocument,
    OutputError,
    OutputMode,
    action_command,
    build_envelope,
    emit,
    emit_json_envelope,
    fail,
    failure_document,
    model_to_dict,
    output_options,
    resolve_output_mode,
    rich_print,
    run_or_preview,
    run_rich_bounded,
    success_document,
)
from odoo_instance_sdk.execution import Command, ExecutionPlan, SemanticPlanObservation
from odoo_instance_sdk.internal.automation import (
    DepsVerifyResult,
)
from odoo_instance_sdk.internal.doctor import CheckResult, DoctorReport
from odoo_instance_sdk.internal.pg.drop import DatabaseDropResult
from odoo_instance_sdk.internal.pg.inventory import DatabaseInventoryItem, DatabaseInventoryResult
from odoo_instance_sdk.internal.resource_inventory import ResourceInventory
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
    ProjectSummary,
    Snapshot,
    StartConfig,
)
from odoo_instance_sdk.project import ProjectConfig
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
    PublicLeafCase(("stop",), ("stop", "--dry-run"), "mutating-or-spawning", True),
    PublicLeafCase(("resource", "list"), ("resource", "list"), "bounded-read-only", False),
    PublicLeafCase(("resource", "doctor"), ("resource", "doctor"), "bounded-read-only", False),
    PublicLeafCase(
        ("env", "checkout"),
        ("env", "checkout", "PROJ-123", "--dry-run"),
        "mutating-or-spawning",
        True,
    ),
    PublicLeafCase(
        ("env", "list"),
        ("env", "list", "--all-projects"),
        "bounded-read-only",
        False,
        variants=("rich-live",),
    ),
    PublicLeafCase(("env", "path"), ("env", "path", "env-1"), "bounded-read-only", False),
    PublicLeafCase(
        ("env", "remove"), ("env", "remove", "env-1", "--yes"), "mutating-or-spawning", True
    ),
    PublicLeafCase(("env", "sync"), ("env", "sync", "env-1"), "mutating-or-spawning", True),
    PublicLeafCase(
        ("backup", "list"),
        ("backup", "list"),
        "bounded-read-only",
        False,
    ),
    PublicLeafCase(
        ("backup", "show"),
        ("backup", "show", "00000000-0000-0000-0000-000000000007"),
        "bounded-read-only",
        False,
    ),
    PublicLeafCase(
        ("backup", "validate"),
        ("backup", "validate", "00000000-0000-0000-0000-000000000007"),
        "bounded-read-only",
        False,
    ),
    PublicLeafCase(
        ("backup", "delete"),
        (
            "backup",
            "delete",
            "00000000-0000-0000-0000-000000000007",
            "--dry-run",
        ),
        "mutating-or-spawning",
        True,
    ),
    PublicLeafCase(("db", "refresh"), ("db", "refresh"), "mutating-or-spawning", True),
    PublicLeafCase(
        ("db", "restore"),
        (
            "db",
            "restore",
            "00000000-0000-0000-0000-000000000007",
            "--dry-run",
        ),
        "mutating-or-spawning",
        True,
    ),
    PublicLeafCase(("db", "list"), ("db", "list"), "bounded-read-only", False),
    PublicLeafCase(
        ("db", "reset-admin-password"), ("db", "reset-admin-password"), "mutating-or-spawning", True
    ),
    PublicLeafCase(
        ("db", "drop"), ("db", "drop", "demo", "--dry-run"), "mutating-or-spawning", True
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


def test_bounded_catalogue_list_inventory_is_explicit() -> None:
    assert {
        case.path
        for case in PUBLIC_LEAF_CASES
        if case.path
        in {
            ("backup", "list"),
            ("db", "list"),
            ("resource", "list"),
            ("module", "list"),
            ("env", "list"),
        }
    } == {
        ("backup", "list"),
        ("db", "list"),
        ("resource", "list"),
        ("module", "list"),
        ("env", "list"),
    }


def test_every_eligible_leaf_uses_the_shared_preview_or_run_helper() -> None:
    """Keep the canonical inventory coupled to the executable composition path."""
    for case in PUBLIC_LEAF_CASES:
        if not case.requires_dry_run:
            continue
        callback = _command(case.path).callback
        assert callback is not None
        callback = inspect.unwrap(callback)
        assert {"run_or_preview", "_run_shell_command"} & set(callback.__code__.co_names), case.path


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
    wrapper_nonce: str | None = None,
    public_plan: ExecutionPlan | None = None,
    execution_calls: list[str] | None = None,
) -> Command[T]:
    if wrapper_nonce is not None:
        from odoo_instance_sdk.internal.proc import PreparedStep, RecordingExecutor

        step = PreparedStep(
            step_id="instance.shell_script",
            argv=("odoo",),
            wrapper_nonce=wrapper_nonce,
        )

        def run(context: object) -> T:
            if error is not None:
                raise error
            if execution_calls is not None:
                execution_calls.append("run")
            return cast("T", cast("Any", context).process(step.step_id))

        return Command.create(
            ExecutionPlan(
                steps=(step.public_projection(),),
                observations=(
                    SemanticPlanObservation(
                        kind="semantic",
                        goal="Preview the bounded operation",
                    ),
                ),
            ),
            run,
            steps=(step,),
            executor=RecordingExecutor(results={step.step_id: value}),
            private_projection=private_projection,
        )

    def simple_run(_context: object) -> T:
        if error is not None:
            raise error
        if execution_calls is not None:
            execution_calls.append("run")
        return value

    return Command.create(
        public_plan or ExecutionPlan(),
        simple_run,
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
            fail_operation
            if failing
            else lambda *_args, **_kwargs: DoctorReport(
                checks=[CheckResult(name="catalogue", status="ok", detail="ready")]
            ),
        )
        return

    if path == ("stop",):
        instance = MagicMock()
        env = _matrix_environment()
        if failing:
            instance._stop_environment_command.side_effect = fail_operation
        else:
            instance._stop_environment_command.return_value = _matrix_command(
                {"status": "stopped", "environment_id": str(env.id)}
            )
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.cli_context.ready_instance",
            lambda _ctx: _resolved_context(MagicMock(), env, instance),
        )
        return

    if path[:1] == ("resource",):
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.resource._resource_command",
            lambda **_kwargs: _matrix_command(
                ResourceInventory(resources=(), findings=(), complete=True),
                error=RuntimeError("isolated external operation failed") if failing else None,
            ),
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

    if path[:1] == ("backup",):
        from zipfile import ZipFile

        from odoo_instance_sdk.commands import backup as backup_commands
        from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

        if failing:
            monkeypatch.setattr(backup_commands, "_catalog", fail_operation)
            return
        db_path = tmp_path / "backup-catalog.sqlite3"
        backup_path = tmp_path / "backup.zip"
        with ZipFile(backup_path, "w") as archive:
            archive.writestr("manifest.json", '{"db_name": "demo"}')
            archive.writestr("dump.sql", "-- test")
        catalog = BackupCatalog(db_path=db_path)
        backup_id = "00000000-0000-0000-0000-000000000007"
        if catalog.get_by_id(backup_id) is None:
            catalog.start_download(
                backup_id,
                "http://localhost:8069",
                "demo",
                "zip",
                True,
                backup_path,
            )
            catalog.success_download(backup_id, "backup.zip", backup_path.stat().st_size, "")
        catalog.close()
        monkeypatch.setattr(
            backup_commands._catalog_path_provider,
            "provider",
            lambda: db_path,
        )
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

    if path == ("db", "restore"):
        client = MagicMock()
        if failing:
            client.environments.refresh_database_command.side_effect = fail_operation
        else:
            client.environments.refresh_database_command.return_value = _matrix_command(
                DatabasePreparationResult(
                    mode=DatabasePreparationAction.RESTORE,
                    restored_database="demo_copy",
                )
            )
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path
        )
        monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_kwargs: client)
        return

    if path == ("db", "list"):
        instance = MagicMock()
        inventory = DatabaseInventoryResult(
            cluster="127.0.0.1:5432",
            databases=(
                DatabaseInventoryItem(
                    cluster="127.0.0.1:5432",
                    cluster_id=None,
                    name="demo",
                    logical_size_bytes=10,
                    active_sessions=0,
                    is_default=True,
                ),
            ),
        )
        from odoo_instance_sdk.internal.pg import inventory as inventory_module

        monkeypatch.setattr(
            inventory_module,
            "build_database_inventory_command",
            fail_operation if failing else lambda *_args, **_kwargs: _matrix_command(inventory),
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.pg._database_instance",
            lambda _ctx: (None, instance),
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path
        )
        return

    if path == ("db", "drop"):
        instance = MagicMock()
        instance._postgres_cluster = SimpleNamespace(endpoint="127.0.0.1:5432")
        drop_command = _matrix_command(
            DatabaseDropResult(database="demo", cluster="127.0.0.1:5432"),
            error=RuntimeError("isolated external operation failed") if failing else None,
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.pg._database_instance",
            lambda _ctx: (None, instance),
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.pg.drop.build_database_drop_command",
            fail_operation if failing else lambda *_args, **_kwargs: drop_command,
        )
        return

    if path[:2] == ("env", "list"):
        snapshot = Snapshot(
            schema_version=3,
            generated_at=datetime(2020, 1, 1, tzinfo=UTC),
            projects=(
                ProjectSummary(
                    id="project-1",
                    name="demo",
                    display_hint="demo",
                    environment_count=0,
                    cluster=None,
                    runtime=None,
                ),
            ),
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

    if path[:2] == ("env", "path"):
        worktree = tmp_path / "worktree"
        worktree.mkdir(exist_ok=True)
        path_environment = _matrix_environment()
        path_environment.worktree_path = str(worktree)
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.env.OdooClient", lambda **_kwargs: MagicMock()
        )
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.env.resolve_environment",
            fail_operation if failing else lambda *_args, **_kwargs: path_environment,
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
            else lambda *_args, **_kwargs: _matrix_command(
                _command_result(0, {"result": 42}), wrapper_nonce="deadbeefdeadbeef"
            ),
        )
        return

    if path == ("exec",):
        monkeypatch.setattr(
            "odoo_instance_sdk.cli.exec_script_command",
            fail_operation
            if failing
            else lambda *_args, **_kwargs: _matrix_command(
                _command_result(0, {"returncode": 0, "stdout": "", "stderr": ""}),
                wrapper_nonce="deadbeefdeadbeef",
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
            if case.path in (("backup", "list"), ("resource", "list")):
                args.append("--all-projects")
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
    assert success_documents[0][0]["dry_run"] is ("--dry-run" in case.args)  # type: ignore[index]
    failure_dry_run = "--dry-run" in case.args and case.path != ("init",)
    assert failure_documents[0][0]["dry_run"] is failure_dry_run  # type: ignore[index]


@pytest.mark.parametrize(
    "case",
    [case for case in PUBLIC_LEAF_CASES if case.is_bounded],
    ids=lambda case: ".".join(case.path),
)
def test_public_cli_leaf_matrix_has_click_rich_contract(  # noqa: C901
    case: PublicLeafCase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exercise every bounded leaf through Click's actual Rich selection."""
    runner = CliRunner()
    outputs: list[str] = []
    for _ in range(2):
        with monkeypatch.context() as isolated:
            original_matrix_command = _matrix_command
            execution_calls: list[str] = []
            original_projection = output_commands._rich_plan_projection

            def validating_projection(document: OutputDocument) -> str:
                rendered = original_projection(document)
                result = document.result
                if isinstance(result, dict):
                    steps = result.get("steps")
                    if isinstance(steps, list):
                        displays = tuple(
                            str(item["display"])
                            for item in steps
                            if isinstance(item, dict)
                            and item.get("kind") == "process"
                            and isinstance(item.get("display"), str)
                        )
                    else:
                        displays = ()
                    assert all(display in rendered for display in displays), rendered
                return rendered

            def rich_matrix_command(value: Any, **kwargs: Any) -> Command[Any]:
                if kwargs.get("wrapper_nonce") is None:
                    kwargs["public_plan"] = _rich_contract_process_plan()
                kwargs["execution_calls"] = execution_calls
                return original_matrix_command(value, **kwargs)

            isolated.setattr(sys.modules[__name__], "_matrix_command", rich_matrix_command)
            isolated.setattr(
                "odoo_instance_sdk.commands.output._rich_plan_projection",
                validating_projection,
            )
            args = list(case.args)
            if case.path in (("backup", "list"), ("resource", "list")):
                args.append("--all-projects")
            if case.path == ("init",):
                args.append(str(tmp_path))
            if case.requires_dry_run and "--dry-run" not in args:
                args.append("--dry-run")
            _patch_leaf_external(isolated, case, failing=False, tmp_path=tmp_path)
            invoked = runner.invoke(cli, [*args, "--format", "rich"])

        assert invoked.exit_code == 0, invoked.output
        assert invoked.stderr == ""
        assert invoked.stdout.strip()
        assert "\x1b[" not in invoked.stdout
        assert '"result"' not in invoked.stdout
        assert '"steps"' not in invoked.stdout
        assert not re.search(r"\}\s*\n\s*\{", invoked.stdout)
        assert not re.search(r"\]\s*\n\s*\[", invoked.stdout)
        if case.path == ("env", "path"):
            assert invoked.stdout == str(tmp_path / "worktree") + "\n"
        else:
            key_value_lines = [
                line
                for line in invoked.stdout.splitlines()
                if re.fullmatch(r"\s*[a-z][a-z0-9_-]*=[^=]+(?:\s+[a-z][a-z0-9_-]*=[^=]+)+\s*", line)
            ]
            assert all(line.lstrip().startswith("status=success") for line in key_value_lines)
        if case.requires_dry_run:
            assert "--dry-run" in args
            assert execution_calls == []
        outputs.append(invoked.stdout)

    assert outputs[0] == outputs[1]


def _rich_contract_process_plan() -> ExecutionPlan:
    from odoo_instance_sdk.internal.proc import PreparedStep

    step = PreparedStep(
        step_id="rich.contract.process",
        argv=("odoo", "--database", "demo"),
        cwd="/worktree",
        read_only=True,
    )
    return ExecutionPlan(
        steps=(step.public_projection(),),
        observations=(
            SemanticPlanObservation(
                kind="semantic",
                goal="Preview the bounded operation",
                targets=("demo",),
            ),
        ),
    )


@pytest.mark.parametrize("mode", ["rich", "json", "toon"])
@pytest.mark.parametrize(
    ("case", "expected_ok", "expected_exit"),
    [
        (DepsVerifyResult(), True, 0),
        (
            DepsVerifyResult(
                pip_check_ok=False,
                distributions=[{"detail": "package requires password='pip-secret'"}],
            ),
            False,
            1,
        ),
        (
            DepsVerifyResult(
                missing_imports=[{"module": "sale", "import": "missing_pkg"}],
            ),
            False,
            1,
        ),
        (
            DepsVerifyResult(
                pip_check_ok=False,
                distributions=[{"detail": "conflict"}],
                missing_imports=[{"module": "sale", "import": "missing_pkg"}],
            ),
            False,
            1,
        ),
    ],
    ids=["success", "distribution-conflict", "missing-import", "combined-failure"],
)
def test_deps_verify_uses_one_success_predicate_across_formats(
    mode: str,
    case: DepsVerifyResult,
    expected_ok: bool,
    expected_exit: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = SimpleNamespace(
        source=SimpleNamespace(python=None),
        instance=SimpleNamespace(config=SimpleNamespace()),
        python_path=lambda: tmp_path / "venv" / "bin" / "python",
        worktree_path=lambda: tmp_path,
    )
    monkeypatch.setattr("odoo_instance_sdk.cli.cli_context.ready_instance", lambda _ctx: context)
    monkeypatch.setattr(
        "odoo_instance_sdk.cli.verify_deps_command",
        lambda **_kwargs: _matrix_command(case),
    )

    invoked = CliRunner().invoke(cli, ["deps", "verify", "--format", mode])

    assert invoked.exit_code == expected_exit, invoked.output
    combined = invoked.stdout + invoked.stderr
    assert "pip-secret" not in combined
    if mode in {"json", "toon"}:
        document = _decode_document(invoked.stdout, mode)
        assert document["ok"] is expected_ok  # type: ignore[index]
        if expected_ok:
            assert document["data"]["pip_check_ok"] is True  # type: ignore[index]
        else:
            assert document["error"]["code"] == "deps_verify_failed"  # type: ignore[index]
            assert document["error"]["details"]["pip_check_ok"] is case.pip_check_ok  # type: ignore[index]
    elif expected_ok:
        assert "pip check: ok" in combined
    else:
        assert "pip check: issues" in combined
        assert "missing_pkg" in combined or "conflict" in combined or "package requires" in combined


def test_deps_verify_dry_run_preserves_configured_uv_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    uv_executable = tmp_path / "tools" / "uv"
    uv_executable.parent.mkdir()
    uv_executable.write_text("")
    uv_executable.chmod(0o755)
    context = SimpleNamespace(
        source=SimpleNamespace(python="3.12"),
        instance=SimpleNamespace(
            config=SimpleNamespace(deferred_runtime=SimpleNamespace(uv_executable=uv_executable))
        ),
        python_path=lambda: tmp_path / "venv" / "bin" / "python",
        worktree_path=lambda: tmp_path,
    )
    captured: dict[str, object] = {}

    def make_command(**kwargs: object) -> Command[DepsVerifyResult]:
        captured.update(kwargs)
        return _matrix_command(DepsVerifyResult())

    monkeypatch.setattr("odoo_instance_sdk.cli.cli_context.ready_instance", lambda _ctx: context)
    monkeypatch.setattr("odoo_instance_sdk.cli.verify_deps_command", make_command)

    invoked = CliRunner().invoke(cli, ["deps", "verify", "--dry-run", "--format", "json"])

    assert invoked.exit_code == 0, invoked.output
    assert captured["uv_executable"] == uv_executable
    assert json.loads(invoked.stdout)["dry_run"] is True


@pytest.mark.parametrize(
    "stdout",
    [
        "__ODCLI_PAYLOAD__foreignnonce1234__ {} __END_PAYLOAD__foreignnonce1234__",
        "__ODCLI_PAYLOAD__deadbeefdeadbeef__ {malformed} __END_PAYLOAD__deadbeefdeadbeef__",
    ],
)
def test_shell_zero_exit_without_valid_bound_frame_is_startup_failure(stdout: str) -> None:
    from odoo_instance_sdk.cli import _run_shell_command, _ShellCommandFailure

    command = _matrix_command(
        CommandResult(args=[], returncode=0, stdout=stdout, stderr="", duration=0.0),
        wrapper_nonce="deadbeefdeadbeef",
    )
    with pytest.raises(_ShellCommandFailure) as caught:
        _run_shell_command(
            command_name="eval",
            build_command=lambda: command,
            mode=OutputMode.JSON,
            dry_run=False,
            project_result=lambda _value, payload: payload,
            commit=False,
        )
    assert caught.value.error_code == "eval_startup_failed"
    assert caught.value.details is None


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


@pytest.mark.parametrize("args", [["--format", "json"], ["--format", "toon"], ["--json"]])
def test_machine_db_drop_requires_yes_before_project_resolution(args: list[str]) -> None:
    with patch(
        "odoo_instance_sdk.commands.pg._database_instance",
        side_effect=AssertionError("confirmation must precede database resolution"),
    ):
        result = CliRunner().invoke(cli, ["db", "drop", "demo", *args])

    assert result.exit_code == 1, result.output
    assert "confirmation_required" in result.output


def test_db_drop_dry_run_emits_plan_without_running_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = _matrix_command(None)
    instance = MagicMock()
    instance._postgres_cluster.endpoint = "127.0.0.1:5432"
    builder = MagicMock(return_value=command)
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.pg._database_instance", lambda _ctx: (None, instance)
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: Path.cwd()
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.pg.drop.build_database_drop_command", builder)

    result = CliRunner().invoke(cli, ["db", "drop", "demo", "--dry-run", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["dry_run"] is True
    builder.assert_called_once()


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


def test_eval_payload_fields_round_trip_in_json_and_toon(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result: dict[str, JsonValue] = {
        "result": None,
        "user_stdout": "\u0434\u043e\n\u043f\u043e\u0441\u043b\u0435\n",
        "user_error": {
            "type": "ValueError",
            "message": "failure",
            "source": {"file": "<odcli-shell-script>", "line": 1, "text": "raise ValueError()"},
        },
        "truncated": True,
    }
    emit_json_envelope(ok=True, command="eval", result=result, mode=OutputMode.JSON)
    json_document = capsys.readouterr().out
    emit_json_envelope(ok=True, command="eval", result=result, mode=OutputMode.TOON)
    toon_document = capsys.readouterr().out

    from toon import DecodeOptions, decode

    json_value = json.loads(json_document)
    toon_value = decode(toon_document, DecodeOptions(indent=2, strict=True))
    assert toon_value == json_value
    assert json_value["data"]["result"] is None
    assert json_value["data"]["user_stdout"] == "\u0434\u043e\n\u043f\u043e\u0441\u043b\u0435\n"
    assert json_value["data"]["truncated"] is True


def test_shell_failure_details_round_trip_and_rich_parity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    details: dict[str, JsonValue] = {
        "result": None,
        "user_stdout": "before\n",
        "user_error": {
            "type": "ValueError",
            "message": "failure",
            "source": {"file": "<odcli-shell-script>", "line": 2, "text": "raise ValueError()"},
        },
        "truncated": False,
    }
    emit_json_envelope(
        ok=False,
        command="eval",
        error_code="eval_user_code_failed",
        error_message="ValueError: failure",
        error_details=details,
        mode=OutputMode.JSON,
    )
    json_document = capsys.readouterr().out
    emit_json_envelope(
        ok=False,
        command="eval",
        error_code="eval_user_code_failed",
        error_message="ValueError: failure",
        error_details=details,
        mode=OutputMode.TOON,
    )
    toon_document = capsys.readouterr().out

    from toon import DecodeOptions, decode

    json_value = json.loads(json_document)
    toon_value = decode(toon_document, DecodeOptions(indent=2, strict=True))
    assert toon_value == json_value
    assert json_value["ok"] is False
    assert "result" not in json_value
    assert "data" not in json_value
    assert set(json_value["error"]["details"]) == {
        "result",
        "user_stdout",
        "user_error",
        "truncated",
    }
    rendered = _rich_shell_projection(
        failure_document(
            command="eval",
            dry_run=False,
            error_code="eval_user_code_failed",
            error_message="ValueError: failure",
            error_details=details,
        )
    )
    assert "Result: null" in rendered
    assert "Output:" in rendered and "before" in rendered
    assert "Error: ValueError: failure" in rendered


def test_typed_output_documents_are_frozen_and_keep_v1_shape() -> None:
    success = success_document(
        command="typed",
        result={"secret": "password=hidden\x00", "items": [1, True]},
        dry_run=True,
    )
    failure = failure_document(
        command="typed",
        dry_run=False,
        error_code="stale_plan",
        error_message="token=hidden",
    )
    assert isinstance(success, OutputDocument)
    assert isinstance(failure.error, OutputError)
    success_builtins = cast("dict[str, dict[str, JsonValue]]", msgspec.to_builtins(success))
    failure_builtins = cast("dict[str, dict[str, JsonValue]]", msgspec.to_builtins(failure))
    assert success_builtins["result"]["secret"] == r"password=hidden\x00"
    assert failure_builtins["error"]["code"] == "stale_plan"
    assert "details" not in failure_builtins["error"]
    with pytest.raises(AttributeError):
        success.ok = False  # type: ignore[misc]


@pytest.mark.parametrize("dry_run", [False, True])
def test_shared_failure_boundary_requires_and_preserves_resolved_dry_run(
    dry_run: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        fail(
            OutputMode.JSON,
            "representative.failure",
            "precondition failed",
            dry_run=dry_run,
        )

    assert caught.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is dry_run
    assert payload["error"]["code"] == "representative_failure_failed"


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


def test_rich_bounded_runner_is_sparse_and_reports_reliable_units(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from odoo_instance_sdk.internal.proc import StepEvent

    def run(observer: Callable[[StepEvent], None]) -> str:
        observer(StepEvent(step_id="download", kind="started"))
        observer(
            StepEvent(
                step_id="download",
                kind="progress",
                elapsed=0.25,
                completed_units=5,
                total_units=10,
            )
        )
        observer(StepEvent(step_id="download", kind="completed", elapsed=0.5))
        return "ok"

    assert run_rich_bounded(run) == "ok"
    output = capsys.readouterr().out
    assert "[download] started elapsed=" in output
    assert "[download] progress units=5/10 (50%) elapsed=0.250s" in output
    assert "[download] completed elapsed=0.500s" in output


def test_rich_bounded_runner_exposes_a_slow_step_before_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.internal.proc import StepEvent

    emitted: list[str] = []
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.output.rich_print",
        lambda value, **_kwargs: emitted.append(value),
    )

    class NonTerminalConsole:
        is_terminal = False

    def run(observer: Callable[[StepEvent], None]) -> str:
        observer(StepEvent(step_id="slow", kind="started", elapsed=0.0))
        assert emitted == ["[slow] started elapsed=0.000s"]
        observer(StepEvent(step_id="slow", kind="progress", elapsed=0.25))
        observer(StepEvent(step_id="slow", kind="completed", elapsed=0.5))
        return "done"

    assert run_rich_bounded(run, console=cast("Console", NonTerminalConsole())) == "done"
    assert emitted[-1] == "[slow] completed elapsed=0.500s"


def test_rich_bounded_runner_is_deterministic_and_omits_unknown_percentage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.internal.proc import StepEvent

    class NonTerminalConsole:
        is_terminal = False

    def render() -> list[str]:
        emitted: list[str] = []
        monkeypatch.setattr(
            "odoo_instance_sdk.commands.output.rich_print",
            lambda value, **_kwargs: emitted.append(value),
        )

        def run(observer: Callable[[StepEvent], None]) -> None:
            observer(StepEvent(step_id="probe", kind="started", elapsed=0.0))
            observer(
                StepEvent(
                    step_id="probe",
                    kind="progress",
                    elapsed=0.25,
                    completed_units=3,
                )
            )
            observer(StepEvent(step_id="probe", kind="completed", elapsed=0.5))

        run_rich_bounded(run, console=cast("Console", NonTerminalConsole()))
        return emitted

    first = render()
    second = render()
    assert first == second
    assert first[1] == "[probe] progress units=3 elapsed=0.250s"
    assert "%" not in first[1]


def test_rich_bounded_runner_closes_tty_live_on_ctrl_c(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.internal.proc import StepEvent

    class TerminalConsole:
        is_terminal = True

    instances: list[object] = []

    class FakeLive:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.exited = False
            instances.append(self)

        def __enter__(self) -> FakeLive:
            return self

        def __exit__(self, *_args: object) -> Literal[False]:
            self.exited = True
            return False

        def update(self, _value: object, *, refresh: bool = False) -> None:
            assert refresh is True

    monkeypatch.setattr("rich.live.Live", FakeLive)

    def run(observer: Callable[[StepEvent], None]) -> None:
        observer(StepEvent(step_id="slow", kind="started", elapsed=0.0))
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_rich_bounded(run, console=cast("Console", TerminalConsole()))
    assert len(instances) == 1
    assert getattr(instances[0], "exited") is True


@pytest.mark.parametrize(
    ("command", "result", "expected"),
    [
        (
            "exec",
            {"database": "demo", "modules": ["sale", "stock"], "commit": True},
            'status=success database=demo modules=["sale","stock"] transaction=commit',
        ),
        (
            "exec",
            {"commit": False},
            "status=success transaction=rollback",
        ),
        (
            "test",
            {"database": "demo", "http_url": "http://localhost:8069", "modules": ["sale"]},
            'status=success database=demo url=http://localhost:8069 modules=["sale"]',
        ),
        ("postgres.up", {"value": "done"}, "status=success"),
    ],
    ids=["exec-commit", "exec-rollback", "test-summary", "no-summary"],
)
def test_rich_success_has_one_common_completion_line(
    command: str,
    result: dict[str, JsonValue],
    expected: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit(
        success_document(command=command, result=result),
        OutputMode.RICH,
        rich=lambda _document: "existing projection",
    )
    lines = capsys.readouterr().out.splitlines()
    assert lines.count(expected) == 1
    assert sum(line.startswith("status=success") for line in lines) == 1


@pytest.mark.parametrize("mode", [OutputMode.JSON, OutputMode.TOON])
def test_rich_completion_does_not_change_machine_document(
    mode: OutputMode,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result: dict[str, JsonValue] = {"database": "demo", "commit": True}
    emit(success_document(command="exec", result=result), mode)
    output = capsys.readouterr().out
    document = _decode_document(output, mode.value)
    assert document["result"] == result  # type: ignore[index]
    assert "status=success" not in output


def test_action_postcondition_failure_emits_failed_without_completion() -> None:
    from odoo_instance_sdk.internal.proc import StepEvent

    events: list[StepEvent] = []

    def operation() -> None:
        raise RuntimeError("postcondition failed")

    with pytest.raises(RuntimeError, match="postcondition failed"):
        action_command("restore", operation).run(observer=events.append)
    assert [(event.step_id, event.kind) for event in events] == [
        ("restore", "started"),
        ("restore", "failed"),
    ]


@pytest.mark.parametrize(
    "operation",
    [
        "db.refresh",
        "db.restore",
        "env.lifecycle",
        "test",
        "module.update",
        "eval",
        "exec",
        "translations.export",
        "postgres.up",
    ],
)
def test_progress_inventory_is_identified_and_machine_silent(
    operation: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from odoo_instance_sdk.internal.proc import StepEvent

    events: list[StepEvent] = []
    command = action_command(operation, lambda: "done")

    def run(observer: Callable[[StepEvent], None]) -> str:
        def observe(event: StepEvent) -> None:
            events.append(event)
            observer(event)

        return command.run(observer=observe)

    result = run_rich_bounded(run)
    captured = capsys.readouterr().out

    assert result == "done"
    assert [event.kind for event in events] == ["started", "completed"]
    assert all(event.step_id and event.operation and event.target for event in events)
    # Rich receives identified lifecycle events and no bare lifecycle line.
    assert f"[{operation}] started" in captured
    assert f"[{operation}] completed" in captured

    run_or_preview(
        lambda: action_command(operation, lambda: "done"),
        command_name=operation,
        mode=OutputMode.JSON,
        dry_run=False,
        result=lambda value: {"value": value},
        progress=True,
    )
    machine_output = capsys.readouterr().out
    assert "started" not in machine_output
    assert "completed" not in machine_output


def test_run_or_preview_maps_ctrl_c_to_exit_130() -> None:
    def operation() -> None:
        raise KeyboardInterrupt

    with pytest.raises(click.exceptions.Exit) as caught:
        run_or_preview(
            lambda: action_command("slow", operation),
            command_name="slow",
            mode=OutputMode.RICH,
            dry_run=False,
            progress=True,
        )
    assert caught.value.exit_code == 130


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


def test_semantic_plan_projection_hides_private_execution_details(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from odoo_instance_sdk.execution import (
        Command,
        ExecutionPlan,
        PlanPrecondition,
        SemanticPlanObservation,
    )

    plan = ExecutionPlan(
        observations=(
            SemanticPlanObservation(
                kind="semantic",
                goal="Update the module",
                targets=("demo",),
                mutations=("write module files",),
                preconditions=(
                    PlanPrecondition(
                        name="http-port-free",
                        status="failed",
                        detail="127.0.0.1:8069 is occupied",
                    ),
                ),
                warnings=("preview only",),
            ),
        ),
    ).with_fingerprint()
    command = Command.create(plan, lambda _context: None)

    assert _emit_plan(command, command_name="module.update", mode=OutputMode.RICH) == 0
    rendered = capsys.readouterr().out
    assert "Goal: Update the module" in rendered
    assert "Preconditions:" in rendered
    assert "127.0.0.1:8069 is occupied" in rendered
    assert "fingerprint" not in rendered


def _assert_actual_builder_rich_preview(
    command: Command[Any],
    *,
    command_name: str,
    capsys: pytest.CaptureFixture[str],
    executor: Any | None = None,
) -> None:
    """Project a real builder's captured plan without invoking its callback."""
    from odoo_instance_sdk.execution import PlanPrecondition, SemanticPlanObservation

    displays = tuple(step.display for step in command.plan.process_steps)
    assert displays, f"{command_name} must capture at least one process"
    semantic_plan = msgspec.structs.replace(
        command.plan,
        observations=(
            SemanticPlanObservation(
                kind="semantic",
                goal=f"Preview {command_name}",
                preconditions=(
                    PlanPrecondition(name="preflight", status="failed", detail="preview only"),
                ),
            ),
        ),
    )
    status, value = run_or_preview(
        lambda: command,
        command_name=command_name,
        mode=OutputMode.RICH,
        dry_run=True,
        preview=lambda _command: model_to_dict(semantic_plan),
    )
    rendered = capsys.readouterr().out
    assert (status, value) == (0, None)
    positions: list[int] = []
    offset = 0
    for display in displays:
        position = rendered.index(display, offset)
        positions.append(position)
        offset = position + len(display)
    assert positions == sorted(positions)
    assert "preflight: failed" in rendered
    assert "fingerprint:" not in rendered
    for private_field in ("argv:", "executable:", "cwd:", "timeout:", "environment:", "stdin:"):
        assert private_field not in rendered
    if executor is not None:
        assert executor.executed == []


@pytest.mark.parametrize(
    "branch",
    [
        "run",
        "project.run",
        "environment.run",
        "module.update",
        "translations.export",
        "test",
        "postgres.up",
        "postgres.stop",
        "environment.sync",
        "environment.remove",
    ],
)
def test_rich_dry_run_uses_real_command_builders(
    branch: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Actual leaf builders retain their process displays in a zero-execution preview."""
    from odoo_instance_sdk.config import InstanceConfig
    from odoo_instance_sdk.internal import proc as proc_module
    from odoo_instance_sdk.internal.address import AddressState
    from odoo_instance_sdk.internal.automation import (
        export_translations_command,
        run_odoo_tests_command,
        update_modules_command,
    )
    from odoo_instance_sdk.internal.proc import RecordingExecutor
    from odoo_instance_sdk.models import OdooTestSpec, StartConfig
    from odoo_instance_sdk.resources import instance as instance_module
    from odoo_instance_sdk.resources.environment import EnvironmentResource
    from odoo_instance_sdk.resources.instance import OdooInstance, _RuntimeBinding
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    instance = OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            start_config=StartConfig(http_port=0, addons_path=[str(tmp_path / "addons")]),
            command_prefix=("python", "odoo-bin"),
            default_cwd=tmp_path,
            configured_database_names=("demo",),
            db_host="127.0.0.1",
            db_port=5432,
            db_user="odoo",
        ),
        _client=MagicMock(),
    )
    executor = RecordingExecutor()
    monkeypatch.setattr(instance_module, "SubprocessExecutor", lambda: executor)
    command: Command[Any]
    if branch in {"run", "project.run", "environment.run"}:
        if branch == "project.run":
            instance._runtime_binding = _RuntimeBinding(
                owner_kind="project",
                owner_id="project",
                project_id="project",
                repository_root=tmp_path,
                git_common_dir=tmp_path / ".git",
            )
        elif branch == "environment.run":
            instance._environment_id = "environment"
        command = instance.run_foreground_command(args=("--stop-after-init",))
    elif branch == "module.update":
        command = update_modules_command(instance, ("sale",))
    elif branch == "translations.export":
        command = export_translations_command(
            instance, ("sale",), ("en_US",), worktree_root=tmp_path
        )
    elif branch == "test":
        monkeypatch.setattr(
            "odoo_instance_sdk.internal.automation.probe_address",
            lambda *_args, **_kwargs: AddressState.FREE,
        )
        command = run_odoo_tests_command(
            instance,
            OdooTestSpec(modules=("sale",), test_tags="/sale"),
            http_interface="127.0.0.1",
            http_port=0,
        )
    elif branch in {"postgres.up", "postgres.stop"}:
        postgres_root = tmp_path / "postgres"
        postgres_root.mkdir()
        (postgres_root / "compose.yaml").write_text("services: {}\n")
        monkeypatch.setattr(
            "odoo_instance_sdk.resources.postgres.get_project_postgres_dir",
            lambda _project_id: postgres_root,
        )
        cluster = PostgresCluster(
            _repository_root=tmp_path,
            _project_id="rich-preview",
            _mode="compose",
            _endpoint_host="127.0.0.1",
            _endpoint_port=5432,
            _image="postgres:16",
            _user="odoo",
        )
        command = (
            cluster.ensure_running_command(executor=executor)
            if branch == "postgres.up"
            else cluster.stop_command(executor=executor)
        )
    else:
        root = tmp_path / "project"
        worktree = root / "worktree"
        (root / ".odcli").mkdir(parents=True)
        worktree.mkdir()
        (root / ".odcli" / "project.toml").write_text(
            '[project]\nrequirements = ["requirements.txt"]\n'
        )
        (root / "requirements.txt").write_text("httpx\n")
        environment = _matrix_public_environment()
        environment = msgspec.structs.replace(
            environment,
            repository_root=str(root),
            worktree_path=str(worktree),
            dependency_lock_path=str(worktree / "uv.lock"),
            python_environment_path=str(worktree / ".venv"),
            generated_config_path=str(worktree / "odoo.conf"),
        )
        resource = EnvironmentResource(_client=MagicMock())
        if branch == "environment.sync":
            monkeypatch.setattr(proc_module, "SubprocessExecutor", lambda: executor)
            command = resource.sync_python_command(environment)
        else:
            command = resource.remove_command(environment, executor=executor)

    _assert_actual_builder_rich_preview(
        command, command_name=branch, capsys=capsys, executor=executor
    )


def test_rich_dry_run_uses_actual_environment_checkout_builder(
    env_client: Any,
    project_manifest: Path,
    fake_python: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.internal import proc as proc_module
    from odoo_instance_sdk.internal.proc import RecordingExecutor
    from odoo_instance_sdk.resources.environment import EnvironmentCheckoutOptions

    executor = RecordingExecutor()
    monkeypatch.setattr(proc_module, "SubprocessExecutor", lambda: executor)

    command = env_client.environments.checkout_command(
        project_manifest,
        "feature/rich-preview",
        options=EnvironmentCheckoutOptions(python=str(fake_python), source_database="comerta"),
    )

    _assert_actual_builder_rich_preview(
        command, command_name="env.checkout", capsys=capsys, executor=executor
    )


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
                snapshot = Snapshot(
                    schema_version=3,
                    generated_at=datetime(2020, 1, 1, tzinfo=UTC),
                    projects=(
                        ProjectSummary(
                            id="project",
                            name=payload,
                            display_hint=payload,
                            environment_count=0,
                            cluster=None,
                            runtime=None,
                        ),
                    ),
                    environments=(),
                )
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


@pytest.mark.parametrize("mode", ["rich", "json", "toon"])
def test_project_module_update_keeps_confirmation_and_output_contract(
    mode: str, tmp_path: Path
) -> None:
    project = ProjectConfig(
        repository_root=tmp_path,
        python=sys.executable,
        odoo_bin=Path(sys.executable),
    )
    instance = SimpleNamespace(
        config=SimpleNamespace(
            start_config=StartConfig(db_name="project_db"),
            command_prefix=(sys.executable, str(tmp_path / "odoo-bin")),
        )
    )
    resolved = ResolvedContext(
        client=cast("Any", object()),
        source=cast("Any", project),
        instance=cast("Any", instance),
        provenance="cwd",
    )
    command = _matrix_command(_command_result(0, {"result": {"updated": ["sale"]}}))
    with (
        patch("odoo_instance_sdk.cli.cli_context.ready_instance", return_value=resolved),
        patch("odoo_instance_sdk.cli.update_modules_command", return_value=command) as update,
    ):
        result = CliRunner().invoke(
            cli,
            ["module", "update", "sale", "--yes", "--format", mode],
        )

    assert result.exit_code == 0, result.output
    update.assert_called_once_with(instance, ("sale",))
    if mode == "rich":
        assert "Updated modules:" in result.output
        assert "sale" in result.output
    else:
        if mode == "json":
            payload = json.loads(result.stdout)
        else:
            from toon import DecodeOptions, decode

            payload = decode(result.stdout, DecodeOptions(indent=2, strict=True))
        assert payload["result"] == payload["data"]
        assert payload["result"]["modules"] == ["sale"]
        assert payload["result"]["updated"] == ["sale"]


def test_project_module_update_incomplete_result_is_a_failure_document(tmp_path: Path) -> None:
    project = ProjectConfig(
        repository_root=tmp_path,
        python=sys.executable,
        odoo_bin=Path(sys.executable),
    )
    incomplete = _command_result(0, {"result": {"updated": []}})

    def shell_script(_source: str, **kwargs: Any) -> Command[CommandResult]:
        converter = kwargs["result_converter"]

        def run(_context: object) -> CommandResult:
            return converter(incomplete) if converter is not None else incomplete

        return Command.create(ExecutionPlan(), run)

    instance = SimpleNamespace(
        config=SimpleNamespace(start_config=StartConfig(db_name="project_db")),
        _shell_script_command=shell_script,
    )
    resolved = ResolvedContext(
        client=cast("Any", object()),
        source=cast("Any", project),
        instance=cast("Any", instance),
        provenance="cwd",
    )
    with patch("odoo_instance_sdk.cli.cli_context.ready_instance", return_value=resolved):
        result = CliRunner().invoke(
            cli,
            ["module", "update", "sale", "--yes", "--format", "json"],
        )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "updated" not in result.stdout
    assert "did not confirm" in payload["error"]["message"]


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


@pytest.mark.parametrize("mode", ["json", "toon"])
def test_resource_machine_projection_is_read_only_and_one_document(
    mode: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The real resource source may inspect a catalogue but never rewrite it."""
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    catalog_path = tmp_path / "backup-catalog.sqlite3"
    catalog = BackupCatalog(db_path=catalog_path)
    catalog.close()
    before = catalog_path.read_bytes()
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.resource._catalog_path_provider.provider",
        lambda: catalog_path,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.resource.get_data_root",
        lambda *, ensure_exists=False: tmp_path / "data",
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.resource.get_backups_dir",
        lambda **_kwargs: tmp_path / "backups",
    )

    result = CliRunner().invoke(cli, ["resource", "list", "--all-projects", "--format", mode])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert result.stdout.count("schema_version") == 1
    document = _decode_document(result.stdout, mode)
    assert document["ok"] is True  # type: ignore[index]
    assert document["result"] == document["data"]  # type: ignore[index]
    assert catalog_path.read_bytes() == before


def test_resource_rich_projections_are_bounded_and_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = ResourceInventory(resources=(), findings=(), complete=True)
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.resource._resource_command",
        lambda **_kwargs: _matrix_command(empty),
    )
    runner = CliRunner()

    first = runner.invoke(cli, ["resource", "list", "--all-projects"])
    second = runner.invoke(cli, ["resource", "list", "--all-projects"])
    doctor = runner.invoke(cli, ["resource", "doctor"])

    assert first.exit_code == second.exit_code == doctor.exit_code == 0
    assert first.output == second.output
    assert "Identity" in first.output
    assert "No resource findings." in doctor.output
    assert "\x1b[" not in first.output + doctor.output


@pytest.mark.parametrize(
    ("command", "render", "result", "headers"),
    [
        pytest.param(
            "backup.list",
            "odoo_instance_sdk.commands.backup._rich_table",
            {
                "backups": [
                    {
                        "id": "backup-1",
                        "source_base_url": "https://odoo.example",
                        "database_name": "demo",
                        "state": "available",
                        "file_present": True,
                        "recorded_bytes": 1024,
                        "catalogue_time": "2026-01-01T00:00:00+00:00",
                    }
                ],
                "next_cursor": None,
            },
            ("UUID", "Source", "Database", "State", "Bytes"),
            id="backup-list",
        ),
        pytest.param(
            "db.list",
            "odoo_instance_sdk.commands.db._list_rich",
            {
                "cluster": "127.0.0.1:5432",
                "databases": [
                    {
                        "cluster": "127.0.0.1:5432",
                        "name": "demo",
                        "logical_size_bytes": 1024,
                        "active_sessions": 2,
                        "is_default": True,
                        "origin": "unknown",
                    }
                ],
            },
            ("Database", "Size", "Sessions", "Default", "Origin"),
            id="db-list",
        ),
        pytest.param(
            "resource.list",
            "odoo_instance_sdk.commands.resource._rich_list",
            {
                "resources": [
                    {
                        "stable_identity": "file:demo",
                        "type": "backup",
                        "name": "demo.zip",
                        "ownership_confidence": "proven",
                        "measured_bytes": 1024,
                        "completeness": "complete",
                        "reclaimable": True,
                    }
                ]
            },
            ("Identity", "Type", "Name", "Measured bytes"),
            id="resource-list",
        ),
    ],
)
def test_catalogue_rich_lists_use_single_human_table(
    command: str,
    render: str,
    result: dict[str, object],
    headers: tuple[str, ...],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """List leaves keep machine integers while Rich renders human bytes."""
    module = __import__(render.rsplit(".", 1)[0], fromlist=[render.rsplit(".", 1)[1]])
    rich_renderer = getattr(module, render.rsplit(".", 1)[1])
    rendered = rich_renderer(success_document(command=command, result=cast("Any", result)))

    assert capsys.readouterr().out == ""
    assert rendered.strip()
    assert all(header in rendered for header in headers)
    assert "1.0 KiB" in rendered
    if command == "db.list":
        assert rendered.count("Cluster: 127.0.0.1:5432") == 1
        assert "cluster=" not in rendered
    assert '"recorded_bytes"' not in rendered
    assert '"logical_size_bytes"' not in rendered
    assert '"measured_bytes"' not in rendered


def test_module_rich_list_uses_single_table() -> None:
    from odoo_instance_sdk.cli import _rich_module_list

    result = success_document(
        command="module.list",
        result={
            "modules": [
                {
                    "name": "sale",
                    "state": "installed",
                    "installed_version": "19.0",
                    "latest_version": None,
                }
            ]
        },
    )
    rendered = _rich_module_list(result)
    assert rendered.strip()
    assert all(header in rendered for header in ("NAME", "STATE", "VERSION"))
    assert "sale" in rendered
    assert '"modules"' not in rendered


@pytest.mark.parametrize(
    ("render", "command", "result", "labels"),
    [
        (
            "odoo_instance_sdk.commands.backup._rich_detail",
            "backup.show",
            {
                "id": "backup-1",
                "database_name": "demo",
                "state": "available",
                "recorded_bytes": 1024,
            },
            ("Backup", "Database Name", "1.0 KiB"),
        ),
        (
            "odoo_instance_sdk.commands.backup._rich_delete",
            "backup.delete",
            {"plan": {"backup_id": "backup-1", "path": "/safe/backup.zip", "state": "available"}},
            ("Delete plan:", "Backup Id", "/safe/backup.zip"),
        ),
        (
            "odoo_instance_sdk.commands.db._restore_rich",
            "db.restore",
            {"restored_database": "demo_copy", "backup": {"id": "backup-1"}},
            ("Database restore", "demo_copy", "backup-1"),
        ),
        (
            "odoo_instance_sdk.commands.resource._rich_doctor",
            "resource.doctor",
            {"findings": [{"severity": "warning", "code": "stale", "message": "inspect"}]},
            ("Severity", "warning", "stale"),
        ),
        (
            "odoo_instance_sdk.cli._rich_module_update",
            "module.update",
            {"modules": ["sale"], "updated": ["sale"]},
            ("Module update", "sale", "updated"),
        ),
        (
            "odoo_instance_sdk.cli._rich_translation_export",
            "translations.export",
            {
                "exports": [
                    {
                        "module": "sale",
                        "requested_lang": "fr_FR",
                        "actual_filename": "sale.po",
                        "bytes_written": 1024,
                    }
                ]
            },
            ("Translation export", "fr_FR", "1.0 KiB"),
        ),
        (
            "odoo_instance_sdk.commands.pg._monitoring_rich",
            "db.init-monitoring",
            {"installed": ["pg_stat_statements"], "already_present": [], "skipped": []},
            ("PostgreSQL monitoring", "Installed", "pg_stat_statements"),
        ),
        (
            "odoo_instance_sdk.commands.pg._cluster_rich",
            "postgres.status",
            {"mode": "compose", "owned": True, "state": "healthy", "endpoint": "127.0.0.1:5432"},
            ("PostgreSQL cluster", "Endpoint", "127.0.0.1:5432"),
        ),
        (
            "odoo_instance_sdk.cli._rich_vscode_generate",
            "vscode.generate",
            {"profile": {"name": "Odoo", "program": "odoo-bin"}},
            ("VS Code launch", "Name", "Odoo"),
        ),
    ],
)
def test_bounded_rich_leaf_renderers_use_labelled_summaries(
    render: str,
    command: str,
    result: dict[str, object],
    labels: tuple[str, ...],
) -> None:
    module_name, function_name = render.rsplit(".", 1)
    renderer = getattr(__import__(module_name, fromlist=[function_name]), function_name)
    rendered = renderer(success_document(command=command, result=cast("Any", result)))

    assert rendered.strip()
    assert all(label in rendered for label in labels)
    assert '"restored_database"' not in rendered
    assert '"bytes_written"' not in rendered
    assert "=" not in rendered


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
        result = CliRunner().invoke(cli, ["env", "checkout", "PROJ-123"])

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
        dry_result = CliRunner().invoke(cli, ["env", "checkout", "PROJ-123", "--dry-run", "--json"])

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
        run_result = CliRunner().invoke(cli, ["env", "checkout", "PROJ-123"])

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
                "checkout": ["env", "checkout", "PROJ-123"],
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
