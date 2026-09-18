"""Shared Rich display helpers for env checkout and list commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text

from odoo_instance_sdk.commands.output import sanitize_terminal_text
from odoo_instance_sdk.internal.cli_format import human_bytes as _human_bytes
from odoo_instance_sdk.models.backup import EnvironmentState, PostgresClusterState
from odoo_instance_sdk.models.footprint import ClusterMetrics
from odoo_instance_sdk.models.monitor import (
    CheckoutClusterSummary,
    CheckoutGitFacts,
    CheckoutInventory,
    CheckoutRow,
    ClusterSnapshot,
)
from odoo_instance_sdk.models.runtime import PidScope

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue

_ENV_LIST_COLUMNS = (
    "KIND",
    "NAME",
    "BRANCH",
    "STATUS",
    "GIT",
    "DB_MODE",
    "DATABASE",
    "WORKTREE",
)
_ENV_LIST_COMPACT_COLUMNS = (
    "NAME",
    "BRANCH / STATUS",
    "DATABASE",
    "GIT A/D",
)
_ENV_LIST_MEDIUM_COLUMNS = (*_ENV_LIST_COMPACT_COLUMNS, "WORKTREE")


def _provider_columns(inventory: CheckoutInventory) -> tuple[str, ...]:
    providers: set[str] = set()
    for row in inventory.rows:
        for fact in row.facts:
            providers.add(fact.provider)
    return tuple(sorted(providers))


def _checkout_status_style(row: CheckoutRow) -> str:
    status = _checkout_status_str(row)
    if status == "removed":
        return "red"
    if status == "running":
        return "green"
    return "dim"


def _checkout_status_str(row: CheckoutRow) -> str:
    if row.lifecycle_state is EnvironmentState.REMOVED:
        return "removed"
    return row.odoo_status


def _git_branch(row: CheckoutRow) -> str:
    return row.git.branch if row.git is not None else "—"


def _git_ahead_from_facts(git: CheckoutGitFacts | None) -> str:
    if git is None or git.ahead is None or git.behind is None:
        return "—"
    return f"↑{git.ahead} ↓{git.behind}"


def _git_diff_from_facts(git: CheckoutGitFacts | None) -> str:
    if git is None or git.added_lines is None or git.deleted_lines is None:
        return "—"
    return f"+{git.added_lines} -{git.deleted_lines}"


def _fact_text(row: CheckoutRow, provider_id: str) -> str:
    for fact in row.facts:
        if fact.provider == provider_id:
            return fact.text
    return "—"


def _checkout_row_values(row: CheckoutRow, provider_columns: tuple[str, ...]) -> dict[str, str]:
    database = row.database or "—"
    db_mode = row.db_mode or "—"
    worktree = _display_path(row.worktree_path) if row.worktree_path else "—"
    git_value = f"{_git_ahead_from_facts(row.git)} {_git_diff_from_facts(row.git)}".strip()
    values = {
        "KIND": row.kind,
        "NAME": row.name,
        "BRANCH": _git_branch(row),
        "STATUS": _checkout_status_str(row),
        "GIT": git_value,
        "DB_MODE": db_mode,
        "DATABASE": database,
        "WORKTREE": worktree,
        "BRANCH / STATUS": f"{_git_branch(row)} / {_checkout_status_str(row)}",
        "GIT A/D": git_value,
        "DATABASE_COMPACT": f"{db_mode} {database}".strip(),
    }
    for provider_id in provider_columns:
        values[provider_id] = _fact_text(row, provider_id)
    return values


def _rich_checkout_compact_rows(
    rows: list[CheckoutRow],
    *,
    provider_columns: tuple[str, ...],
    include_worktree: bool,
) -> list[Text]:
    rendered: list[Text] = []
    for row in rows:
        values = _checkout_row_values(row, provider_columns)
        label = "Main checkout" if row.kind == "main" else f"Environment {row.name}"
        lines = [
            label,
            f"  branch={_compact_value(values['BRANCH'], 42)} status={values['STATUS']}",
            f"  database={_compact_value(values['DATABASE_COMPACT'], 52)} git={values['GIT A/D']}",
        ]
        for provider_id in provider_columns:
            lines.append(f"  {provider_id}={_compact_value(values[provider_id], 52)}")
        if include_worktree:
            lines.append(f"  worktree={_compact_value(values['WORKTREE'], 64)}")
        rendered.append(
            Text(sanitize_terminal_text("\n".join(lines), preserve_newlines=True), style="")
        )
    return rendered


def _compact_value(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)] + "…"


def _display_path(path: str) -> str:
    """Shorten only Rich path presentation; machine paths stay absolute."""
    candidate = Path(path)
    try:
        relative = candidate.resolve().relative_to(Path.home().resolve())
    except (OSError, ValueError):
        return path
    return "~" if not relative.parts else f"~/{relative.as_posix()}"


def _checkout_cluster_summary_line(cluster: CheckoutClusterSummary) -> str:
    parts = ["  PostgreSQL", cluster.state.value]
    if cluster.unavailability_reason and cluster.unavailability_reason not in {
        "external_not_owned"
    }:
        parts.append(cluster.unavailability_reason)
        return "  ".join(parts)
    if cluster.mode == "external":
        parts.append("external")
    elif cluster.state is PostgresClusterState.STOPPED:
        parts.append("stopped")
    return "  ".join(parts)


def _cluster_summary_line(cluster: ClusterSnapshot) -> str:
    parts = ["  PostgreSQL", cluster.state.value]
    if cluster.unavailability_reason and cluster.unavailability_reason not in {
        "external_not_owned"
    }:
        parts.append(cluster.unavailability_reason)
        return "  ".join(parts)
    parts.extend(_cluster_identity_parts(cluster))
    parts.extend(_cluster_metrics_parts(cluster.metrics))
    return "  ".join(parts)


def _cluster_identity_parts(cluster: ClusterSnapshot) -> list[str]:
    out: list[str] = []
    container = cluster.container
    if container is not None and container.id is not None:
        out.append(f"container={container.id[:12]}")
        if container.pid is not None:
            scope_prefix = "vm" if container.pid_scope is PidScope.DOCKER_VM else "host"
            out.append(f"pid={scope_prefix}:{container.pid}")
    elif cluster.mode == "external":
        out.append("external")
    elif cluster.state is PostgresClusterState.STOPPED:
        out.append("stopped")
    elif cluster.container is None:
        out.append("missing")
    return out


def _cluster_metrics_parts(metrics: ClusterMetrics | None) -> list[str]:
    if metrics is None:
        return []
    out: list[str] = []
    if metrics.cpu_percent is not None:
        out.append(f"cpu={metrics.cpu_percent:.1f}%")
    if metrics.memory_usage_bytes is not None:
        out.append(f"ram={_human_bytes(metrics.memory_usage_bytes)}")
    if metrics.volume_usage_bytes is not None:
        out.append(f"disk={_human_bytes(metrics.volume_usage_bytes)}")
    return out


def _plan_lines(title: str, plan: dict[str, JsonValue]) -> list[str]:
    """Pure Rich projection for a structured domain/command plan."""
    return [
        title,
        *[
            f"{key}: {json.dumps(value, default=str, sort_keys=True)}"
            for key, value in plan.items()
        ],
    ]


def _env_columns_for_width(width: int) -> tuple[str, ...]:
    """Keep semantic identity columns visible as terminal width decreases."""
    if width < 120:
        return _ENV_LIST_COMPACT_COLUMNS
    if width < 240:
        return _ENV_LIST_MEDIUM_COLUMNS
    return _ENV_LIST_COLUMNS
