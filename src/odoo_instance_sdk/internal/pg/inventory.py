"""CLI-private PostgreSQL database inventory and provenance projection."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, cast

import msgspec

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.pg.builder import build_psql_specification

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command, JsonValue
    from odoo_instance_sdk.internal.proc import ProcessExecutor, ProcessResult, RunContext
    from odoo_instance_sdk.resources.instance import OdooInstance
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


_INVENTORY_STEP = "database.list.inventory"


class _CatalogUnset:
    """Sentinel for preserving the legacy lazy-catalog behavior."""


_CATALOG_UNSET = _CatalogUnset()
_INVENTORY_SQL = """
SELECT COALESCE(json_agg(row ORDER BY row->>'name'), '[]'::json)
FROM (
  SELECT json_build_object(
    'name', d.datname,
    'logical_size_bytes', pg_database_size(d.datname),
    'active_sessions', (
      SELECT count(*)::bigint FROM pg_stat_activity a
      WHERE a.datname = d.datname AND a.pid <> pg_backend_pid()
    )
  ) AS row
  FROM pg_database d
  WHERE NOT d.datistemplate AND d.datallowconn
) inventory;
"""


class DatabaseInventoryItem(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One exact database identity and its read-only local relationships."""

    cluster: str
    cluster_id: str | None
    name: str
    logical_size_bytes: int | None
    active_sessions: int
    is_default: bool
    environment_ids: tuple[str, ...] = ()
    runtime_bindings: tuple[str, ...] = ()
    restore_backup_ids: tuple[str, ...] = ()
    origin: Literal["restore", "unknown"] = "unknown"


class DatabaseInventoryResult(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    """Deterministic project-cluster database inventory."""

    __odcli_structural_paths__: ClassVar[frozenset[str]] = frozenset({"tracked"})

    cluster: str
    databases: tuple[DatabaseInventoryItem, ...]
    tracked: bool = False


def _credentials(instance: OdooInstance) -> tuple[str, int, str | None, str | None]:
    config = instance.config
    cluster = getattr(instance, "_postgres_cluster", None)
    if cluster is None:
        raise ConfigError("database inventory requires the resolved project PostgreSQL cluster")
    host = cast("str", getattr(cluster, "endpoint_host", None) or config.db_host or "127.0.0.1")
    port = int(getattr(cluster, "endpoint_port", None) or config.db_port or 5432)
    user = cast("str | None", getattr(cluster, "_user", None) or config.db_user)
    password = cast("str | None", getattr(cluster, "_password", None) or config.db_password)
    if password is None and getattr(cluster, "owned", False):
        password_file = getattr(cluster, "password_file", None)
        if isinstance(password_file, Path) and password_file.is_file():
            password = password_file.read_text(encoding="utf-8").strip() or None
    return host, port, user, password


def _decode_rows(result: ProcessResult) -> tuple[dict[str, JsonValue], ...]:
    if result.returncode != 0:
        raise ConfigError("database inventory query failed")
    raw = (
        result.stdout.decode(errors="replace")
        if isinstance(result.stdout, bytes)
        else result.stdout
    )
    try:
        payload = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ConfigError("database inventory query returned invalid data") from exc
    if not isinstance(payload, list):
        raise ConfigError("database inventory query returned invalid rows")
    rows: list[dict[str, JsonValue]] = []
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ConfigError("database inventory query returned invalid database identity")
        size = item.get("logical_size_bytes")
        sessions = item.get("active_sessions", 0)
        if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
            raise ConfigError("database inventory query returned invalid logical size")
        if not isinstance(sessions, int) or isinstance(sessions, bool) or sessions < 0:
            raise ConfigError("database inventory query returned invalid session count")
        rows.append({"name": item["name"], "logical_size_bytes": size, "active_sessions": sessions})
    return tuple(rows)


def _relationships(
    catalog: BackupCatalog,
    *,
    host: str,
    port: int,
    cluster_id: str | None,
    names: set[str],
) -> dict[str, tuple[tuple[str, ...], tuple[str, ...], Literal["restore", "unknown"]]]:
    restored: dict[str, tuple[list[str], list[str]]] = {name: ([], []) for name in names}
    for row in catalog._list_restore_bindings(host, port):
        name = str(row["database_name"])
        if name not in restored:
            continue
        backup_ids, proven_clusters = restored[name]
        row_cluster = row["cluster_id"]
        if row_cluster is None and isinstance(row["backup_id"], str):
            backup_ids.append(row["backup_id"])
        elif cluster_id is not None and isinstance(row_cluster, str) and row_cluster == cluster_id:
            if isinstance(row["backup_id"], str):
                backup_ids.append(row["backup_id"])
            proven_clusters.append(row_cluster)
    return {
        name: (
            tuple(dict.fromkeys(backup_ids)),
            tuple(dict.fromkeys(proven_clusters)),
            "restore" if proven_clusters else "unknown",
        )
        for name, (backup_ids, proven_clusters) in restored.items()
    }


def build_database_inventory_command(
    instance: OdooInstance,
    project_root: str | Path,
    *,
    tracked: bool = False,
    catalog: BackupCatalog | None | _CatalogUnset = _CATALOG_UNSET,
    executor: ProcessExecutor | None = None,
) -> Command[DatabaseInventoryResult]:
    """Capture a direct PostgreSQL inventory without Odoo reconciliation."""
    from odoo_instance_sdk.execution import Command, ExecutionPlan
    from odoo_instance_sdk.internal.proc import (
        ProcessResult,
        SubprocessExecutor,
        prepared_command,
    )
    from odoo_instance_sdk.project import ProjectConfig
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    project = ProjectConfig.load(Path(project_root).resolve())
    cluster = getattr(instance, "_postgres_cluster", None)
    if cluster is None:
        raise ConfigError("database inventory requires the resolved project PostgreSQL cluster")
    host, port, user, password = _credentials(instance)
    step = build_psql_specification(
        host=host,
        port=port,
        user=user,
        password=password,
        database="postgres",
        stdin=_INVENTORY_SQL.encode(),
        timeout=30.0,
        mode="captured",
        step_id=_INVENTORY_STEP,
        _trusted_args=("-q", "-t", "-A"),
        _read_only=True,
    ).prepared_step
    process_executor = executor or SubprocessExecutor()

    def run(context: RunContext[DatabaseInventoryResult]) -> DatabaseInventoryResult:
        result = context.process(_INVENTORY_STEP)
        if not isinstance(result, ProcessResult):
            raise ConfigError("database inventory query returned no process result")
        rows = _decode_rows(result)
        source_catalog = catalog
        relationships: dict[
            str, tuple[tuple[str, ...], tuple[str, ...], Literal["restore", "unknown"]]
        ]
        if source_catalog is _CATALOG_UNSET:
            source_catalog = instance._client.get_catalog()
        if not isinstance(source_catalog, BackupCatalog):
            relationships = {str(row["name"]): ((), (), "unknown") for row in rows}
            environment_rows: tuple[sqlite3.Row, ...] = ()
        else:
            claim = source_catalog._get_postgres_cluster(str(getattr(cluster, "_project_id", "")))
            active_id = (
                str(claim.cluster_id)
                if claim is not None and claim.state == "active" and cluster.mode == "compose"
                else None
            )
            relationships = _relationships(
                source_catalog,
                host=host,
                port=port,
                cluster_id=active_id,
                names={str(row["name"]) for row in rows},
            )
            environment_rows = tuple(source_catalog.list_environments(include_removed=False))
        endpoint = str(getattr(cluster, "endpoint", f"{host}:{port}"))
        items: list[DatabaseInventoryItem] = []
        default = project.default_source_database
        for row in rows:
            name = str(row["name"])
            backups, proven_clusters, origin = relationships[name]
            environment_ids = tuple(
                str(env["id"])
                for env in environment_rows
                if env["source_db_name"] == name or env["target_db_name"] == name
            )
            item = DatabaseInventoryItem(
                cluster=endpoint,
                cluster_id=proven_clusters[0] if proven_clusters else None,
                name=name,
                logical_size_bytes=cast("int | None", row["logical_size_bytes"]),
                active_sessions=cast("int", row["active_sessions"]),
                is_default=name == default,
                environment_ids=environment_ids,
                runtime_bindings=tuple(f"{env_id}:{name}" for env_id in environment_ids),
                restore_backup_ids=backups,
                origin=origin,
            )
            if not tracked or item.origin == "restore":
                items.append(item)
        items.sort(key=lambda item: item.name)
        return DatabaseInventoryResult(cluster=endpoint, databases=tuple(items), tracked=tracked)

    plan = ExecutionPlan(steps=(step.public_projection(),))
    return Command.from_prepared(
        plan,
        prepared_command(run, (step,), executor=process_executor),
    )


__all__ = ["DatabaseInventoryItem", "DatabaseInventoryResult", "build_database_inventory_command"]
