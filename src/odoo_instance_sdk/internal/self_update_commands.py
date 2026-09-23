"""Frozen ``Command`` builders for ``odcli update``."""

from __future__ import annotations

import json
import sys
import time
from typing import cast

import msgspec

from odoo_instance_sdk.exceptions import (
    LockConflictError,
    PreflightFailedError,
    UnsupportedInstallError,
    UpdateError,
)
from odoo_instance_sdk.execution import Command, ExecutionPlan, JsonValue
from odoo_instance_sdk.internal.locks import exclusive_lock
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
    _DEFAULT_REF,
    _JOURNAL_VERSION,
    _MAINTENANCE_ENV,
    _MANUAL_INSTALL_ARGV,
    _MIN_PYTHON,
    _SUPPORTED_PLATFORMS,
    InstalledProvenance,
    _catalog_schema_version,
    _clear_journal,
    _clear_snapshot,
    _dry_run_flag_unsupported,
    _extract_target_sha,
    _failure_result,
    _install_argv,
    _install_reached_target,
    _is_full_sha,
    _maintenance_argv,
    _phase_durations,
    _preflight_disk_check,
    _process_output_text,
    _read_journal,
    _restore_snapshot,
    _snapshot_metadata,
    _update_journal_path,
    _update_lock_path,
    _update_snapshot_dir,
    _uv_version_string,
    _validate_catalog_migration_path,
    _validate_storage_migration_path,
    _verify_installed_revision,
    _write_journal,
    _write_snapshot,
    read_uv_tool_direct_url,
)
from odoo_instance_sdk.models.update import UpdateOutcome, UpdateResult


def _build_failure_command(result: UpdateResult) -> Command[UpdateResult]:
    steps: tuple[PreparedAction, ...] = (
        PreparedAction(
            step_id="update.inspect",
            action="inspect",
            description="Inspect installed OdCLI provenance",
            read_only=True,
        ),
        PreparedAction(
            step_id="update.resolve",
            action="resolve",
            description="Resolve the requested revision to an immutable commit",
            read_only=True,
        ),
    )

    def callback(context: RunContext[UpdateResult]) -> UpdateResult:
        context.action("update.inspect")
        context.complete_action("update.inspect")
        context.action("update.resolve")
        context.complete_action("update.resolve")
        return result

    plan = ExecutionPlan(steps=tuple(step.public_projection() for step in steps))
    return Command.create(plan, callback, steps)


def _build_check_command(
    *,
    ref: str,
    provenance: InstalledProvenance,
    executor: ProcessExecutor | None,
) -> Command[UpdateResult]:
    check_step = PreparedStep(
        step_id="update.resolve.check",
        argv=_install_argv(ref, dry_run=True),
        read_only=True,
    )

    def callback(context: RunContext[UpdateResult]) -> UpdateResult:
        context.action("update.inspect")
        context.complete_action("update.inspect")
        context.action("update.resolve")
        result = cast("ProcessResult", context.process_prepared(check_step))
        if result.returncode != 0:
            stderr = result.stderr if isinstance(result.stderr, str) else ""
            if _dry_run_flag_unsupported(str(stderr)):
                detail = str(stderr).strip() or "uv rejected --dry-run"
                return _failure_result(
                    "unsupported_install",
                    provenance=provenance,
                    manual_argv=_MANUAL_INSTALL_ARGV,
                    next_step=(
                        f"uv {_uv_version_string()} does not support tool install --dry-run; {detail}"
                    ),
                )
            return _failure_result(
                "unsupported_install",
                provenance=provenance,
                manual_argv=_MANUAL_INSTALL_ARGV,
                next_step="upgrade uv or install manually with manual_argv",
            )
        output = _process_output_text(result)
        target_sha = _extract_target_sha(ref, str(output))
        if target_sha is None:
            return _failure_result(
                "unsupported_install",
                provenance=provenance,
                manual_argv=_MANUAL_INSTALL_ARGV,
                next_step="could not parse target SHA from uv output (sha_unparsed)",
            )
        context.complete_action("update.resolve")
        outcome: UpdateOutcome = (
            "already_current"
            if provenance.commit_id is not None and target_sha == provenance.commit_id
            else "updated"
        )
        return UpdateResult(
            outcome=outcome,
            source_repo=provenance.source_repo,
            previous_version=provenance.version,
            target_version=None,
            final_version=provenance.version if outcome == "already_current" else None,
            previous_sha=provenance.commit_id,
            target_sha=target_sha,
            final_sha=provenance.commit_id if outcome == "already_current" else None,
            executable_path=(
                str(provenance.uv_tool_bin_path) if provenance.uv_tool_bin_path else None
            ),
            tool_env_path=(
                str(provenance.uv_tool_env_path) if provenance.uv_tool_env_path else None
            ),
            snapshot_state="absent",
            journal_state="absent",
            next_step=(
                None
                if outcome == "already_current"
                else "run `odcli update` to apply the new revision"
            ),
        )

    steps: tuple[PreparedStep | PreparedAction, ...] = (
        PreparedAction(
            step_id="update.inspect",
            action="inspect",
            description="Inspect installed OdCLI provenance",
            read_only=True,
        ),
        PreparedAction(
            step_id="update.resolve",
            action="resolve",
            description="Resolve target revision via uv --dry-run",
            read_only=True,
        ),
        check_step,
    )
    plan = ExecutionPlan(steps=tuple(step.public_projection() for step in steps))
    prepared = prepared_command(callback, steps, executor=executor or SubprocessExecutor())
    return Command.from_prepared(plan, prepared)


def _build_already_current_command(
    provenance: InstalledProvenance,
) -> Command[UpdateResult]:
    steps: tuple[PreparedAction, ...] = (
        PreparedAction(
            step_id="update.inspect",
            action="inspect",
            description="Inspect installed OdCLI provenance",
            read_only=True,
        ),
    )

    def callback(context: RunContext[UpdateResult]) -> UpdateResult:
        context.action("update.inspect")
        context.complete_action("update.inspect")
        return UpdateResult(
            outcome="already_current",
            source_repo=provenance.source_repo,
            previous_version=provenance.version,
            target_version=provenance.version,
            final_version=provenance.version,
            previous_sha=provenance.commit_id,
            target_sha=provenance.commit_id,
            final_sha=provenance.commit_id,
            executable_path=(
                str(provenance.uv_tool_bin_path) if provenance.uv_tool_bin_path else None
            ),
            tool_env_path=(
                str(provenance.uv_tool_env_path) if provenance.uv_tool_env_path else None
            ),
            snapshot_state="absent",
            journal_state="absent",
            next_step=None,
        )

    plan = ExecutionPlan(steps=tuple(step.public_projection() for step in steps))
    return Command.create(plan, callback, steps)


def _skip_planned_steps(context: RunContext[UpdateResult], *step_ids: str) -> None:
    for step_id in step_ids:
        if context.planned(step_id) and not context.consumed(step_id):
            context.skip(step_id)


def resolve_update_target_sha(
    ref: str,
    *,
    executor: ProcessExecutor | None,
) -> str:
    """Resolve a mutable ref before any update command is constructed."""
    if _is_full_sha(ref):
        return ref.lower()
    step = PreparedStep(
        step_id="update.resolve",
        argv=_install_argv(ref, dry_run=True),
        read_only=True,
    )
    result = cast("ProcessResult", (executor or SubprocessExecutor()).execute(step))
    if result.returncode != 0:
        stderr = result.stderr if isinstance(result.stderr, str) else ""
        detail = stderr.strip() or "uv could not resolve the requested revision"
        raise UnsupportedInstallError(detail, manual_argv=_MANUAL_INSTALL_ARGV)
    target_sha = _extract_target_sha(ref, _process_output_text(result))
    if target_sha is None:
        raise UnsupportedInstallError(
            "could not parse target SHA from uv output (sha_unparsed)",
            manual_argv=_MANUAL_INSTALL_ARGV,
        )
    return target_sha


def _journal_resume_phase(journal: dict[str, JsonValue] | None) -> str | None:
    if journal is None:
        return None
    phase = journal.get("phase")
    return phase if isinstance(phase, str) else None


def _journal_snapshot_sha(
    journal: dict[str, JsonValue] | None,
    provenance: InstalledProvenance,
) -> str | None:
    if journal is not None:
        raw = journal.get("snapshot_sha")
        if isinstance(raw, str) and raw:
            return raw
    return provenance.commit_id


def _recovery_step_for_snapshot(snapshot_sha: str | None) -> PreparedStep:
    return PreparedStep(
        step_id="update.recovery",
        argv=_install_argv(snapshot_sha or _DEFAULT_REF),
        mutating=True,
    )


def _attempt_rollback(
    executor: ProcessExecutor,
    *,
    snapshot_sha: str | None,
    recovery_step: PreparedStep,
) -> str:
    if not snapshot_sha:
        return "not_attempted"
    result = cast("ProcessResult", executor.execute(recovery_step))
    if result.returncode != 0:
        return "failed"
    _restore_snapshot(_update_snapshot_dir())
    try:
        _verify_installed_revision(snapshot_sha)
    except UpdateError:
        return "failed"
    return "restored"


def _build_mutating_command(  # noqa: C901
    *,
    ref: str,
    provenance: InstalledProvenance,
    executor: ProcessExecutor | None,
    allow_downgrade: bool,
) -> Command[UpdateResult]:
    install_step = PreparedStep(
        step_id="update.install",
        argv=_install_argv(ref),
        mutating=True,
    )
    executable = provenance.uv_tool_bin_path
    if executable is None:
        raise UnsupportedInstallError(
            "could not locate the uv-tool odcli executable path",
            manual_argv=_MANUAL_INSTALL_ARGV,
        )
    maintenance_step = PreparedStep(
        step_id="update.migrate",
        argv=_maintenance_argv(executable),
        environment=((_MAINTENANCE_ENV, "1"),),
        mutating=True,
    )
    public_steps: tuple[PreparedStep | PreparedAction, ...] = (
        PreparedAction(
            step_id="update.inspect",
            action="inspect",
            description="Inspect installed OdCLI provenance",
            read_only=True,
        ),
        PreparedAction(
            step_id="update.resolve",
            action="resolve",
            description="Resolve the requested revision to an immutable commit",
            read_only=True,
        ),
        PreparedAction(
            step_id="update.preflight",
            action="preflight",
            description="Verify free space, schema, and migration path",
            read_only=True,
        ),
        PreparedAction(
            step_id="update.quiesce",
            action="quiesce",
            description="Acquire the exclusive update lock",
            mutating=False,
        ),
        PreparedAction(
            step_id="update.snapshot",
            action="snapshot",
            description="Snapshot affected metadata for rollback",
            mutating=True,
        ),
        install_step,
        maintenance_step,
        PreparedAction(
            step_id="update.verify",
            action="verify",
            description="Verify the new executable, schema, and journal",
            read_only=True,
        ),
        PreparedAction(
            step_id="update.commit",
            action="commit",
            description="Mark success and clear the snapshot",
            mutating=True,
        ),
    )

    def _preflight() -> str | None:
        if sys.version_info < _MIN_PYTHON:
            return (
                f"Python {'.'.join(str(part) for part in _MIN_PYTHON)}+ is required; "
                f"found {sys.version_info.major}.{sys.version_info.minor}"
            )
        if sys.platform not in _SUPPORTED_PLATFORMS:
            return f"unsupported platform {sys.platform!r}"
        disk_error = _preflight_disk_check()
        if disk_error is not None:
            return disk_error
        if _catalog_schema_version() == "unreadable":
            return "catalog schema is unreadable"
        try:
            _validate_catalog_migration_path()
            _validate_storage_migration_path()
        except PreflightFailedError as exc:
            return str(exc)
        if (
            not allow_downgrade
            and provenance.commit_id is not None
            and _is_full_sha(ref)
            and int(ref.lower(), 16) < int(provenance.commit_id.lower(), 16)
        ):
            return "downgrade refused without --allow-downgrade"
        return None

    def _parse_maintenance_stdout(stdout: str) -> UpdateResult:
        payload = json.loads(stdout)
        if not isinstance(payload, dict):
            raise UpdateError("maintenance produced a non-object JSON document")
        return msgspec.convert(payload, UpdateResult)

    def _run_update_phases(context: RunContext[UpdateResult]) -> UpdateResult:  # noqa: C901
        active_executor = executor or SubprocessExecutor()
        durations: dict[str, float] = {}
        journal_path = _update_journal_path()
        snapshot_dir = _update_snapshot_dir()
        journal_payload = _read_journal(journal_path)
        resume_phase = _journal_resume_phase(journal_payload)
        snapshot_sha = _journal_snapshot_sha(journal_payload, provenance)

        def _begin(phase: str) -> float:
            return time.monotonic()

        def _end(phase: str, started: float) -> None:
            durations[phase] = time.monotonic() - started

        started = _begin("inspect")
        context.action("update.inspect")
        context.complete_action("update.inspect")
        _end("inspect", started)

        context.action("update.resolve")
        context.complete_action("update.resolve")

        started = _begin("preflight")
        context.action("update.preflight")
        preflight_error = _preflight()
        context.complete_action("update.preflight")
        _end("preflight", started)
        if preflight_error is not None:
            _skip_planned_steps(
                context,
                "update.quiesce",
                "update.snapshot",
                "update.install",
                "update.migrate",
                "update.verify",
                "update.commit",
            )
            return _failure_result(
                "preflight_failed",
                provenance=provenance,
                next_step=preflight_error,
                phase_durations=_phase_durations(durations),
            )

        started = _begin("quiesce")
        context.action("update.quiesce")
        try:
            with exclusive_lock(_update_lock_path()):
                context.complete_action("update.quiesce")
                _end("quiesce", started)

                if resume_phase not in {"migrate", "install"}:
                    started = _begin("snapshot")
                    context.action("update.snapshot")
                    _write_snapshot(snapshot_dir, _snapshot_metadata(provenance, ref))
                    snapshot_sha = provenance.commit_id
                    _write_journal(
                        journal_path,
                        {
                            "version": _JOURNAL_VERSION,
                            "phase": "snapshot",
                            "target_ref": ref,
                            "snapshot_sha": snapshot_sha,
                            "maintenance_pid": None,
                        },
                    )
                    context.complete_action("update.snapshot")
                    _end("snapshot", started)
                else:
                    _skip_planned_steps(context, "update.snapshot")

                if resume_phase in {"migrate", "install"}:
                    _skip_planned_steps(context, "update.install")
                else:
                    started = _begin("install")
                    install_result = cast("ProcessResult", context.process_prepared(install_step))
                    _end("install", started)
                    if install_result.returncode != 0 and not _install_reached_target(
                        ref=ref,
                        output=_process_output_text(install_result),
                        previous_sha=provenance.commit_id,
                    ):
                        _clear_journal(journal_path)
                        _clear_snapshot(snapshot_dir)
                        raise UpdateError(  # noqa: TRY301
                            f"uv tool install failed with exit {install_result.returncode}",
                        )
                    _write_journal(
                        journal_path,
                        {
                            "version": _JOURNAL_VERSION,
                            "phase": "install",
                            "target_ref": ref,
                            "snapshot_sha": snapshot_sha,
                            "maintenance_pid": None,
                        },
                    )

                started = _begin("migrate")
                _write_journal(
                    journal_path,
                    {
                        "version": _JOURNAL_VERSION,
                        "phase": "migrate",
                        "target_ref": ref,
                        "snapshot_sha": snapshot_sha,
                        "maintenance_pid": None,
                    },
                )
                recovery_step = _recovery_step_for_snapshot(snapshot_sha)
                maintenance_result = cast(
                    "ProcessResult", context.process_prepared(maintenance_step)
                )
                _end("migrate", started)
                if maintenance_result.returncode != 0:
                    rollback = _attempt_rollback(
                        active_executor,
                        snapshot_sha=snapshot_sha,
                        recovery_step=recovery_step,
                    )
                    _write_journal(
                        journal_path,
                        {
                            "version": _JOURNAL_VERSION,
                            "phase": "migrate",
                            "target_ref": ref,
                            "snapshot_sha": snapshot_sha,
                            "maintenance_pid": None,
                            "recovery_argv": list(recovery_step.argv),
                        },
                    )
                    if rollback == "restored":
                        _clear_journal(journal_path)
                        _clear_snapshot(snapshot_dir)
                        _skip_planned_steps(context, "update.verify", "update.commit")
                        return _failure_result(
                            "rolled_back",
                            provenance=provenance,
                            rollback_outcome="restored",
                            next_step="maintenance failed; the previous revision was restored",
                            phase_durations=_phase_durations(durations),
                        )
                    _skip_planned_steps(context, "update.verify", "update.commit")
                    return _failure_result(
                        "update_incomplete",
                        provenance=provenance,
                        recovery_argv=recovery_step.argv,
                        rollback_outcome=rollback,
                        snapshot_state="present",
                        journal_state="present",
                        next_step=(
                            "maintenance process exited non-zero; "
                            "run the recorded recovery uv install to restore the previous revision"
                        ),
                        phase_durations=_phase_durations(durations),
                    )

                maintenance_stdout = (
                    maintenance_result.stdout
                    if isinstance(maintenance_result.stdout, str)
                    else (maintenance_result.stdout or b"").decode("utf-8", "replace")
                )
                started = _begin("verify")
                context.action("update.verify")
                try:
                    maintenance_result_model = _parse_maintenance_stdout(maintenance_stdout)
                    _verify_installed_revision(None, target_ref=ref)
                except UpdateError:
                    raise
                except (json.JSONDecodeError, msgspec.ValidationError) as exc:
                    context.fail_action("update.verify", exc)
                    _skip_planned_steps(context, "update.commit")
                    return _failure_result(
                        "update_incomplete",
                        provenance=provenance,
                        recovery_argv=recovery_step.argv,
                        rollback_outcome="not_attempted",
                        snapshot_state="present",
                        journal_state="present",
                        next_step=f"maintenance produced invalid JSON: {exc}",
                        phase_durations=_phase_durations(durations),
                    )
                context.complete_action("update.verify")
                _end("verify", started)

                started = _begin("commit")
                context.action("update.commit")
                _clear_snapshot(snapshot_dir)
                _clear_journal(journal_path)
                context.complete_action("update.commit")
                _end("commit", started)
        except LockConflictError:
            raise
        except UpdateError:
            raise
        except Exception as exc:
            raise UpdateError(str(exc)) from exc

        final_provenance = read_uv_tool_direct_url()
        return UpdateResult(
            outcome="updated",
            source_repo=provenance.source_repo,
            previous_version=provenance.version,
            target_version=final_provenance.version,
            final_version=final_provenance.version,
            previous_sha=provenance.commit_id,
            target_sha=maintenance_result_model.final_sha or final_provenance.commit_id,
            final_sha=maintenance_result_model.final_sha or final_provenance.commit_id,
            executable_path=(
                str(final_provenance.uv_tool_bin_path)
                if final_provenance.uv_tool_bin_path
                else None
            ),
            tool_env_path=(
                str(final_provenance.uv_tool_env_path)
                if final_provenance.uv_tool_env_path
                else None
            ),
            executed_migration_ids=maintenance_result_model.executed_migration_ids,
            skipped_migration_ids=maintenance_result_model.skipped_migration_ids,
            final_schema_versions=maintenance_result_model.final_schema_versions,
            snapshot_state="cleared",
            journal_state="cleared",
            rollback_outcome=None,
            next_step=None,
            phase_durations=_phase_durations(durations),
        )

    def callback(context: RunContext[UpdateResult]) -> UpdateResult:
        return _run_update_phases(context)

    plan = ExecutionPlan(steps=tuple(step.public_projection() for step in public_steps))
    prepared = prepared_command(
        callback,
        public_steps,
        executor=executor or SubprocessExecutor(),
    )
    return Command.from_prepared(plan, prepared)
