from __future__ import annotations

import json
import multiprocessing as mp
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import msgspec
import pytest

from odoo_instance_sdk import (
    OdooInstanceSdkError,
    PgAdminDatabaseNotFoundError,
    PgAdminEnvironmentNotFoundError,
    PgAdminNotEligibleError,
    PgAdminOpenResult,
    PgAdminOpenState,
    PgAdminUnavailableError,
)
from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.exceptions import (
    DatabaseManagerUnavailableError,
    EnvironmentConflictError,
    PlanValidationError,
)
from odoo_instance_sdk.execution import Command, ExecutionPlan, ProcessStep
from odoo_instance_sdk.internal.proc import (
    PreparedProcess,
    PreparedStep,
    ProcessResultLike,
    RecordingExecutor,
    RunContext,
    active_context,
)
from odoo_instance_sdk.internal.proc.executor import ProcessResult
from odoo_instance_sdk.models import PostgresClusterState
from odoo_instance_sdk.resources.environment import (
    DevelopmentEnvironment,
    EnvironmentDatabaseMode,
    EnvironmentResource,
    EnvironmentState,
    _capture_checkout_stage,
    _CheckoutPlanningState,
    _ExpressionApi,
    _find_odoo_requirements,
    _pgadmin_command_steps,
    _planning_result,
    _PlanningOutcome,
    _process_stderr,
    _rebase_requirement_paths,
    _restore_audit_backup,
    _validate_checkout_stage,
    _validate_owned_artifact,
)
from odoo_instance_sdk.resources.postgres import PostgresCluster

_MP_RESOURCE: Any = None
_MP_ENV: Any = None
_MP_STORE: Any = None
_MP_STORE_LOCK: Any = None
_MP_BARRIER: Any = None
_MP_PHASE_BARRIER: Any = None


def _run_public_pgadmin_in_process(result_queue: Any) -> None:  # noqa: C901
    """Run a captured public command with an optional phase-completion barrier."""
    from odoo_instance_sdk.internal import pgadmin_container, pgadmin_files

    try:
        recording = RecordingExecutor()

        def result_factory(prepared: PreparedProcess) -> ProcessResult:  # noqa: C901
            argv = tuple(prepared.argv)
            step_id = prepared.step_id
            if step_id in {"pgadmin.postgres.status.ps", "pgadmin.identity.ps"}:
                return ProcessResult(
                    argv, 0, '{"Service":"postgres","ID":"postgres-id"}\n', "", 0.0, None, ()
                )
            if step_id == "pgadmin.identity.inspect":
                return ProcessResult(
                    argv,
                    0,
                    json.dumps(
                        {
                            "Name": "/odcli_pg_project-postgres-1",
                            "Config": {
                                "User": "odoo",
                                "Labels": {"com.docker.compose.project": "odcli_pg_project"},
                            },
                            "NetworkSettings": {"Networks": {"odcli_pg_project_default": {}}},
                        }
                    ),
                    "",
                    0.0,
                    None,
                    (),
                )
            if step_id == "pgadmin.identity.network":
                return ProcessResult(
                    argv,
                    0,
                    '{"Labels":{"com.docker.compose.project":"odcli_pg_project"}}',
                    "",
                    0.0,
                    None,
                    (),
                )
            if step_id in {
                "pgadmin.container.inspect.0",
                "pgadmin.reconciliation.inspect.0",
                "pgadmin.container.inspect.1",
                "pgadmin.container.inspect.2",
                "pgadmin.container.refresh.inspect",
            }:
                container = _MP_STORE["container"]
                if container is None:
                    return ProcessResult(argv, 1, "", "No such container", 0.0, None, ())
                return ProcessResult(argv, 0, json.dumps(container), "", 0.0, None, ())
            if step_id == "pgadmin.container.run":
                args = list(argv)
                labels: dict[str, str] = {}
                for index, value in enumerate(args[:-1]):
                    if value == "--label":
                        key, label_value = args[index + 1].split("=", 1)
                        labels[key] = label_value
                mounts: list[dict[str, object]] = []
                for index, value in enumerate(args[:-1]):
                    if value == "--mount":
                        fields = dict(
                            item.split("=", 1)
                            for item in args[index + 1].removeprefix("type=bind,").split(",")
                            if "=" in item
                        )
                        mounts.append(
                            {
                                "Source": fields["source"],
                                "Destination": fields["destination"],
                                "RW": "readonly" not in args[index + 1],
                            }
                        )
                port = next(value for value in args if value.startswith("127.0.0.1:"))
                host_port = port.split(":", 2)[1]
                network = args[args.index("--network") + 1]
                container = {
                    "Id": "pgadmin-id",
                    "Name": f"/{pgadmin_files.PGADMIN_CONTAINER_NAME}",
                    "Config": {
                        "Image": pgadmin_files.PGADMIN_IMAGE,
                        "User": str(pgadmin_files.PGADMIN_RUNTIME_UID),
                        "Env": [
                            "PGADMIN_CONFIG_SERVER_MODE=False",
                            "PGADMIN_REPLACE_SERVERS_ON_STARTUP=True",
                            f"PGADMIN_DEFAULT_EMAIL={pgadmin_files.PGADMIN_DEFAULT_EMAIL}",
                            f"PGADMIN_DEFAULT_PASSWORD_FILE={pgadmin_files.PGADMIN_PASSWORD_DESTINATION}",
                            f"PGPASS_FILE={pgadmin_files.PGADMIN_PGPASS_DESTINATION}",
                        ],
                        "Labels": labels,
                    },
                    "State": {"Running": True},
                    "NetworkSettings": {"Networks": {network: {}}},
                    "Mounts": mounts,
                    "HostConfig": {
                        "PortBindings": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": host_port}]}
                    },
                }
                with _MP_STORE_LOCK:
                    if _MP_STORE["container"] is None:
                        _MP_STORE["container"] = container
                        _MP_STORE["runs"] += 1
                return ProcessResult(argv, 0, "pgadmin-id\n", "", 0.0, None, ())
            if step_id == "pgadmin.container.verify":
                return ProcessResult(
                    argv,
                    0,
                    json.dumps(
                        [
                            {
                                "host": "odcli_pg_project-postgres-1",
                                "port": 5432,
                                "username": "odoo",
                                "maintenance_db": "odoo",
                                "db_res": "odoo",
                            }
                        ]
                    ),
                    "",
                    0.0,
                    None,
                    (),
                )
            return ProcessResult(argv, 0, "", "", 0.0, None, ())

        recording.result_factory = result_factory
        command = _MP_RESOURCE.open_pgadmin_command(_MP_ENV, executor=recording)
        plan_ids = tuple(step.step_id for step in command.plan.steps)
        phase_plan_ids = plan_ids[: plan_ids.index("pgadmin.reconciliation.inspect.0")]
        reconciliation_plan_ids = plan_ids[plan_ids.index("pgadmin.reconciliation.inspect.0") :]
        consumed_ids: list[str] = []
        reconciliation_deadlines: list[float] = []
        original_process_prepared = RunContext.process_prepared
        original_consume = RunContext._consume
        original_reconcile = pgadmin_container.reconcile_container

        def record_process(
            context: RunContext[object], requested: PreparedStep
        ) -> ProcessResultLike:
            consumed_ids.append(requested.step_id)
            return original_process_prepared(context, requested)

        def record_consume(context: RunContext[object], step_id: str) -> object:
            consumed_ids.append(step_id)
            return original_consume(context, step_id)

        def record_reconcile(*args: object, **kwargs: object) -> PgAdminOpenResult:
            deadline = kwargs.get("deadline")
            assert isinstance(deadline, float)
            reconciliation_deadlines.append(deadline)
            return cast("Callable[..., PgAdminOpenResult]", original_reconcile)(*args, **kwargs)

        setattr(RunContext, "process_prepared", record_process)
        setattr(RunContext, "_consume", record_consume)
        pgadmin_container.reconcile_container = record_reconcile
        _MP_BARRIER.wait(timeout=10)
        result = command.run()
        if _MP_PHASE_BARRIER is not None:
            _MP_PHASE_BARRIER.wait(timeout=10)
        result_queue.put(
            (
                "ok",
                result.state.value,
                result.url,
                phase_plan_ids,
                reconciliation_plan_ids,
                tuple(consumed_ids),
                tuple(reconciliation_deadlines),
            )
        )
    except BaseException as exc:
        result_queue.put(("error", type(exc).__name__, str(exc)))


def _environment(
    *,
    state: EnvironmentState = EnvironmentState.READY,
    database: str | None = "odoo",
    copy: bool = False,
) -> DevelopmentEnvironment:
    return DevelopmentEnvironment(
        id=uuid.uuid4(),
        name="feature",
        repository_root="/repo",
        git_common_dir="/repo/.git",
        branch="feature",
        base_ref="HEAD",
        worktree_path="/repo/.worktrees/feature",
        generated_config_path="/repo/.odcli/environments/feature/odoo.conf",
        python_environment_path="/venv/bin/python",
        python_environment_owned=False,
        dependency_lock_path="/repo/.odcli/environments/feature/requirements.lock",
        http_interface="127.0.0.1",
        http_port=8069,
        db_mode=EnvironmentDatabaseMode.COPY if copy else EnvironmentDatabaseMode.SHARED,
        source_db_name=None if copy else database,
        target_db_name=database if copy else None,
        state=state,
        created_at=datetime.now(UTC),
    )


def _resource() -> tuple[EnvironmentResource, MagicMock]:
    client = MagicMock()
    return EnvironmentResource(_client=client), client


def _healthy_cluster() -> SimpleNamespace:
    return SimpleNamespace(
        mode="compose",
        owned=True,
        status=MagicMock(return_value=PostgresClusterState.HEALTHY),
    )


def _install_healthy_preflight(
    monkeypatch: pytest.MonkeyPatch,
    resource: EnvironmentResource,
    *,
    exists: bool = True,
) -> tuple[MagicMock, MagicMock, SimpleNamespace]:
    instance = MagicMock()
    instance.databases.exists.return_value = exists
    from_environment = cast("MagicMock", resource._client.instance.from_environment)
    from_environment.return_value = instance
    cluster = _healthy_cluster()
    monkeypatch.setattr(
        PostgresCluster,
        "from_project",
        staticmethod(lambda path: cluster),
    )
    return instance, from_environment, cluster


def test_pgadmin_linux_command_reserves_acl_steps_before_lifecycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Linux's mutation-dependent ACL work has an explicit phase boundary."""
    from odoo_instance_sdk.internal import pgadmin_files

    cluster = SimpleNamespace(
        mode="compose",
        compose_file=tmp_path / "compose.yaml",
        compose_project_name="odcli_pg_project",
        _user="odoo",
    )
    selector = _environment()
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.environment._pgadmin_cluster_snapshot",
        lambda selected: cluster,
    )
    monkeypatch.setattr(pgadmin_files, "_linux", lambda: True)
    monkeypatch.setattr(pgadmin_files, "select_port", lambda paths: 15555)

    steps = _pgadmin_command_steps(selector)

    assert all(not step.step_id.startswith("pgadmin.acl.") for step in steps)
    assert all(token not in repr(steps) for token in ("<runtime>", "<secret>"))


def test_requirement_path_helpers_preserve_absolute_and_external_inputs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo.mkdir()
    worktree.mkdir()
    absolute = tmp_path / "absolute.txt"

    rebased = _rebase_requirement_paths(
        ["requirements.txt", str(absolute), "../outside.txt"], repo, worktree
    )

    assert rebased == [str(worktree / "requirements.txt"), str(absolute), "../outside.txt"]
    assert _find_odoo_requirements(worktree) is None
    requirements = worktree / "odoo" / "requirements.txt"
    requirements.parent.mkdir()
    requirements.touch()
    assert _find_odoo_requirements(worktree) == requirements


def test_checkout_stage_helpers_keep_typed_error_branches() -> None:
    empty_state = cast(
        "_CheckoutPlanningState",
        SimpleNamespace(
            private=SimpleNamespace(
                branch="", worktree_argv=(), db_mode=EnvironmentDatabaseMode.SHARED
            )
        ),
    )
    assert _validate_checkout_stage(empty_state).error is not None

    no_command_state = cast(
        "_CheckoutPlanningState",
        SimpleNamespace(
            private=SimpleNamespace(
                branch="feature", worktree_argv=(), db_mode=EnvironmentDatabaseMode.SHARED
            )
        ),
    )
    assert _validate_checkout_stage(no_command_state).error is not None

    copy_state = cast(
        "_CheckoutPlanningState",
        SimpleNamespace(
            private=SimpleNamespace(
                branch="feature",
                worktree_argv=("git",),
                db_mode=EnvironmentDatabaseMode.COPY,
                target_database=None,
            )
        ),
    )
    assert _validate_checkout_stage(copy_state).error is not None

    valid_state = cast(
        "_CheckoutPlanningState",
        SimpleNamespace(
            private=SimpleNamespace(
                branch="feature",
                worktree_argv=("git",),
                db_mode=EnvironmentDatabaseMode.SHARED,
            )
        ),
    )
    assert _validate_checkout_stage(valid_state).state is valid_state
    incomplete_state = cast(
        "_CheckoutPlanningState", SimpleNamespace(public=None, execution_plan=None)
    )
    assert _capture_checkout_stage(incomplete_state).error is not None

    api = cast(
        "_ExpressionApi",
        SimpleNamespace(Ok=lambda value: ("ok", value), Error=lambda error: ("error", error)),
    )
    assert _planning_result(api, _PlanningOutcome(error=PlanValidationError("invalid")))
    assert _planning_result(api, _PlanningOutcome())
    assert _process_stderr(cast("ProcessResult", SimpleNamespace(stderr=b"binary"))) == "binary"
    assert _process_stderr(cast("ProcessResult", SimpleNamespace(stderr="text"))) == "text"


def test_environment_artifact_and_restore_helpers_fail_closed(tmp_path: Path) -> None:
    expected = tmp_path / "artifact"
    expected.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(expected, target_is_directory=True)
    with pytest.raises(EnvironmentConflictError):
        _validate_owned_artifact(linked, linked, "dir")
    with pytest.raises(EnvironmentConflictError):
        _validate_owned_artifact(expected, expected, "file")

    typed_client = cast("OdooClient", SimpleNamespace())
    assert (
        _restore_audit_backup(typed_client, {"db_port": "not-a-port"}, None, available=True) is None
    )
    assert (
        _restore_audit_backup(typed_client, {"db_port": "not-a-port"}, "odoo", available=True)
        is None
    )


def test_open_pgadmin_uses_only_selector_and_returns_typed_lifecycle_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, _client = _resource()
    env = _environment(copy=True)
    command = MagicMock()
    command.run.return_value = PgAdminOpenResult(
        state=PgAdminOpenState.STARTED,
        url="http://127.0.0.1:5050",
    )
    command_factory = MagicMock(return_value=command)
    monkeypatch.setattr(EnvironmentResource, "open_pgadmin_command", command_factory)

    result = resource.open_pgadmin(env)

    assert result.state is PgAdminOpenState.STARTED
    assert result.url == "http://127.0.0.1:5050"
    command_factory.assert_called_once_with(env)
    command.run.assert_called_once_with()


@pytest.mark.parametrize(
    "state",
    [
        EnvironmentState.CREATING,
        EnvironmentState.FAILED,
        EnvironmentState.REMOVING,
        EnvironmentState.CLEANUP_FAILED,
        EnvironmentState.REMOVED,
    ],
)
def test_open_pgadmin_rejects_non_ready_without_preflight_mutation(
    state: EnvironmentState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, _client = _resource()

    with pytest.raises(PgAdminNotEligibleError) as exc_info:
        resource.open_pgadmin(_environment(state=state))

    assert str(exc_info.value) == "pgAdmin is not eligible for this environment"
    cast("MagicMock", resource._client.instance.from_environment).assert_not_called()


def test_open_pgadmin_rejects_unresolved_database_before_cluster_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, _client = _resource()
    from_project = MagicMock()
    monkeypatch.setattr(PostgresCluster, "from_project", from_project)

    with pytest.raises(PgAdminNotEligibleError):
        resource.open_pgadmin(_environment(database=None))

    from_project.assert_not_called()
    cast("MagicMock", resource._client.instance.from_environment).assert_not_called()


def test_open_pgadmin_maps_missing_catalog_environment_to_sanitized_error() -> None:
    resource, client = _resource()
    catalog = MagicMock()
    catalog.get_environment.return_value = None
    catalog.list_environments.return_value = []
    client.get_catalog.return_value = catalog

    with pytest.raises(PgAdminEnvironmentNotFoundError) as exc_info:
        resource.open_pgadmin("submitted-value-without-details")

    assert str(exc_info.value) == "environment was not found"
    assert "submitted-value" not in str(exc_info.value)


@pytest.mark.parametrize(
    "mode,owned",
    [("external", False), ("compose", False)],
)
def test_open_pgadmin_rejects_non_owned_compose_cluster_without_mutation(
    mode: str,
    owned: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, _client = _resource()
    cluster = SimpleNamespace(mode=mode, owned=owned, status=MagicMock())
    monkeypatch.setattr(
        PostgresCluster,
        "from_project",
        staticmethod(lambda path: cluster),
    )

    with pytest.raises(PgAdminNotEligibleError):
        resource.open_pgadmin(_environment())

    cluster.status.assert_not_called()
    cast("MagicMock", resource._client.instance.from_environment).assert_not_called()


@pytest.mark.parametrize(
    "state",
    [PostgresClusterState.STOPPED, PostgresClusterState.UNHEALTHY],
)
def test_open_pgadmin_rejects_ineligible_cluster_state_without_mutation(
    state: PostgresClusterState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, _client = _resource()
    cluster = SimpleNamespace(
        mode="compose",
        owned=True,
        status=MagicMock(return_value=state),
    )
    monkeypatch.setattr(
        PostgresCluster,
        "from_project",
        staticmethod(lambda path: cluster),
    )

    with pytest.raises(PgAdminNotEligibleError):
        resource.open_pgadmin(_environment())

    cast("MagicMock", resource._client.instance.from_environment).assert_not_called()


@pytest.mark.parametrize(
    "state",
    [PostgresClusterState.UNKNOWN, PostgresClusterState.UNREACHABLE],
)
def test_open_pgadmin_maps_inconclusive_cluster_to_sanitized_unavailable(
    state: PostgresClusterState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, _client = _resource()
    cluster = SimpleNamespace(
        mode="compose",
        owned=True,
        status=MagicMock(return_value=state),
    )
    monkeypatch.setattr(
        PostgresCluster,
        "from_project",
        staticmethod(lambda path: cluster),
    )

    with pytest.raises(PgAdminUnavailableError) as exc_info:
        resource.open_pgadmin(_environment())

    assert str(exc_info.value) == "pgAdmin is unavailable"
    cast("MagicMock", resource._client.instance.from_environment).assert_not_called()


def test_open_pgadmin_maps_missing_database_and_never_enters_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, _client = _resource()
    instance, _from_environment, _cluster = _install_healthy_preflight(
        monkeypatch, resource, exists=False
    )

    with pytest.raises(PgAdminDatabaseNotFoundError) as exc_info:
        resource.open_pgadmin(_environment())

    assert str(exc_info.value) == "selected database was not found"
    instance.databases.exists.assert_called_once_with("odoo")


def test_open_pgadmin_maps_inconclusive_database_to_sanitized_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, _client = _resource()
    instance, _from_environment, _cluster = _install_healthy_preflight(monkeypatch, resource)
    instance.databases.exists.side_effect = DatabaseManagerUnavailableError(
        "secret /internal/config detail"
    )

    with pytest.raises(PgAdminUnavailableError) as exc_info:
        resource.open_pgadmin(_environment())

    assert str(exc_info.value) == "pgAdmin is unavailable"
    assert "/internal/config" not in str(exc_info.value)


def test_pgadmin_errors_are_public_sdk_errors() -> None:
    assert issubclass(PgAdminEnvironmentNotFoundError, OdooInstanceSdkError)
    assert issubclass(PgAdminNotEligibleError, OdooInstanceSdkError)
    assert issubclass(PgAdminDatabaseNotFoundError, OdooInstanceSdkError)
    assert issubclass(PgAdminUnavailableError, OdooInstanceSdkError)


def test_open_pgadmin_command_manifest_is_consumed_by_one_recording_ledger(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    resource, _client = _resource()
    env = _environment()
    compose = SimpleNamespace(
        mode="compose",
        compose_file=tmp_path / "compose.yaml",
        compose_project_name="odcli_pg_project",
        _user="odoo",
    )
    monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(lambda _path: compose))
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pgadmin_files.PgAdminPaths.from_defaults",
        classmethod(
            lambda cls: SimpleNamespace(
                admin_password=tmp_path / "admin-password",
                pgpass=tmp_path / "pgpass",
                servers_json=tmp_path / "servers.json",
                data_dir=tmp_path / "data",
                lock=tmp_path / "locks" / "pgadmin.lock",
            )
        ),
    )
    executor = RecordingExecutor()

    carrier = MagicMock()

    def reconcile(context: RunContext[PgAdminOpenResult]) -> PgAdminOpenResult:
        context.process("pgadmin.postgres.status.ps")
        context.process("pgadmin.postgres.status.health")
        return PgAdminOpenResult(state=PgAdminOpenState.STARTED, url="http://127.0.0.1:5050")

    carrier.reconcile.side_effect = lambda context, **_: reconcile(context)

    monkeypatch.setattr(
        EnvironmentResource, "_open_pgadmin_impl", lambda *_args, **_kwargs: carrier
    )
    command = resource.open_pgadmin_command(env, executor=executor)

    assert command.plan.steps
    assert any(isinstance(step, ProcessStep) for step in command.plan.steps)
    assert [step.step_id for step in command.plan.process_steps] == [
        "pgadmin.postgres.status.ps",
        "pgadmin.postgres.status.health",
    ]
    assert command.run().state is PgAdminOpenState.STARTED
    assert [step.step_id for step in executor.executed] == [
        "pgadmin.postgres.status.ps",
        "pgadmin.postgres.status.health",
    ]
    carrier.reconcile.assert_called_once_with(
        cast("RunContext[PgAdminOpenResult]", carrier.reconcile.call_args.args[0]),
        lock_held=True,
    )


def test_open_pgadmin_phase_command_returns_only_safe_explicit_reconciliation_handle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    resource, _client = _resource()
    env = _environment()
    cluster = SimpleNamespace(
        mode="compose",
        compose_file=tmp_path / "compose.yaml",
        compose_project_name="odcli_pg_project",
        _user="odoo",
    )
    monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(lambda _path: cluster))
    recording = RecordingExecutor()
    result = PgAdminOpenResult(state=PgAdminOpenState.STARTED, url="http://127.0.0.1:5050")
    reconciliation = Command.create(ExecutionPlan(), lambda _context: result, executor=recording)
    carrier = MagicMock()
    carrier.reconciliation_command.return_value = reconciliation

    def provision(*_args: object, **_kwargs: object) -> object:
        context = active_context()
        assert context is not None
        context.process("pgadmin.postgres.status.ps")
        context.process("pgadmin.postgres.status.health")
        return carrier

    monkeypatch.setattr(EnvironmentResource, "_open_pgadmin_impl", provision)

    handle = resource.open_pgadmin_phase_command(env, executor=recording).run()

    assert handle.reconciliation_command() is reconciliation
    assert not any(
        hasattr(handle, name)
        for name in ("executor", "runner", "steps", "secret_values", "password", "fingerprint")
    )
    public = msgspec.to_builtins(handle)
    assert set(public) == {"reconciliation"}
    assert "secret" not in repr(handle)
    assert "secret" not in repr(public)


def test_open_pgadmin_command_construction_has_no_lifecycle_state_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from odoo_instance_sdk.config import InstanceConfig
    from odoo_instance_sdk.internal import pgadmin_files
    from odoo_instance_sdk.internal.postgres_compose import SubprocessComposeRunner
    from odoo_instance_sdk.resources.instance import OdooInstance

    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    data_root = tmp_path / "data-root"
    data_root.mkdir()
    paths = pgadmin_files.PgAdminPaths(
        root=data_root / "pgadmin",
        private_dir=data_root / "pgadmin" / "private",
        data_dir=data_root / "pgadmin" / "data",
        admin_password=data_root / "pgadmin" / "private" / "admin-password",
        pgpass=data_root / "pgadmin" / "private" / ".pgpass",
        servers_json=data_root / "pgadmin" / "private" / "servers.json",
        metadata=data_root / "pgadmin" / "private" / "metadata.json",
        lock=tmp_path / "locks" / "pgadmin.lock",
    )
    cluster = PostgresCluster(
        _repository_root=tmp_path,
        _project_id="project",
        _mode="compose",
        _endpoint_host="127.0.0.1",
        _endpoint_port=15432,
        _image="postgres:16@sha256:" + "a" * 64,
        _user="odoo",
        _compose_runner=SubprocessComposeRunner(),
    )
    instance = OdooInstance(
        config=InstanceConfig(base_url="http://127.0.0.1:8069", db_password="secret"),
        _client=cast("OdooClient", _resource()[1]),
    )
    resource, _client = _resource()
    monkeypatch.setattr(PostgresCluster, "compose_file", property(lambda _self: compose_file))
    monkeypatch.setattr(PostgresCluster, "_compose_file", lambda _self: compose_file)
    monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(lambda _path: cluster))
    monkeypatch.setattr(
        pgadmin_files.PgAdminPaths, "from_defaults", classmethod(lambda _cls: paths)
    )
    monkeypatch.setattr(
        EnvironmentResource, "_configured_pgadmin_instance", lambda _self, _env: instance
    )
    monkeypatch.setattr(
        EnvironmentResource, "_require_pgadmin_database", lambda _self, _instance, _database: None
    )

    def fail_lock(**_: object) -> object:
        raise AssertionError("command construction acquired the pgAdmin lifecycle lock")

    monkeypatch.setattr(pgadmin_files, "pgadmin_lock", fail_lock)

    command = resource.open_pgadmin_command(_environment())

    assert command.plan.fingerprint
    assert not paths.root.exists()
    assert not paths.lock.exists()
    assert not (paths.private_dir / ".fingerprint-key").exists()


def test_open_pgadmin_real_cluster_uses_one_captured_docker_ledger(  # noqa: C901
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The public pgAdmin command owns every Docker child it launches."""
    from odoo_instance_sdk.config import InstanceConfig
    from odoo_instance_sdk.internal import pgadmin_container, pgadmin_files
    from odoo_instance_sdk.internal.postgres_compose import SubprocessComposeRunner
    from odoo_instance_sdk.resources.instance import OdooInstance

    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    data_root = tmp_path / "data-root"
    data_root.mkdir()
    paths = pgadmin_files.PgAdminPaths(
        root=data_root / "pgadmin",
        private_dir=data_root / "pgadmin" / "private",
        data_dir=data_root / "pgadmin" / "data",
        admin_password=data_root / "pgadmin" / "private" / "admin-password",
        pgpass=data_root / "pgadmin" / "private" / ".pgpass",
        servers_json=data_root / "pgadmin" / "private" / "servers.json",
        metadata=data_root / "pgadmin" / "private" / "metadata.json",
        lock=tmp_path / "locks" / "pgadmin.lock",
    )
    cluster = PostgresCluster(
        _repository_root=tmp_path,
        _project_id="project",
        _mode="compose",
        _endpoint_host="127.0.0.1",
        _endpoint_port=15432,
        _image="postgres:16@sha256:" + "a" * 64,
        _user="odoo",
        _compose_runner=SubprocessComposeRunner(),
    )
    resource, _client = _resource()
    env = _environment()
    instance = OdooInstance(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            db_password="database-secret",
            db_host="127.0.0.1",
            db_port=5432,
            db_user="odoo",
        ),
        _client=cast("OdooClient", _client),
    )
    monkeypatch.setattr(PostgresCluster, "compose_file", property(lambda _self: compose_file))
    monkeypatch.setattr(PostgresCluster, "_compose_file", lambda _self: compose_file)
    monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(lambda _path: cluster))
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pgadmin_files.PgAdminPaths.from_defaults",
        classmethod(lambda _cls: paths),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pgadmin_files.get_data_root", lambda **_: data_root
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.pgadmin_files._linux", lambda: True)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pgadmin_files.shutil.which", lambda _: "/bin/tool"
    )
    monkeypatch.setattr("odoo_instance_sdk.resources.postgres.docker_available", lambda: True)
    monkeypatch.setattr(
        EnvironmentResource, "_configured_pgadmin_instance", lambda _self, _env: instance
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _name: "/bin/psql"
    )
    monkeypatch.setattr(
        instance.databases.__class__,
        "list",
        lambda _self: (_ for _ in ()).throw(
            DatabaseManagerUnavailableError("database manager down")
        ),
    )
    monkeypatch.setattr(pgadmin_container, "_wait_ready", lambda _port, *, deadline: None)
    recording = RecordingExecutor()
    first_command = resource.open_pgadmin_command(env, executor=recording)
    assert not paths.root.exists()
    captured = resource._capture_pgadmin_command_inputs(env, cluster)
    assert captured is not None
    assert captured.identity.port == 5432
    paths.root.mkdir(mode=0o710)
    paths.private_dir.mkdir(mode=0o710)
    paths.root.chmod(0o710)
    paths.private_dir.chmod(0o710)

    configured = {
        "Config": {
            "Image": pgadmin_files.PGADMIN_IMAGE,
            "User": str(pgadmin_files.PGADMIN_RUNTIME_UID),
            "Env": [
                "PGADMIN_CONFIG_SERVER_MODE=False",
                "PGADMIN_REPLACE_SERVERS_ON_STARTUP=True",
                f"PGADMIN_DEFAULT_EMAIL={pgadmin_files.PGADMIN_DEFAULT_EMAIL}",
                f"PGADMIN_DEFAULT_PASSWORD_FILE={pgadmin_files.PGADMIN_PASSWORD_DESTINATION}",
                f"PGPASS_FILE={pgadmin_files.PGADMIN_PGPASS_DESTINATION}",
            ],
            "Labels": {
                pgadmin_files.PGADMIN_LABEL_MANAGED: "true",
                pgadmin_files.PGADMIN_LABEL_FINGERPRINT: "not-used-before-reconciliation",
                pgadmin_files.PGADMIN_LABEL_NETWORK: captured.identity.network,
            },
        },
        "State": {"Running": True},
        "NetworkSettings": {"Networks": {captured.identity.network: {}}},
        "Mounts": [
            {
                "Source": str(paths.admin_password),
                "Destination": pgadmin_files.PGADMIN_PASSWORD_DESTINATION,
                "RW": False,
            },
            {
                "Source": str(paths.pgpass),
                "Destination": pgadmin_files.PGADMIN_PGPASS_DESTINATION,
                "RW": False,
            },
            {
                "Source": str(paths.servers_json),
                "Destination": pgadmin_files.PGADMIN_SERVERS_DESTINATION,
                "RW": False,
            },
            {
                "Source": str(paths.data_dir),
                "Destination": pgadmin_files.PGADMIN_DATA_DESTINATION,
                "RW": True,
            },
        ],
        "HostConfig": {"PortBindings": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5050"}]}},
    }
    current_container: dict[str, object] | None = None
    result_map: dict[str, ProcessResultLike] = {
        "pgadmin.postgres.status.ps": ProcessResult(
            argv=(),
            returncode=0,
            stdout='{"Service":"postgres","ID":"postgres-id"}\n',
            stderr="",
            duration=0.0,
            cwd=None,
            environment=(),
        ),
        "pgadmin.postgres.status.health": ProcessResult((), 0, "", "", 0.0, None, ()),
        "pgadmin.identity.ps": ProcessResult(
            (), 0, '{"Service":"postgres","ID":"postgres-id"}\n', "", 0.0, None, ()
        ),
        "pgadmin.identity.inspect": ProcessResult(
            (),
            0,
            '{"Name":"/odcli_pg_project-postgres-1","Config":{"User":"odoo",'
            '"Labels":{"com.docker.compose.project":"odcli_pg_project"}},'
            '"NetworkSettings":{"Networks":{"odcli_pg_project_default":{}}}}',
            "",
            0.0,
            None,
            (),
        ),
        "pgadmin.identity.network": ProcessResult(
            (), 0, '{"Labels":{"com.docker.compose.project":"odcli_pg_project"}}', "", 0.0, None, ()
        ),
        "pgadmin.container.refresh": ProcessResult((), 0, "", "", 0.0, None, ()),
        "pgadmin.container.verify": ProcessResult(
            (),
            0,
            json.dumps(
                [
                    {
                        "host": captured.identity.host,
                        "port": captured.identity.port,
                        "username": captured.identity.user,
                        "maintenance_db": "odoo",
                        "db_res": "odoo",
                    }
                ]
            ),
            "",
            0.0,
            None,
            (),
        ),
    }

    def result_factory(prepared: PreparedProcess) -> ProcessResultLike:  # noqa: C901
        argv = prepared.argv
        if prepared.step_id == "pgadmin.database.exists.psql":
            return ProcessResult(argv, 0, "1\n", "", 0.0, None, ())
        if argv and argv[0] == "setfacl":
            return ProcessResult(argv, 0, "", "", 0.0, None, ())
        if argv and argv[0] == "getfacl":
            path = Path(argv[-1])
            if path == paths.data_dir:
                output = sorted(pgadmin_files._directory_acl(0o770))
                output.extend(
                    f"default:{entry}" for entry in sorted(pgadmin_files._default_directory_acl())
                )
            elif path == paths.root or path == paths.private_dir:
                output = sorted(pgadmin_files._directory_acl(0o710))
            elif path == paths.private_dir / ".fingerprint-key":
                output = sorted(pgadmin_files._fingerprint_key_acl())
            else:
                output = sorted(pgadmin_files._file_acl())
            return ProcessResult(argv, 0, "\n".join(output), "", 0.0, None, ())
        nonlocal current_container
        if prepared.step_id in {
            "pgadmin.container.inspect.0",
            "pgadmin.reconciliation.inspect.0",
            "pgadmin.container.inspect.1",
        }:
            if current_container is None:
                return ProcessResult(argv, 1, "", "No such container", 0.0, None, ())
            return ProcessResult(argv, 0, json.dumps(current_container), "", 0.0, None, ())
        if prepared.step_id == "pgadmin.container.run":
            current_container = json.loads(json.dumps(configured))
            current_container["Id"] = "created-id"
            config = cast("dict[str, object]", current_container["Config"])
            labels = cast("dict[str, str]", config["Labels"])
            for index, argument in enumerate(argv[:-1]):
                if argument == "--label" and argv[index + 1].startswith(
                    f"{pgadmin_files.PGADMIN_LABEL_FINGERPRINT}="
                ):
                    labels[pgadmin_files.PGADMIN_LABEL_FINGERPRINT] = argv[index + 1].split("=", 1)[
                        1
                    ]
                    break
            return ProcessResult(argv, 0, "created-id\n", "", 0.0, None, ())
        if prepared.step_id == "pgadmin.container.refresh.inspect":
            assert current_container is not None
            return ProcessResult(argv, 0, json.dumps(current_container), "", 0.0, None, ())
        result = result_map.get(prepared.step_id)
        if result is None:
            return ProcessResult(argv, 0, "", "", 0.0, None, ())
        return result

    recording.result_factory = result_factory
    consumption_sequences: dict[str, list[str]] = {}
    execution_sequences: dict[str, list[PreparedStep]] = {}
    reconciliation_deadlines: dict[str, list[float]] = {}
    active_run = threading.local()
    original_process_prepared = RunContext.process_prepared
    original_consume = RunContext._consume
    original_reconcile = pgadmin_container.reconcile_container

    def record_process(self: RunContext[object], requested: PreparedStep) -> ProcessResultLike:
        sequence = getattr(active_run, "consumption", None)
        executions = getattr(active_run, "execution", None)
        assert sequence is not None
        assert executions is not None
        sequence.append(requested.step_id)
        executions.append(requested)
        return original_process_prepared(self, requested)

    def record_consume(self: RunContext[object], step_id: str) -> object:
        sequence = getattr(active_run, "consumption", None)
        assert sequence is not None
        sequence.append(step_id)
        return original_consume(self, step_id)

    def record_reconcile_deadline(*args: object, **kwargs: object) -> PgAdminOpenResult:
        label = getattr(active_run, "label", None)
        deadline = kwargs.get("deadline")
        assert isinstance(label, str)
        assert isinstance(deadline, float)
        reconciliation_deadlines.setdefault(label, []).append(deadline)
        return cast("Callable[..., PgAdminOpenResult]", original_reconcile)(*args, **kwargs)

    def run_once(label: str, command: Command[PgAdminOpenResult]) -> PgAdminOpenResult:
        sequence: list[str] = []
        executions: list[PreparedStep] = []
        active_run.label = label
        active_run.consumption = sequence
        active_run.execution = executions
        try:
            return command.run()
        finally:
            consumption_sequences[label] = sequence
            execution_sequences[label] = executions
            active_run.label = None
            active_run.consumption = None
            active_run.execution = None

    monkeypatch.setattr(RunContext, "process_prepared", record_process)
    monkeypatch.setattr(RunContext, "_consume", record_consume)
    monkeypatch.setattr(pgadmin_container, "reconcile_container", record_reconcile_deadline)
    assert first_command.plan.process_steps
    assert "pgadmin.container.run" in {step.step_id for step in first_command.plan.process_steps}
    assert "pgadmin.reconciliation.inspect.0" in {step.step_id for step in first_command.plan.steps}
    full_ids = [step.step_id for step in first_command.plan.steps]
    assert full_ids.index("pgadmin.database.exists.psql") < full_ids.index("pgadmin.identity.ps")

    key_written = threading.Event()
    release_after_key = threading.Event()
    ensure_guard = threading.Lock()
    pause_once = True
    original_ensure_key = pgadmin_files._ensure_fingerprint_key

    def pause_after_key_write(
        ensure_paths: pgadmin_files.PgAdminPaths,
        *,
        expected_key: bytes | None = None,
    ) -> None:
        nonlocal pause_once
        original_ensure_key(ensure_paths, expected_key=expected_key)
        with ensure_guard:
            pause = pause_once
            pause_once = False
        if pause:
            key_written.set()
            assert release_after_key.wait(timeout=5)

    monkeypatch.setattr(pgadmin_files, "_ensure_fingerprint_key", pause_after_key_write)
    results: dict[str, PgAdminOpenResult] = {}
    errors: dict[str, BaseException] = {}
    second_started = threading.Event()

    def run_thread(label: str, command: Command[PgAdminOpenResult]) -> None:
        if label == "second":
            second_started.set()
        try:
            results[label] = run_once(label, command)
        except BaseException as exc:
            errors[label] = exc

    first_thread = threading.Thread(target=run_thread, args=("first", first_command))
    first_thread.start()
    assert key_written.wait(timeout=5)
    # Capture the second command only after the first private key write.  Both
    # immutable commands therefore carry the same key without any run-time
    # convergence or private-key substitution.
    second_command = resource.open_pgadmin_command(env, executor=recording)
    assert first_command.plan.process_steps == second_command.plan.process_steps
    second_thread = threading.Thread(target=run_thread, args=("second", second_command))
    second_thread.start()
    assert second_started.wait(timeout=5)
    time.sleep(0.05)
    assert second_thread.is_alive()
    release_after_key.set()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert not errors, errors
    first_result = results["first"]
    assert first_result.state is PgAdminOpenState.STARTED
    first_execution = execution_sequences["first"]
    second_result = results["second"]
    assert second_result.state is PgAdminOpenState.REUSED
    successive_command = resource.open_pgadmin_command(env, executor=recording)
    assert run_once("successive", successive_command).state is PgAdminOpenState.REUSED
    for label, command in (
        ("first", first_command),
        ("second", second_command),
        ("successive", successive_command),
    ):
        assert consumption_sequences[label] == [step.step_id for step in command.plan.steps], (
            consumption_sequences[label],
            command.plan,
        )
        assert len(reconciliation_deadlines[label]) == 1
    assert (
        len({reconciliation_deadlines[label][0] for label in ("first", "second", "successive")})
        == 3
    )
    assert sum(step.step_id == "pgadmin.container.run" for step in recording.executed) == 1

    public = {step.step_id: step for step in first_command.plan.process_steps}
    executed_ids = [step.step_id for step in first_execution]
    assert len(executed_ids) == len(set(executed_ids))
    assert "pgadmin.container.inspect.3" not in executed_ids
    assert executed_ids.index("pgadmin.acl.admin.final.set") < executed_ids.index(
        "pgadmin.acl.admin.final"
    )
    assert executed_ids.index("pgadmin.container.inspect.0") < executed_ids.index(
        "pgadmin.acl.metadata.final"
    )
    assert executed_ids.index("pgadmin.acl.admin.final.set") < executed_ids.index(
        "pgadmin.container.run"
    )
    for step in first_execution:
        if step.step_id in public:
            inspected = public[step.step_id]
            assert tuple(inspected.argv) == tuple(step.public_projection().argv)

    first_run = next(step for step in first_execution if step.step_id == "pgadmin.container.run")
    first_label = next(
        first_run.argv[index + 1].split("=", 1)[1]
        for index, argument in enumerate(first_run.argv[:-1])
        if argument == "--label"
        and first_run.argv[index + 1].startswith(f"{pgadmin_files.PGADMIN_LABEL_FINGERPRINT}=")
    )
    assert (
        first_label
        == pgadmin_files.execution_fingerprint_inputs(
            paths, captured.identity, captured.database, "database-secret"
        ).fingerprint
    )
    object.__setattr__(instance.config, "db_password", "rotated-secret")
    rotated_command = resource.open_pgadmin_command(env, executor=recording)
    assert run_once("rotated", rotated_command).state is PgAdminOpenState.RECONFIGURED
    assert consumption_sequences["rotated"] == [step.step_id for step in rotated_command.plan.steps]
    assert len(reconciliation_deadlines["rotated"]) == 1

    assert paths.pgpass.read_text() == "odcli_pg_project-postgres-1:5432:*:odoo:rotated-secret\n"
    assert "database-secret" not in paths.pgpass.read_text()
    rotated_run = [step for step in recording.executed if step.step_id == "pgadmin.container.run"][
        -1
    ]
    rotated_label = next(
        rotated_run.argv[index + 1].split("=", 1)[1]
        for index, argument in enumerate(rotated_run.argv[:-1])
        if argument == "--label"
        and rotated_run.argv[index + 1].startswith(f"{pgadmin_files.PGADMIN_LABEL_FINGERPRINT}=")
    )
    assert rotated_label != first_label
    assert "rotated-secret" not in repr(rotated_command.plan)


def test_public_pgadmin_first_run_converges_across_processes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two processes capture and run one public command against one lifecycle."""
    if "fork" not in mp.get_all_start_methods():
        pytest.skip("the production Linux regression requires fork")

    from odoo_instance_sdk.config import InstanceConfig
    from odoo_instance_sdk.internal import pgadmin, pgadmin_container, pgadmin_files
    from odoo_instance_sdk.internal.address import AddressState
    from odoo_instance_sdk.internal.postgres_compose import SubprocessComposeRunner
    from odoo_instance_sdk.resources.instance import OdooInstance

    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    data_root = tmp_path / "data-root"
    data_root.mkdir()
    paths = pgadmin_files.PgAdminPaths(
        root=data_root / "pgadmin",
        private_dir=data_root / "pgadmin" / "private",
        data_dir=data_root / "pgadmin" / "data",
        admin_password=data_root / "pgadmin" / "private" / "admin-password",
        pgpass=data_root / "pgadmin" / "private" / ".pgpass",
        servers_json=data_root / "pgadmin" / "private" / "servers.json",
        metadata=data_root / "pgadmin" / "private" / "metadata.json",
        lock=tmp_path / "locks" / "pgadmin.lock",
    )
    # The two forked commands must capture the same immutable key before
    # either process enters execution; a missing key would intentionally make
    # one independently captured command stale rather than converge it.
    paths.private_dir.mkdir(parents=True)
    paths.root.chmod(0o710)
    paths.private_dir.chmod(0o710)
    key_path = paths.private_dir / ".fingerprint-key"
    key_path.write_bytes(b"k" * 32)
    key_path.chmod(0o600)
    cluster = PostgresCluster(
        _repository_root=tmp_path,
        _project_id="project",
        _mode="compose",
        _endpoint_host="127.0.0.1",
        _endpoint_port=15432,
        _image="postgres:16@sha256:" + "a" * 64,
        _user="odoo",
        _compose_runner=SubprocessComposeRunner(),
    )
    resource, client = _resource()
    env = _environment()
    instance = OdooInstance(
        config=InstanceConfig(base_url="http://127.0.0.1:8069", db_password="database-secret"),
        _client=cast("OdooClient", client),
    )
    monkeypatch.setattr(PostgresCluster, "compose_file", property(lambda _self: compose_file))
    monkeypatch.setattr(PostgresCluster, "_compose_file", lambda _self: compose_file)
    monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(lambda _path: cluster))
    monkeypatch.setattr(
        pgadmin_files.PgAdminPaths, "from_defaults", classmethod(lambda _cls: paths)
    )
    monkeypatch.setattr(pgadmin_files, "get_data_root", lambda **_: data_root)
    monkeypatch.setattr(pgadmin_files, "_linux", lambda: False)
    monkeypatch.setattr(pgadmin, "probe_address", lambda *_args: AddressState.FREE)
    monkeypatch.setattr(pgadmin_container, "_wait_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        EnvironmentResource, "_configured_pgadmin_instance", lambda _self, _env: instance
    )
    monkeypatch.setattr(
        EnvironmentResource, "_require_pgadmin_database", lambda _self, _instance, _database: None
    )
    monkeypatch.setattr(
        EnvironmentResource, "_validate_healthy_owned_compose", lambda _self, value: value
    )

    context = mp.get_context("fork")
    with context.Manager() as manager:
        global _MP_BARRIER, _MP_ENV, _MP_PHASE_BARRIER, _MP_RESOURCE, _MP_STORE, _MP_STORE_LOCK  # noqa: PLW0603
        _MP_BARRIER = context.Barrier(2)
        _MP_PHASE_BARRIER = context.Barrier(2)
        _MP_ENV = env
        _MP_RESOURCE = resource
        _MP_STORE = manager.dict(container=None, runs=0)
        _MP_STORE_LOCK = manager.Lock()
        result_queue = context.Queue()
        processes = [
            context.Process(target=_run_public_pgadmin_in_process, args=(result_queue,))
            for _ in range(2)
        ]
        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join(15)
            assert all(process.exitcode == 0 for process in processes)
            results = [result_queue.get(timeout=5) for _ in processes]
        finally:
            for process in processes:
                if process.is_alive():
                    process.kill()
                    process.join()
            _MP_BARRIER = None
            _MP_ENV = None
            _MP_PHASE_BARRIER = None
            _MP_RESOURCE = None
            _MP_STORE = None
            _MP_STORE_LOCK = None

    assert all(result[0] == "ok" for result in results), results
    assert {result[1] for result in results} == {"started", "reused"}
    assert {result[2] for result in results} == {"http://127.0.0.1:5050"}
    phase_plans = {tuple(result[3]) for result in results}
    reconciliation_plans = {tuple(result[4]) for result in results}
    assert len(phase_plans) == 1
    assert len(reconciliation_plans) == 1
    for result in results:
        assert tuple(result[5]) == tuple(result[3]) + tuple(result[4])
        assert len(result[6]) == 1
    assert len({result[6][0] for result in results}) == 2
    assert _MP_STORE is None
    assert (paths.private_dir / ".fingerprint-key").read_bytes()
