"""Tests for CheckoutInventory projection and environment-facts providers."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest

from odoo_instance_sdk.internal.checkout_inventory import build_checkout_inventory
from odoo_instance_sdk.models import (
    CheckoutRow,
    DatabaseFootprint,
    EnvironmentArtifacts,
    EnvironmentFactsSummary,
    EnvironmentSnapshot,
    EnvironmentState,
    GitActivity,
    GitActivityState,
    GitDiff,
    PgAdminEligibility,
    PgAdminEligibilityState,
    ProjectSummary,
    PythonEnvFootprint,
    RuntimeMetrics,
    RuntimeState,
    Snapshot,
    StorageFootprint,
)


def _runtime(
    *,
    state: RuntimeState = RuntimeState.READY,
    database: str | None = "demo",
) -> RuntimeMetrics:
    return RuntimeMetrics(
        state=state,
        root_pid=100 if state is not RuntimeState.STOPPED else None,
        child_pids=(101,) if state is not RuntimeState.STOPPED else (),
        process_count=2 if state is not RuntimeState.STOPPED else 0,
        cpu_percent=5.0 if state is not RuntimeState.STOPPED else None,
        memory_bytes=1024 if state is not RuntimeState.STOPPED else None,
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        http_url="http://127.0.0.1:8069" if state is not RuntimeState.STOPPED else None,
        http_port=8069 if state is not RuntimeState.STOPPED else None,
        database_name=database,
        commit_sha="abc1234567890",
        branch="main",
    )


def _git(
    *,
    branch: str = "feat/x",
    ahead: int | None = 2,
    behind: int | None = 0,
    diff: GitDiff | None = GitDiff(added=4, deleted=1),
    state: GitActivityState = GitActivityState.AHEAD,
) -> GitActivity:
    return GitActivity(
        default_branch="main",
        head_sha="abc1234567890",
        short_sha="abc1234",
        branch=branch,
        ahead=ahead,
        behind=behind,
        diff=diff,
        state=state,
    )


def _project(
    *,
    project_id: str = "project_demo",
    runtime: RuntimeMetrics | None = None,
    repository_root: str = "/repo/demo",
) -> ProjectSummary:
    return ProjectSummary(
        id=project_id,
        name="demo",
        display_hint="demo",
        repository_root=repository_root,
        environment_count=1,
        cluster=None,
        runtime=runtime,
    )


def _environment(
    *,
    env_id: str = "env-1",
    project_id: str = "project_demo",
    name: str = "demo-env",
    lifecycle_state: EnvironmentState = EnvironmentState.READY,
    runtime: RuntimeMetrics | None = None,
) -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        id=env_id,
        project_id=project_id,
        name=name,
        branch="feat/x",
        short_sha="abc1234",
        db_mode="shared",
        database="demo",
        lifecycle_state=lifecycle_state,
        allocated_http_port=8069,
        observed_port=None,
        artifacts=EnvironmentArtifacts(
            worktree_exists=True,
            worktree_registered=True,
            config_exists=True,
            python_exists=True,
            python_contained=True,
            dependency_lock_exists=True,
            backup_exists=None,
        ),
        runtime=runtime or _runtime(),
        git=_git(),
        storage=StorageFootprint(
            total_bytes=0,
            complete=True,
            worktree_bytes=0,
            python_environment=PythonEnvFootprint(owned=False, bytes=None),
            database=DatabaseFootprint(
                owned=False,
                postgres_bytes=None,
                filestore_bytes=None,
                total_bytes=None,
            ),
            other_files_bytes=None,
        ),
        pgadmin=PgAdminEligibility(state=PgAdminEligibilityState.ELIGIBLE),
    )


def _snapshot(
    projects: tuple[ProjectSummary, ...],
    environments: tuple[EnvironmentSnapshot, ...],
) -> Snapshot:
    return Snapshot(
        schema_version=4,
        generated_at=datetime(2024, 1, 1, tzinfo=UTC),
        projects=projects,
        environments=environments,
    )


class _StaticProvider:
    def __init__(self, provider_id: str, summaries: Mapping[str, EnvironmentFactsSummary]) -> None:
        self._provider_id = provider_id
        self._summaries = summaries

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def collect(self, rows: Sequence[CheckoutRow]) -> Mapping[str, EnvironmentFactsSummary]:
        return dict(self._summaries)


class _SlowProvider:
    @property
    def provider_id(self) -> str:
        return "slow"

    def collect(self, rows: Sequence[CheckoutRow]) -> Mapping[str, EnvironmentFactsSummary]:
        time.sleep(5)
        return {}


class _FailingProvider:
    @property
    def provider_id(self) -> str:
        return "broken"

    def collect(self, rows: Sequence[CheckoutRow]) -> Mapping[str, EnvironmentFactsSummary]:
        raise RuntimeError("provider failed")


@pytest.mark.unit
def test_build_checkout_inventory_main_then_environment_order() -> None:
    project = _project()
    env = _environment()
    inventory = build_checkout_inventory(
        _snapshot((project,), (env,)),
        worktree_paths={env.id: "/worktrees/env-1"},
        git_collector=lambda _path, _ref: _git(branch="main", ahead=0, behind=0),
    )

    assert inventory.schema_version == 1
    assert len(inventory.rows) == 2
    assert inventory.rows[0].kind == "main"
    assert inventory.rows[0].environment_id is None
    assert inventory.rows[1].kind == "environment"
    assert inventory.rows[1].environment_id == env.id
    assert inventory.rows[1].worktree_path == "/worktrees/env-1"


@pytest.mark.unit
def test_build_checkout_inventory_stopped_main_runtime() -> None:
    project = _project(runtime=_runtime(state=RuntimeState.STOPPED, database=None))
    inventory = build_checkout_inventory(
        _snapshot((project,), ()),
        git_collector=lambda _path, _ref: _git(state=GitActivityState.CLEAN, ahead=0, behind=0),
    )

    assert inventory.rows[0].odoo_status == "stopped"
    assert inventory.rows[0].database is None


@pytest.mark.unit
def test_build_checkout_inventory_git_ahead_and_diff() -> None:
    project = _project()
    inventory = build_checkout_inventory(
        _snapshot((project,), ()),
        git_collector=lambda _path, _ref: _git(ahead=3, behind=1, diff=GitDiff(added=7, deleted=2)),
    )

    git = inventory.rows[0].git
    assert git is not None
    assert git.ahead == 3
    assert git.behind == 1
    assert git.added_lines == 7
    assert git.deleted_lines == 2


@pytest.mark.unit
def test_build_checkout_inventory_all_projects_stable_order() -> None:
    project_a = _project(project_id="project_a", repository_root="/repo/a")
    project_b = _project(project_id="project_b", repository_root="/repo/b")
    env_a = _environment(env_id="env-a", project_id="project_a", name="a")
    env_b = _environment(env_id="env-b", project_id="project_b", name="b")
    inventory = build_checkout_inventory(
        _snapshot((project_b, project_a), (env_b, env_a)),
        git_collector=lambda _path, _ref: _git(),
    )

    row_names = [row.name for row in inventory.rows]
    assert row_names.index("demo") < row_names.index("a")
    assert row_names.index("demo") < row_names.index("b")


@pytest.mark.unit
def test_build_checkout_inventory_include_removed() -> None:
    project = _project()
    removed = _environment(
        env_id="env-removed",
        lifecycle_state=EnvironmentState.REMOVED,
        runtime=_runtime(state=RuntimeState.STOPPED),
    )
    inventory = build_checkout_inventory(
        _snapshot((project,), (removed,)),
        include_removed=True,
        worktree_paths={"env-removed": "/removed"},
        git_collector=lambda _path, _ref: _git(),
    )

    assert len(inventory.rows) == 2
    assert inventory.rows[1].lifecycle_state is EnvironmentState.REMOVED


@pytest.mark.unit
def test_build_checkout_inventory_three_providers_and_failed_provider() -> None:
    project = _project()
    env = _environment()
    inventory = build_checkout_inventory(
        _snapshot((project,), (env,)),
        worktree_paths={env.id: "/worktrees/env-1"},
        git_collector=lambda _path, _ref: _git(),
        facts_providers=(
            _StaticProvider(
                "alpha",
                {
                    "main": EnvironmentFactsSummary(
                        provider="alpha", state="available", text="main-alpha"
                    ),
                    env.id: EnvironmentFactsSummary(
                        provider="alpha", state="available", text="env-alpha"
                    ),
                },
            ),
            _FailingProvider(),
            _StaticProvider(
                "beta",
                {
                    "main": EnvironmentFactsSummary(
                        provider="beta", state="available", text="main-beta"
                    )
                },
            ),
            _SlowProvider(),
            _StaticProvider(
                "gamma",
                {env.id: EnvironmentFactsSummary(provider="gamma", state="available", text="g")},
            ),
        ),
        facts_timeout_seconds=0.05,
    )

    main_row = inventory.rows[0]
    env_row = inventory.rows[1]
    assert tuple(fact.provider for fact in main_row.facts) == ("alpha", "beta")
    assert tuple(fact.provider for fact in env_row.facts) == ("alpha", "gamma")
    assert all(fact.text for fact in main_row.facts)


@pytest.mark.unit
def test_build_checkout_inventory_no_providers() -> None:
    project = _project()
    inventory = build_checkout_inventory(
        _snapshot((project,), ()),
        facts_providers=(),
        git_collector=lambda _path, _ref: _git(),
    )

    assert inventory.rows[0].facts == ()


@pytest.mark.unit
def test_build_checkout_inventory_unknown_project() -> None:
    inventory = build_checkout_inventory(
        _snapshot((), ()),
        project_id="missing",
        git_collector=lambda _path, _ref: _git(),
    )

    assert inventory.complete is False
    assert inventory.unavailability_reason == "project_not_found"
    assert inventory.rows == ()
