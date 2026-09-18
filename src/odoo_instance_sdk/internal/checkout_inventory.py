"""Checkout inventory projection from one monitor snapshot plus Git facts.

This module is a projection boundary. It consumes one captured
``EnvironmentMonitor.snapshot()`` result plus Git facts of the main
checkout and builds the frozen ``CheckoutInventory`` model. It does not
own a catalogue, perform a second sample, instantiate ``OdooClient``,
query ``BackupCatalog``, run Docker/filesystem reconciliation, probe
ports, or regroup catalog rows into an alternative model.

The narrow environment-facts entry point lives here too: providers are
discovered via Python entry points, receive immutable core checkout
rows, and return frozen summaries with deterministic order, a short
bounded timeout, and isolated per-provider/per-row errors.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from odoo_instance_sdk.models import (
    CheckoutClusterSummary,
    CheckoutGitFacts,
    CheckoutInventory,
    CheckoutOdooStatus,
    CheckoutRow,
    ClusterSnapshot,
    EnvironmentFactsSummary,
    EnvironmentSnapshot,
    EnvironmentState,
    GitActivity,
    GitActivityState,
    ProjectSummary,
    RuntimeState,
    Snapshot,
)

if TYPE_CHECKING:
    from typing import Any  # noqa: F401


_CHECKOUT_INVENTORY_SCHEMA_VERSION = 1
_ENVIRONMENT_FACTS_ENTRY_POINT_GROUP = "odoo_instance_sdk.environment_facts"
_ENVIRONMENT_FACTS_TIMEOUT_SECONDS = 2.0


type EnvironmentFactsProviderId = str


@runtime_checkable
class EnvironmentFactsProvider(Protocol):
    """One read-only environment-facts provider.

    A provider receives the immutable core checkout rows of one
    ``CheckoutInventory`` sample and returns a mapping of
    ``environment_id -> EnvironmentFactsSummary``. The main checkout row
    uses the synthetic id ``"main"``. A provider SHALL NOT patch Click
    or Rich, run its own live loop, or mutate the core snapshot.
    """

    @property
    def provider_id(self) -> EnvironmentFactsProviderId: ...

    def collect(self, rows: Sequence[CheckoutRow]) -> Mapping[str, EnvironmentFactsSummary]: ...


def _odoo_status(
    runtime_state: RuntimeState | None, lifecycle_state: EnvironmentState | None
) -> CheckoutOdooStatus:
    """Compact Odoo status without PID or metrics.

    ``running`` covers READY and NOT_READY (the process is alive); ``stopped``
    covers STOPPED, removed, and a missing runtime. ``unavailable`` is reserved
    for future use; the current projection never emits it because the monitor
    already degrades runtime failures to a stopped shape.
    """
    if lifecycle_state is EnvironmentState.REMOVED:
        return "stopped"
    if runtime_state is None or runtime_state is RuntimeState.STOPPED:
        return "stopped"
    return "running"


def _git_facts_from_activity(git: GitActivity) -> CheckoutGitFacts:
    """Project one ``GitActivity`` into a frozen ``CheckoutGitFacts`` row."""
    if git.state is GitActivityState.ORPHAN or git.ahead is None or git.behind is None:
        return CheckoutGitFacts(
            branch=git.branch,
            short_sha=git.short_sha,
            ahead=None,
            behind=None,
            added_lines=None,
            deleted_lines=None,
        )
    added = git.diff.added if git.diff is not None else None
    deleted = git.diff.deleted if git.diff is not None else None
    return CheckoutGitFacts(
        branch=git.branch,
        short_sha=git.short_sha,
        ahead=git.ahead,
        behind=git.behind,
        added_lines=added,
        deleted_lines=deleted,
    )


def _cluster_summary(
    project_id: str, cluster: ClusterSnapshot | None
) -> CheckoutClusterSummary | None:
    if cluster is None:
        return None
    return CheckoutClusterSummary(
        project_id=project_id,
        mode=cluster.mode,
        state=cluster.state,
        unavailability_reason=cluster.unavailability_reason,
    )


def _resolve_main_git_facts(
    project: ProjectSummary,
    *,
    base_ref: str | None,
    git_collector: Callable[[Path, str], GitActivity] | None,
) -> CheckoutGitFacts | None:
    """Collect Git facts of the main checkout worktree.

    Uses the injected ``git_collector`` (defaulting to the stateless
    ``collect_git_activity``) so tests can avoid real Git subprocesses.
    On any failure the main checkout row keeps a partial Git identity
    without ahead/behind/diff.
    """
    worktree = Path(project.repository_root)
    effective_base_ref = base_ref or "main"
    try:
        if git_collector is None:
            from odoo_instance_sdk.internal.git_activity import collect_git_activity

            activity = collect_git_activity(worktree, base_ref=effective_base_ref)
        else:
            activity = git_collector(worktree, effective_base_ref)
    except Exception:
        return CheckoutGitFacts(branch="unknown")
    return _git_facts_from_activity(activity)


def _main_row(
    project: ProjectSummary,
    *,
    base_ref: str | None,
    git_collector: Callable[[Path, str], GitActivity] | None,
) -> CheckoutRow:
    git_facts = _resolve_main_git_facts(project, base_ref=base_ref, git_collector=git_collector)
    runtime = project.runtime
    return CheckoutRow(
        kind="main",
        project_id=project.id,
        environment_id=None,
        name=project.name,
        worktree_path=project.repository_root,
        odoo_status=_odoo_status(runtime.state if runtime is not None else None, None),
        db_mode=None,
        database=runtime.database_name if runtime is not None else None,
        git=git_facts,
    )


def _environment_row(environment: EnvironmentSnapshot, worktree_path: str) -> CheckoutRow:
    return CheckoutRow(
        kind="environment",
        project_id=environment.project_id,
        environment_id=environment.id,
        name=environment.name,
        worktree_path=worktree_path,
        odoo_status=_odoo_status(environment.runtime.state, environment.lifecycle_state),
        db_mode=environment.db_mode,
        database=environment.database,
        git=_git_facts_from_activity(environment.git),
        lifecycle_state=environment.lifecycle_state,
    )


def _discover_providers() -> tuple[EnvironmentFactsProvider, ...]:
    """Discover environment-facts providers via Python entry points.

    Deterministic order by provider id. A missing optional package or a
    provider that fails to load is not an error and SHALL NOT add an
    empty column.
    """
    discovered: list[EnvironmentFactsProvider] = []
    try:
        eps = entry_points(group=_ENVIRONMENT_FACTS_ENTRY_POINT_GROUP)
    except Exception:
        return ()
    for entry_point in sorted(eps, key=lambda item: item.name):
        try:
            loaded = entry_point.load()
        except Exception:
            continue
        provider = loaded() if callable(loaded) and not isinstance(loaded, type) else loaded
        if not isinstance(provider, EnvironmentFactsProvider):
            continue
        discovered.append(provider)
    return tuple(discovered)


def _collect_facts(
    rows: Sequence[CheckoutRow],
    *,
    providers: Sequence[EnvironmentFactsProvider] | None = None,
    timeout_seconds: float = _ENVIRONMENT_FACTS_TIMEOUT_SECONDS,
) -> dict[str, tuple[EnvironmentFactsSummary, ...]]:
    """Collect environment-facts summaries with isolated per-provider errors.

    Deterministic provider order, a short bounded timeout, and isolated
    per-provider/per-row errors. A failed, incompatible, or slow provider
    SHALL NOT hide core rows or other providers' summaries and SHALL NOT
    write progress or noise to machine stdout.
    """
    discovered = providers if providers is not None else _discover_providers()
    by_row: dict[str, tuple[EnvironmentFactsSummary, ...]] = {}
    for provider in discovered:
        try:
            summaries = _collect_one_provider(provider, rows, timeout_seconds=timeout_seconds)
        except Exception:
            continue
        for row_id, summary in summaries.items():
            by_row.setdefault(row_id, ())
            existing = by_row[row_id]
            existing_ids = {item.provider for item in existing}
            if summary.provider in existing_ids:
                continue
            by_row[row_id] = (*existing, summary)
    return by_row


def _collect_one_provider(
    provider: EnvironmentFactsProvider,
    rows: Sequence[CheckoutRow],
    *,
    timeout_seconds: float,
) -> Mapping[str, EnvironmentFactsSummary]:
    """Call one provider with a bounded timeout, isolating any failure."""
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(provider.collect, rows)
        try:
            return future.result(timeout=timeout_seconds)
        except TimeoutError:
            return {}
        finally:
            future.cancel()


def _attach_facts(
    rows: Sequence[CheckoutRow], facts: Mapping[str, tuple[EnvironmentFactsSummary, ...]]
) -> tuple[CheckoutRow, ...]:
    """Return rows with their environment-facts summaries attached in place."""
    if not facts:
        return tuple(rows)
    from msgspec.structs import replace as _replace

    attached: list[CheckoutRow] = []
    for row in rows:
        row_id = row.environment_id or "main"
        row_facts = facts.get(row_id)
        if row_facts:
            attached.append(_replace(row, facts=row_facts))
        else:
            attached.append(row)
    return tuple(attached)


def build_checkout_inventory(
    snapshot: Snapshot,
    *,
    project_id: str | None = None,
    include_removed: bool = False,
    worktree_paths: Mapping[str, str] | None = None,
    base_ref_resolver: Callable[[str], str | None] | None = None,
    git_collector: Callable[[Path, str], GitActivity] | None = None,
    facts_providers: Sequence[EnvironmentFactsProvider] | None = None,
    facts_timeout_seconds: float = _ENVIRONMENT_FACTS_TIMEOUT_SECONDS,
) -> CheckoutInventory:
    """Project one ``CheckoutInventory`` from one canonical snapshot.

    Three ownership kinds in order: the main checkout of each selected
    project, then each non-removed environment. The main checkout SHALL
    NOT be modelled as a synthetic environment. Git facts of the main
    checkout are collected once via the injected ``git_collector`` (or
    the stateless ``collect_git_activity`` default). Environment-facts
    providers are discovered via Python entry points with deterministic
    order, a short bounded timeout, and isolated per-provider/per-row
    errors.
    """
    sample_time = snapshot.generated_at
    projects = tuple(
        project for project in snapshot.projects if project_id is None or project.id == project_id
    )
    if project_id is not None and not projects:
        return CheckoutInventory(
            schema_version=_CHECKOUT_INVENTORY_SCHEMA_VERSION,
            generated_at=snapshot.generated_at,
            sample_time=sample_time,
            project_id=project_id,
            rows=(),
            clusters=(),
            complete=False,
            unavailability_reason="project_not_found",
        )

    environments = tuple(
        environment
        for environment in snapshot.environments
        if (project_id is None or environment.project_id == project_id)
        and (include_removed or environment.lifecycle_state is not EnvironmentState.REMOVED)
    )
    environments_by_project: dict[str, list[EnvironmentSnapshot]] = {}
    for environment in environments:
        environments_by_project.setdefault(environment.project_id, []).append(environment)

    paths = worktree_paths or {}
    clusters: list[CheckoutClusterSummary] = []
    rows: list[CheckoutRow] = []
    for project in sorted(projects, key=lambda item: item.id):
        cluster = _cluster_summary(project.id, project.cluster)
        if cluster is not None:
            clusters.append(cluster)
        base_ref = base_ref_resolver(project.id) if base_ref_resolver is not None else None
        rows.append(_main_row(project, base_ref=base_ref, git_collector=git_collector))
        for environment in sorted(
            environments_by_project.get(project.id, ()), key=lambda item: item.id
        ):
            worktree_path = paths.get(environment.id, "")
            rows.append(_environment_row(environment, worktree_path))

    facts = _collect_facts(rows, providers=facts_providers, timeout_seconds=facts_timeout_seconds)
    rows_with_facts = _attach_facts(rows, facts)

    return CheckoutInventory(
        schema_version=_CHECKOUT_INVENTORY_SCHEMA_VERSION,
        generated_at=snapshot.generated_at,
        sample_time=sample_time,
        project_id=project_id,
        rows=rows_with_facts,
        clusters=tuple(clusters),
        complete=True,
        unavailability_reason=None,
    )


__all__ = [
    "CheckoutInventory",
    "EnvironmentFactsProvider",
    "EnvironmentFactsProviderId",
    "EnvironmentFactsSummary",
    "build_checkout_inventory",
]
