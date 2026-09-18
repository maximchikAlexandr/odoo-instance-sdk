from __future__ import annotations

import configparser
import contextlib
import json
import os
import shutil
import sqlite3
import subprocess
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from odoo_instance_sdk.exceptions import (
    ConfigError,
    EnvironmentConflictError,
    PlanValidationError,
)
from odoo_instance_sdk.internal.address import AddressState, probe_address
from odoo_instance_sdk.internal.applied_settings import (
    AppliedSettingsError,
    decode_applied_settings,
    encode_applied_settings,
)
from odoo_instance_sdk.internal.dependency_sync import (
    build_trusted_sync_argv,
)
from odoo_instance_sdk.internal.odoo_config import (
    parse_db_names,
    parse_odoo_config,
)
from odoo_instance_sdk.internal.paths import resolve_environment_artifact_paths
from odoo_instance_sdk.models import (
    Backup,
    BackupFormat,
    DevelopmentEnvironment,
    EnvironmentDatabaseMode,
    EnvironmentState,
    PostgresClusterState,
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment.checkout_planning import (
    _APPLIED_CONFIG_BINDINGS as _APPLIED_CONFIG_BINDINGS,
)
from odoo_instance_sdk.resources.environment.checkout_planning import (
    _CHECKOUT_WORKTREE_TIMEOUT as _CHECKOUT_WORKTREE_TIMEOUT,
)
from odoo_instance_sdk.resources.environment.checkout_planning import (
    EnvironmentSelector as EnvironmentSelector,
)
from odoo_instance_sdk.resources.environment.checkout_planning import (
    T as T,
)
from odoo_instance_sdk.resources.environment.checkout_planning import (
    _CheckoutPlan as _CheckoutPlan,
)
from odoo_instance_sdk.resources.environment.checkout_planning import (
    _CheckoutPlanningState as _CheckoutPlanningState,
)
from odoo_instance_sdk.resources.environment.checkout_planning import (
    _CheckoutSnapshot as _CheckoutSnapshot,
)
from odoo_instance_sdk.resources.environment.checkout_planning import (
    _configured_addons as _configured_addons,
)
from odoo_instance_sdk.resources.environment.checkout_planning import (
    _dependency_evidence as _dependency_evidence,
)
from odoo_instance_sdk.resources.environment.checkout_planning import (
    _ExpressionApi as _ExpressionApi,
)
from odoo_instance_sdk.resources.environment.checkout_planning import (
    _ExpressionResult as _ExpressionResult,
)
from odoo_instance_sdk.resources.environment.checkout_planning import (
    _git_ticket as _git_ticket,
)
from odoo_instance_sdk.resources.environment.checkout_planning import (
    _PgAdminCommandInputs as _PgAdminCommandInputs,
)
from odoo_instance_sdk.resources.environment.checkout_planning import (
    _PlanningError as _PlanningError,
)
from odoo_instance_sdk.resources.environment.checkout_planning import (
    _PlanningOutcome as _PlanningOutcome,
)
from odoo_instance_sdk.storage.backup_catalog import normalize_db_host

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        ProcessResult,
        RunContext,
        Step,
    )
    from odoo_instance_sdk.resources.postgres import PostgresCluster
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


def _generated_applied_components(
    plan: _CheckoutPlan,
) -> tuple[Mapping[str, str] | None, tuple[str, ...] | None]:
    if plan.source_config is None:
        return None, None
    if not plan.generated_config.is_file():
        raise ConfigError(f"generated config is unavailable: {plan.generated_config}")
    try:
        config = parse_odoo_config(plan.generated_config)
    except (OSError, configparser.Error, ValueError) as exc:
        raise ConfigError(f"generated config is unreadable: {plan.generated_config}") from exc
    addons = _configured_addons(config)
    managed = {key: value for key, value in config.items() if key not in _APPLIED_CONFIG_BINDINGS}
    return managed, addons


def _checkout_applied_settings(plan: _CheckoutPlan) -> str:
    managed_config, addons = _generated_applied_components(plan)
    return encode_applied_settings(
        python={
            "selector": str(plan.python_selector) if plan.python_selector is not None else None,
            "path": plan.python_path,
            "owned": plan.python_owned,
        },
        dependencies=_dependency_evidence(plan.dependency_inputs),
        managed_config=managed_config,
        addons=addons,
        git={"ticket": _git_ticket(plan.branch), "branch": plan.branch, "base": plan.base_ref},
    )


def _known_applied_component(
    components: Mapping[str, JsonValue], name: str, field: str
) -> JsonValue | None:
    value = components.get(name)
    if not isinstance(value, dict) or value.get("status") != "known":
        return None
    return value if not field else value.get(field)


def _sync_applied_settings(
    catalog: BackupCatalog,
    env: DevelopmentEnvironment,
    project: ProjectConfig,
    inputs: Sequence[str],
) -> str:
    row = catalog.get_environment(str(env.id))
    if row is None:
        raise ConfigError("environment row disappeared during sync")
    raw = row["applied_settings_json"]
    if not isinstance(raw, str):
        raise ConfigError("stored applied settings are malformed")
    try:
        document = decode_applied_settings(raw)
    except AppliedSettingsError as exc:
        raise ConfigError("stored applied settings are malformed") from exc
    components = document.get("components")
    if not isinstance(components, dict):
        raise ConfigError("stored applied settings are malformed")
    managed_config = _known_applied_component(components, "odoo", "values")
    addons = _known_applied_component(components, "addons", "paths")
    git = _known_applied_component(components, "git", "")
    managed_values = managed_config if isinstance(managed_config, dict) else None
    addon_values = tuple(str(path) for path in addons) if isinstance(addons, list) else None
    git_values = (
        {
            field: git[field]
            for field in ("ticket", "branch", "base")
            if isinstance(git, dict) and isinstance(git.get(field), str)
        }
        if isinstance(git, dict)
        else None
    )
    return encode_applied_settings(
        python={
            "selector": str(project.python) if project.python is not None else None,
            "path": env.python_environment_path,
            "owned": env.python_environment_owned,
        },
        dependencies=_dependency_evidence(inputs),
        managed_config=cast("Mapping[str, JsonValue] | None", managed_values),
        addons=addon_values,
        git=cast("Mapping[str, JsonValue] | None", git_values),
    )


def _decode_runtime_json(raw: str | None) -> dict[str, str]:

    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: str(v) for k, v in data.items() if isinstance(v, str)}


def _http_fields_from_generated_config(generated_config_path: str) -> tuple[str, int]:
    """Read http_interface/http_port from the generated odoo.conf (single source of truth)."""
    try:
        cfg = parse_odoo_config(generated_config_path)
    except Exception:
        return "127.0.0.1", 8069
    http_interface = cfg.get("http_interface") or "127.0.0.1"
    http_port_raw = cfg.get("http_port", "8069")
    try:
        http_port = int(http_port_raw)
    except ValueError:
        http_port = 8069
    return http_interface, http_port


def _row_to_env(row: sqlite3.Row) -> DevelopmentEnvironment:
    def _get(key: str) -> JsonValue:
        r = row
        return cast("JsonValue", r[key])

    def _opt(key: str) -> str | None:
        r = row
        try:
            v = cast("JsonValue", r[key])
        except (KeyError, IndexError):
            return None
        if v is None:
            return None
        return str(v)

    backup_raw: JsonValue = None
    with contextlib.suppress(KeyError, IndexError):
        backup_raw = cast("JsonValue", row["backup_id"])
    env_id = str(_get("id"))
    python_owned = bool(_get("python_environment_owned"))
    artifacts = resolve_environment_artifact_paths(
        environment_id=env_id,
        repository_root=str(_get("repository_root")),
        git_common_dir=str(_get("git_common_dir")),
        python_environment_owned=python_owned,
        python_environment_path=str(_get("python_environment_path")),
    )
    http_interface, http_port = _http_fields_from_generated_config(
        str(artifacts.generated_config_path)
    )

    return DevelopmentEnvironment(
        id=uuid.UUID(env_id),
        name=str(_get("name")),
        repository_root=str(_get("repository_root")),
        git_common_dir=str(_get("git_common_dir")),
        branch=str(_get("branch")),
        base_ref=str(_get("base_ref")),
        worktree_path=str(artifacts.worktree_path),
        generated_config_path=str(artifacts.generated_config_path),
        python_environment_path=str(artifacts.python_environment_path),
        python_environment_owned=python_owned,
        dependency_lock_path=str(artifacts.dependency_lock_path),
        http_interface=http_interface,
        http_port=http_port,
        db_mode=EnvironmentDatabaseMode(str(_get("db_mode"))),
        source_db_name=_opt("source_db_name"),
        target_db_name=_opt("target_db_name"),
        backup_id=uuid.UUID(str(backup_raw)) if backup_raw is not None else None,
        state=EnvironmentState(str(_get("state"))),
        created_at=datetime.fromisoformat(str(_get("created_at"))),
        last_used_at=datetime.fromisoformat(str(_get("last_used_at")))
        if _opt("last_used_at")
        else None,
        removed_at=datetime.fromisoformat(str(_get("removed_at"))) if _opt("removed_at") else None,
        last_error=_opt("last_error"),
    )


def _row_to_backup(row: sqlite3.Row) -> Backup | None:
    r = row
    try:
        path = cast("JsonValue", r["path"])
    except (KeyError, IndexError):
        return None
    if path is None or not Path(str(path)).is_file():
        return None
        size_raw: JsonValue = None
    with contextlib.suppress(KeyError, IndexError):
        size_raw = cast("JsonValue", r["size_bytes"])
    return Backup(
        id=uuid.UUID(str(r["id"])),
        source_base_url=str(r["source_base_url"]),
        database_name=str(r["database_name"]),
        format=BackupFormat(str(r["format"])),
        filestore_requested=bool(r["filestore_requested"]),
        path=str(path),
        filename=str(r["filename"]) if r["filename"] else "",
        size_bytes=int(str(size_raw)) if size_raw is not None else 0,
        sha256=str(r["sha256"]) if r["sha256"] else "",
        downloaded_at=datetime.fromisoformat(str(r["downloaded_at"])),
    )


def _infer_single_db(cfg: dict[str, str]) -> str | None:
    names = parse_db_names(cfg.get("db_name"))
    if len(names) == 1:
        return names[0]
    return None


def _resolve_python_bin(py: str | Path, repo_root: Path) -> str:
    s = str(py)
    p = Path(s)
    if not p.is_absolute():
        candidate = shutil.which(s)
        if candidate:
            return candidate
        p = (repo_root / p).resolve()
    return str(p)


def _owned_python_executable(venv: Path) -> str:
    if os.name == "nt":
        return str(venv / "Scripts" / "python.exe")
    return str(venv / "bin" / "python")


def _is_venv(pybin: str) -> bool:
    try:
        from odoo_instance_sdk.internal.proc import ProcessExecutionError, run_captured

        proc = run_captured(
            [pybin, "-c", "import sys; print(sys.prefix != sys.base_prefix)"],
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return False
        return isinstance(proc.stdout, str) and proc.stdout.strip().lower() == "true"
    except (OSError, subprocess.TimeoutExpired, ProcessExecutionError):
        return False


def _port_free(host: str, port: int) -> bool:
    return probe_address(host, port) is AddressState.FREE


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor) if path.is_absolute() else Path()
    for part in path.parts[1:] if path.is_absolute() else path.parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _validate_owned_artifact(path: Path, expected: Path, kind: Literal["file", "dir"]) -> None:
    if path.absolute() != expected.absolute():
        raise EnvironmentConflictError("unsafe_environment_path", f"unexpected {kind} path: {path}")
    if _has_symlink_component(path):
        raise EnvironmentConflictError("unsafe_environment_path", f"symlinked {kind} path: {path}")
    if not path.exists():
        return
    valid = path.is_file() if kind == "file" else path.is_dir()
    if not valid:
        raise EnvironmentConflictError("unsafe_environment_path", f"unexpected {kind} type: {path}")


def _restore_audit_backup(
    client: OdooClient,
    config: Mapping[str, str],
    database: str | None,
    *,
    available: bool,
) -> Backup | None:
    """Read restore provenance, using an existing catalog or SQLite read-only mode."""
    if database is None:
        return None
    host = config.get("db_host")
    try:
        port = int(config.get("db_port", "5432"))
    except ValueError:
        port = 5432
    catalog = getattr(client, "_catalog", None)
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    if isinstance(catalog, BackupCatalog):
        if available:
            return catalog.latest_restore(host, port, database)
        return catalog.latest_restore_provenance(host, port, database)

    from odoo_instance_sdk.internal.paths import get_catalog_path

    path = get_catalog_path()
    if not path.is_file():
        return None
    return _restore_audit_backup_from_sqlite(path, config, database, available=available)


def _restore_audit_backup_from_sqlite(
    path: Path,
    config: Mapping[str, str],
    database: str,
    *,
    available: bool,
) -> Backup | None:
    host = config.get("db_host")
    try:
        port = int(config.get("db_port", "5432"))
    except ValueError:
        port = 5432
    query = (
        "SELECT b.* FROM restores r INNER JOIN backups b ON b.id = r.backup_id "
        "WHERE r.db_host=? AND r.db_port=? AND r.database_name=?"
    )
    if available:
        query += " AND b.state='available' AND b.path IS NOT NULL"
    query += " ORDER BY r.restored_at DESC, r.sequence DESC LIMIT 1"
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        row = conn.execute(query, (normalize_db_host(host), port, database)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        if conn is not None:
            conn.close()
    if row is None:
        return None
    downloaded_at = row["downloaded_at"] or row["started_at"]
    if downloaded_at is None:
        return None
    return Backup(
        id=uuid.UUID(str(row["id"])),
        source_base_url=str(row["source_base_url"]),
        database_name=str(row["database_name"]),
        format=BackupFormat(str(row["format"])),
        filestore_requested=bool(row["filestore_requested"]),
        path=str(row["path"] or ""),
        filename=str(row["filename"] or ""),
        size_bytes=int(row["size_bytes"] or 0),
        sha256=str(row["sha256"] or ""),
        downloaded_at=datetime.fromisoformat(str(downloaded_at)),
        source_git_branch=(
            str(row["source_git_branch"]) if row["source_git_branch"] is not None else None
        ),
    )


def _checkout_steps(plan: _CheckoutPlan) -> tuple[Step, ...]:
    """Return the private steps that are projected and consumed by checkout."""
    from odoo_instance_sdk.internal.pg.builder import build_psql_specification
    from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep

    steps: list[Step] = [
        PreparedStep(
            step_id="checkout.validate.git.toplevel",
            argv=("git", "-C", str(plan.repo_root), "rev-parse", "--show-toplevel"),
            read_only=True,
        ),
        PreparedStep(
            step_id="checkout.validate.git.common-dir",
            argv=("git", "-C", str(plan.repo_root), "rev-parse", "--git-common-dir"),
            read_only=True,
        ),
        PreparedStep(
            step_id="checkout.validate.git.base",
            argv=(
                "git",
                "-C",
                str(plan.repo_root),
                "rev-parse",
                "--verify",
                plan.base_ref,
            ),
            read_only=True,
        ),
        PreparedAction("checkout.catalog"),
        PreparedStep(
            step_id="checkout.worktree",
            argv=plan.worktree_argv,
            cwd=str(plan.repo_root),
            timeout=_CHECKOUT_WORKTREE_TIMEOUT,
            mode="captured",
            mutating=True,
        ),
    ]
    if plan.branch_revalidator is not None:
        # Ticket allocation revalidation is part of the immutable checkout
        # boundary.  Capture its Git reads here so the callback cannot open an
        # unplanned default ``process`` step while the command is running.
        steps.extend(
            (
                PreparedStep(
                    step_id="checkout.ticket.local-heads",
                    argv=(
                        "git",
                        "-C",
                        str(plan.repo_root),
                        "for-each-ref",
                        "--format=%(refname:strip=2)",
                        "refs/heads",
                    ),
                    cwd=str(plan.repo_root),
                    read_only=True,
                ),
                PreparedStep(
                    step_id="checkout.ticket.remote-heads",
                    argv=(
                        "git",
                        "-C",
                        str(plan.repo_root),
                        "ls-remote",
                        "--heads",
                        "origin",
                        plan.branch,
                        f"{plan.branch}_*",
                    ),
                    cwd=str(plan.repo_root),
                    read_only=True,
                ),
            )
        )
    if plan.source_config is not None:
        steps.append(PreparedAction("checkout.generated_config"))
    if plan.options.create_venv and plan.python_selector is not None:
        steps.append(
            PreparedStep(
                step_id="checkout.venv",
                argv=("uv", "venv", str(plan.venv), "--python", str(plan.python_selector)),
                cwd=str(plan.repo_root),
                mode="captured",
                mutating=True,
            )
        )
    if plan.hash_lock is not None:
        install_argv = build_trusted_sync_argv(_owned_python_executable(plan.venv), plan.hash_lock)
    elif plan.dependency_inputs:
        steps.append(
            PreparedStep(
                step_id="checkout.dependencies.compile",
                argv=(
                    "uv",
                    "pip",
                    "compile",
                    *plan.dependency_inputs,
                    "-o",
                    str(plan.dependency_lock),
                ),
                cwd=str(plan.worktree),
                mode="captured",
                mutating=True,
            )
        )
        install_argv = (
            (
                "uv",
                "pip",
                "sync",
                "--python",
                str(Path(plan.python_path) / "bin" / "python"),
                str(plan.dependency_lock),
            )
            if plan.python_owned
            else (
                "uv",
                "pip",
                "install",
                "--python",
                plan.python_path,
                "-r",
                str(plan.dependency_lock),
            )
        )
    else:
        install_argv = None
    if install_argv is not None:
        steps.append(
            PreparedStep(
                step_id="checkout.dependencies.install",
                argv=install_argv,
                cwd=str(plan.worktree),
                mode="captured",
                mutating=True,
            )
        )
    if plan.python_owned:
        steps.append(
            PreparedStep(
                step_id="checkout.runtime.preflight",
                argv=(_owned_python_executable(plan.venv), plan.odoo_bin, "--help"),
                cwd=plan.runtime_cwd,
                timeout=30.0,
                read_only=True,
            )
        )
    if plan.db_mode is EnvironmentDatabaseMode.COPY and plan.target_database is not None:
        raw_port = plan.config_values.get("db_port")
        try:
            db_port = int(raw_port) if raw_port else 5432
        except ValueError:
            db_port = 5432
        escaped_database = plan.target_database.replace("'", "''")
        steps.extend(
            build_psql_specification(
                step_id=step_id,
                host=plan.config_values.get("db_host"),
                port=db_port,
                user=plan.config_values.get("db_user"),
                password=plan.config_values.get("db_password"),
                database="postgres",
                args=(
                    "-c",
                    f"SELECT 1 FROM pg_database WHERE datname='{escaped_database}'",
                ),
                _trusted_args=("-t", "-A"),
                timeout=30.0,
                _require_binary=False,
            ).prepared_step
            for step_id in (
                "database.restore.exists-before",
                "database.restore.exists-after",
            )
        )
    steps.extend(
        (
            PreparedAction("checkout.database"),
            PreparedStep(
                step_id="checkout.cleanup.worktree",
                argv=(
                    "git",
                    "-C",
                    str(plan.repo_root),
                    "worktree",
                    "remove",
                    str(plan.worktree),
                ),
                timeout=30.0,
                mutating=True,
            ),
            PreparedAction("checkout.cleanup"),
        )
    )
    return tuple(steps)


def _pgadmin_cluster_snapshot(selector: EnvironmentSelector) -> PostgresCluster | None:
    """Capture the selected compose object once for command construction."""
    if not isinstance(selector, DevelopmentEnvironment):
        return None
    selected_database = (
        selector.target_db_name
        if selector.db_mode is EnvironmentDatabaseMode.COPY
        else selector.source_db_name
    )
    if selected_database is None:
        return None
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    try:
        return PostgresCluster.from_project(Path(selector.repository_root))
    except Exception:
        return None


def _pgadmin_captured_cluster_state(context: RunContext[T]) -> PostgresClusterState:
    """Consume the captured finite Compose status phase exactly once."""
    result = cast("ProcessResult", context.process("pgadmin.postgres.status.ps"))
    if result.returncode != 0:
        if context.planned("pgadmin.postgres.status.health"):
            context.skip("pgadmin.postgres.status.health")
        return PostgresClusterState.UNKNOWN
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    if not any(line.strip() for line in stdout.splitlines()):
        if context.planned("pgadmin.postgres.status.health"):
            context.skip("pgadmin.postgres.status.health")
        return PostgresClusterState.STOPPED
    health = cast("ProcessResult", context.process("pgadmin.postgres.status.health"))
    if health.returncode == 0:
        return PostgresClusterState.HEALTHY
    return PostgresClusterState.STARTING


def _skip_planned_pgadmin_database_probe() -> None:
    """Account a fallback-only probe when preflight exits before database lookup."""
    from odoo_instance_sdk.internal.proc import active_context

    context = active_context()
    if (
        context is not None
        and context.planned("pgadmin.database.exists.psql")
        and not context.consumed("pgadmin.database.exists.psql")
    ):
        context.skip("pgadmin.database.exists.psql")


def _pgadmin_command_steps(
    selector: EnvironmentSelector,
    *,
    cluster: PostgresCluster | None = None,
    inputs: _PgAdminCommandInputs | None = None,
    include_reconciliation: bool = False,
) -> tuple[PreparedStep | PreparedAction, ...]:
    """Describe the pgAdmin child-process boundary from captured inputs."""
    if not isinstance(selector, DevelopmentEnvironment):
        return ()
    from odoo_instance_sdk.internal import pgadmin_files
    from odoo_instance_sdk.internal.pgadmin_files import PGADMIN_CONTAINER_NAME
    from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep

    try:
        cluster = cluster or _pgadmin_cluster_snapshot(selector)
        if cluster is None:
            return ()
        if cluster.mode != "compose":
            return ()
        compose_file = cluster.compose_file
        prefix = (
            "docker",
            "compose",
            "--project-name",
            cluster.compose_project_name,
            "-f",
            str(compose_file),
        )
        paths = inputs.paths if inputs is not None else pgadmin_files.PgAdminPaths.from_defaults()
    except Exception:
        # The regular typed preflight remains authoritative for unresolved
        # selectors/configuration.  A command with no process manifest keeps
        # that error path observable rather than inventing a partial plan.
        return ()

    status_steps: list[PreparedStep | PreparedAction] = [
        PreparedStep(
            step_id="pgadmin.postgres.status.ps",
            argv=(*prefix, "ps", "--format", "json"),
            cwd=str(compose_file.parent),
            read_only=True,
        ),
        PreparedStep(
            step_id="pgadmin.postgres.status.health",
            argv=(
                *prefix,
                "exec",
                "-T",
                "postgres",
                "pg_isready",
                "-U",
                str(getattr(cluster, "_user", "") or ""),
                "-d",
                "postgres",
            ),
            cwd=str(compose_file.parent),
            read_only=True,
        ),
    ]
    if inputs is None:
        return tuple(status_steps)

    identity = inputs.identity

    steps: list[PreparedStep | PreparedAction] = [*status_steps]
    if inputs.database_probe is not None:
        steps.append(inputs.database_probe)
    steps.extend(
        [
            PreparedStep(
                step_id="pgadmin.identity.ps",
                argv=(*prefix, "ps", "--format", "json"),
                read_only=True,
            ),
            PreparedStep(
                step_id="pgadmin.identity.inspect",
                argv=("docker", "inspect", "--format", "json", identity.container_name),
                read_only=True,
            ),
            PreparedStep(
                step_id="pgadmin.identity.network",
                argv=("docker", "network", "inspect", "--format", "json", identity.network),
                read_only=True,
            ),
            PreparedStep(
                step_id="pgadmin.container.inspect.0",
                argv=("docker", "inspect", "--format", "json", PGADMIN_CONTAINER_NAME),
                read_only=True,
            ),
            PreparedAction(
                step_id="pgadmin.port.revalidate",
                action="revalidate-pgadmin-port",
                description="Revalidate the captured loopback port under the pgAdmin lifecycle lock",
                read_only=True,
            ),
            PreparedAction(
                step_id="pgadmin.prepare",
                action="provision-pgadmin-reconciliation",
                description=(
                    "Run the locked pgAdmin provisioning phase and return exact reconciliation inputs"
                ),
                mutating=True,
            ),
        ]
    )
    file_acl = ",".join(sorted(pgadmin_files._file_acl()))
    acl_specs = (
        (
            (
                "pgadmin.acl.root.set",
                (
                    "setfacl",
                    "--set",
                    ",".join(sorted(pgadmin_files._directory_acl(0o710))),
                    str(paths.root),
                ),
            ),
            ("pgadmin.acl.root.validate", ("getfacl", "-cp", str(paths.root))),
            (
                "pgadmin.acl.private.set",
                (
                    "setfacl",
                    "--set",
                    ",".join(sorted(pgadmin_files._directory_acl(0o710))),
                    str(paths.private_dir),
                ),
            ),
            ("pgadmin.acl.private.validate", ("getfacl", "-cp", str(paths.private_dir))),
            (
                "pgadmin.acl.data.set",
                (
                    "setfacl",
                    "--set",
                    ",".join(sorted(pgadmin_files._directory_acl(0o770))),
                    str(paths.data_dir),
                ),
            ),
            (
                "pgadmin.acl.data.default.set",
                (
                    "setfacl",
                    "--default",
                    "--set",
                    ",".join(sorted(pgadmin_files._default_directory_acl())),
                    str(paths.data_dir),
                ),
            ),
            ("pgadmin.acl.data.validate", ("getfacl", "-cp", str(paths.data_dir))),
            ("pgadmin.acl.data.default.validate", ("getfacl", "-cp", str(paths.data_dir))),
            ("pgadmin.acl.admin.existing", ("getfacl", "-cp", str(paths.admin_password))),
            (
                "pgadmin.acl.admin.final.set",
                ("setfacl", "--set", file_acl, str(paths.admin_password)),
            ),
            ("pgadmin.acl.pgpass.existing", ("getfacl", "-cp", str(paths.pgpass))),
            (
                "pgadmin.acl.pgpass.final.set",
                ("setfacl", "--set", file_acl, str(paths.pgpass)),
            ),
            ("pgadmin.acl.servers.existing", ("getfacl", "-cp", str(paths.servers_json))),
            (
                "pgadmin.acl.servers.final.set",
                ("setfacl", "--set", file_acl, str(paths.servers_json)),
            ),
            ("pgadmin.acl.metadata.existing", ("getfacl", "-cp", str(paths.metadata))),
            (
                "pgadmin.acl.metadata.final.set",
                ("setfacl", "--set", file_acl, str(paths.metadata)),
            ),
            ("pgadmin.acl.admin.final", ("getfacl", "-cp", str(paths.admin_password))),
            ("pgadmin.acl.pgpass.final", ("getfacl", "-cp", str(paths.pgpass))),
            ("pgadmin.acl.servers.final", ("getfacl", "-cp", str(paths.servers_json))),
            ("pgadmin.acl.metadata.final", ("getfacl", "-cp", str(paths.metadata))),
        )
        if pgadmin_files._linux()
        else ()
    )
    for step_id, argv in acl_specs:
        steps.append(
            PreparedStep(
                step_id=step_id,
                argv=argv,
                read_only=argv[0] == "getfacl",
                mutating=argv[0] == "setfacl",
            )
        )
    if include_reconciliation and inputs.fingerprint_inputs is not None:
        from odoo_instance_sdk.internal.pgadmin_container import (
            reconciliation_inspect_step,
            reconciliation_steps,
        )

        steps.extend(
            [
                *pgadmin_files.preparation_revalidation_steps(paths),
                reconciliation_inspect_step(),
                PreparedAction(
                    step_id="pgadmin.reconciliation.port.revalidate",
                    action="pgadmin_reconciliation_port_revalidate",
                    description="Revalidate the captured loopback port under the lifecycle lock",
                    read_only=True,
                ),
                *reconciliation_steps(
                    paths=paths,
                    port=inputs.port,
                    network=identity.network,
                    fingerprint=inputs.fingerprint_inputs.fingerprint,
                    secret_values=(
                        inputs.fingerprint_inputs.fingerprint,
                        inputs.password,
                    ),
                ),
            ]
        )
    return tuple(steps)


def _planning_result(
    expression_api: _ExpressionApi, outcome: _PlanningOutcome
) -> _ExpressionResult:
    """Adapt one concrete pure stage outcome to the bounded Result type."""
    if outcome.error is not None:
        return expression_api.Error(outcome.error)
    if outcome.state is None:
        return expression_api.Error(PlanValidationError("checkout stage produced no state"))
    return expression_api.Ok(outcome.state)


def _planning_error_outcome(error: _PlanningError) -> _PlanningOutcome:
    """Keep an expected planning failure typed while leaving the Result boundary."""
    return _PlanningOutcome(error=error)


def _validate_checkout_stage(state: _CheckoutPlanningState) -> _PlanningOutcome:
    """Validate captured checkout invariants without touching external state."""
    plan = state.private
    if not plan.branch.strip():
        return _PlanningOutcome(error=PlanValidationError("checkout branch must not be empty"))
    if not plan.worktree_argv:
        return _PlanningOutcome(error=PlanValidationError("checkout planning produced no command"))
    if plan.db_mode is EnvironmentDatabaseMode.COPY and plan.target_database is None:
        return _PlanningOutcome(
            error=PlanValidationError("copy checkout requires a target database")
        )
    return _PlanningOutcome(state=state)


def _normalize_checkout_stage(state: _CheckoutPlanningState) -> _PlanningOutcome:
    """Build immutable public projections from already captured values."""
    from odoo_instance_sdk.resources.environment.checkout_planning import (
        _execution_plan,
        _public_checkout_plan,
    )

    public = _public_checkout_plan(state.private, state.provenance, state.freshness, state.warnings)
    execution_plan = _execution_plan(
        state.private, state.provenance, state.freshness, state.warnings
    )
    return _PlanningOutcome(state=replace(state, public=public, execution_plan=execution_plan))


def _capture_checkout_stage(state: _CheckoutPlanningState) -> _PlanningOutcome:
    """Capture the final private/public pair without adding effects or locks."""
    if state.public is None or state.execution_plan is None:
        return _PlanningOutcome(error=PlanValidationError("checkout projections are incomplete"))
    snapshot = _CheckoutSnapshot(
        private=state.private,
        public=state.public,
        execution_plan=state.execution_plan,
    )
    return _PlanningOutcome(state=replace(state, snapshot=snapshot))
