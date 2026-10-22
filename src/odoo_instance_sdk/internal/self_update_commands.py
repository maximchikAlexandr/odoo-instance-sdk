"""Frozen ``Command`` builders for ``odcli update``."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import msgspec

from odoo_instance_sdk.exceptions import (
    LockConflictError,
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
    _JOURNAL_VERSION,
    _MAINTENANCE_ENV,
    _MANUAL_INSTALL_ARGV,
    InstalledProvenance,
    _clear_journal,
    _clear_snapshot,
    _extract_target_sha,
    _failure_result,
    _install_argv,
    _install_reached_target,
    _is_full_sha,
    _is_supported_source_origin,
    _maintenance_argv,
    _phase_durations,
    _process_output_text,
    _read_journal,
    _resolve_argv,
    _restore_snapshot,
    _snapshot_metadata,
    _update_journal_path,
    _update_lock_path,
    _update_snapshot_dir,
    _verify_installed_revision,
    _write_journal,
    _write_snapshot,
    read_uv_tool_direct_url,
)
from odoo_instance_sdk.internal.self_update_ancestry import (
    RevisionRelation,
    revision_probe_steps as _revision_probe_steps,
    run_revision_probe as _run_revision_probe,
)
from odoo_instance_sdk.internal.self_update_composition import (
    companion_install_argv,
    companion_rollback_verified,
    journal_composition_fields,
    read_uv_tool_companion_in_environment,
    resume_update_argv,
    skip_companion_verification,
    verify_companion_revision,
)
from odoo_instance_sdk.internal.self_update_planning import (
    _build_preflight_command,
    _build_staged_command,
)
from odoo_instance_sdk.internal.self_update_policy import (
    preflight_error as _policy_preflight_error,
    probe_source_repo as _probe_source_repo,
    skip_revision_probe as _skip_revision_probe,
    source_origin_step as _source_origin_step,
)
from odoo_instance_sdk.internal.self_update_recovery import (
    build_update_recovery_projection,
    journal_resume_phase,
    journal_snapshot_sha,
    journal_target_ref,
    recovery_step_for_snapshot,
)
from odoo_instance_sdk.models.update import UpdateOutcome, UpdateResult

__all__ = ["_build_preflight_command", "_build_staged_command"]


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
    check_step = (
        None
        if _is_full_sha(ref)
        else PreparedStep(
            step_id="update.resolve.check",
            argv=_resolve_argv(ref),
            read_only=True,
        )
    )
    recovery_projection = build_update_recovery_projection(
        _update_journal_path().parent.parent,
        provenance.uv_tool_bin_path,
    )

    def callback(context: RunContext[UpdateResult]) -> UpdateResult:
        context.action("update.inspect")
        context.complete_action("update.inspect")
        context.action("update.resolve")
        target_sha = ref.lower()
        if check_step is not None:
            result = cast("ProcessResult", context.process_prepared(check_step))
            if result.returncode != 0:
                stderr = result.stderr if isinstance(result.stderr, str) else ""
                return _failure_result(
                    "unsupported_install",
                    provenance=provenance,
                    manual_argv=_MANUAL_INSTALL_ARGV,
                    next_step=str(stderr).strip() or "git ls-remote could not resolve the ref",
                )
            output = _process_output_text(result)
            resolved_sha = _extract_target_sha(ref, str(output))
            if resolved_sha is None:
                return _failure_result(
                    "unsupported_install",
                    provenance=provenance,
                    manual_argv=_MANUAL_INSTALL_ARGV,
                    next_step="could not parse target SHA from git ls-remote output (sha_unparsed)",
                )
            target_sha = resolved_sha
        context.complete_action("update.resolve")
        recovery = recovery_projection.state
        if recovery.has_evidence:
            recovery_argv = (
                recovery_projection.resume_command.argv
                if recovery_projection.resume_command is not None
                else None
            )
            return _failure_result(
                "update_incomplete",
                provenance=provenance,
                target_sha=recovery.target_ref or target_sha,
                recovery_argv=recovery_argv,
                snapshot_state=recovery.snapshot_state,
                journal_state=recovery.journal_state,
                next_step=(
                    "run the recorded recovery_argv to resume the interrupted update"
                    if recovery_argv is not None
                    else recovery.diagnostic or "update recovery evidence is incomplete"
                ),
            )
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
            description="Resolve target revision via git ls-remote",
            read_only=True,
        ),
        *((check_step,) if check_step is not None else ()),
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


def _preflight_error(
    *,
    ref: str,
    provenance: InstalledProvenance,
    allow_downgrade: bool,
    relation: RevisionRelation | None = None,
    source_origin_verified: bool = False,
) -> str | None:
    return _policy_preflight_error(
        ref=ref,
        provenance=provenance,
        allow_downgrade=allow_downgrade,
        relation=relation,
        source_origin_verified=source_origin_verified,
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


@dataclass(slots=True)
class _UpdateSession:
    ref: str
    provenance: InstalledProvenance
    executor: ProcessExecutor
    allow_downgrade: bool
    install_step: PreparedStep
    maintenance_step: PreparedStep
    verify_step: PreparedStep
    companion_install_step: PreparedStep | None
    companion_version_step: PreparedStep | None
    companion_metadata_step: PreparedStep | None
    companion_rollback_step: PreparedStep | None
    ancestry_steps: tuple[PreparedStep, ...]
    source_origin_step: PreparedStep | None
    cleanup_step_id: str | None
    context: RunContext[UpdateResult]
    journal_path: Path = field(init=False)
    snapshot_dir: Path = field(init=False)
    resume_phase: str | None = field(init=False)
    snapshot_sha: str | None = field(init=False)
    journal_target_ref: str | None = field(init=False)
    durations: dict[str, float] = field(default_factory=dict)
    recovery_step: PreparedStep | None = None
    maintenance_result: ProcessResult | None = None
    maintenance_result_model: UpdateResult | None = None
    source_origin_verified: bool = False

    def _composition_fields(self) -> dict[str, JsonValue]:
        return journal_composition_fields(self.provenance, self.ref)

    def _resume_command(self) -> tuple[str, ...] | None:
        return resume_update_argv(self.provenance.uv_tool_bin_path, self.ref)

    def __post_init__(self) -> None:
        self.journal_path = _update_journal_path()
        self.snapshot_dir = _update_snapshot_dir()
        journal = _read_journal(self.journal_path)
        self.resume_phase = journal_resume_phase(journal)
        self.snapshot_sha = journal_snapshot_sha(journal, self.provenance)
        self.journal_target_ref = journal_target_ref(journal)
        if self.journal_target_ref is not None:
            if _is_full_sha(self.ref) and self.ref.lower() != self.journal_target_ref:
                raise UpdateError("requested ref conflicts with the unfinished update journal")
            self.ref = self.journal_target_ref

    def _start(self, phase: str) -> float:
        return time.monotonic()

    def _finish(self, phase: str, started: float) -> None:
        self.durations[phase] = time.monotonic() - started

    def inspect_and_resolve(self) -> None:
        started = self._start("inspect")
        self.context.action("update.inspect")
        self.context.complete_action("update.inspect")
        if self.provenance.companion is not None:
            self.context.action("update.warn.companion")
            self.context.complete_action("update.warn.companion")
        self._finish("inspect", started)
        self.ref = self.ref.lower()

    def preflight(self) -> str | None:
        installed_sha = self.provenance.commit_id
        self.source_origin_verified = self.source_origin_step is None
        if self.source_origin_step is not None:
            origin_result = cast(
                "ProcessResult", self.context.process_prepared(self.source_origin_step)
            )
            origin_output = origin_result.stdout if isinstance(origin_result.stdout, str) else ""
            self.source_origin_verified = (
                origin_result.returncode == 0 and _is_supported_source_origin(origin_output)
            )
        relation = "unknown"
        if (
            self.source_origin_verified
            and installed_sha is not None
            and _is_full_sha(self.ref)
            and _is_full_sha(installed_sha)
        ):
            relation = _run_revision_probe(
                self.context,
                self.ancestry_steps,
                installed_sha=installed_sha.lower(),
                target_sha=self.ref.lower(),
                cleanup_step_id=self.cleanup_step_id,
            )
        elif self.ancestry_steps:
            _skip_revision_probe(self.context, self.ancestry_steps, self.cleanup_step_id)
        return _preflight_error(
            ref=self.ref,
            provenance=self.provenance,
            allow_downgrade=self.allow_downgrade,
            relation=cast("RevisionRelation", relation),
            source_origin_verified=self.source_origin_verified,
        )

    def snapshot(self) -> None:
        started = self._start("snapshot")
        self.context.action("update.snapshot")
        _write_snapshot(self.snapshot_dir, _snapshot_metadata(self.provenance, self.ref))
        self.snapshot_sha = self.provenance.commit_id
        _write_journal(
            self.journal_path,
            {
                "version": _JOURNAL_VERSION,
                "phase": "snapshot",
                "target_ref": self.ref,
                "snapshot_sha": self.snapshot_sha,
                "maintenance_pid": None,
                **self._composition_fields(),
            },
        )
        self.context.complete_action("update.snapshot")
        self._finish("snapshot", started)

    def _write_install_journal(self, *, phase: str = "install") -> None:
        _write_journal(
            self.journal_path,
            {
                "version": _JOURNAL_VERSION,
                "phase": phase,
                "target_ref": self.ref,
                "snapshot_sha": self.snapshot_sha,
                "maintenance_pid": None,
                **self._composition_fields(),
            },
        )

    def resume_snapshot(self) -> str | None:
        self.context.skip("update.snapshot")
        snapshot_sha = self.snapshot_sha
        target_ref = self.journal_target_ref
        installed_sha = self.provenance.commit_id
        if snapshot_sha is None or target_ref is None or installed_sha is None:
            raise UpdateError("snapshot resume has incomplete immutable provenance")
        installed_sha = installed_sha.lower()
        if installed_sha == snapshot_sha:
            return self.install()
        if installed_sha == target_ref:
            self.context.skip("update.install")
            self._write_install_journal()
            return self.install(core=False)
        raise UpdateError("snapshot resume found an unexpected installed revision")

    def install(self, *, core: bool = True) -> str | None:
        started = self._start("install")
        if core:
            result = cast("ProcessResult", self.context.process_prepared(self.install_step))
            if result.returncode != 0 and not _install_reached_target(
                ref=self.ref,
                output=_process_output_text(result),
                previous_sha=self.provenance.commit_id,
            ):
                _clear_journal(self.journal_path)
                _clear_snapshot(self.snapshot_dir)
                raise UpdateError(f"uv tool install failed with exit {result.returncode}")
        if self.companion_install_step is not None:
            self._write_install_journal()
            companion_result = cast(
                "ProcessResult", self.context.process_prepared(self.companion_install_step)
            )
            if companion_result.returncode != 0:
                self._finish("install", started)
                return "odcli-multica coordinated install failed; recovery evidence was retained"
        self._write_install_journal()
        self._finish("install", started)
        return None

    def migrate(self) -> UpdateResult | None:
        started = self._start("migrate")
        _write_journal(
            self.journal_path,
            {
                "version": _JOURNAL_VERSION,
                "phase": "migrate",
                "target_ref": self.ref,
                "snapshot_sha": self.snapshot_sha,
                "maintenance_pid": None,
                **self._composition_fields(),
            },
        )
        recovery_step = recovery_step_for_snapshot(
            self.snapshot_sha,
            source_repo=self.provenance.source_repo,
            extras=self.provenance.requested_extras,
        )
        self.recovery_step = recovery_step
        result = cast("ProcessResult", self.context.process_prepared(self.maintenance_step))
        self.maintenance_result = result
        self._finish("migrate", started)
        if result.returncode == 0:
            _skip_planned_steps(self.context, "update.recovery.companion")
            return None
        rollback = _attempt_rollback(
            self.executor,
            snapshot_sha=self.snapshot_sha,
            recovery_step=recovery_step,
        )
        companion = self.provenance.companion
        if (
            rollback == "restored"
            and companion is not None
            and self.companion_rollback_step is not None
        ):
            result = cast(
                "ProcessResult",
                self.context.process_prepared(self.companion_rollback_step),
            )
            try:
                restored = read_uv_tool_companion_in_environment(
                    companion.source_repo, companion.environment
                )
            except (UpdateError, UnsupportedInstallError):
                restored = None
            if not companion_rollback_verified(result, restored, companion):
                rollback = "failed"
        else:
            _skip_planned_steps(self.context, "update.recovery.companion")
        _write_journal(
            self.journal_path,
            {
                "version": _JOURNAL_VERSION,
                "phase": "migrate",
                "target_ref": self.ref,
                "snapshot_sha": self.snapshot_sha,
                "maintenance_pid": None,
                "recovery_argv": list(recovery_step.argv),
                **self._composition_fields(),
            },
        )
        if rollback == "restored":
            _clear_journal(self.journal_path)
            _clear_snapshot(self.snapshot_dir)
            skip_companion_verification(self.context)
            return _failure_result(
                "rolled_back",
                provenance=self.provenance,
                rollback_outcome="restored",
                next_step="maintenance failed; the previous revision was restored",
                phase_durations=_phase_durations(self.durations),
            )
        self._write_install_journal(phase="snapshot")
        skip_companion_verification(self.context)
        return _failure_result(
            "update_incomplete",
            provenance=self.provenance,
            recovery_argv=self._resume_command() or recovery_step.argv,
            rollback_outcome=rollback,
            snapshot_state="present",
            journal_state="present",
            next_step=(
                "maintenance process exited non-zero; "
                "run the recorded recovery uv install to restore the previous revision"
            ),
            phase_durations=_phase_durations(self.durations),
        )

    @staticmethod
    def _parse_maintenance_stdout(stdout: str) -> UpdateResult:
        payload = json.loads(stdout)
        if not isinstance(payload, dict):
            raise UpdateError("maintenance produced a non-object JSON document")
        return msgspec.convert(payload, UpdateResult)

    def verify(self, maintenance_result: ProcessResult) -> UpdateResult | None:
        stdout = (
            maintenance_result.stdout
            if isinstance(maintenance_result.stdout, str)
            else (maintenance_result.stdout or b"").decode("utf-8", "replace")
        )
        started = self._start("verify")
        self.context.action("update.verify")
        try:
            self.maintenance_result_model = self._parse_maintenance_stdout(stdout)
            version_result = cast("ProcessResult", self.context.process_prepared(self.verify_step))
            if version_result.returncode != 0:
                raise UpdateError("odcli --version failed after update")  # noqa: TRY301
            _verify_installed_revision(
                None,
                target_ref=self.ref,
                version_result=version_result,
            )
            if self.companion_version_step is not None and self.companion_metadata_step is not None:
                companion_version = cast(
                    "ProcessResult", self.context.process_prepared(self.companion_version_step)
                )
                companion_metadata = cast(
                    "ProcessResult", self.context.process_prepared(self.companion_metadata_step)
                )
                verify_companion_revision(companion_version, companion_metadata, self.ref)
        except UpdateError as exc:
            self.context.fail_action("update.verify", exc)
            _skip_planned_steps(self.context, "update.commit")
            self._write_install_journal(phase="snapshot")
            self._finish("verify", started)
            return _failure_result(
                "update_incomplete",
                provenance=self.provenance,
                recovery_argv=(
                    self._resume_command()
                    if self.provenance.companion is not None
                    else self.recovery_step.argv
                    if self.recovery_step
                    else None
                ),
                rollback_outcome="not_attempted",
                snapshot_state="present",
                journal_state="present",
                next_step=str(exc),
                phase_durations=_phase_durations(self.durations),
            )
        except (json.JSONDecodeError, msgspec.ValidationError) as exc:
            self.context.fail_action("update.verify", exc)
            _skip_planned_steps(self.context, "update.verify.version", "update.commit")
            self._write_install_journal(phase="snapshot")
            self._finish("verify", started)
            return _failure_result(
                "update_incomplete",
                provenance=self.provenance,
                recovery_argv=(
                    self._resume_command()
                    if self.provenance.companion is not None
                    else self.recovery_step.argv
                    if self.recovery_step
                    else None
                ),
                rollback_outcome="not_attempted",
                snapshot_state="present",
                journal_state="present",
                next_step=f"maintenance produced invalid JSON: {exc}",
                phase_durations=_phase_durations(self.durations),
            )
        self.context.complete_action("update.verify")
        self._finish("verify", started)
        return None

    def commit(self) -> None:
        started = self._start("commit")
        self.context.action("update.commit")
        _clear_snapshot(self.snapshot_dir)
        _clear_journal(self.journal_path)
        self.context.complete_action("update.commit")
        self._finish("commit", started)

    def resume_or_install(self) -> str | None:
        if self.resume_phase == "snapshot":
            return self.resume_snapshot()
        if self.resume_phase in {"migrate", "install"}:
            _skip_planned_steps(self.context, "update.snapshot", "update.install")
            if self.resume_phase == "install" and self.companion_install_step is not None:
                self._write_install_journal()
                result = cast(
                    "ProcessResult", self.context.process_prepared(self.companion_install_step)
                )
                if result.returncode != 0:
                    return "odcli-multica coordinated install could not be resumed"
            elif self.resume_phase == "migrate":
                _skip_planned_steps(self.context, "update.install.companion")
            return None
        self.snapshot()
        return self.install()

    def result(self) -> UpdateResult:
        if self.maintenance_result_model is None:
            raise UpdateError("maintenance result was not verified")
        final_provenance = read_uv_tool_direct_url()
        model = self.maintenance_result_model
        return UpdateResult(
            outcome="updated",
            source_repo=self.provenance.source_repo,
            previous_version=self.provenance.version,
            target_version=final_provenance.version,
            final_version=final_provenance.version,
            previous_sha=self.provenance.commit_id,
            target_sha=model.final_sha or final_provenance.commit_id,
            final_sha=model.final_sha or final_provenance.commit_id,
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
            executed_migration_ids=model.executed_migration_ids,
            skipped_migration_ids=model.skipped_migration_ids,
            final_schema_versions=model.final_schema_versions,
            snapshot_state="cleared",
            journal_state="cleared",
            rollback_outcome=None,
            next_step=None,
            phase_durations=_phase_durations(self.durations),
        )

    def run(self) -> UpdateResult:
        self.inspect_and_resolve()
        started = self._start("preflight")
        self.context.action("update.preflight")
        preflight_error = self.preflight()
        self.context.complete_action("update.preflight")
        self._finish("preflight", started)
        if preflight_error is not None:
            _skip_planned_steps(
                self.context,
                "update.quiesce",
                "update.snapshot",
                "update.install",
                "update.migrate",
                "update.verify",
                "update.verify.version",
                "update.commit",
            )
            return _failure_result(
                "preflight_failed",
                provenance=self.provenance,
                next_step=preflight_error,
                phase_durations=_phase_durations(self.durations),
            )
        started = self._start("quiesce")
        self.context.action("update.quiesce")
        try:
            with exclusive_lock(_update_lock_path()):
                self.context.complete_action("update.quiesce")
                self._finish("quiesce", started)
                install_failure = self.resume_or_install()
                if install_failure is not None:
                    _skip_planned_steps(
                        self.context,
                        "update.migrate",
                        "update.verify",
                        "update.verify.version",
                        "update.verify.companion.version",
                        "update.verify.companion.revisions",
                        "update.recovery.companion",
                        "update.commit",
                    )
                    companion = self.provenance.companion
                    recovery = (
                        self._resume_command()
                        or companion_install_argv(
                            companion.package_sha,
                            sdk_ref=companion.sdk_sha,
                            source_repo=self.provenance.source_repo,
                        )
                        if companion is not None
                        else None
                    )
                    return _failure_result(
                        "update_incomplete",
                        provenance=self.provenance,
                        recovery_argv=recovery,
                        snapshot_state="present",
                        journal_state="present",
                        next_step=install_failure,
                        phase_durations=_phase_durations(self.durations),
                    )
                migration_failure = self.migrate()
                if migration_failure is not None:
                    return migration_failure
                maintenance_result = self.maintenance_result
                if maintenance_result is None:
                    skip_companion_verification(self.context)
                    return _failure_result(
                        "update_incomplete",
                        provenance=self.provenance,
                        rollback_outcome="not_attempted",
                        snapshot_state="present",
                        journal_state="present",
                        next_step="maintenance process did not produce a result",
                        phase_durations=_phase_durations(self.durations),
                    )
                verification_failure = self.verify(maintenance_result)
                if verification_failure is not None:
                    return verification_failure
                self.commit()
        except LockConflictError:
            raise
        except UpdateError:
            raise
        except Exception as exc:
            raise UpdateError(str(exc)) from exc
        return self.result()


def _build_mutating_command(
    *,
    ref: str,
    provenance: InstalledProvenance,
    executor: ProcessExecutor | None,
    allow_downgrade: bool,
) -> Command[UpdateResult]:
    if not _is_full_sha(ref):
        raise UnsupportedInstallError(
            "mutation command requires an immutable resolved revision",
            manual_argv=_MANUAL_INSTALL_ARGV,
        )
    install_step = PreparedStep(
        step_id="update.install",
        argv=_install_argv(
            ref,
            source_repo=provenance.source_repo,
            extras=provenance.requested_extras,
        ),
        mutating=True,
    )
    companion = provenance.companion
    companion_install_step = (
        PreparedStep(
            step_id="update.install.companion",
            argv=companion_install_argv(ref, source_repo=provenance.source_repo),
            mutating=True,
        )
        if companion is not None
        else None
    )
    companion_version_step = (
        PreparedStep(
            step_id="update.verify.companion.version",
            argv=(str(companion.launcher), "--version"),
            read_only=True,
        )
        if companion is not None
        else None
    )
    companion_metadata_step = (
        PreparedStep(
            step_id="update.verify.companion.revisions",
            argv=(
                str(companion.environment / "bin" / "python"),
                "-c",
                (
                    "import importlib.metadata as m, json; "
                    "print(json.dumps({n: json.loads(m.distribution(n).read_text('direct_url.json'))"
                    "['vcs_info']['commit_id'] for n in ('odcli-multica', 'odoo-instance-sdk')}))"
                ),
            ),
            read_only=True,
        )
        if companion is not None
        else None
    )
    companion_rollback_step = (
        PreparedStep(
            step_id="update.recovery.companion",
            argv=companion_install_argv(
                companion.package_sha,
                sdk_ref=companion.sdk_sha,
                source_repo=companion.source_repo,
            ),
            mutating=True,
        )
        if companion is not None
        else None
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
    verify_step = PreparedStep(
        step_id="update.verify.version",
        argv=(str(Path(executable).absolute()), "--version"),
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
        if canonical_source_repo and provenance.commit_id and _is_full_sha(provenance.commit_id)
        else ()
    )
    cleanup_action = (
        PreparedAction(
            step_id="update.inspect.ancestry-cleanup",
            action="cleanup",
            description="Remove the temporary ancestry object store",
            mutating=True,
        )
        if ancestry_steps
        else None
    )
    public_steps: tuple[PreparedStep | PreparedAction, ...] = (
        PreparedAction(
            step_id="update.inspect",
            action="inspect",
            description=(
                "Inspect installed OdCLI and odcli-multica provenance"
                if companion is not None
                else "Inspect installed OdCLI provenance"
            ),
            read_only=True,
        ),
        *(
            (
                PreparedAction(
                    step_id="update.warn.companion",
                    action="warn",
                    description="Warning: this update replaces both odcli and odcli-multica",
                    read_only=True,
                ),
            )
            if companion is not None
            else ()
        ),
        PreparedAction(
            step_id="update.preflight",
            action="preflight",
            description="Verify free space, schema, and migration path",
            read_only=True,
        ),
        *((origin_step,) if origin_step is not None else ()),
        *ancestry_steps,
        *((cleanup_action,) if cleanup_action is not None else ()),
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
        *((companion_install_step,) if companion_install_step is not None else ()),
        *((companion_rollback_step,) if companion_rollback_step is not None else ()),
        maintenance_step,
        PreparedAction(
            step_id="update.verify",
            action="verify",
            description="Verify the new executable, schema, and journal",
            read_only=True,
        ),
        verify_step,
        *((companion_version_step,) if companion_version_step is not None else ()),
        *((companion_metadata_step,) if companion_metadata_step is not None else ()),
        PreparedAction(
            step_id="update.commit",
            action="commit",
            description="Mark success and clear the snapshot",
            mutating=True,
        ),
    )
    active_executor = executor or SubprocessExecutor()

    def callback(context: RunContext[UpdateResult]) -> UpdateResult:
        return _UpdateSession(
            ref=ref,
            provenance=provenance,
            executor=active_executor,
            allow_downgrade=allow_downgrade,
            install_step=install_step,
            maintenance_step=maintenance_step,
            verify_step=verify_step,
            companion_install_step=companion_install_step,
            companion_version_step=companion_version_step,
            companion_metadata_step=companion_metadata_step,
            companion_rollback_step=companion_rollback_step,
            ancestry_steps=ancestry_steps,
            source_origin_step=origin_step,
            cleanup_step_id=cleanup_action.step_id if cleanup_action else None,
            context=context,
        ).run()

    plan = ExecutionPlan(steps=tuple(step.public_projection() for step in public_steps))
    prepared = prepared_command(callback, public_steps, executor=active_executor)
    return Command.from_prepared(plan, prepared)
