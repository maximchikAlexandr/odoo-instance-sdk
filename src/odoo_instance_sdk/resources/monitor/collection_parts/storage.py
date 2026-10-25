from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.db_name import validate_filestore_containment
from odoo_instance_sdk.internal.pg.size import database_size_bytes
from odoo_instance_sdk.internal.storage_footprint import _directory_size, _walk_size
from odoo_instance_sdk.models import ProjectStorageFootprint, SnapshotRequest, SnapshotSection
from odoo_instance_sdk.resources.monitor.planning import (
    _EXPENSIVE_TTL,
    _PROBE_TIMEOUT_SECONDS,
    _ProjectPlan,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult


def _database_identity(host: str | None, port: int | None, database: str) -> tuple[str, int, str]:
    normalized_host = (host or "localhost").casefold()
    if normalized_host in {"127.0.0.1", "::1"}:
        normalized_host = "localhost"
    return normalized_host, port or 5432, database


def _filestore_size(
    path: Path,
    probe_results: dict[str, ProcessResult] | None,
    probe_id: str,
) -> int | None:
    if probe_results is None:
        return _directory_size(path) if path.exists() else 0
    size = _measured(probe_results.get(probe_id))
    return 0 if size is None and not path.exists() else size


def _project_runtime_storage_steps(
    runtime: sqlite3.Row,
    repository: Path,
    measured_databases: set[tuple[str, int, str]],
    measured_filestores: set[Path],
    *,
    du: str,
    postgresql: bool,
) -> tuple[PreparedStep, ...]:
    from odoo_instance_sdk.internal.proc import PreparedStep

    database = runtime["database_name"]
    if not database:
        return ()
    steps: list[PreparedStep] = []
    try:
        from odoo_instance_sdk.project import ProjectConfig, managed_filestore_path

        project = ProjectConfig.load(repository)
        data_dir = managed_filestore_path(project)
        filestore = validate_filestore_containment(data_dir, str(database)).resolve()
    except Exception:
        filestore = None
    if filestore is not None and filestore not in measured_filestores:
        measured_filestores.add(filestore)
        steps.append(
            PreparedStep(
                step_id=f"monitor.{runtime['owner_id']}.storage.filestore",
                argv=(du, "-sb", str(filestore)),
                cwd=str(filestore.parent),
                timeout=_PROBE_TIMEOUT_SECONDS,
                read_only=True,
                text=True,
            )
        )
    if not postgresql:
        return tuple(steps)
    try:
        from odoo_instance_sdk.resources.postgres import PostgresCluster

        cluster = PostgresCluster.from_project(repository)
    except Exception:
        return tuple(steps)
    identity = _database_identity(cluster.endpoint_host, cluster.endpoint_port, str(database))
    if identity in measured_databases:
        return tuple(steps)
    measured_databases.add(identity)
    from odoo_instance_sdk.internal.pg.builder import build_psql_specification

    escaped_database = str(database).replace("'", "''")
    steps.append(
        build_psql_specification(
            step_id=f"monitor.{runtime['owner_id']}.storage.postgres",
            host=cluster.endpoint_host,
            port=cluster.endpoint_port,
            user=cluster._user,
            password=None,
            database=str(database),
            args=("-w", "-c", f"SELECT pg_database_size('{escaped_database}')"),
            _trusted_args=("-t", "-A"),
            timeout=_PROBE_TIMEOUT_SECONDS,
            _allow_missing_user=True,
            _require_binary=False,
        ).prepared_step
    )
    return tuple(steps)


def _catalog_project_runtime_storage_steps(
    runtimes: tuple[sqlite3.Row, ...],
    repositories: dict[str, Path],
    measured_databases: set[tuple[str, int, str]],
    measured_filestores: set[Path],
    *,
    du: str,
    sections: frozenset[SnapshotSection],
    project_id: str | None,
    request: SnapshotRequest | None,
) -> tuple[PreparedStep, ...]:
    if "storage" not in sections:
        return ()
    steps: list[PreparedStep] = []
    for runtime in runtimes:
        name = str(runtime["owner_id"])
        repository = repositories.get(name)
        if repository is None or not runtime["database_name"]:
            continue
        if project_id is not None and name != project_id:
            continue
        if request is not None and request.project_ids and name not in request.project_ids:
            continue
        steps.extend(
            _project_runtime_storage_steps(
                runtime,
                repository,
                measured_databases,
                measured_filestores,
                du=du,
                postgresql="postgresql" in sections,
            )
        )
    return tuple(steps)


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _measured(result: ProcessResult | None) -> int | None:
    if result is None or result.returncode != 0:
        return None
    try:
        return int(str(result.stdout).strip().split()[0])
    except (TypeError, ValueError, IndexError):
        return None


class _StorageMixin:
    if TYPE_CHECKING:
        _project_storage_cache: dict[str, tuple[float, ProjectStorageFootprint]]

    def _collect_project_storage(  # noqa: C901
        self,
        plan: _ProjectPlan,
        sections: frozenset[SnapshotSection],
        probe_results: dict[str, ProcessResult] | None = None,
    ) -> ProjectStorageFootprint:
        if "storage" not in sections:
            return ProjectStorageFootprint(total_bytes=None, complete=False)
        now = time.monotonic()
        cached = self._project_storage_cache.get(plan.project_id)
        if "postgresql" in sections and cached is not None and now - cached[0] < _EXPENSIVE_TTL:
            return cached[1]

        known: list[int] = []
        complete = True
        measured_dirs: list[Path] = []
        seen_paths: set[Path] = set()

        def add_path(path: Path, probe_id: str | None, *, required: bool) -> None:
            nonlocal complete
            try:
                canonical = path.expanduser().resolve()
            except OSError:
                complete = False
                return
            if canonical in seen_paths or any(
                _contains(parent, canonical) for parent in measured_dirs
            ):
                return
            seen_paths.add(canonical)
            try:
                if canonical.is_dir():
                    result = (
                        probe_results.get(probe_id)
                        if probe_results is not None and probe_id
                        else None
                    )
                    size = _measured(result) if probe_results is not None and probe_id else None
                    if size is None:
                        size = (
                            _directory_size(canonical)
                            if probe_results is None
                            else _walk_size(canonical)
                        )
                    if size is not None:
                        measured_dirs.append(canonical)
                elif canonical.is_file():
                    size = canonical.stat().st_size
                elif required:
                    size = None
                else:
                    return
            except OSError:
                size = None
            if size is None:
                complete = False
            else:
                known.append(size)

        add_path(plan.repo_root, f"monitor.{plan.project_id}.storage.project", required=True)
        for environment in plan.environments:
            row = environment.row
            if str(row["state"]) == "removed":
                continue
            env_id = str(row["id"])
            config_path = Path(str(row["generated_config_path"]))
            root = config_path.parent
            add_path(
                Path(str(row["worktree_path"])), f"monitor.{env_id}.storage.worktree", required=True
            )
            if bool(int(row["python_environment_owned"])):
                add_path(
                    Path(str(row["python_environment_path"])),
                    f"monitor.{env_id}.storage.python",
                    required=True,
                )
            add_path(config_path, None, required=False)
            add_path(Path(str(row["dependency_lock_path"])), None, required=False)
            add_path(root / "odoo.log", None, required=False)
            add_path(root / "cache", f"monitor.{env_id}.storage.cache", required=False)
            add_path(root / "artifacts", f"monitor.{env_id}.storage.artifacts", required=False)

        backup_bytes: int | None = 0
        backup_total_addition = 0
        seen_backups: set[tuple[int, int]] = set()
        backup_count = 0
        backup_measured = False
        for backup in getattr(plan, "backups", ()):
            if str(backup["state"]) != "available":
                continue
            backup_count += 1
            value = backup["path"]
            try:
                path = Path(str(value)).expanduser().resolve() if value else None
                if path is None or not path.is_file():
                    complete = False
                    backup_bytes = None
                    continue
                metadata = path.stat()
                backup_identity = (metadata.st_dev, metadata.st_ino)
                if backup_identity in seen_backups:
                    continue
                seen_backups.add(backup_identity)
                file_bytes = metadata.st_size
                backup_measured = True
                if backup_bytes is not None:
                    backup_bytes += file_bytes
                if not any(_contains(parent, path) for parent in measured_dirs):
                    backup_total_addition += file_bytes
            except OSError:
                complete = False
                backup_bytes = None
        if backup_count and backup_measured:
            known.append(backup_total_addition)

        seen_databases: set[tuple[str, int, str]] = set()
        seen_filestores: set[Path] = set()
        for environment in plan.environments:
            row = environment.row
            if str(row["state"]) == "removed":
                continue
            database = (
                row["target_db_name"] if str(row["db_mode"]) == "copy" else row["source_db_name"]
            )
            if not database:
                continue
            env_id = str(row["id"])
            try:
                from odoo_instance_sdk.models import StartConfig

                config = StartConfig.from_odoo_config(str(row["generated_config_path"]))
            except Exception:
                complete = False
                continue
            identity = _database_identity(
                config.db_host,
                config.db_port,
                str(database),
            )
            if identity not in seen_databases:
                seen_databases.add(identity)
                db_bytes: int | None = None
                if "postgresql" in sections:
                    if probe_results is not None:
                        db_bytes = _measured(
                            probe_results.get(f"monitor.{env_id}.storage.postgres")
                        )
                    else:
                        db_bytes = database_size_bytes(
                            host=config.db_host,
                            port=config.db_port or 5432,
                            user=config.db_user,
                            password=config.db_password,
                            database_name=str(database),
                        )
                if db_bytes is None:
                    complete = False
                else:
                    known.append(db_bytes)
            if config.data_dir is not None:
                try:
                    filestore = validate_filestore_containment(
                        Path(config.data_dir), str(database)
                    ).resolve()
                except (ConfigError, OSError):
                    complete = False
                    continue
                if filestore in seen_filestores or any(
                    _contains(parent, filestore) for parent in measured_dirs
                ):
                    continue
                seen_filestores.add(filestore)
                filestore_bytes = _filestore_size(
                    filestore, probe_results, f"monitor.{env_id}.storage.filestore"
                )
                if filestore_bytes is None:
                    complete = False
                else:
                    known.append(filestore_bytes)
            else:
                complete = False

        project_runtime = getattr(plan, "project_runtime", None)
        project_database = project_runtime["database_name"] if project_runtime is not None else None
        if project_database:
            cluster = plan.cluster
            if cluster is None:
                complete = False
            else:
                identity = _database_identity(
                    cluster.endpoint_host, cluster.endpoint_port, str(project_database)
                )
                if identity not in seen_databases:
                    seen_databases.add(identity)
                    db_bytes = None
                    if "postgresql" in sections:
                        if probe_results is not None:
                            db_bytes = _measured(
                                probe_results.get(f"monitor.{plan.project_id}.storage.postgres")
                            )
                        else:
                            db_bytes = database_size_bytes(
                                host=cluster.endpoint_host,
                                port=cluster.endpoint_port,
                                user=cluster._user,
                                password=None,
                                database_name=str(project_database),
                            )
                    if db_bytes is None:
                        complete = False
                    else:
                        known.append(db_bytes)
            try:
                from odoo_instance_sdk.project import ProjectConfig, managed_filestore_path

                project = ProjectConfig.load(plan.repo_root)
                data_dir = managed_filestore_path(project)
                filestore = validate_filestore_containment(
                    data_dir, str(project_database)
                ).resolve()
            except Exception:
                complete = False
            else:
                if filestore not in seen_filestores and not any(
                    _contains(parent, filestore) for parent in measured_dirs
                ):
                    seen_filestores.add(filestore)
                    filestore_bytes = _filestore_size(
                        filestore,
                        probe_results,
                        f"monitor.{plan.project_id}.storage.filestore",
                    )
                    if filestore_bytes is None:
                        complete = False
                    else:
                        known.append(filestore_bytes)

        total = sum(known) if known else None
        footprint = ProjectStorageFootprint(
            total_bytes=total,
            complete=complete and total is not None,
            backup_bytes=backup_bytes,
        )
        if "postgresql" in sections:
            self._project_storage_cache[plan.project_id] = (now, footprint)
        return footprint
