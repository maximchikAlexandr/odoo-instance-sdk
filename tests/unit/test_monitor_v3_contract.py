from __future__ import annotations

from pathlib import Path

import msgspec
import pytest

import odoo_instance_sdk as sdk
from odoo_instance_sdk.exceptions import ProjectManifestNotFoundError
from odoo_instance_sdk.models import (
    EnvironmentSnapshot,
    EnvironmentState,
    HttpError,
    HttpErrorCode,
    PgAdminEligibility,
    PgAdminEligibilityState,
    PgAdminOpenRequest,
    PgAdminOpenResult,
    PgAdminOpenState,
    PostgresClusterState,
)
from odoo_instance_sdk.resources.monitor import EnvironmentMonitor
from odoo_instance_sdk.resources.postgres import PostgresCluster
from tests.unit.monitor_support import (
    FakeDockerProvider,
    FakePostgresCluster,
    make_catalog,
    make_env,
    patch_from_project,
    seed_env,
)


def test_pgadmin_http_models_are_public_frozen_and_unknown_field_forbidden() -> None:
    assert sdk.PgAdminEligibility is PgAdminEligibility
    assert sdk.PgAdminEligibilityState is PgAdminEligibilityState
    assert sdk.PgAdminOpenRequest is PgAdminOpenRequest
    assert sdk.PgAdminOpenResult is PgAdminOpenResult
    assert sdk.PgAdminOpenState is PgAdminOpenState
    assert sdk.HttpError is HttpError
    assert sdk.HttpErrorCode is HttpErrorCode
    assert tuple(field.name for field in msgspec.structs.fields(PgAdminEligibility)) == ("state",)
    assert tuple(field.name for field in msgspec.structs.fields(PgAdminOpenRequest)) == (
        "environment_id",
    )
    assert tuple(field.name for field in msgspec.structs.fields(PgAdminOpenResult)) == (
        "state",
        "url",
    )
    assert tuple(field.name for field in msgspec.structs.fields(HttpError)) == ("code", "message")
    assert tuple(field.name for field in msgspec.structs.fields(EnvironmentSnapshot)) == (
        "id",
        "project_id",
        "name",
        "branch",
        "short_sha",
        "db_mode",
        "database",
        "lifecycle_state",
        "allocated_http_port",
        "observed_port",
        "artifacts",
        "runtime",
        "git",
        "storage",
        "pgadmin",
    )

    eligibility = PgAdminEligibility(state=PgAdminEligibilityState.ELIGIBLE)
    with pytest.raises(AttributeError):
        eligibility.state = PgAdminEligibilityState.CLUSTER_UNHEALTHY  # type: ignore[misc]

    with pytest.raises(TypeError):
        PgAdminEligibility(state=PgAdminEligibilityState.ELIGIBLE, extra="rejected")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        PgAdminOpenRequest(environment_id="env-1", extra="rejected")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        PgAdminOpenResult(state=PgAdminOpenState.STARTED, url="http://127.0.0.1", extra="rejected")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        HttpError(code=HttpErrorCode.invalid_request, message="invalid", extra="rejected")  # type: ignore[call-arg]

    assert (
        msgspec.json.decode(
            msgspec.json.encode(
                PgAdminOpenResult(state=PgAdminOpenState.STARTED, url="http://127.0.0.1:5050")
            ),
            type=PgAdminOpenResult,
        ).state
        is PgAdminOpenState.STARTED
    )
    assert (
        msgspec.json.encode(
            HttpError(code=HttpErrorCode.invalid_request, message="invalid request")
        )
        == b'{"code":"invalid_request","message":"invalid request"}'
    )


def test_pgadmin_enum_values_and_safe_reprs() -> None:
    assert [item.value for item in PgAdminEligibilityState] == [
        "eligible",
        "environment_not_ready",
        "database_unresolved",
        "cluster_not_owned",
        "cluster_unhealthy",
    ]
    assert [item.value for item in PgAdminOpenState] == ["started", "reused", "reconfigured"]
    assert [item.value for item in HttpErrorCode] == [
        "invalid_request",
        "monitor_snapshot_failed",
        "environment_not_found",
        "pgadmin_not_eligible",
        "database_not_found",
        "pgadmin_unavailable",
    ]

    assert "secret-environment" not in repr(PgAdminOpenRequest(environment_id="secret-environment"))
    assert "password" not in repr(
        PgAdminOpenResult(state=PgAdminOpenState.STARTED, url="http://user:password@127.0.0.1")
    )
    assert "secret diagnostic" not in repr(
        HttpError(code=HttpErrorCode.pgadmin_unavailable, message="secret diagnostic")
    )


@pytest.mark.parametrize(
    ("lifecycle", "database", "cluster", "cluster_state", "expected"),
    [
        (
            EnvironmentState.CREATING,
            "db",
            FakePostgresCluster(mode="external"),
            PostgresClusterState.HEALTHY,
            PgAdminEligibilityState.ENVIRONMENT_NOT_READY,
        ),
        (
            EnvironmentState.READY,
            None,
            FakePostgresCluster(mode="external"),
            PostgresClusterState.HEALTHY,
            PgAdminEligibilityState.DATABASE_UNRESOLVED,
        ),
        (
            EnvironmentState.READY,
            "db",
            FakePostgresCluster(mode="external"),
            PostgresClusterState.HEALTHY,
            PgAdminEligibilityState.CLUSTER_NOT_OWNED,
        ),
        (
            EnvironmentState.READY,
            "db",
            FakePostgresCluster(mode="compose", state=PostgresClusterState.UNHEALTHY),
            PostgresClusterState.UNHEALTHY,
            PgAdminEligibilityState.CLUSTER_UNHEALTHY,
        ),
        (
            EnvironmentState.READY,
            "db",
            FakePostgresCluster(mode="compose"),
            PostgresClusterState.HEALTHY,
            PgAdminEligibilityState.ELIGIBLE,
        ),
    ],
)
def test_snapshot_v3_eligibility_precedence_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lifecycle: EnvironmentState,
    database: str | None,
    cluster: FakePostgresCluster,
    cluster_state: PostgresClusterState,
    expected: PgAdminEligibilityState,
) -> None:
    catalog = make_catalog(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    seed_env(
        catalog,
        make_env(
            "env-1",
            worktree_path=str(worktree),
            state=lifecycle.value,
            source_db_name=database,
        ),
    )
    catalog.close()
    patch_from_project(monkeypatch, cluster)
    docker = FakeDockerProvider()

    snapshot = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3",
        docker_provider=docker,
    ).snapshot()

    assert snapshot.schema_version == 4
    assert snapshot.environments[0].pgadmin.state is expected
    assert snapshot.environments[0].observed_port is None
    assert docker.calls == (
        1 if cluster.mode == "compose" and cluster_state is not PostgresClusterState.STOPPED else 0
    )


def test_snapshot_v3_missing_cluster_is_not_owned_without_lifecycle_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = make_catalog(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    seed_env(catalog, make_env("env-1", worktree_path=str(worktree)))
    catalog.close()

    def missing_manifest(*_args: object, **_kwargs: object) -> object:
        raise ProjectManifestNotFoundError("missing manifest")

    monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(missing_manifest))
    docker = FakeDockerProvider()

    snapshot = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3",
        docker_provider=docker,
    ).snapshot()

    assert snapshot.schema_version == 4
    assert snapshot.environments[0].pgadmin.state is PgAdminEligibilityState.CLUSTER_NOT_OWNED
    assert docker.calls == 0


def test_removed_snapshot_does_not_probe_cluster_for_pgadmin_eligibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = make_catalog(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    seed_env(catalog, make_env("env-1", worktree_path=str(worktree), state="removed"))
    catalog.close()

    def forbidden_probe(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("removed-only snapshots must not probe a cluster")

    monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(forbidden_probe))
    snapshot = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3").snapshot(
        include_removed=True
    )

    assert snapshot.environments[0].pgadmin.state is PgAdminEligibilityState.ENVIRONMENT_NOT_READY
