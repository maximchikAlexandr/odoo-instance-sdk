from __future__ import annotations

import contextlib
import json
import sys
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import BackupCatalogError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.models import PostRestoreCheckpoint, StartConfig
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment import EnvironmentResource
from odoo_instance_sdk.resources.instance import OdooInstance


class _CompletionCatalog:
    def __init__(self, project_id: str, root: Path, target: str, checkpoint: str) -> None:
        self.claim = SimpleNamespace(state="active", project_id=project_id)
        self.row: dict[str, Any] = {
            "sequence": 1,
            "database_name": target,
            "backup_id": "backup-id",
            "source_kind": "local_archive",
            "source_sha256": "a" * 64,
            "cluster_id": "cluster-id",
            "data_directory": str(root / ".odcli" / "filestore"),
            "database_oid": "database-oid",
            "state": "incomplete",
            "post_restore_checkpoint": checkpoint,
        }
        self.fail_completion = False

    def _latest_restore_binding(self, _host: str, _port: int, _target: str) -> dict[str, Any]:
        return dict(self.row)

    def _get_postgres_cluster_by_id(self, _cluster_id: str) -> SimpleNamespace:
        return self.claim

    def _advance_restore_checkpoint(
        self,
        _host: str,
        _port: int,
        _target: str,
        *,
        expected_sequence: int,
        expected_checkpoint: PostRestoreCheckpoint,
        checkpoint: PostRestoreCheckpoint,
    ) -> int:
        assert self.row["sequence"] == expected_sequence
        assert self.row["post_restore_checkpoint"] == expected_checkpoint.value
        self.row["sequence"] += 1
        self.row["post_restore_checkpoint"] = checkpoint.value
        return int(self.row["sequence"])

    def _complete_restore_binding(self, *_args: object, **_kwargs: object) -> None:
        if self.fail_completion:
            raise BackupCatalogError("injected catalogue commit failure")
        self.row["state"] = "complete"
        self.row["post_restore_checkpoint"] = None


@pytest.fixture
def completion_setup(
    project_manifest: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str, ProjectConfig, _CompletionCatalog, EnvironmentResource]:
    from odoo_instance_sdk.internal.dbprep import completion
    from odoo_instance_sdk.resources.database import DatabaseResource
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    root = project_manifest
    filestore = root / ".odcli" / "filestore"
    source = root / "odoo.conf"
    source.write_text(
        source.read_text(encoding="utf-8").replace("/tmp/odoo_data", str(filestore)),
        encoding="utf-8",
    )
    project = ProjectConfig.load(root)
    target = "restored_db"
    cluster_id = "cluster-id"
    project_id = "restore-test"
    catalog = _CompletionCatalog(project_id, root, target, "reset_not_requested")
    cluster = MagicMock(spec=PostgresCluster)
    cluster.endpoint_host = "127.0.0.1"
    cluster.endpoint_port = 5432
    cluster._project_id = project_id
    cluster._restore_provenance.return_value = (cluster_id, str(filestore))

    client = MagicMock()
    client.get_catalog.return_value = catalog
    start_config = StartConfig.from_odoo_config(source)
    instance = OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            configured_database_names=(project.default_source_database or "old_db",),
            start_config=start_config,
            command_prefix=(sys.executable, str(project.odoo_bin)),
            default_cwd=root,
        ),
        _client=client,
    )
    client.instance.from_project.return_value = instance

    monkeypatch.setattr(completion, "_load_project", lambda _project: (project, root))
    monkeypatch.setattr(
        completion, "_wait_for_preparation_lock", lambda *_a, **_kw: contextlib.nullcontext()
    )
    monkeypatch.setattr(PostgresCluster, "from_project", lambda _root: cluster)
    monkeypatch.setattr(
        DatabaseResource,
        "list_inventory",
        lambda _self, _project: SimpleNamespace(
            databases=(SimpleNamespace(name=target, database_oid="database-oid"),)
        ),
    )
    filestore.mkdir(parents=True, exist_ok=True)
    resource = EnvironmentResource(_client=client)
    client.environments = resource
    return root, target, project, catalog, resource


def test_public_cli_completion_dry_run_has_no_project_or_temporary_file_writes(
    completion_setup: tuple[Path, str, ProjectConfig, _CompletionCatalog, EnvironmentResource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.internal import automation

    root, target, _project, _catalog, resource = completion_setup
    client = resource._client
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_kwargs: client)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: root)
    monkeypatch.setattr(
        automation,
        "list_modules",
        lambda *_args, **_kwargs: [SimpleNamespace(name="base")],
    )

    writes: list[tuple[str, str]] = []
    active = True
    root_prefix = str(root)

    def audit(event: str, args: tuple[object, ...]) -> None:
        if not active:
            return
        if event == "open" and args and str(args[0]).startswith(root_prefix):
            mode = str(args[1]) if len(args) > 1 else ""
            flags = int(args[2]) if len(args) > 2 and isinstance(args[2], int) else 0
            if any(value in mode for value in ("w", "a", "x", "+")) or flags & 3:
                writes.append((event, str(args[0])))
        elif event in {"os.remove", "os.chmod", "os.mkdir", "os.rename", "os.replace"}:
            for value in args:
                if isinstance(value, (str, bytes)) and str(value).startswith(root_prefix):
                    writes.append((event, str(value)))

    import sys

    sys.addaudithook(audit)
    result = CliRunner().invoke(
        cli,
        [
            "db",
            "restore",
            "--complete",
            target,
            "--update-module",
            "base",
            "--dry-run",
            "--format",
            "json",
        ],
    )
    active = False

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["dry_run"] is True
    assert document["result"]["observations"][0]["target_database"] == target
    assert writes == []


def test_public_cli_completion_revalidation_uses_captured_inventory_and_reset_processes(
    completion_setup: tuple[Path, str, ProjectConfig, _CompletionCatalog, EnvironmentResource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run real completion and child commands through deterministic process steps."""
    from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult, SubprocessExecutor
    from odoo_instance_sdk.models import PostRestoreCheckpoint
    from odoo_instance_sdk.project import PostgresProjectConfig
    from odoo_instance_sdk.resources.database import DatabaseResource

    root, target, _project, catalog, resource = completion_setup
    import msgspec

    compose_project = msgspec.structs.replace(
        _project,
        postgres=PostgresProjectConfig(mode="compose", image="postgres:16", port=5432),
    )
    generated_config = root / ".odcli" / "odoo.conf"
    generated_config.parent.mkdir(parents=True, exist_ok=True)
    generated_config.write_text("[options]\n", encoding="utf-8")
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.dbprep.completion._load_project",
        lambda _project: (compose_project, root),
    )
    monkeypatch.setattr(ProjectConfig, "load", classmethod(lambda _cls, _root: compose_project))
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    project_cluster = cast("Any", PostgresCluster.from_project(root))
    project_cluster.mode = "compose"
    project_cluster.endpoint_host = "127.0.0.1"
    project_cluster.endpoint_port = 5432
    project_cluster.password_file = root / ".odcli" / "postgres-password"
    project_cluster.password_file.write_text("postgres-secret\n", encoding="utf-8")
    catalog.row["database_oid"] = 12345
    catalog.row["post_restore_checkpoint"] = PostRestoreCheckpoint.RESET_PENDING.value
    client = cast("Any", resource._client)
    instance = client.instance.from_project.return_value
    instance._postgres_cluster = SimpleNamespace(
        _project_id="restore-test",
        endpoint_host="127.0.0.1",
        endpoint_port=5432,
        _user="odoo",
        _password="test-password",
        mode="compose",
        owned=True,
        ensure_running=lambda **_kwargs: None,
    )

    def result_for_step(step: object) -> ProcessResult:
        prepared = cast("PreparedStep", step)
        assert hasattr(prepared, "step_id")
        if prepared.step_id == "database.list.inventory":
            stdout = json.dumps(
                [
                    {
                        "name": target,
                        "database_oid": 12345,
                        "logical_size_bytes": 0,
                        "active_sessions": 0,
                    }
                ]
            )
        elif prepared.step_id == "database.prepare.default-switch.git.toplevel":
            stdout = str(root)
        elif prepared.step_id == "database.prepare.default-switch.git.tracked":
            return ProcessResult(
                argv=prepared.argv,
                returncode=1,
                stdout="",
                stderr="",
                duration=0.0,
                cwd=prepared.cwd,
                environment=prepared.environment,
            )
        elif prepared.step_id == "instance.shell_script":
            stdout = ""
        else:
            pytest.fail(f"unexpected child process step: {prepared.step_id}")
        return ProcessResult(
            argv=prepared.argv,
            returncode=0,
            stdout=stdout,
            stderr="",
            duration=0.0,
            cwd=prepared.cwd,
            environment=prepared.environment,
        )

    def execute(_self: SubprocessExecutor, step: object, **_kwargs: object) -> ProcessResult:
        return result_for_step(step)

    monkeypatch.setattr(SubprocessExecutor, "execute", execute)

    def inventory(self: DatabaseResource, project_root: str | Path) -> object:
        from odoo_instance_sdk.internal.proc import RecordingExecutor

        executor = RecordingExecutor(result_factory=result_for_step)
        return self.list_inventory_command(project_root, executor=executor).run()

    monkeypatch.setattr(DatabaseResource, "list_inventory", inventory)

    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.OdooClient", lambda **_kwargs: resource._client
    )
    monkeypatch.setattr("odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: root)
    result = CliRunner().invoke(
        cli,
        [
            "db",
            "restore",
            "--complete",
            target,
            "--yes",
            "--format",
            "json",
        ],
        env={"ODCLI_ADMIN_PASSWORD": "test-admin-password"},
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["ok"] is True
    assert document["result"]["completed_stages"] == ["admin_reset", "default_switch"]
    assert catalog.row["state"] == "complete"


@pytest.mark.parametrize(
    "drift",
    ["source_sha256", "database_oid", "data_directory", "sequence", "claim"],
)
def test_public_completion_revalidates_all_evidence_after_module_mutation(
    completion_setup: tuple[Path, str, ProjectConfig, _CompletionCatalog, EnvironmentResource],
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    from odoo_instance_sdk.resources.database import DatabaseResource
    from odoo_instance_sdk.resources.module import ModuleResource

    root, target, _project, catalog, resource = completion_setup
    executed: list[str] = []

    def update_modules(_self: object, _modules: object) -> Command[None]:
        def mutate(_context: object) -> None:
            executed.append("module_update")
            if drift == "claim":
                catalog.claim.project_id = "foreign-project"
            elif drift == "sequence":
                catalog.row["sequence"] += 1
            elif drift == "data_directory":
                catalog.row["data_directory"] = "/foreign/filestore"
            elif drift == "database_oid":
                catalog.row["database_oid"] = "changed-database-oid"
            else:
                catalog.row["source_sha256"] = "b" * 64

        return Command.create(ExecutionPlan(), mutate)

    def reset_admin(_self: object, **_kwargs: object) -> Command[None]:
        return Command.create(ExecutionPlan(), lambda _context: executed.append("admin_reset"))

    monkeypatch.setattr(ModuleResource, "update_command", update_modules)
    monkeypatch.setattr(DatabaseResource, "reset_admin_password_command", reset_admin)
    catalog.row["post_restore_checkpoint"] = PostRestoreCheckpoint.RESET_PENDING.value

    command = resource.complete_restore_command(
        root,
        target,
        update_modules=("base",),
        admin_password="test-secret",
    )
    with pytest.raises(BackupCatalogError, match="plan is stale"):
        command.run()

    assert executed == ["module_update"]
    assert catalog.row["state"] == "incomplete"
    assert ProjectConfig.load(root).default_source_database == "comerta"


def test_public_completion_compensates_project_files_when_catalogue_commit_fails(
    completion_setup: tuple[Path, str, ProjectConfig, _CompletionCatalog, EnvironmentResource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.internal.dbprep import completion
    from odoo_instance_sdk.internal.generated_config import project_generated_config_path

    root, target, project, catalog, resource = completion_setup
    catalog.fail_completion = True
    manifest_before = (root / ".odcli" / "project.toml").read_bytes()
    generated_config = project_generated_config_path(root)
    generated_config.parent.mkdir(parents=True, exist_ok=True)
    generated_before = b"previous generated database config\n"
    generated_config.write_bytes(generated_before)
    original_switch = getattr(completion, "switch_project_default")

    def publish_project_files(project_root: Path, switched: ProjectConfig) -> None:
        original_switch(project_root, switched)
        generated_config.write_bytes(b"new generated database config\n")

    monkeypatch.setattr(
        completion,
        "validate_generated_config_target",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(completion, "switch_project_default", publish_project_files)

    command = resource.complete_restore_command(root, target)
    with pytest.raises(BackupCatalogError, match="catalogue commit failure") as failure:
        command.run()

    restored = ProjectConfig.load(root)
    assert restored.default_source_database == project.default_source_database
    assert (root / ".odcli" / "project.toml").read_bytes() == manifest_before
    assert generated_config.read_bytes() == generated_before
    assert catalog.row["state"] == "incomplete"
    context = getattr(failure.value, "failure_context")
    assert context.published is False
    assert context.effective_default == project.default_source_database


def test_module_failure_suppresses_reset_and_publication(
    completion_setup: tuple[Path, str, ProjectConfig, _CompletionCatalog, EnvironmentResource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.resources.database import DatabaseResource
    from odoo_instance_sdk.resources.module import ModuleResource

    root, target, project, catalog, resource = completion_setup
    catalog.row["post_restore_checkpoint"] = PostRestoreCheckpoint.RESET_PENDING.value
    actions: list[str] = []

    def fail_modules(_self: object, _modules: object) -> Command[None]:
        def fail(_context: object) -> None:
            actions.append("module_update")
            raise RuntimeError("injected module update failure")

        return Command.create(ExecutionPlan(), fail)

    monkeypatch.setattr(ModuleResource, "update_command", fail_modules)
    monkeypatch.setattr(
        DatabaseResource,
        "reset_admin_password_command",
        lambda *_args, **_kwargs: Command.create(
            ExecutionPlan(), lambda _context: actions.append("admin_reset")
        ),
    )
    command = resource.complete_restore_command(
        root, target, update_modules=("base",), admin_password="test-secret"
    )

    with pytest.raises(RuntimeError, match="module update failure"):
        command.run()

    assert actions == ["module_update"]
    assert catalog.row["state"] == "incomplete"
    assert catalog.row["post_restore_checkpoint"] == PostRestoreCheckpoint.RESET_PENDING.value
    assert ProjectConfig.load(root).default_source_database == project.default_source_database


def test_reset_not_requested_does_not_build_or_run_reset_command(
    completion_setup: tuple[Path, str, ProjectConfig, _CompletionCatalog, EnvironmentResource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.resources.database import DatabaseResource

    root, target, _project, catalog, resource = completion_setup
    monkeypatch.setattr(
        DatabaseResource,
        "reset_admin_password_command",
        lambda *_args, **_kwargs: pytest.fail("reset command must not be built"),
    )

    result = resource.complete_restore_command(root, target).run()

    assert result.completed_stages == ("default_switch",)
    assert result.published is True
    assert catalog.row["state"] == "complete"
    assert catalog.row["post_restore_checkpoint"] is None


def test_completion_preserves_module_order_and_deduplicates_requests(
    completion_setup: tuple[Path, str, ProjectConfig, _CompletionCatalog, EnvironmentResource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.resources.database import DatabaseResource
    from odoo_instance_sdk.resources.module import ModuleResource

    root, target, _project, catalog, resource = completion_setup
    catalog.row["post_restore_checkpoint"] = PostRestoreCheckpoint.RESET_PENDING.value
    actions: list[object] = []

    def update_modules(_self: object, modules: Sequence[str]) -> Command[None]:
        requested = tuple(modules)
        return Command.create(
            ExecutionPlan(), lambda _context: actions.append(("module_update", requested))
        )

    monkeypatch.setattr(ModuleResource, "update_command", update_modules)
    monkeypatch.setattr(
        DatabaseResource,
        "reset_admin_password_command",
        lambda *_args, **_kwargs: Command.create(
            ExecutionPlan(), lambda _context: actions.append("admin_reset")
        ),
    )

    result = resource.complete_restore_command(
        root,
        target,
        update_modules=("mail", "base", "mail"),
        admin_password="test-secret",
    ).run()

    assert actions == [("module_update", ("mail", "base")), "admin_reset"]
    assert result.requested_modules == ("mail", "base")
    assert result.completed_stages == ("module_update", "admin_reset", "default_switch")


def test_completion_revalidates_after_reset_before_checkpoint_advance(
    completion_setup: tuple[Path, str, ProjectConfig, _CompletionCatalog, EnvironmentResource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.resources.database import DatabaseResource

    root, target, project, catalog, resource = completion_setup
    catalog.row["post_restore_checkpoint"] = PostRestoreCheckpoint.RESET_PENDING.value
    resets: list[str] = []

    def reset_admin(_self: object, **_kwargs: object) -> Command[None]:
        def mutate(_context: object) -> None:
            resets.append("admin_reset")
            catalog.row["database_oid"] = "changed-database-oid"

        return Command.create(ExecutionPlan(), mutate)

    monkeypatch.setattr(DatabaseResource, "reset_admin_password_command", reset_admin)
    command = resource.complete_restore_command(root, target, admin_password="test-secret")

    with pytest.raises(BackupCatalogError, match="plan is stale"):
        command.run()

    assert resets == ["admin_reset"]
    assert catalog.row["state"] == "incomplete"
    assert catalog.row["post_restore_checkpoint"] == PostRestoreCheckpoint.RESET_PENDING.value
    assert ProjectConfig.load(root).default_source_database == project.default_source_database


@pytest.mark.parametrize("owner_case", ["missing", "foreign"])
def test_completion_refuses_missing_or_foreign_restore_ownership(
    completion_setup: tuple[Path, str, ProjectConfig, _CompletionCatalog, EnvironmentResource],
    monkeypatch: pytest.MonkeyPatch,
    owner_case: str,
) -> None:
    root, target, project, catalog, resource = completion_setup
    before = (root / ".odcli" / "project.toml").read_bytes()
    if owner_case == "missing":
        monkeypatch.setattr(catalog, "_latest_restore_binding", lambda *_args: None)
        message = "no tracked restore binding"
    else:
        catalog.claim = SimpleNamespace(state="active", project_id="foreign")
        message = "not owned"

    with pytest.raises(BackupCatalogError, match=message):
        resource.complete_restore_command(root, target)

    assert (root / ".odcli" / "project.toml").read_bytes() == before
    assert ProjectConfig.load(root).default_source_database == project.default_source_database


def test_completion_rejects_stale_binding_before_any_mutation(
    completion_setup: tuple[Path, str, ProjectConfig, _CompletionCatalog, EnvironmentResource],
) -> None:
    root, target, project, catalog, resource = completion_setup
    command = resource.complete_restore_command(root, target)
    catalog.row["source_sha256"] = "c" * 64

    with pytest.raises(BackupCatalogError, match="plan is stale"):
        command.run()

    assert catalog.row["state"] == "incomplete"
    assert ProjectConfig.load(root).default_source_database == project.default_source_database


def test_completion_refuses_managed_filestore_data_directory_mismatch(
    completion_setup: tuple[Path, str, ProjectConfig, _CompletionCatalog, EnvironmentResource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.internal.dbprep import completion

    root, target, project, _catalog, resource = completion_setup
    before = (root / ".odcli" / "project.toml").read_bytes()
    (root / ".odcli" / "other-filestore").mkdir()
    monkeypatch.setattr(
        completion,
        "managed_filestore_path",
        lambda _config: root / ".odcli" / "other-filestore",
    )

    with pytest.raises(
        BackupCatalogError, match="does not match the recorded restore data directory"
    ):
        resource.complete_restore_command(root, target)

    assert (root / ".odcli" / "project.toml").read_bytes() == before
    assert ProjectConfig.load(root).default_source_database == project.default_source_database


def test_completion_lock_serializes_competing_publication_attempts(
    completion_setup: tuple[Path, str, ProjectConfig, _CompletionCatalog, EnvironmentResource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.internal.dbprep import completion

    root, target, _project, catalog, resource = completion_setup
    lock = threading.Lock()

    @contextlib.contextmanager
    def serialized_lock(*_args: object, **_kwargs: object) -> Any:
        with lock:
            yield

    monkeypatch.setattr(completion, "_wait_for_preparation_lock", serialized_lock)
    commands = [resource.complete_restore_command(root, target) for _ in range(2)]
    barrier = threading.Barrier(2)

    def execute(command: Any) -> str:
        barrier.wait(timeout=5)
        try:
            command.run()
        except BackupCatalogError as error:
            return str(error)
        return "completed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(execute, commands))

    assert outcomes.count("completed") == 1
    assert sum("plan is stale" in outcome for outcome in outcomes) == 1
    assert catalog.row["state"] == "complete"
