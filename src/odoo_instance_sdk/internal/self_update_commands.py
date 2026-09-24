"""Frozen ``Command`` builders for ``odcli update``."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
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
    _canonical_supported_source_repo,
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
    _storage_migration_state,
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
from odoo_instance_sdk.internal.self_update_ancestry import (
    RevisionRelation,
)
from odoo_instance_sdk.internal.self_update_ancestry import (
    git_revision_relation as _git_revision_relation,
)
from odoo_instance_sdk.internal.self_update_ancestry import (
    revision_probe_steps as _revision_probe_steps,
)
from odoo_instance_sdk.internal.self_update_ancestry import (
    run_revision_probe as _run_revision_probe,
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


def _journal_resume_phase(journal: dict[str, JsonValue] | None) -> str | None:
    if journal is None:
        return None
    phase = journal.get("phase")
    return phase if isinstance(phase, str) else None


def _journal_target_ref(journal: dict[str, JsonValue] | None) -> str | None:
    if journal is None:
        return None
    raw = journal.get("target_ref")
    if isinstance(raw, str) and _is_full_sha(raw):
        return raw.lower()
    raise UpdateError("unfinished update journal has no immutable target_ref")


def _journal_snapshot_sha(
    journal: dict[str, JsonValue] | None,
    provenance: InstalledProvenance,
) -> str | None:
    if journal is not None:
        raw = journal.get("snapshot_sha")
        if isinstance(raw, str) and _is_full_sha(raw):
            return raw.lower()
        raise UpdateError("unfinished update journal has no immutable snapshot_sha")
    return provenance.commit_id


def _validate_downgrade_snapshot_restore() -> str | None:
    """Prove that the pre-install snapshot can restore the current data state."""
    storage_state = _storage_migration_state()
    if storage_state not in {"absent", "complete"}:
        return f"downgrade snapshot cannot restore storage state {storage_state!r}"
    if _catalog_schema_version() == "unreadable":
        return "downgrade snapshot cannot restore an unreadable catalog"
    return None


def _revision_preflight_error(
    *,
    ref: str,
    provenance: InstalledProvenance,
    allow_downgrade: bool,
    relation: RevisionRelation | None = None,
) -> str | None:
    if not _is_full_sha(ref):
        return None
    installed_sha = provenance.commit_id
    if installed_sha is None or not _is_full_sha(installed_sha):
        return "cannot verify revision ancestry; installed commit is unavailable"
    canonical_source_repo = _canonical_supported_source_repo(provenance.source_repo)
    if canonical_source_repo is None:
        return "cannot verify revision ancestry; source provenance is unsupported"
    relation = relation or _git_revision_relation(
        source_repo=canonical_source_repo,
        installed_sha=installed_sha.lower(),
        target_sha=ref.lower(),
    )
    if relation == "ancestor" and not allow_downgrade:
        return "downgrade refused without --allow-downgrade"
    if relation == "ancestor":
        downgrade_error = _validate_downgrade_snapshot_restore()
        if downgrade_error is not None:
            return downgrade_error
    if relation not in {"same", "descendant", "ancestor"}:
        return "cannot verify revision ancestry; target history is unavailable"
    return None


def _preflight_error(
    *,
    ref: str,
    provenance: InstalledProvenance,
    allow_downgrade: bool,
    relation: RevisionRelation | None = None,
) -> str | None:
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
    return _revision_preflight_error(
        ref=ref,
        provenance=provenance,
        allow_downgrade=allow_downgrade,
        relation=relation,
    )


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


@dataclass(slots=True)
class _UpdateSession:
    ref: str
    provenance: InstalledProvenance
    executor: ProcessExecutor
    allow_downgrade: bool
    install_step: PreparedStep
    maintenance_step: PreparedStep
    ancestry_steps: tuple[PreparedStep, ...]
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

    def __post_init__(self) -> None:
        self.journal_path = _update_journal_path()
        self.snapshot_dir = _update_snapshot_dir()
        journal = _read_journal(self.journal_path)
        self.resume_phase = _journal_resume_phase(journal)
        self.snapshot_sha = _journal_snapshot_sha(journal, self.provenance)
        self.journal_target_ref = _journal_target_ref(journal)
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
        self._finish("inspect", started)
        self.ref = self.ref.lower()

    def preflight(self) -> str | None:
        installed_sha = self.provenance.commit_id
        relation = "unknown"
        if installed_sha is not None and _is_full_sha(self.ref) and _is_full_sha(installed_sha):
            relation = _run_revision_probe(
                self.context,
                self.ancestry_steps,
                installed_sha=installed_sha.lower(),
                target_sha=self.ref.lower(),
            )
        return _preflight_error(
            ref=self.ref,
            provenance=self.provenance,
            allow_downgrade=self.allow_downgrade,
            relation=cast("RevisionRelation", relation),
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
            },
        )
        self.context.complete_action("update.snapshot")
        self._finish("snapshot", started)

    def _write_install_journal(self) -> None:
        _write_journal(
            self.journal_path,
            {
                "version": _JOURNAL_VERSION,
                "phase": "install",
                "target_ref": self.ref,
                "snapshot_sha": self.snapshot_sha,
                "maintenance_pid": None,
            },
        )

    def resume_snapshot(self) -> None:
        """Resume a snapshot-phase crash without replacing its rollback point."""
        self.context.skip("update.snapshot")
        snapshot_sha = self.snapshot_sha
        target_ref = self.journal_target_ref
        installed_sha = self.provenance.commit_id
        if snapshot_sha is None or target_ref is None or installed_sha is None:
            raise UpdateError("snapshot resume has incomplete immutable provenance")
        installed_sha = installed_sha.lower()
        if installed_sha == snapshot_sha:
            self.install()
            return
        if installed_sha == target_ref:
            self.context.skip("update.install")
            self._write_install_journal()
            return
        raise UpdateError("snapshot resume found an unexpected installed revision")

    def install(self) -> None:
        started = self._start("install")
        result = cast("ProcessResult", self.context.process_prepared(self.install_step))
        self._finish("install", started)
        if result.returncode != 0 and not _install_reached_target(
            ref=self.ref,
            output=_process_output_text(result),
            previous_sha=self.provenance.commit_id,
        ):
            _clear_journal(self.journal_path)
            _clear_snapshot(self.snapshot_dir)
            raise UpdateError(f"uv tool install failed with exit {result.returncode}")
        self._write_install_journal()

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
            },
        )
        recovery_step = _recovery_step_for_snapshot(self.snapshot_sha)
        self.recovery_step = recovery_step
        result = cast("ProcessResult", self.context.process_prepared(self.maintenance_step))
        self.maintenance_result = result
        self._finish("migrate", started)
        if result.returncode == 0:
            return None
        rollback = _attempt_rollback(
            self.executor,
            snapshot_sha=self.snapshot_sha,
            recovery_step=recovery_step,
        )
        _write_journal(
            self.journal_path,
            {
                "version": _JOURNAL_VERSION,
                "phase": "migrate",
                "target_ref": self.ref,
                "snapshot_sha": self.snapshot_sha,
                "maintenance_pid": None,
                "recovery_argv": list(recovery_step.argv),
            },
        )
        if rollback == "restored":
            _clear_journal(self.journal_path)
            _clear_snapshot(self.snapshot_dir)
            _skip_planned_steps(self.context, "update.verify", "update.commit")
            return _failure_result(
                "rolled_back",
                provenance=self.provenance,
                rollback_outcome="restored",
                next_step="maintenance failed; the previous revision was restored",
                phase_durations=_phase_durations(self.durations),
            )
        _skip_planned_steps(self.context, "update.verify", "update.commit")
        return _failure_result(
            "update_incomplete",
            provenance=self.provenance,
            recovery_argv=recovery_step.argv,
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
            _verify_installed_revision(None, target_ref=self.ref)
        except UpdateError:
            raise
        except (json.JSONDecodeError, msgspec.ValidationError) as exc:
            self.context.fail_action("update.verify", exc)
            _skip_planned_steps(self.context, "update.commit")
            return _failure_result(
                "update_incomplete",
                provenance=self.provenance,
                recovery_argv=self.recovery_step.argv if self.recovery_step else None,
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

    def resume_or_install(self) -> None:
        if self.resume_phase == "snapshot":
            self.resume_snapshot()
        elif self.resume_phase in {"migrate", "install"}:
            _skip_planned_steps(self.context, "update.snapshot", "update.install")
        else:
            self.snapshot()
            self.install()

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
                self.resume_or_install()
                migration_failure = self.migrate()
                if migration_failure is not None:
                    return migration_failure
                maintenance_result = self.maintenance_result
                if maintenance_result is None:
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


def _build_staged_command(
    *,
    ref: str,
    provenance: InstalledProvenance,
    executor: ProcessExecutor | None,
) -> Command[UpdateResult]:
    """Build the first, read-only stage of the mutable-ref update flow."""
    resolve_step = PreparedStep(
        step_id="update.resolve",
        argv=_install_argv(ref, dry_run=True),
        read_only=True,
    )
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
    """Run the read-only checks before a resolved mutation plan is shown."""
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
    canonical_source_repo = _canonical_supported_source_repo(provenance.source_repo)
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
    steps: tuple[PreparedAction | PreparedStep, ...] = (
        inspect_step,
        preflight_step,
        *ancestry_steps,
    )

    def callback(context: RunContext[UpdateResult]) -> UpdateResult:
        context.action(inspect_step.step_id)
        context.complete_action(inspect_step.step_id)
        context.action(preflight_step.step_id)
        relation = "unknown"
        if provenance.commit_id is not None and _is_full_sha(ref):
            relation = _run_revision_probe(
                context,
                ancestry_steps,
                installed_sha=provenance.commit_id.lower(),
                target_sha=ref.lower(),
            )
        error = _preflight_error(
            ref=ref,
            provenance=provenance,
            allow_downgrade=allow_downgrade,
            relation=cast("RevisionRelation", relation),
        )
        context.complete_action(preflight_step.step_id)
        if error is not None:
            return _failure_result(
                "preflight_failed",
                provenance=provenance,
                next_step=error,
            )
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
    canonical_source_repo = _canonical_supported_source_repo(provenance.source_repo)
    ancestry_steps = (
        _revision_probe_steps(
            source_repo=canonical_source_repo,
            installed_sha=provenance.commit_id or "",
            target_sha=ref,
        )
        if canonical_source_repo and provenance.commit_id and _is_full_sha(provenance.commit_id)
        else ()
    )
    public_steps: tuple[PreparedStep | PreparedAction, ...] = (
        PreparedAction(
            step_id="update.inspect",
            action="inspect",
            description="Inspect installed OdCLI provenance",
            read_only=True,
        ),
        PreparedAction(
            step_id="update.preflight",
            action="preflight",
            description="Verify free space, schema, and migration path",
            read_only=True,
        ),
        *ancestry_steps,
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
    active_executor = executor or SubprocessExecutor()

    def callback(context: RunContext[UpdateResult]) -> UpdateResult:
        return _UpdateSession(
            ref=ref,
            provenance=provenance,
            executor=active_executor,
            allow_downgrade=allow_downgrade,
            install_step=install_step,
            maintenance_step=maintenance_step,
            ancestry_steps=ancestry_steps,
            context=context,
        ).run()

    plan = ExecutionPlan(steps=tuple(step.public_projection() for step in public_steps))
    prepared = prepared_command(callback, public_steps, executor=active_executor)
    return Command.from_prepared(plan, prepared)
