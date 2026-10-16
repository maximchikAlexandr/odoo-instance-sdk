from __future__ import annotations  # noqa: I001 -- keep checkout model aliases grouped; remove when Ruff supports grouped aliases.

import re
import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, TypeVar, Union, cast

import msgspec

from odoo_instance_sdk.exceptions import (
    ConfigError,
    EnvironmentConflictError,
    PlanError,
)
from odoo_instance_sdk.internal.dependency_sync import (
    resolve_hash_lock,
)
from odoo_instance_sdk.internal.dbreplace_recovery import (
    CopyReplacementRecovery,
    recovery_from_row,
    recovery_mapping,
)
from odoo_instance_sdk.internal.project_init import (
    project_owned_data_dir,
    verify_project_owned_data_dir,
)
from odoo_instance_sdk.models import (
    Backup,
    BackupFreshness,
    BackupProvenanceComparison,
    DatabasePreparationAction,
    EnvironmentCheckoutPlan,
    EnvironmentPythonMode,
    EnvironmentCodeOwnership,
    DevelopmentEnvironment as _DevelopmentEnvironment,
    EnvironmentDatabaseMode as _EnvironmentDatabaseMode,
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
        ProcessResult,
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


def _resolve_checkout_data_dir(project: ProjectConfig, repo_root: Path) -> Path | None:
    if project.postgres is None or project.postgres.mode != "compose":
        return None
    data_dir = project_owned_data_dir(repo_root)
    verify_project_owned_data_dir(repo_root, data_dir, require_exists=False)
    return data_dir


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
    remote_name: str | None = None
    backup_id: uuid.UUID | str | None = None


@dataclass(frozen=True, slots=True)
class _PythonMode:
    mode: Literal["create", "reuse"]
    interpreter: str | None


@dataclass(frozen=True, slots=True)
class CopyCleanupPlan:
    """Validated COPY ownership retained for one destructive cleanup operation."""

    target_database: str
    backup_id: uuid.UUID | None
    instance: OdooInstance | None
    backup: Backup | None
    stage: CopyJournalStage
    backup_ownership: Literal["owned", "borrowed", "unknown"] = "unknown"
    rollback_database: str | None = None
    rollback_filestore: Path | None = None


def _replacement_retained_error(value: str | None) -> dict[str, JsonValue]:
    """Decode legacy evidence only for an explicit cleanup repair."""
    if not isinstance(value, str):
        return {}
    decoded = recovery_from_row({"last_error": value}, allow_legacy=True)
    return decoded if isinstance(decoded, dict) else {}


def _validate_retained_removal_evidence(
    catalog: BackupCatalog,
    env: DevelopmentEnvironment,
    expected: Mapping[str, JsonValue] | None = None,
) -> None:
    """Reject a removal command whose durable replacement evidence changed."""
    row = catalog.get_environment(str(env.id))
    retained_value = recovery_from_row(row, allow_legacy=True)
    retained = (
        recovery_mapping(retained_value)
        if isinstance(retained_value, CopyReplacementRecovery)
        else (retained_value or {})
    )
    if expected is not None:
        retained = dict(expected)
    if not retained:
        return
    current_value = recovery_from_row(catalog.get_environment(str(env.id)), allow_legacy=True)
    current = (
        recovery_mapping(current_value)
        if isinstance(current_value, CopyReplacementRecovery)
        else (current_value or {})
    )
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


@dataclass(frozen=True, slots=True)
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
    source_name: str | None = None
    source_base_url: str | None = None
    source_git_branch: str | None = None
    selected_backup: Backup | None = None
    branch_revalidator: Callable[[RunContext[DevelopmentEnvironment]], None] | None = None
    data_dir: Path | None = None
    project_root: Path | None = None
    project_id: str | None = None
    checkout_commit_sha: str | None = None
    code_ownership: EnvironmentCodeOwnership = EnvironmentCodeOwnership.SDK_OWNED
    artifact_root: Path | None = None
    adoption_input_fingerprint: str | None = None
    adopted: bool = False


@dataclass(frozen=True, slots=True)
class _CheckoutSnapshot:
    """One resolved checkout input set shared by preview and execution."""

    private: _CheckoutPlan
    public: EnvironmentCheckoutPlan
    execution_plan: ExecutionPlan


@dataclass(frozen=True, slots=True)
class _CheckoutPlanningState:
    private: _CheckoutPlan
    provenance: BackupProvenanceComparison
    freshness: BackupFreshness
    warnings: tuple[str, ...]
    public: EnvironmentCheckoutPlan | None = None
    execution_plan: ExecutionPlan | None = None
    snapshot: _CheckoutSnapshot | None = None


@dataclass(frozen=True, slots=True)
class _PgAdminCommandInputs:
    """Private values needed by the locked pgAdmin provisioning phase."""

    identity: PostgresIdentity
    paths: PgAdminPaths
    port: int
    password: str
    database: str
    database_probe: PreparedStep | None = None
    fingerprint_inputs: PgAdminFingerprintInputs | None = None


@dataclass(frozen=True, slots=True)
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
    return discover_dependency_inputs(project_cfg, repo_root, worktree)


def _rebase_project_path(path: Path, repo_root: Path, worktree: Path) -> Path:
    """Rebase a repository-local path from the project checkout to a worktree."""
    root = repo_root.resolve()
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve(strict=False)
    if resolved.is_relative_to(root):
        return (worktree / resolved.relative_to(root)).resolve(strict=False)
    return path


def _append_dependency_candidate(
    output: list[str],
    seen: set[Path],
    candidate: Path,
    *,
    explicit: bool,
    source_candidate: Path | None = None,
) -> None:
    canonical = candidate.resolve(strict=False)
    validation_candidate = source_candidate or candidate
    if explicit and not validation_candidate.is_file():
        raise ConfigError(
            f"dependency input is unavailable: explicit input is not a regular file: {candidate}"
        )
    if not validation_candidate.is_file():
        return
    if canonical in seen:
        return
    seen.add(canonical)
    output.append(str(canonical))


def _discover_addon_requirements(root: Path) -> tuple[Path, ...]:
    """Find module and immediate-parent requirement files in stable walk order."""
    if not root.is_dir():
        return ()
    found: list[Path] = []
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        dirs.sort()
        files.sort()
        if "__manifest__.py" not in files:
            continue
        module = Path(current)
        found.extend((module / "requirements.txt", module.parent / "requirements.txt"))
    return tuple(found)


def discover_dependency_inputs(
    project_cfg: ProjectConfig, repo_root: Path, worktree: Path
) -> tuple[str, ...]:
    """Return canonical, deterministic dependency files for a project checkout.

    Discovered files are best-effort, while manifest-configured requirement
    files are explicit inputs and therefore fail closed when unavailable.
    """
    output: list[str] = []
    seen: set[Path] = set()
    root = repo_root.resolve()
    checkout = worktree.resolve()
    scan_root = checkout if checkout.is_dir() else root
    addon_roots = project_cfg.addon_roots
    for configured_root in addon_roots:
        addon_root = _rebase_project_path(configured_root, root, scan_root)
        for candidate in _discover_addon_requirements(addon_root):
            rebased = _rebase_project_path(candidate, scan_root, checkout)
            _append_dependency_candidate(
                output, seen, rebased, explicit=False, source_candidate=candidate
            )

    configured_odoo_bin = project_cfg.odoo_bin
    if configured_odoo_bin is not None:
        odoo_bin = _rebase_project_path(configured_odoo_bin, root, scan_root)
        source_candidate = odoo_bin.parent / "requirements.txt"
        candidate = _rebase_project_path(source_candidate, scan_root, checkout)
        _append_dependency_candidate(
            output, seen, candidate, explicit=False, source_candidate=source_candidate
        )
    else:
        for source_candidate in (
            scan_root / "requirements.txt",
            scan_root / "odoo" / "requirements.txt",
        ):
            candidate = _rebase_project_path(source_candidate, scan_root, checkout)
            _append_dependency_candidate(
                output, seen, candidate, explicit=False, source_candidate=source_candidate
            )

    for configured in project_cfg.requirements:
        source_candidate = _rebase_project_path(Path(configured), root, root)
        if not source_candidate.is_file():
            raise ConfigError(
                "dependency input is unavailable: explicit input is not a regular file: "
                f"{source_candidate}"
            )
        candidate = _rebase_project_path(source_candidate, root, checkout)
        _append_dependency_candidate(
            output, seen, candidate, explicit=False, source_candidate=source_candidate
        )
    return tuple(output)


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


def _process_stderr(result: ProcessResult) -> str:
    stderr = result.stderr
    if isinstance(stderr, bytes):
        return stderr.decode(errors="replace")
    return stderr or ""


def _execution_plan(
    plan: _CheckoutPlan,
    provenance: BackupProvenanceComparison,
    freshness: BackupFreshness,
    warnings: tuple[str, ...],
) -> ExecutionPlan:
    """Build the public process/action projection from one private snapshot."""
    from odoo_instance_sdk.execution import ExecutionPlan
    from odoo_instance_sdk.resources.environment.checkout_artifacts import _checkout_steps

    probe_argvs = (
        ("git", "-C", str(plan.repo_root), "rev-parse", "--show-toplevel"),
        ("git", "-C", str(plan.repo_root), "rev-parse", "--git-common-dir"),
        ("git", "-C", str(plan.repo_root), "rev-parse", "--verify", plan.base_ref),
        ("git", "-C", str(plan.repo_root), "rev-parse", "--verify", f"refs/heads/{plan.branch}"),
        ("git", "-C", str(plan.repo_root), "ls-remote", "--heads", "origin", plan.branch),
        ("git", "--version"),
        ("uv", "--version"),
    )
    observations: list[JsonValue] = []
    for argv in probe_argvs:
        observations.append(
            cast(
                "JsonValue",
                {
                    "argv": list(argv),
                    "returncode": 0,
                    "read_only": True,
                    "executed_during_planning": True,
                },
            )
        )

    steps = [step.public_projection() for step in _checkout_steps(plan)]
    execution = ExecutionPlan(
        steps=tuple(steps),
        observations=tuple(observations),
        warnings=warnings,
    )
    return execution.with_fingerprint(secrets=tuple(plan.config_values.values()))


def _public_checkout_plan(
    plan: _CheckoutPlan,
    provenance: BackupProvenanceComparison,
    freshness: BackupFreshness,
    warnings: tuple[str, ...],
) -> EnvironmentCheckoutPlan:
    actions: tuple[DatabasePreparationAction, ...] = ()
    if plan.project.refresh_after_hours is not None and freshness is not BackupFreshness.FRESH:
        actions = (
            DatabasePreparationAction.DOWNLOAD,
            DatabasePreparationAction.RESTORE,
            DatabasePreparationAction.SWITCH_DEFAULT,
        )
    return EnvironmentCheckoutPlan(
        name=plan.name,
        branch=plan.branch,
        effective_base_ref=plan.base_ref,
        db_mode=plan.db_mode,
        source_database=plan.source_database,
        target_database=plan.target_database,
        python_mode=(
            EnvironmentPythonMode.CREATE if plan.python_owned else EnvironmentPythonMode.REUSE
        ),
        provenance=provenance,
        freshness=freshness,
        preparation_actions=actions,
        warnings=warnings,
    )


def _load_project(env: DevelopmentEnvironment) -> ProjectConfig:
    return ProjectConfig.load(Path(env.repository_root))


def _rebase_requirement_paths(paths: list[str], repo_root: Path, worktree: Path) -> list[str]:
    rebased: list[str] = []
    for p in paths:
        candidate = Path(p)
        if candidate.is_absolute():
            rebased.append(str(candidate))
            continue
        resolved_repo = (repo_root / candidate).resolve()
        resolved_work = (worktree / candidate).resolve()
        if resolved_repo.is_relative_to(repo_root.resolve()):
            rebased.append(str(resolved_work))
        else:
            rebased.append(str(candidate))
    return rebased


def _find_odoo_requirements(worktree: Path) -> Path | None:
    for candidate in (worktree / "requirements.txt", worktree / "odoo" / "requirements.txt"):
        if candidate.is_file():
            return candidate
    return None
