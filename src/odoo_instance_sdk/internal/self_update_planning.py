"""Read-only command builders for OdCLI self-update."""

from __future__ import annotations

from typing import cast

from odoo_instance_sdk.exceptions import UnsupportedInstallError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    ProcessExecutor,
    ProcessResult,
    RunContext,
    SubprocessExecutor,
    prepared_command,
)
from odoo_instance_sdk.internal.self_update import (
    _MANUAL_INSTALL_ARGV,
    InstalledProvenance,
    _extract_target_sha,
    _failure_result,
    _is_full_sha,
    _is_supported_source_origin,
    _process_output_text,
    _resolve_argv,
)
from odoo_instance_sdk.internal.self_update_ancestry import (
    RevisionRelation,
    revision_probe_steps as _revision_probe_steps,
    run_revision_probe as _run_revision_probe,
)
from odoo_instance_sdk.internal.self_update_policy import (
    preflight_error as _preflight_error,
    probe_source_repo as _probe_source_repo,
    skip_revision_probe as _skip_revision_probe,
    source_origin_step as _source_origin_step,
)
from odoo_instance_sdk.models.update import UpdateOutcome, UpdateResult

_ANCESTRY_CLEANUP_STEP_ID = "update.inspect.ancestry-cleanup"


def _build_staged_command(
    *,
    ref: str,
    provenance: InstalledProvenance,
    executor: ProcessExecutor | None,
) -> Command[UpdateResult]:
    resolve_step = PreparedStep(step_id="update.resolve", argv=_resolve_argv(ref), read_only=True)
    active_executor = executor or SubprocessExecutor()

    def target_sha_from_result(result: ProcessResult) -> str:
        if result.returncode != 0:
            stderr = result.stderr if isinstance(result.stderr, str) else ""
            raise UnsupportedInstallError(
                stderr.strip() or "uv could not resolve the requested revision",
                manual_argv=_MANUAL_INSTALL_ARGV,
            )
        target_sha = _extract_target_sha(ref, _process_output_text(result))
        if target_sha is None:
            raise UnsupportedInstallError(
                "could not parse target SHA from uv output (sha_unparsed)",
                manual_argv=_MANUAL_INSTALL_ARGV,
            )
        return target_sha

    def callback(context: RunContext[UpdateResult]) -> UpdateResult:
        context.action("update.inspect")
        context.complete_action("update.inspect")
        result = cast("ProcessResult", context.process_prepared(resolve_step))
        target_sha = target_sha_from_result(result)
        outcome: UpdateOutcome = (
            "already_current"
            if provenance.commit_id is not None and target_sha == provenance.commit_id
            else "updated"
        )
        return UpdateResult(
            outcome=outcome,
            source_repo=provenance.source_repo,
            previous_version=provenance.version,
            target_version=provenance.version if outcome == "already_current" else None,
            final_version=provenance.version if outcome == "already_current" else None,
            previous_sha=provenance.commit_id,
            target_sha=target_sha,
            final_sha=provenance.commit_id if outcome == "already_current" else None,
            executable_path=str(provenance.uv_tool_bin_path)
            if provenance.uv_tool_bin_path
            else None,
            tool_env_path=str(provenance.uv_tool_env_path) if provenance.uv_tool_env_path else None,
            snapshot_state="absent",
            journal_state="absent",
            next_step=(
                None
                if outcome == "already_current"
                else "run `odcli update` to apply the resolved revision"
            ),
        )

    inspect_step = PreparedAction(
        step_id="update.inspect",
        action="inspect",
        description="Inspect installed OdCLI provenance",
        read_only=True,
    )
    steps: tuple[PreparedAction | PreparedStep, ...] = (inspect_step, resolve_step)
    plan = ExecutionPlan(steps=tuple(step.public_projection() for step in steps))
    prepared = prepared_command(callback, steps, executor=active_executor)
    return Command.from_prepared(plan, prepared)


def _build_preflight_command(
    *,
    ref: str,
    provenance: InstalledProvenance,
    allow_downgrade: bool,
    executor: ProcessExecutor | None,
) -> Command[UpdateResult]:
    inspect_step = PreparedAction(
        step_id="update.inspect",
        action="inspect",
        description="Inspect installed OdCLI provenance",
        read_only=True,
    )
    preflight_step = PreparedAction(
        step_id="update.preflight",
        action="preflight",
        description="Verify free space, schema, and migration path",
        read_only=True,
    )
    origin_step = _source_origin_step(provenance)
    canonical_source_repo = _probe_source_repo(provenance)
    ancestry_steps = (
        _revision_probe_steps(
            source_repo=canonical_source_repo,
            installed_sha=provenance.commit_id or "",
            target_sha=ref,
        )
        if canonical_source_repo
        and _is_full_sha(ref)
        and provenance.commit_id
        and _is_full_sha(provenance.commit_id)
        else ()
    )
    cleanup_action = (
        PreparedAction(
            step_id=_ANCESTRY_CLEANUP_STEP_ID,
            action="cleanup",
            description="Remove the temporary ancestry object store",
            mutating=True,
        )
        if ancestry_steps
        else None
    )
    steps: tuple[PreparedAction | PreparedStep, ...] = (
        inspect_step,
        preflight_step,
        *((origin_step,) if origin_step is not None else ()),
        *ancestry_steps,
        *((cleanup_action,) if cleanup_action is not None else ()),
    )

    def callback(context: RunContext[UpdateResult]) -> UpdateResult:
        context.action(inspect_step.step_id)
        context.complete_action(inspect_step.step_id)
        context.action(preflight_step.step_id)
        source_origin_verified = origin_step is None
        if origin_step is not None:
            origin_result = cast("ProcessResult", context.process_prepared(origin_step))
            origin_output = origin_result.stdout if isinstance(origin_result.stdout, str) else ""
            source_origin_verified = origin_result.returncode == 0 and _is_supported_source_origin(
                origin_output
            )
        relation = "unknown"
        if source_origin_verified and provenance.commit_id is not None and _is_full_sha(ref):
            relation = _run_revision_probe(
                context,
                ancestry_steps,
                installed_sha=provenance.commit_id.lower(),
                target_sha=ref.lower(),
                cleanup_step_id=cleanup_action.step_id if cleanup_action else None,
            )
        elif ancestry_steps:
            _skip_revision_probe(
                context, ancestry_steps, cleanup_action.step_id if cleanup_action else None
            )
        error = _preflight_error(
            ref=ref,
            provenance=provenance,
            allow_downgrade=allow_downgrade,
            relation=cast("RevisionRelation", relation),
            source_origin_verified=source_origin_verified,
        )
        context.complete_action(preflight_step.step_id)
        if error is not None:
            return _failure_result("preflight_failed", provenance=provenance, next_step=error)
        return UpdateResult(
            outcome="updated",
            source_repo=provenance.source_repo,
            previous_version=provenance.version,
            previous_sha=provenance.commit_id,
            target_sha=ref,
            snapshot_state="absent",
            journal_state="absent",
        )

    plan = ExecutionPlan(steps=tuple(step.public_projection() for step in steps))
    return Command.create(plan, callback, steps, executor=executor or SubprocessExecutor())


__all__ = ["_build_preflight_command", "_build_staged_command"]
