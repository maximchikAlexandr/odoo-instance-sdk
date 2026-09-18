from __future__ import annotations

# ruff: noqa: F821
import json
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, TypeVar, Union

import msgspec

from odoo_instance_sdk.exceptions import (
    ConfigError,
    EnvironmentConflictError,
    PlanError,
)
from odoo_instance_sdk.internal.dependency_sync import (
    resolve_hash_lock,
)
from odoo_instance_sdk.models import (
    Backup,
    BackupFreshness,
    BackupProvenanceComparison,
    EnvironmentCheckoutPlan,
)
from odoo_instance_sdk.models import (
    DevelopmentEnvironment as _DevelopmentEnvironment,
)
from odoo_instance_sdk.models import (
    EnvironmentDatabaseMode as _EnvironmentDatabaseMode,
)
from odoo_instance_sdk.models import (
    EnvironmentState as _EnvironmentState,
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.storage.backup_catalog import CopyJournalStage

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command, ExecutionPlan, JsonValue
    from odoo_instance_sdk.internal.pgadmin_files import (
        PgAdminFingerprintInputs,
        PgAdminPaths,
        PostgresIdentity,
    )
    from odoo_instance_sdk.internal.proc import (
        PreparedStep,
        RunContext,
    )
    from odoo_instance_sdk.resources.instance import OdooInstance
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
EnvironmentSelector = Union[str, "DevelopmentEnvironment"]
type _PlanningError = PlanError | ConfigError | EnvironmentConflictError
T = TypeVar("T")
EnvironmentState = _EnvironmentState
DevelopmentEnvironment = _DevelopmentEnvironment
EnvironmentDatabaseMode = _EnvironmentDatabaseMode
type _EnvironmentList = list[DevelopmentEnvironment]
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")
_PGADMIN_LIFECYCLE_TIMEOUT = 60.0
_CHECKOUT_WORKTREE_TIMEOUT = 300.0
_REQUIREMENT_OPERATOR = re.compile(r"\s*(===|==|~=|!=|<=|>=|<|>|;|@)\s*")
_APPLIED_CONFIG_BINDINGS = frozenset(
    {
        "admin_passwd",
        "addons_path",
        "data_dir",
        "db_host",
        "db_name",
        "db_password",
        "db_port",
        "db_user",
        "dbfilter",
        "http_interface",
        "http_port",
        "logfile",
    }
)


class EnvironmentCheckoutOptions(msgspec.Struct, frozen=True, kw_only=True):
    base_ref: str | None = None
    name: str | None = None
    config_path: Path | None = None
    db_mode: EnvironmentDatabaseMode = EnvironmentDatabaseMode.SHARED
    source_database: str | None = None
    target_database: str | None = None
    odoo_bin: Path | None = None
    python: str | Path | None = None
    create_venv: bool = False
    http_port: int | None = None
    hash_lock: str | Path | None = None
    hash_lock_sha256: str | None = None


class _PythonMode:
    mode: Literal["create", "reuse"]
    interpreter: str | None


class CopyCleanupPlan:
    """Validated COPY ownership retained for one destructive cleanup operation."""

    target_database: str
    backup_id: uuid.UUID | None
    instance: OdooInstance | None
    backup: Backup | None
    stage: CopyJournalStage
    rollback_database: str | None = None
    rollback_filestore: Path | None = None


def _replacement_retained_error(value: str | None) -> dict[str, JsonValue]:
    if not isinstance(value, str) or "copy replacement cleanup_failed" not in value:
        return {}
    try:
        payload = value.split("retained=", 1)[1].split(";", 1)[0]
        decoded = json.loads(payload)
    except (IndexError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _validate_retained_removal_evidence(
    catalog: BackupCatalog, env: DevelopmentEnvironment
) -> None:
    """Reject a removal command whose durable replacement evidence changed."""
    retained = _replacement_retained_error(env.last_error)
    if not retained:
        return
    row = catalog.get_environment(str(env.id))
    current = _replacement_retained_error(None if row is None else row["last_error"])
    for key in (
        "backup_id",
        "previous_backup_id",
        "target_database",
        "rollback_database",
        "target_present",
        "rollback_present",
        "rollback_filestore_present",
        "published",
    ):
        if current.get(key) != retained.get(key):
            raise EnvironmentConflictError(
                "replacement_conflict",
                "retained replacement evidence changed before removal",
            )


class _CheckoutPlan:
    """Fully resolved immutable checkout inputs; no mutation is allowed while building it."""

    project: ProjectConfig
    env_id: uuid.UUID
    name: str
    repo_root: Path
    git_common_dir: str
    branch: str
    base_ref: str
    base_revision: str
    worktree: Path
    venv: Path
    generated_config: Path
    dependency_lock: Path
    env_root: Path
    python_path: str
    python_owned: bool
    python_selector: str | Path | None
    http_interface: str
    http_port: int
    db_mode: EnvironmentDatabaseMode
    source_database: str | None
    target_database: str | None
    source_config: Path | None
    config_values: Mapping[str, str]
    odoo_bin: str
    runtime_cwd: str
    dependency_inputs: tuple[str, ...]
    hash_lock: Path | None
    worktree_argv: tuple[str, ...]
    created_at: str
    options: EnvironmentCheckoutOptions
    branch_revalidator: Callable[[RunContext[DevelopmentEnvironment]], None] | None = None


class _CheckoutSnapshot:
    """One resolved checkout input set shared by preview and execution."""

    private: _CheckoutPlan
    public: EnvironmentCheckoutPlan
    execution_plan: ExecutionPlan


class _CheckoutPlanningState:
    private: _CheckoutPlan
    provenance: BackupProvenanceComparison
    freshness: BackupFreshness
    warnings: tuple[str, ...]
    public: EnvironmentCheckoutPlan | None = None
    execution_plan: ExecutionPlan | None = None
    snapshot: _CheckoutSnapshot | None = None


class _PgAdminCommandInputs:
    """Private values needed by the locked pgAdmin provisioning phase."""

    identity: PostgresIdentity
    paths: PgAdminPaths
    port: int
    password: str
    database: str
    database_probe: PreparedStep | None = None
    fingerprint_inputs: PgAdminFingerprintInputs | None = None


class _PlanningOutcome:
    state: _CheckoutPlanningState | None = None
    error: _PlanningError | None = None


def _checkout_public_plan(command: Command[DevelopmentEnvironment]) -> EnvironmentCheckoutPlan:
    """Read the domain projection captured inside one private command."""
    projection = command._private_projection()
    if not isinstance(projection, EnvironmentCheckoutPlan):
        raise PlanError("checkout command has no captured domain plan")
    return projection


class _ExpressionResult(Protocol):
    def bind(
        self, mapper: Callable[[_CheckoutPlanningState], _ExpressionResult]
    ) -> _ExpressionResult: ...

    def default_with(
        self, getter: Callable[[_PlanningError], _PlanningOutcome]
    ) -> _CheckoutPlanningState | _PlanningOutcome: ...


class _ExpressionApi(Protocol):
    def Ok(self, value: _CheckoutPlanningState) -> _ExpressionResult: ...

    def Error(self, error: _PlanningError) -> _ExpressionResult: ...


_StrList = list[str]


def _resolve_checkout_hash_lock(
    options: EnvironmentCheckoutOptions, repo_root: Path
) -> Path | None:
    hash_lock = resolve_hash_lock(
        options.hash_lock,
        options.hash_lock_sha256,
        base_dir=repo_root,
    )
    if hash_lock is not None and not options.create_venv:
        raise ConfigError("hash-locked dependency sync requires an owned environment")
    return hash_lock


def _resolve_checkout_dependency_inputs(
    project_cfg: ProjectConfig,
    repo_root: Path,
    worktree: Path,
    hash_lock: Path | None,
) -> tuple[str, ...]:
    if hash_lock is not None:
        return ()
    dependency_paths = list(project_cfg.requirements)
    odoo_requirements = _find_odoo_requirements(repo_root)
    if odoo_requirements is not None and str(odoo_requirements) not in dependency_paths:
        dependency_paths.append(str(odoo_requirements))
    return tuple(_rebase_requirement_paths(dependency_paths, repo_root, worktree))


def _encode_runtime_json(odoo_bin: str, runtime_cwd: str) -> str:
    import json

    return json.dumps({"odoo_bin": odoo_bin, "runtime_cwd": runtime_cwd})


def _dependency_evidence(paths: Sequence[str]) -> dict[str, tuple[str, ...]]:
    from odoo_instance_sdk.internal.proc.redaction import redacted_projection

    evidence: dict[str, tuple[str, ...]] = {}
    for path in paths:
        candidate = Path(path)
        if not candidate.is_file():
            raise ConfigError(f"dependency input is unavailable: {candidate}")
        try:
            raw = candidate.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"dependency input is unavailable: {candidate}") from exc
        entries: list[str] = []
        for raw_line in raw.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            line = re.sub(r"\s+#.*$", "", line).strip()
            if not line:
                continue
            line = " ".join(line.split())
            line = _REQUIREMENT_OPERATOR.sub(r"\1", line)
            projected = redacted_projection(line, field="dependency")
            if not isinstance(projected, str):
                raise ConfigError(f"dependency input is malformed: {candidate}")
            entries.append(projected)
        evidence[str(candidate.resolve(strict=False))] = tuple(sorted(entries))
    return evidence


def _configured_addons(config: Mapping[str, str]) -> tuple[str, ...] | None:
    raw = config.get("addons_path")
    if raw is None:
        return None
    return tuple(path.strip() for path in raw.split(",") if path.strip())


def _git_ticket(branch: str) -> str:
    match = re.fullmatch(r"(?P<ticket>[A-Za-z][A-Za-z0-9]*-[0-9]+)(?:_[1-9][0-9]*)?", branch)
    return match.group("ticket") if match is not None else ""
