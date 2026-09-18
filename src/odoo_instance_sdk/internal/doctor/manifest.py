from __future__ import annotations

import configparser
import os
import shutil
import sqlite3
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from odoo_instance_sdk.commands.context import ResolvedContext, RuntimeView
from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
from odoo_instance_sdk.exceptions import (
    ConfigError,
    OdooInstanceSdkError,
    ProjectManifestNotFoundError,
)
from odoo_instance_sdk.internal import paths as _paths
from odoo_instance_sdk.internal.address import AddressState, probe_address
from odoo_instance_sdk.internal.applied_settings import (
    decode_applied_settings,
    encode_applied_settings,
)
from odoo_instance_sdk.internal.executables import resolve_optional_executable
from odoo_instance_sdk.internal.generated_config import _rebase_path
from odoo_instance_sdk.internal.odoo_config import parse_odoo_config
from odoo_instance_sdk.internal.postgres_compose import docker_available
from odoo_instance_sdk.models import DevelopmentEnvironment, PostgresClusterState, StartConfig
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment.checkout_planning import (
    _APPLIED_CONFIG_BINDINGS,
    _configured_addons,
    _dependency_evidence,
    _find_odoo_requirements,
    _rebase_requirement_paths,
)
from odoo_instance_sdk.resources.postgres import PostgresCluster

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.internal.applied_settings import SettingsValue
    from odoo_instance_sdk.resources.environment import DevelopmentEnvironment


STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_ERROR = "error"
STATUS_INFO = "info"

_ORDER = {STATUS_OK: 0, STATUS_INFO: 1, STATUS_WARN: 2, STATUS_ERROR: 3}


@dataclass(slots=True)
class CheckResult:
    name: str
    status: str
    detail: str
    environment_id: str | None = None
    environment_name: str | None = None
    remediations: tuple[DoctorRemediation, ...] = ()
    facts: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DoctorRemediation:
    """Secret-free advice; constructing it never authorizes execution."""

    description: str
    argv: tuple[str, ...]
    mutating: bool
    dry_run_supported: bool

    @property
    def supports_dry_run(self) -> bool:
        return self.dry_run_supported

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "description": self.description,
            "argv": list(self.argv),
            "mutating": self.mutating,
            "dry_run_supported": self.dry_run_supported,
        }


DriftStatus = Literal["in_sync", "drifted", "unknown"]


@dataclass(frozen=True, slots=True)
class _DriftComponent:
    component: str
    status: DriftStatus
    reason: str
    remediation: str

    def as_dict(self) -> dict[str, str]:
        return {
            "component": self.component,
            "status": self.status,
            "reason": self.reason,
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class _EnvironmentDrift:
    environment_id: str
    environment_name: str
    components: tuple[_DriftComponent, ...]
    git_context: Mapping[str, JsonValue]

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "environment_id": self.environment_id,
            "environment_name": self.environment_name,
            "components": cast("JsonValue", [component.as_dict() for component in self.components]),
            "git_context": dict(self.git_context),
        }


@dataclass(frozen=True, slots=True)
class _CurrentDriftEvidence:
    components: Mapping[str, JsonValue]
    reasons: Mapping[str, str]


@dataclass(slots=True)
class DoctorReport:
    checks: list[CheckResult] = field(default_factory=list)
    context: dict[str, str | None] = field(default_factory=lambda: {"project_source": None})
    drift: list[_EnvironmentDrift] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(c.status == STATUS_ERROR for c in self.checks)

    @property
    def warnings(self) -> list[str]:
        return [c.detail for c in self.checks if c.status == STATUS_WARN]


def run_doctor(
    client: OdooClient,
    project_path: Path | None,
    *,
    resolved_context: ResolvedContext | None = None,
) -> DoctorReport:
    report = DoctorReport()

    project_root = (
        resolved_context.project_root
        if resolved_context is not None
        else _resolve_project_root(project_path)
    )
    if project_root is None:
        report.context["project_source"] = None
        report.checks.append(
            CheckResult(
                "manifest",
                STATUS_ERROR,
                "no .odcli/project.toml found; run odcli init or pass --project PATH",
            )
        )
        envs = client.environments.list()
    else:
        if resolved_context is not None:
            report.context["project_source"] = resolved_context.output_provenance.get(
                "project_source"
            )
        else:
            report.context["project_source"] = "explicit" if project_path is not None else "cwd"
        _check_manifest(report, project_root)
        if resolved_context is not None and not isinstance(resolved_context.source, ProjectConfig):
            selected_environment = resolved_context.source
            _check_environment_runtime(
                report,
                client,
                selected_environment,
                resolved_context=resolved_context,
            )
        else:
            _check_project_runtime(
                report,
                client,
                project_root,
                selection_source=(
                    resolved_context.provenance
                    if resolved_context is not None
                    else "explicit"
                    if project_path is not None
                    else "cwd"
                ),
                resolved_context=resolved_context,
            )
        envs = client.environments.list(project=project_root, include_removed=True)

    _check_uv(report)
    _check_optional_executables(report)
    _check_catalog(report, client)
    _check_orphaned(report, client)
    _check_postgres(report, project_root)

    from odoo_instance_sdk.internal.doctor.runtime import _project_environment_drift

    for env in envs:
        if resolved_context is None:
            _check_environment_runtime(report, client, env)
        _check_environment(report, client, env)
        row = client.get_catalog().get_environment(str(env.id))
        raw_applied = row["applied_settings_json"] if row is not None else None
        report.drift.append(_project_environment_drift(env, raw_applied))

    report.checks.sort(key=lambda c: _ORDER.get(c.status, 0))
    return report


def _check_project_runtime(
    report: DoctorReport,
    client: OdooClient,
    project_root: Path,
    *,
    selection_source: Literal["explicit", "cwd", "worktree"] = "cwd",
    resolved_context: ResolvedContext | None = None,
) -> None:
    """Report configured and available project runtime values without starting it."""
    project: ProjectConfig | None = None
    config_path: Path | None = None
    start = StartConfig()
    try:
        if resolved_context is not None and isinstance(resolved_context.source, ProjectConfig):
            project = resolved_context.source
        else:
            project = ProjectConfig.load(project_root)
        config_path = _resolve_source_config(project, project_root)
        if project.postgres is not None and project.postgres.mode == "compose":
            generated = project_root / ".odcli" / "odoo.conf"
            if generated.is_file():
                config_path = generated
        start = (
            StartConfig.from_odoo_config(config_path) if config_path is not None else StartConfig()
        )
        database = project.default_source_database or start.db_name
        database_available = _database_available(project_root, database)
        view = (
            resolved_context.runtime
            if resolved_context is not None
            else _runtime_view(
                project,
                start=start,
                command_prefix=_project_command_prefix(project),
                client=client,
                provenance="cwd",
            )
        )
        facts = _runtime_facts(
            view,
            configured_python=str(project.python) if project.python is not None else None,
            configured_odoo_bin=str(project.odoo_bin) if project.odoo_bin is not None else None,
            config_path=str(config_path) if config_path is not None else None,
            database=database,
            database_available=database_available,
            database_status=(
                "available" if database_available else _database_status(project_root, database)
            ),
            selection_source=selection_source,
        )
        if resolved_context is not None and resolved_context.materialization_error is not None:
            facts["resolution_error"] = resolved_context.materialization_error
        runtime_available = _runtime_is_available(facts) and (
            resolved_context is None or resolved_context.materialization_error is None
        )
        report.checks.append(
            CheckResult(
                "runtime",
                STATUS_OK if runtime_available else STATUS_WARN,
                _runtime_detail(facts),
                facts=facts,
                remediations=()
                if runtime_available
                else (
                    DoctorRemediation(
                        description="Inspect project initialization inputs",
                        argv=("odcli", "init", "--dry-run"),
                        mutating=True,
                        dry_run_supported=True,
                    ),
                ),
            )
        )
    except Exception as exc:
        if project is None:
            report.checks.append(
                CheckResult("runtime", STATUS_WARN, f"configured runtime unavailable: {exc}")
            )
            return
        database = project.default_source_database or start.db_name
        database_available = _database_available(project_root, database)
        facts = _unresolved_runtime_facts(
            project,
            config_path=config_path,
            start=start,
            database=database,
            database_available=database_available,
            database_status=(
                "available" if database_available else _database_status(project_root, database)
            ),
            selection_source=selection_source,
            resolution_error=str(exc),
        )
        report.checks.append(
            CheckResult(
                "runtime",
                STATUS_WARN,
                _runtime_detail(facts),
                facts=facts,
                remediations=(
                    DoctorRemediation(
                        description="Inspect project initialization inputs",
                        argv=("odcli", "init", "--dry-run"),
                        mutating=True,
                        dry_run_supported=True,
                    ),
                ),
            )
        )


def _check_environment_runtime(
    report: DoctorReport,
    client: OdooClient,
    env: DevelopmentEnvironment,
    *,
    selection_source: Literal["explicit", "cwd", "worktree"] = "worktree",
    resolved_context: ResolvedContext | None = None,
) -> None:
    runtime_row = client.get_catalog().get_environment_runtime(str(env.id))
    odoo_bin = runtime_row["odoo_bin"] if runtime_row is not None else None
    try:
        start = StartConfig.from_odoo_config(env.generated_config_path)
    except Exception:
        start = StartConfig(http_interface=env.http_interface, http_port=env.http_port)
    try:
        view = (
            resolved_context.runtime
            if resolved_context is not None
            else _runtime_view(
                env,
                start=start,
                command_prefix=(str(env.python_environment_path), str(odoo_bin))
                if odoo_bin is not None
                else None,
                client=client,
                provenance="worktree",
            )
        )
        facts = _runtime_facts(
            view,
            configured_python=env.python_environment_path,
            configured_odoo_bin=str(odoo_bin) if odoo_bin is not None else None,
            config_path=env.generated_config_path,
            database=env.target_db_name or env.source_db_name or start.db_name,
            database_available=_database_available(
                Path(env.repository_root), env.target_db_name or env.source_db_name or start.db_name
            ),
            database_status=_database_status(
                Path(env.repository_root), env.target_db_name or env.source_db_name or start.db_name
            ),
            selection_source=(
                resolved_context.provenance if resolved_context is not None else selection_source
            ),
        )
        if resolved_context is not None and resolved_context.materialization_error is not None:
            facts["resolution_error"] = resolved_context.materialization_error
    except Exception as exc:
        facts = {
            "owner_kind": "environment",
            "selection_source": (
                resolved_context.provenance if resolved_context is not None else selection_source
            ),
            "configured": {
                "python": env.python_environment_path,
                "odoo_bin": str(odoo_bin) if odoo_bin is not None else None,
                "config": env.generated_config_path,
                "database": env.target_db_name or env.source_db_name or start.db_name,
                "http_url": f"http://{start.http_interface}:{start.http_port}",
            },
            "resolved": {},
            "available": {
                "python": False,
                "odoo_bin": False,
                "config": False,
                "database": False,
                "http": False,
            },
            "resolution_error": str(exc),
        }
    status = STATUS_OK if _runtime_is_available(facts) else STATUS_WARN
    report.checks.append(
        CheckResult(
            "runtime",
            status,
            _runtime_detail(facts),
            environment_id=str(env.id),
            environment_name=env.name,
            facts=facts,
            remediations=()
            if status == STATUS_OK
            else (
                DoctorRemediation(
                    description="Repair or synchronize environment runtime artifacts",
                    argv=("odcli", "env", "sync", str(env.id), "--dry-run"),
                    mutating=True,
                    dry_run_supported=True,
                ),
            ),
        )
    )


def _runtime_facts(
    view: RuntimeView,
    *,
    configured_python: str | None,
    configured_odoo_bin: str | None,
    config_path: str | None,
    database: str | None,
    database_available: bool,
    database_status: str,
    selection_source: Literal["explicit", "cwd", "worktree"],
) -> dict[str, JsonValue]:
    configured = {
        "python": configured_python,
        "odoo_bin": configured_odoo_bin,
        "config": config_path,
        "database": database,
        "http_url": view.http_url,
    }
    resolved_python = str(view.python_path)
    resolved_odoo_bin = str(view.command_prefix[-1]) if view.command_prefix else None
    available = {
        "python": _path_available(resolved_python),
        "odoo_bin": _path_available(resolved_odoo_bin),
        "config": _path_available(config_path),
        "database": database_available,
        "http": _http_available(view.http_interface, view.http_port),
    }
    return {
        "owner_kind": view.owner_kind,
        "selection_source": selection_source,
        "database_status": database_status,
        "configured": cast("JsonValue", configured),
        "resolved": cast("JsonValue", {"python": resolved_python, "odoo_bin": resolved_odoo_bin}),
        "available": cast("JsonValue", available),
    }


def _unresolved_runtime_facts(
    project: ProjectConfig,
    *,
    config_path: Path | None,
    start: StartConfig,
    database: str | None,
    database_available: bool,
    database_status: str,
    selection_source: Literal["explicit", "cwd", "worktree"],
    resolution_error: str,
) -> dict[str, JsonValue]:
    """Keep useful configured facts when RuntimeView cannot resolve an owner."""
    configured_odoo_bin = str(project.odoo_bin) if project.odoo_bin is not None else None
    return {
        "owner_kind": "project",
        "selection_source": selection_source,
        "database_status": database_status,
        "configured": cast(
            "JsonValue",
            {
                "python": str(project.python) if project.python is not None else None,
                "odoo_bin": configured_odoo_bin,
                "config": str(config_path) if config_path is not None else None,
                "database": database,
                "http_url": f"http://{start.http_interface}:{start.http_port}",
            },
        ),
        "resolved": cast("JsonValue", {"python": None, "odoo_bin": configured_odoo_bin}),
        "available": cast(
            "JsonValue",
            {
                "python": False,
                "odoo_bin": _path_available(configured_odoo_bin),
                "config": _path_available(str(config_path) if config_path is not None else None),
                "database": database_available,
                "http": _http_available(start.http_interface, start.http_port),
            },
        ),
        "resolution_error": resolution_error,
    }


def _runtime_view(
    source: ProjectConfig | DevelopmentEnvironment,
    *,
    start: StartConfig,
    command_prefix: tuple[str, ...] | None,
    client: OdooClient | None,
    provenance: Literal["cwd", "worktree"],
) -> RuntimeView:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.resources.instance import OdooInstance

    runtime_client = client or OdooClient(config=OdooClientConfig(executable="odoo"))
    instance = OdooInstance(
        config=InstanceConfig(
            base_url=f"http://{start.http_interface}:{start.http_port}",
            start_config=start,
            command_prefix=command_prefix,
        ),
        _client=runtime_client,
    )
    return ResolvedContext(
        client=runtime_client,
        instance=instance,
        source=source,
        provenance=provenance,
    ).runtime


def _project_command_prefix(project: ProjectConfig) -> tuple[str, ...] | None:
    if project.odoo_bin is None:
        return None
    from odoo_instance_sdk.internal.project_runtime import resolve_project_runtime

    try:
        odoo_bin = resolve_project_runtime(
            project.repository_root, project.odoo_bin, field="odoo_bin"
        )
    except Exception:
        odoo_bin = project.odoo_bin
    return (str(project.python) if project.python is not None else "python", str(odoo_bin))


def _database_available(project_root: Path, database: str | None) -> bool:
    return _database_status(project_root, database) == "available"


def _database_status(
    project_root: Path, database: str | None
) -> Literal["available", "missing", "ambiguous", "unavailable"]:
    if not database or not database.strip():
        return "missing"
    if len([name for name in database.split(",") if name.strip()]) != 1:
        return "ambiguous"
    try:
        if PostgresCluster.from_project(project_root).status() is PostgresClusterState.HEALTHY:
            return "available"
    except Exception:
        pass
    return "unavailable"


def _http_available(host: str, port: int) -> bool:
    return probe_address(host, port) is AddressState.OCCUPIED


def _path_available(value: str | None) -> bool:
    if not value:
        return False
    try:
        return Path(value).exists()
    except OSError:
        return False


def _runtime_detail(facts: dict[str, JsonValue]) -> str:
    configured = facts["configured"]
    available = facts["available"]
    return f"owner={facts['owner_kind']} configured={configured} available={available}"


def _runtime_is_available(facts: dict[str, JsonValue]) -> bool:
    available = facts.get("available")
    return isinstance(available, dict) and all(value is True for value in available.values())


def _resolve_project_root(project_path: Path | None) -> Path | None:
    if project_path is not None:
        return Path(project_path).resolve()
    current = Path.cwd().resolve()
    seen: set[Path] = set()
    while current not in seen:
        seen.add(current)
        candidate = current / ".odcli" / "project.toml"
        if candidate.is_file():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return None


def _check_manifest(report: DoctorReport, project_root: Path) -> None:
    manifest = project_root / ".odcli" / "project.toml"
    if not manifest.is_file():
        report.checks.append(CheckResult("manifest", STATUS_ERROR, f"manifest missing: {manifest}"))
        return
    try:
        ProjectConfig.load(project_root)
    except Exception as e:
        report.checks.append(CheckResult("manifest", STATUS_ERROR, f"manifest unparseable: {e}"))
        return
    report.checks.append(CheckResult("manifest", STATUS_OK, str(manifest)))


def _check_uv(report: DoctorReport) -> None:
    uv = shutil.which("uv")
    if uv is None:
        report.checks.append(CheckResult("uv", STATUS_WARN, "uv not found in PATH"))
    else:
        report.checks.append(CheckResult("uv", STATUS_OK, uv))


def _check_optional_executables(report: DoctorReport) -> None:
    """Report optional host capabilities without making health fail."""
    for name in ("msgfmt", "git-absorb"):
        capability = resolve_optional_executable(name)
        detail = capability.path or f"{name} not found (optional)"
        remediations: tuple[DoctorRemediation, ...] = ()
        if not capability.available and name == "git-absorb":
            if sys.platform == "darwin":
                install_hint = "brew install git-absorb"
            elif sys.platform.startswith("win"):
                install_hint = "py -m pip install git-absorb"
            else:
                install_hint = "python -m pip install git-absorb"
            remediations = (
                DoctorRemediation(
                    description=f"Install git-absorb ({install_hint})",
                    argv=("git-absorb", "--help"),
                    mutating=False,
                    dry_run_supported=True,
                ),
            )
        report.checks.append(
            CheckResult(
                name,
                STATUS_OK if capability.available else STATUS_INFO,
                detail,
                remediations=remediations,
            )
        )


def _check_catalog(report: DoctorReport, client: OdooClient) -> None:
    from odoo_instance_sdk.internal.paths import get_catalog_path

    catalog_path = get_catalog_path()
    if not catalog_path.exists():
        report.checks.append(
            CheckResult("catalog", STATUS_ERROR, f"catalog missing: {catalog_path}")
        )
        return
    conn = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True)
    try:
        from odoo_instance_sdk.storage.catalog_migrate import CATALOG_REVISION, catalog_revision

        revision = catalog_revision(conn)
    except sqlite3.Error as e:
        report.checks.append(CheckResult("catalog", STATUS_ERROR, f"catalog unreadable: {e}"))
        return
    finally:
        conn.close()
    if revision == CATALOG_REVISION:
        report.checks.append(
            CheckResult("catalog", STATUS_OK, f"{catalog_path} (revision={revision})")
        )
    else:
        report.checks.append(
            CheckResult(
                "catalog",
                STATUS_ERROR,
                f"catalog revision={revision!r}, expected {CATALOG_REVISION!r}",
            )
        )


def _check_orphaned(report: DoctorReport, client: OdooClient) -> None:
    environments_root = _paths.get_environments_root()
    if not environments_root.is_dir():
        return
    catalog = client.get_catalog()
    known_rows = catalog.list_environments(include_removed=True)
    known_ids = {str(row["id"]) for row in known_rows}
    for repo_key_dir in environments_root.iterdir():
        if not repo_key_dir.is_dir():
            continue
        for env_id_dir in repo_key_dir.iterdir():
            if not env_id_dir.is_dir():
                continue
            try:
                uuid.UUID(env_id_dir.name)
            except ValueError:
                continue
            if env_id_dir.name not in known_ids:
                report.checks.append(
                    CheckResult(
                        "orphaned",
                        STATUS_WARN,
                        f"orphaned artifact: {env_id_dir} (no matching catalog row)",
                    )
                )


def _check_postgres(report: DoctorReport, project_root: Path | None) -> None:
    """Read-only cluster checks: mode, ownership, endpoint, health, Docker availability."""
    if project_root is None:
        return
    try:
        cluster = PostgresCluster.from_project(project_root)
    except ProjectManifestNotFoundError:
        return
    except OdooInstanceSdkError as exc:
        report.checks.append(CheckResult("postgres.cluster", STATUS_WARN, str(exc)))
        return
    if cluster.owned and not docker_available():
        report.checks.append(
            CheckResult("postgres.compose", STATUS_WARN, "docker not found in PATH")
        )
    state = cluster.status()
    report.checks.append(
        CheckResult(
            "postgres.cluster",
            _postgres_state_to_status(state),
            (
                f"mode={cluster.mode} owned={cluster.owned} "
                f"state={state.value} endpoint={cluster.endpoint}"
            ),
        )
    )


def _postgres_state_to_status(state: PostgresClusterState) -> str:
    if state == PostgresClusterState.HEALTHY:
        return STATUS_OK
    if state in (PostgresClusterState.STARTING, PostgresClusterState.STOPPED):
        return STATUS_INFO
    return STATUS_WARN


def _check_environment(
    report: DoctorReport, client: OdooClient, env: DevelopmentEnvironment
) -> None:
    from odoo_instance_sdk.internal.doctor import runtime as _runtime

    eid = str(env.id)
    ename = env.name

    _runtime._check_worktree(report, env, eid, ename)
    _runtime._check_python(report, env, eid, ename)
    _runtime._check_dependencies(report, env, eid, ename)
    _runtime._check_generated_config(report, env, eid, ename)
    _runtime._check_port(report, env, eid, ename)
    if env.db_mode == "copy" and env.backup_id is not None:
        _runtime._check_backup(report, client, env, eid, ename)


def _current_drift_components(
    env: DevelopmentEnvironment,
) -> _CurrentDriftEvidence:
    """Capture normalized current evidence without changing any resource."""
    worktree = Path(env.worktree_path)
    project: ProjectConfig | None
    try:
        project = ProjectConfig.load(Path(env.repository_root))
    except Exception:
        project = None

    dependency_values: SettingsValue | None = None
    if project is not None:
        inputs = _rebase_requirement_paths(
            list(project.requirements), Path(env.repository_root), worktree
        )
        odoo_requirements = _find_odoo_requirements(Path(env.repository_root))
        if odoo_requirements is not None and str(odoo_requirements) not in inputs:
            inputs.append(str(odoo_requirements))
        try:
            dependency_values = _dependency_evidence(inputs)
        except (ConfigError, OSError, UnicodeError):
            dependency_values = None

    source_config: Mapping[str, JsonValue] | None
    source_addons: tuple[str, ...] | None
    artifact_config: Mapping[str, JsonValue] | None
    artifact_addons: tuple[str, ...] | None
    if project is None:
        source_config, source_addons = None, None
    else:
        source_config, source_addons = _read_config_components(
            _resolve_source_config(project, Path(env.repository_root))
        )
        source_config, source_addons = _rebase_source_components(
            source_config, source_addons, Path(env.repository_root), worktree
        )
    artifact_config, artifact_addons = _read_config_components(Path(env.generated_config_path))

    python_component: JsonValue | None = None
    if project is not None and _python_artifact_available(
        Path(env.python_environment_path), env.python_environment_owned
    ):
        python_component = _component_from_codec(
            python={
                "selector": str(project.python) if project.python is not None else None,
                "path": env.python_environment_path,
                "owned": env.python_environment_owned,
            }
        ).get("python")
    dependency_component = (
        _component_from_codec(dependencies=dependency_values).get("dependencies")
        if dependency_values is not None
        else None
    )
    source_components = _component_from_codec(
        managed_config=source_config,
        addons=source_addons,
    )
    artifact_components = _component_from_codec(
        managed_config=artifact_config,
        addons=artifact_addons,
    )
    from odoo_instance_sdk.internal.doctor.manifest_drift import _live_git_component

    components: dict[str, JsonValue] = {
        "python": python_component,
        "dependencies": dependency_component,
        "odoo_config": _paired_component(
            source_components.get("odoo"), artifact_components.get("odoo")
        ),
        "addons": _paired_component(
            source_components.get("addons"), artifact_components.get("addons")
        ),
        "git_provenance": _live_git_component(worktree, env.base_ref),
    }
    return _CurrentDriftEvidence(
        components=components,
        reasons=_paired_reasons(source_components, artifact_components),
    )


def _paired_reasons(
    source: Mapping[str, JsonValue], artifact: Mapping[str, JsonValue]
) -> dict[str, str]:
    reasons: dict[str, str] = {}
    pairs = (
        (
            "odoo",
            "odoo_config",
            "source and generated Odoo settings differ",
        ),
        ("addons", "addons", "source and generated add-on paths differ"),
    )
    for source_key, output_key, reason in pairs:
        source_value = source.get(source_key)
        artifact_value = artifact.get(source_key)
        if source_value != artifact_value and _is_known(source_value) and _is_known(artifact_value):
            reasons[output_key] = reason
    return reasons


def _resolve_source_config(project: ProjectConfig, repo_root: Path) -> Path | None:
    configured = project.source_config
    if configured is None:
        default = repo_root / "odoo.conf"
        return default if default.is_file() else None
    path = Path(configured)
    return (repo_root / path).resolve() if not path.is_absolute() else path


def _read_config_components(
    path: Path | None,
) -> tuple[Mapping[str, JsonValue] | None, tuple[str, ...] | None]:
    if path is None:
        return None, None
    try:
        raw = path.read_text(encoding="utf-8")
        parser = configparser.RawConfigParser(interpolation=None)
        parser.read_string(raw)
        if not parser.has_section("options"):
            return None, None
        parsed = parse_odoo_config(path)
    except (OSError, UnicodeError, configparser.Error, ValueError):
        return None, None
    return (
        {key: value for key, value in parsed.items() if key not in _APPLIED_CONFIG_BINDINGS},
        _configured_addons(parsed),
    )


def _rebase_source_components(
    config: Mapping[str, JsonValue] | None,
    addons: tuple[str, ...] | None,
    repo_root: Path,
    worktree: Path,
) -> tuple[Mapping[str, JsonValue] | None, tuple[str, ...] | None]:
    if config is not None:
        normalized = dict(config)
        upgrade_path = normalized.get("upgrade_path")
        if isinstance(upgrade_path, str):
            normalized["upgrade_path"] = ",".join(
                _rebase_path(item.strip(), repo_root, worktree)
                for item in upgrade_path.split(",")
                if item.strip()
            )
        config = normalized
    if addons is not None:
        addons = tuple(_rebase_path(item, repo_root, worktree) for item in addons)
    return config, addons


def _python_artifact_available(path: Path, owned: bool) -> bool:
    candidate = (
        path / ("Scripts/python.exe" if os.name == "nt" else "bin/python") if owned else path
    )
    try:
        return candidate.is_file() and os.access(candidate, os.R_OK | os.X_OK)
    except OSError:
        return False


def _component_from_codec(
    *,
    python: Mapping[str, JsonValue] | None = None,
    dependencies: SettingsValue | None = None,
    managed_config: Mapping[str, JsonValue] | None = None,
    addons: tuple[str, ...] | None = None,
) -> dict[str, JsonValue]:
    document = decode_applied_settings(
        encode_applied_settings(
            python=python,
            dependencies=dependencies,
            managed_config=managed_config,
            addons=addons,
        )
    )
    components = document["components"]
    if not isinstance(components, dict):
        return {}
    return dict(components)


def _is_known(value: JsonValue) -> bool:
    return isinstance(value, dict) and value.get("status") == "known"


def _paired_component(source: JsonValue, artifact: JsonValue) -> JsonValue | None:
    if not _is_known(source) or not _is_known(artifact):
        return None
    return artifact
