from __future__ import annotations

# ruff: noqa: F821
from pathlib import Path
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.models import (
    BackupFreshness,
    BackupProvenanceComparison,
    DatabasePreparationAction,
    EnvironmentCheckoutPlan,
    EnvironmentPythonMode,
)
from odoo_instance_sdk.project import ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import ExecutionPlan, JsonValue
    from odoo_instance_sdk.internal.proc import (
        ProcessResult,
    )


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
    from odoo_instance_sdk.execution import ActionStep, ExecutionPlan, ExecutionStep
    from odoo_instance_sdk.internal.proc import PreparedStep

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

    steps: list[ExecutionStep] = []
    for step in _checkout_steps(plan):
        if isinstance(step, PreparedStep):
            steps.append(step.public_projection())
        else:
            action_details: JsonValue = None
            action = step.step_id.removeprefix("checkout.")
            description = "Execute checkout action"
            if action == "catalog":
                action = "record_environment"
                description = "Record the environment in the catalog"
                action_details = {"environment_id": str(plan.env_id)}
            elif action == "generated_config":
                action = "write_generated_config"
                description = "Generate the checkout Odoo configuration"
                action_details = {"path": str(plan.generated_config)}
            elif action == "database":
                action = "prepare_database"
                description = "Prepare the selected checkout database"
                action_details = {
                    "mode": plan.db_mode.value,
                    "database": plan.target_database or plan.source_database,
                }
            elif action == "cleanup":
                action = "cleanup_on_failure"
                description = "Remove owned checkout artifacts if execution fails"
                action_details = {"root": str(plan.env_root)}
            steps.append(
                ActionStep(
                    step_id=step.step_id,
                    action=action,
                    description=description,
                    details=action_details,
                    mutating=True,
                )
            )
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
