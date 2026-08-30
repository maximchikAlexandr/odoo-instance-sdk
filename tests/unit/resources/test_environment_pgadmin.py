from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

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
from odoo_instance_sdk.execution import ProcessStep
from odoo_instance_sdk.internal.proc import RecordingExecutor, active_context
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
    """The inspectable plan includes the bounded ACL lifecycle on Linux."""
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

    assert sum(step.step_id.startswith("pgadmin.acl.get.") for step in steps) == 32
    assert sum(step.step_id.startswith("pgadmin.acl.set.") for step in steps) == 32
    assert sum(step.step_id.startswith("pgadmin.acl.default-set.") for step in steps) == 32


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
    instance, from_environment, cluster = _install_healthy_preflight(monkeypatch, resource)
    lifecycle = MagicMock(
        return_value=PgAdminOpenResult(
            state=PgAdminOpenState.STARTED,
            url="http://127.0.0.1:5050",
        )
    )
    monkeypatch.setattr(EnvironmentResource, "_open_pgadmin_lifecycle", lifecycle)

    result = resource.open_pgadmin(env)

    assert result.state is PgAdminOpenState.STARTED
    assert result.url == "http://127.0.0.1:5050"
    from_environment.assert_called_once_with(env)
    instance.databases.exists.assert_called_once_with("odoo")
    lifecycle.assert_called_once_with(
        environment=env,
        instance=instance,
        cluster=cluster,
        database="odoo",
    )


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
    lifecycle = MagicMock()
    monkeypatch.setattr(EnvironmentResource, "_open_pgadmin_lifecycle", lifecycle)

    with pytest.raises(PgAdminNotEligibleError) as exc_info:
        resource.open_pgadmin(_environment(state=state))

    assert str(exc_info.value) == "pgAdmin is not eligible for this environment"
    cast("MagicMock", resource._client.instance.from_environment).assert_not_called()
    lifecycle.assert_not_called()


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
    lifecycle = MagicMock()
    monkeypatch.setattr(EnvironmentResource, "_open_pgadmin_lifecycle", lifecycle)

    with pytest.raises(PgAdminNotEligibleError):
        resource.open_pgadmin(_environment())

    cluster.status.assert_not_called()
    cast("MagicMock", resource._client.instance.from_environment).assert_not_called()
    lifecycle.assert_not_called()


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
    lifecycle = MagicMock()
    monkeypatch.setattr(EnvironmentResource, "_open_pgadmin_lifecycle", lifecycle)

    with pytest.raises(PgAdminNotEligibleError):
        resource.open_pgadmin(_environment())

    cast("MagicMock", resource._client.instance.from_environment).assert_not_called()
    lifecycle.assert_not_called()


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
    lifecycle = MagicMock()
    monkeypatch.setattr(EnvironmentResource, "_open_pgadmin_lifecycle", lifecycle)

    with pytest.raises(PgAdminDatabaseNotFoundError) as exc_info:
        resource.open_pgadmin(_environment())

    assert str(exc_info.value) == "selected database was not found"
    instance.databases.exists.assert_called_once_with("odoo")
    lifecycle.assert_not_called()


def test_open_pgadmin_maps_inconclusive_database_to_sanitized_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, _client = _resource()
    instance, _from_environment, _cluster = _install_healthy_preflight(monkeypatch, resource)
    instance.databases.exists.side_effect = DatabaseManagerUnavailableError(
        "secret /internal/config detail"
    )
    lifecycle = MagicMock()
    monkeypatch.setattr(EnvironmentResource, "_open_pgadmin_lifecycle", lifecycle)

    with pytest.raises(PgAdminUnavailableError) as exc_info:
        resource.open_pgadmin(_environment())

    assert str(exc_info.value) == "pgAdmin is unavailable"
    assert "/internal/config" not in str(exc_info.value)
    lifecycle.assert_not_called()


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
            )
        ),
    )
    executor = RecordingExecutor()

    def consume_manifest(_self: object, _selector: object, **_kwargs: object) -> PgAdminOpenResult:
        context = active_context()
        assert context is not None
        context.process("pgadmin.postgres.status.ps")
        context.process("pgadmin.postgres.status.health")
        return PgAdminOpenResult(state=PgAdminOpenState.STARTED, url="http://127.0.0.1:5050")

    monkeypatch.setattr(EnvironmentResource, "_open_pgadmin_impl", consume_manifest)
    command = resource.open_pgadmin_command(env, executor=executor)

    assert command.plan.steps
    assert any(isinstance(step, ProcessStep) for step in command.plan.steps)
    assert [step.step_id for step in command.plan.process_steps[:2]] == [
        "pgadmin.postgres.status.ps",
        "pgadmin.postgres.status.health",
    ]
    command.run()
    assert [step.step_id for step in executor.executed] == [
        "pgadmin.postgres.status.ps",
        "pgadmin.postgres.status.health",
    ]
