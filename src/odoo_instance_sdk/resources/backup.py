from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

from odoo_instance_sdk.exceptions import (
    BackupCatalogError,
    BackupNotAvailableError,
    BackupNotFoundError,
    BackupValidationUnavailableError,
    LockConflictError,
    StalePlanError,
)
from odoo_instance_sdk.internal.backup_validation import validate_dump, validate_zip
from odoo_instance_sdk.internal.locks import backup_lock_path, exclusive_lock
from odoo_instance_sdk.internal.process_env import sanitized_child_environment
from odoo_instance_sdk.internal.repo_key import git_common_dir, repo_key
from odoo_instance_sdk.internal.urls import normalize_base_url
from odoo_instance_sdk.models import (
    Backup,
    BackupDeletionResult,
    BackupEvent,
    BackupFormat,
    BackupInspectResult,
    BackupPinResult,
    BackupPruneCandidate,
    BackupPrunePlan,
    BackupPruneResult,
    BackupPruneSkip,
    BackupRetentionPolicy,
    BackupRetentionUpdateResult,
    BackupState,
    BackupValidationResult,
    BackupValidationStatus,
)
from odoo_instance_sdk.resources.catalog import backup_projection_to_inspect_result
from odoo_instance_sdk.storage.catalog.helpers import _row_to_backup

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        ProcessExecutor,
        RunContext,
    )
    from odoo_instance_sdk.project import ProjectConfig
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

T = TypeVar("T")


def _file_identity(path: Path) -> tuple[int, int] | None:
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise BackupCatalogError("Unable to inspect backup file") from exc
    return stat_result.st_dev, stat_result.st_ino


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _managed_backup_roots(client: OdooClient) -> tuple[Path, ...]:
    from odoo_instance_sdk.internal.paths import get_backups_dir

    roots: list[Path] = []
    configured = client.config.backups_directory
    if configured is not None:
        roots.append(Path(configured).expanduser().resolve(strict=False))
    roots.append(get_backups_dir(ensure_exists=False).resolve(strict=False))
    return tuple(dict.fromkeys(roots))


def _is_sdk_managed_backup_path(client: OdooClient, path: Path) -> bool:
    """Prove a path is an SDK-managed regular-file location, fail closed."""
    try:
        if path.is_symlink():
            return False
        if path.exists() and not path.is_file():
            return False
        resolved = path.resolve(strict=False)
        return any(
            root == resolved or root in resolved.parents for root in _managed_backup_roots(client)
        )
    except (OSError, RuntimeError):
        return False


def _project_id(project: ProjectConfig | str | Path) -> str:
    if isinstance(project, str) and project.startswith("project_"):
        return project
    if hasattr(project, "repository_root"):
        root = Path(cast("ProjectConfig", project).repository_root).resolve()
    else:
        root = Path(project).expanduser().resolve()
    return f"project_{repo_key(root, git_common_dir(root))}"


def _policy_fingerprint(policy: BackupRetentionPolicy) -> str:
    payload = json.dumps(
        {"auto_prune": policy.auto_prune, "retention_days": policy.retention_days},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(slots=True, kw_only=True)
class BackupResource:
    _client: OdooClient

    def retention(self) -> BackupRetentionPolicy:
        from odoo_instance_sdk.internal.backup_retention import read_retention_policy

        return read_retention_policy()

    def set_retention_command(
        self,
        *,
        retention_days: int | None = None,
        auto_prune: bool | None = None,
        dry_run: bool = False,
        executor: ProcessExecutor | None = None,
    ) -> Command[BackupRetentionUpdateResult]:
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.backup_retention import (
            read_retention_policy,
            retention_path,
            write_retention_policy,
        )
        from odoo_instance_sdk.internal.proc import (
            PreparedAction,
            SubprocessExecutor,
            prepared_command,
        )

        current = read_retention_policy()
        days = current.retention_days if retention_days is None else retention_days
        enabled = current.auto_prune if auto_prune is None else auto_prune
        desired = BackupRetentionPolicy(
            retention_days=days,
            auto_prune=enabled,
            path=str(retention_path()),
        )
        action_id = "backup.retention.preview" if dry_run else "backup.retention.update"
        action = PreparedAction(
            step_id=action_id,
            action=action_id,
            description="Preview or update user backup retention settings",
            read_only=dry_run,
            mutating=not dry_run,
        )

        def run(context: RunContext[BackupRetentionUpdateResult]) -> BackupRetentionUpdateResult:
            context.action(action_id)
            changed = False if dry_run else write_retention_policy(desired)
            result = BackupRetentionUpdateResult(policy=desired, changed=changed)
            context.complete_action(action_id)
            return result

        return Command.from_prepared(
            ExecutionPlan(steps=(action.public_projection(),)),
            prepared_command(run, (action,), executor=executor or SubprocessExecutor()),
        )

    def set_retention(
        self,
        *,
        retention_days: int | None = None,
        auto_prune: bool | None = None,
        dry_run: bool = False,
    ) -> BackupRetentionUpdateResult:
        return self.set_retention_command(
            retention_days=retention_days, auto_prune=auto_prune, dry_run=dry_run
        ).run()

    def set_pinned_command(
        self,
        backup_id: str,
        pinned: bool,
        *,
        dry_run: bool = False,
        executor: ProcessExecutor | None = None,
    ) -> Command[BackupPinResult]:
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.proc import (
            PreparedAction,
            SubprocessExecutor,
            prepared_command,
        )

        catalog = self._client.get_catalog()
        canonical_id = catalog._canonical_backup_id(backup_id)
        row = catalog.get_by_id(canonical_id)
        if row is None:
            raise BackupNotFoundError(f"Backup {canonical_id} not found in catalog")
        desired = bool(pinned) if type(pinned) is bool else pinned
        if type(desired) is not bool:
            raise TypeError("pinned must be a boolean")
        changed = bool(row["pinned"]) != desired
        action_id = "backup.pin.preview" if dry_run else "backup.pin.update"
        action = PreparedAction(
            step_id=action_id,
            action=action_id,
            description="Preview or update one backup pin state",
            read_only=dry_run,
            mutating=not dry_run,
        )

        def run(context: RunContext[BackupPinResult]) -> BackupPinResult:
            context.action(action_id)
            if dry_run:
                actual_changed = False
            else:
                with exclusive_lock(backup_lock_path(canonical_id)):
                    actual_changed = catalog.set_pinned(canonical_id, desired)
            context.complete_action(action_id)
            return BackupPinResult(
                backup_id=uuid.UUID(canonical_id),
                pinned=desired,
                changed=actual_changed if not dry_run else changed,
            )

        return Command.from_prepared(
            ExecutionPlan(steps=(action.public_projection(),)),
            prepared_command(run, (action,), executor=executor or SubprocessExecutor()),
        )

    def set_pinned(self, backup_id: str, pinned: bool, *, dry_run: bool = False) -> BackupPinResult:
        return self.set_pinned_command(backup_id, pinned, dry_run=dry_run).run()

    def _build_prune_plan(  # noqa: C901 -- the retention matrix is intentionally explicit
        self,
        project: ProjectConfig | str | Path,
        *,
        policy: BackupRetentionPolicy,
        now: datetime | None = None,
    ) -> BackupPrunePlan:
        project_id = _project_id(project)
        cutoff = (now or datetime.now(UTC)) - timedelta(days=policy.retention_days)
        catalog = self._client.get_catalog()
        candidates: list[BackupPruneCandidate] = []
        protected: list[BackupPruneSkip] = []
        skipped: list[BackupPruneSkip] = []
        for row in catalog.retention_rows(project_id):
            backup_id = uuid.UUID(str(row["id"]))
            recorded_size = row["size_bytes"] if isinstance(row["size_bytes"], int) else 0
            if row["state"] != BackupState.AVAILABLE.value:
                skipped.append(BackupPruneSkip(backup_id=backup_id, reason="not available"))
                continue
            timestamp = catalog._retention_timestamp(row)
            if timestamp is None:
                skipped.append(
                    BackupPruneSkip(
                        backup_id=backup_id,
                        reason="unknown catalogue timestamp",
                        size_bytes=recorded_size,
                    )
                )
                continue
            if timestamp >= cutoff:
                skipped.append(
                    BackupPruneSkip(
                        backup_id=backup_id,
                        reason="younger than retention cutoff",
                        size_bytes=recorded_size,
                    )
                )
                continue
            if catalog._retention_group(row) is None:
                skipped.append(
                    BackupPruneSkip(
                        backup_id=backup_id,
                        reason="unknown historical source group",
                        size_bytes=recorded_size,
                    )
                )
                continue
            path_value = row["path"]
            if not isinstance(path_value, str) or not path_value:
                skipped.append(
                    BackupPruneSkip(
                        backup_id=backup_id, reason="unknown backup path", size_bytes=recorded_size
                    )
                )
                continue
            path = Path(path_value)
            try:
                stat_result = path.lstat()
            except (FileNotFoundError, OSError):
                skipped.append(
                    BackupPruneSkip(
                        backup_id=backup_id,
                        reason="missing or unreadable backup file",
                        size_bytes=recorded_size,
                    )
                )
                continue
            if not _is_sdk_managed_backup_path(self._client, path):
                skipped.append(
                    BackupPruneSkip(
                        backup_id=backup_id,
                        reason="external or non-regular backup file",
                        size_bytes=recorded_size,
                    )
                )
                continue
            reason = catalog.deletion_protection_reason(str(backup_id))
            if reason is not None:
                protected.append(
                    BackupPruneSkip(
                        backup_id=backup_id, reason=reason, size_bytes=stat_result.st_size
                    )
                )
                continue
            try:
                with exclusive_lock(backup_lock_path(str(backup_id))):
                    pass
            except LockConflictError:
                protected.append(
                    BackupPruneSkip(
                        backup_id=backup_id,
                        reason="busy lifecycle lock",
                        size_bytes=stat_result.st_size,
                    )
                )
                continue
            candidates.append(
                BackupPruneCandidate(
                    backup_id=backup_id,
                    path=str(path),
                    file_identity=(stat_result.st_dev, stat_result.st_ino),
                    size_bytes=stat_result.st_size,
                )
            )
        return BackupPrunePlan(
            project_id=project_id,
            policy=policy,
            policy_fingerprint=_policy_fingerprint(policy),
            cutoff=cutoff,
            candidates=tuple(candidates),
            protected=tuple(protected),
            skipped=tuple(skipped),
        )

    def prune_command(
        self,
        project: ProjectConfig | str | Path,
        *,
        dry_run: bool = False,
        executor: ProcessExecutor | None = None,
    ) -> Command[BackupPruneResult]:
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.backup_retention import read_retention_policy
        from odoo_instance_sdk.internal.proc import (
            PreparedAction,
            SubprocessExecutor,
            prepared_command,
        )

        policy = read_retention_policy()
        plan = self._build_prune_plan(project, policy=policy)
        action_id = "backup.prune.preview" if dry_run else "backup.prune"
        action = PreparedAction(
            step_id=action_id,
            action=action_id,
            description="Preview or execute project backup retention",
            read_only=dry_run,
            mutating=not dry_run,
        )

        def run(context: RunContext[BackupPruneResult]) -> BackupPruneResult:
            context.action(action_id)
            result = self._execute_prune_plan(plan, dry_run=dry_run)
            context.complete_action(action_id)
            return result

        return Command.from_prepared(
            ExecutionPlan(steps=(action.public_projection(),)),
            prepared_command(run, (action,), executor=executor or SubprocessExecutor()),
        )

    def prune(
        self, project: ProjectConfig | str | Path, *, dry_run: bool = False
    ) -> BackupPruneResult:
        return self.prune_command(project, dry_run=dry_run).run()

    def _execute_prune_plan(  # noqa: C901 -- each candidate is revalidated before deletion
        self, plan: BackupPrunePlan, *, dry_run: bool
    ) -> BackupPruneResult:
        from odoo_instance_sdk.internal.backup_retention import read_retention_policy

        if dry_run:
            return BackupPruneResult(
                plan=plan,
                skipped=plan.protected + plan.skipped,
                dry_run=True,
            )
        deleted: list[uuid.UUID] = []
        skipped: list[BackupPruneSkip] = list(plan.protected + plan.skipped)
        failed: list[uuid.UUID] = []
        failures: list[BackupPruneSkip] = []
        removed_bytes = 0
        policy_changed = False
        catalog = self._client.get_catalog()
        for candidate in plan.candidates:
            current_policy = read_retention_policy()
            current_fingerprint = _policy_fingerprint(current_policy)
            if current_fingerprint != plan.policy_fingerprint:
                if deleted:
                    policy_changed = True
                    break
                raise StalePlanError(
                    "backup retention policy changed; replan before pruning",
                    expected=plan.policy_fingerprint,
                    actual=current_fingerprint,
                )
            try:
                with exclusive_lock(backup_lock_path(str(candidate.backup_id))):
                    current_policy = read_retention_policy()
                    current_fingerprint = _policy_fingerprint(current_policy)
                    if current_fingerprint != plan.policy_fingerprint:
                        if deleted:
                            policy_changed = True
                            break
                        raise StalePlanError(  # noqa: TRY301 -- abort before any deletion
                            "backup retention policy changed; replan before pruning",
                            expected=plan.policy_fingerprint,
                            actual=current_fingerprint,
                        )
                    row = catalog.get_by_id(str(candidate.backup_id))
                    if row is None:
                        skipped.append(
                            BackupPruneSkip(
                                backup_id=candidate.backup_id, reason="catalog row disappeared"
                            )
                        )
                        continue
                    if row["state"] != BackupState.AVAILABLE.value:
                        skipped.append(
                            BackupPruneSkip(
                                backup_id=candidate.backup_id,
                                reason="backup is no longer available",
                            )
                        )
                        continue
                    reason = catalog.deletion_protection_reason(str(candidate.backup_id))
                    if reason is not None:
                        skipped.append(
                            BackupPruneSkip(
                                backup_id=candidate.backup_id,
                                reason=reason,
                                size_bytes=candidate.size_bytes,
                            )
                        )
                        continue
                    path = Path(str(row["path"] or ""))
                    current_identity = _file_identity(path)
                    if (
                        path != Path(candidate.path)
                        or current_identity != candidate.file_identity
                        or not _is_sdk_managed_backup_path(self._client, path)
                    ):
                        skipped.append(
                            BackupPruneSkip(
                                backup_id=candidate.backup_id,
                                reason="file identity changed",
                                size_bytes=candidate.size_bytes,
                            )
                        )
                        continue
                    actual_size = path.stat().st_size
                    backup = _row_to_backup(row, require_file=False)
                    if backup is None:
                        skipped.append(
                            BackupPruneSkip(
                                backup_id=candidate.backup_id,
                                reason="backup metadata is incomplete",
                            )
                        )
                        continue
                    try:
                        content_matches = actual_size == backup.size_bytes and (
                            not backup.sha256 or _file_sha256(path) == backup.sha256
                        )
                    except OSError:
                        content_matches = False
                    if not content_matches:
                        skipped.append(
                            BackupPruneSkip(
                                backup_id=candidate.backup_id,
                                reason="file content changed",
                                size_bytes=candidate.size_bytes,
                            )
                        )
                        continue
                    self._delete_impl_locked(
                        backup,
                        catalog=catalog,
                        captured_path=path,
                        captured_identity=current_identity,
                    )
                    removed_bytes += actual_size
                    deleted.append(candidate.backup_id)
            except LockConflictError:
                skipped.append(
                    BackupPruneSkip(
                        backup_id=candidate.backup_id,
                        reason="busy lifecycle lock",
                        size_bytes=candidate.size_bytes,
                    )
                )
            except (BackupNotAvailableError, BackupNotFoundError) as exc:
                skipped.append(
                    BackupPruneSkip(
                        backup_id=candidate.backup_id,
                        reason=str(exc),
                        size_bytes=candidate.size_bytes,
                    )
                )
            except StalePlanError:
                raise
            except Exception as exc:
                failed.append(candidate.backup_id)
                failures.append(
                    BackupPruneSkip(
                        backup_id=candidate.backup_id,
                        reason=str(exc),
                        size_bytes=candidate.size_bytes,
                    )
                )
        return BackupPruneResult(
            plan=plan,
            deleted_ids=tuple(deleted),
            skipped_ids=tuple(item.backup_id for item in skipped),
            failed_ids=tuple(failed),
            removed_bytes=removed_bytes,
            skipped=tuple(skipped),
            failures=tuple(failures),
            policy_changed=policy_changed,
            warnings=(
                ("backup retention policy changed; replan before pruning",)
                if policy_changed
                else ()
            ),
        )

    def list(
        self,
        *,
        source_base_url: str | None = None,
        database_name: str | None = None,
        format: BackupFormat | None = None,
    ) -> tuple[Backup, ...]:
        catalog = self._client.get_catalog()
        if source_base_url is not None:
            source_base_url = normalize_base_url(source_base_url)
        backups = catalog.list_backups(
            source_base_url=source_base_url,
            database_name=database_name,
            format=format.value if format else None,
        )
        return tuple(backups)

    def latest(
        self,
        source_base_url: str,
        database_name: str,
        *,
        format: BackupFormat | None = None,
    ) -> Backup | None:
        catalog = self._client.get_catalog()
        return catalog.latest_backup(
            source_base_url=normalize_base_url(source_base_url),
            database_name=database_name,
            format=format.value if format else None,
        )

    def history(
        self,
        *,
        source_base_url: str | None = None,
        database_name: str | None = None,
        backup_id: str | None = None,
    ) -> tuple[BackupEvent, ...]:
        catalog = self._client.get_catalog()
        if source_base_url is not None:
            source_base_url = normalize_base_url(source_base_url)
        events = catalog.get_backup_history(
            source_base_url=source_base_url,
            database_name=database_name,
            backup_id=backup_id,
        )
        return tuple(events)

    def inspect(self, backup_id: str) -> BackupInspectResult:
        return self.inspect_command(backup_id).run()

    def inspect_command(
        self, backup_id: str, *, executor: ProcessExecutor | None = None
    ) -> Command[BackupInspectResult]:
        return self._command(
            "backup.inspect",
            "Resolve one complete backup UUID from the catalogue",
            lambda: backup_projection_to_inspect_result(
                self._client.get_catalog()._resolve_backup_projection(backup_id)
            ),
            executor=executor,
            read_only=True,
        )

    def delete(self, backup: Backup) -> BackupDeletionResult:
        return self.delete_command(backup).run()

    def delete_command(
        self, backup: Backup, *, executor: ProcessExecutor | None = None
    ) -> Command[BackupDeletionResult]:
        return self._command(
            "backup.delete",
            "Delete a backup artifact and record its deletion",
            lambda: self._delete_impl(backup),
            executor=executor,
            mutating=True,
        )

    def _delete_impl(self, backup: Backup) -> BackupDeletionResult:
        catalog = self._client.get_catalog()
        captured_path = Path(backup.path)
        captured_identity = _file_identity(captured_path)
        with exclusive_lock(backup_lock_path(str(backup.id))):
            return self._delete_impl_locked(
                backup,
                catalog=catalog,
                captured_path=captured_path,
                captured_identity=captured_identity,
            )

    def _delete_impl_locked(  # noqa: C901 -- all deletion guards share one lifecycle lock
        self,
        backup: Backup,
        *,
        catalog: object,
        captured_path: Path,
        captured_identity: tuple[int, int] | None,
    ) -> BackupDeletionResult:
        catalog = cast("BackupCatalog", catalog)
        existing = catalog.get_by_id(str(backup.id))
        if existing is None:
            raise BackupNotFoundError(f"Backup {backup.id} not found in catalog")
        existing_path = Path(existing["path"] or "")
        if existing["state"] == BackupState.DELETED.value:
            prior_deleted_at = (
                datetime.fromisoformat(existing["deleted_at"])
                if existing["deleted_at"]
                else datetime.now(UTC)
            )
            return BackupDeletionResult(
                file_existed=existing_path.is_file(),
                already_deleted=True,
                deleted_at=prior_deleted_at,
            )
        if existing["state"] == BackupState.DOWNLOADING.value:
            raise BackupNotAvailableError(
                f"Backup {backup.id} is downloading and cannot be deleted"
            )
        if existing["state"] != BackupState.AVAILABLE.value:
            raise BackupNotAvailableError(
                f"Backup {backup.id} is in state {existing['state']!r}, not available"
            )
        reason = catalog.deletion_protection_reason(str(backup.id))
        if reason is not None:
            raise BackupNotAvailableError(f"Backup {backup.id} is protected: {reason}")
        catalog.verify_identity(backup)
        if existing_path != captured_path:
            raise BackupNotAvailableError(
                f"Backup {backup.id} path changed or is outside its recorded directory"
            )
        if existing_path.is_symlink():
            raise BackupNotAvailableError(f"Backup {backup.id} path must not be a symlink")
        if not _is_sdk_managed_backup_path(self._client, existing_path):
            raise BackupNotAvailableError(
                f"Backup {backup.id} path changed or is outside its recorded directory"
            )
        current_identity = _file_identity(existing_path)
        if current_identity != captured_identity:
            raise BackupNotAvailableError(f"Backup {backup.id} file identity changed")
        file_existed = current_identity is not None
        if file_existed and not existing_path.is_file():
            raise BackupNotAvailableError(f"Backup {backup.id} path is not a regular file")
        existing_path.unlink(missing_ok=True)
        if existing_path.exists() or existing_path.is_symlink():
            raise OSError(f"Backup file still exists after deletion: {existing_path}")
        catalog.record_deletion(str(backup.id))
        return BackupDeletionResult(
            file_existed=file_existed,
            already_deleted=False,
            deleted_at=datetime.now(UTC),
        )

    def validate(
        self,
        backup: Backup,
        *,
        raise_if_unavailable: bool = False,
        timeout: float = 60.0,
    ) -> BackupValidationResult:
        return self.validate_command(
            backup,
            raise_if_unavailable=raise_if_unavailable,
            timeout=timeout,
        ).run()

    def validate_command(
        self,
        backup: Backup,
        *,
        raise_if_unavailable: bool = False,
        timeout: float = 60.0,
        executor: ProcessExecutor | None = None,
    ) -> Command[BackupValidationResult]:
        from odoo_instance_sdk.internal.proc import PreparedStep

        steps: tuple[PreparedStep, ...] = ()
        if backup.format == BackupFormat.DUMP:
            executable = shutil.which("pg_restore")
            if executable is not None:
                steps = (
                    PreparedStep(
                        step_id="backup.validate.pg-restore",
                        argv=(executable, "--list", str(Path(backup.path))),
                        environment_snapshot=tuple(sorted(sanitized_child_environment().items())),
                        timeout=timeout,
                        read_only=True,
                        text=True,
                    ),
                )
        return self._command(
            "backup.validate",
            "Validate a backup artifact",
            lambda: self._validate_impl(
                backup,
                raise_if_unavailable=raise_if_unavailable,
                timeout=timeout,
                process_step_id=(steps[0].step_id if steps else None),
            ),
            executor=executor,
            read_only=True,
            steps=steps,
        )

    def _validate_impl(
        self,
        backup: Backup,
        *,
        raise_if_unavailable: bool,
        timeout: float,
        process_step_id: str | None = None,
    ) -> BackupValidationResult:
        with exclusive_lock(backup_lock_path(str(backup.id))):
            return self._validate_impl_locked(
                backup,
                raise_if_unavailable=raise_if_unavailable,
                timeout=timeout,
                process_step_id=process_step_id,
            )

    def _validate_impl_locked(
        self,
        backup: Backup,
        *,
        raise_if_unavailable: bool,
        timeout: float,
        process_step_id: str | None = None,
    ) -> BackupValidationResult:
        catalog = self._client.get_catalog()
        catalog.verify_identity(backup, verify_content=True)

        if not Path(backup.path).exists():
            raise BackupNotAvailableError(f"Backup file not found: {backup.path}")

        if backup.format == BackupFormat.DUMP:
            return self._validate_dump(
                backup,
                timeout=timeout,
                raise_if_unavailable=raise_if_unavailable,
                process_step_id=process_step_id,
            )

        zip_result = validate_zip(Path(backup.path))
        return self._record_and_build(
            backup,
            BackupValidationStatus.VALID if zip_result.valid else BackupValidationStatus.INVALID,
            errors=zip_result.errors,
            error_code=zip_result.error_code,
            db_name=zip_result.db_name,
            db_version=zip_result.db_version,
        )

    def _command(
        self,
        step_id: str,
        description: str,
        callback: Callable[[], T],
        *,
        executor: ProcessExecutor | None,
        read_only: bool = False,
        mutating: bool = False,
        steps: Sequence[PreparedStep] = (),
    ) -> Command[T]:
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.proc import PreparedAction, SubprocessExecutor

        step = PreparedAction(
            step_id=step_id,
            action=step_id,
            description=description,
            read_only=read_only,
            mutating=mutating,
        )

        def run(context: RunContext[T]) -> T:
            context.action(step_id)
            result = callback()
            context.complete_action(step_id)
            return result

        prepared_steps: tuple[PreparedAction | PreparedStep, ...] = (step, *steps)
        from odoo_instance_sdk.internal.proc import prepared_command

        return Command.from_prepared(
            ExecutionPlan(steps=tuple(item.public_projection() for item in prepared_steps)),
            prepared_command(
                run,
                prepared_steps,
                executor=executor or SubprocessExecutor(),
            ),
        )

    def _validate_dump(
        self,
        backup: Backup,
        *,
        timeout: float,
        raise_if_unavailable: bool,
        process_step_id: str | None,
    ) -> BackupValidationResult:
        try:
            dump_result = validate_dump(
                Path(backup.path),
                timeout=timeout,
                raise_if_unavailable=raise_if_unavailable,
                step_id=process_step_id,
            )
        except BackupValidationUnavailableError as e:
            self._record_and_build(
                backup,
                BackupValidationStatus.UNAVAILABLE,
                validator="pg_restore",
                errors=(str(e),),
            )
            raise

        if dump_result.unavailable:
            return self._record_and_build(
                backup,
                BackupValidationStatus.UNAVAILABLE,
                validator=None,
                exit_code=1,
                errors=dump_result.errors,
            )

        valid = dump_result.valid
        return self._record_and_build(
            backup,
            BackupValidationStatus.VALID if valid else BackupValidationStatus.INVALID,
            validator="pg_restore",
            exit_code=0 if valid else 1,
            errors=dump_result.errors,
        )

    def _record_and_build(
        self,
        backup: Backup,
        status: BackupValidationStatus,
        *,
        validator: str | None = None,
        exit_code: int | None = None,
        errors: tuple[str, ...] = (),
        error_code: str | None = None,
        db_name: str | None = None,
        db_version: str | None = None,
    ) -> BackupValidationResult:
        catalog = self._client.get_catalog()
        with contextlib.suppress(BackupCatalogError):
            catalog.record_validation(
                str(backup.id),
                status,
                validator=validator,
                exit_code=exit_code,
                message="; ".join(errors) or None,
            )
        valid = status == BackupValidationStatus.VALID
        return BackupValidationResult(
            valid=valid,
            errors=errors,
            error_code=error_code,
            db_name=db_name,
            db_version=db_version,
        )
