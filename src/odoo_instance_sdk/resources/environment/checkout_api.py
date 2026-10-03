from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import ConfigError, EnvironmentConflictError
from odoo_instance_sdk.internal.repo_key import git_common_dir, repo_key
from odoo_instance_sdk.models import (
    Backup,
    BackupFreshness,
    BackupProvenanceComparison,
    DatabaseRefreshOptions,
    EnvironmentCheckoutPlan,
    EnvironmentCheckoutResult,
    EnvironmentDatabaseMode,
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment.checkout_artifacts import (
    _checkout_execution_plan_with_private_steps,
    _row_to_env,
)
from odoo_instance_sdk.resources.environment.checkout_planning import (
    EnvironmentCheckoutOptions,
    _checkout_public_plan,
    _CheckoutPlan,
    _CheckoutSnapshot,
    _execution_plan,
    _public_checkout_plan,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import RunContext
    from odoo_instance_sdk.models.backup import DevelopmentEnvironment
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


class _CheckoutApiMixin:
    if TYPE_CHECKING:
        _client: OdooClient

        def _build_checkout_snapshot(
            self,
            project: ProjectConfig | Path,
            branch: str,
            *,
            options: EnvironmentCheckoutOptions,
        ) -> _CheckoutSnapshot: ...

        def _command_from_snapshot(
            self, snapshot: _CheckoutSnapshot
        ) -> Command[DevelopmentEnvironment]: ...

        def _prepare_checkout(
            self,
            project: ProjectConfig | Path,
            branch: str,
            *,
            options: EnvironmentCheckoutOptions,
            dry_run_paths: bool,
            checkout_path: Path | None = None,
        ) -> _CheckoutPlan: ...

        def _audit_checkout_plan(
            self,
            project: ProjectConfig | Path,
            branch: str,
            options: EnvironmentCheckoutOptions,
        ) -> tuple[BackupProvenanceComparison, BackupFreshness, tuple[str, ...]]: ...

    def _copy_source_metadata(
        self, project: ProjectConfig, options: EnvironmentCheckoutOptions
    ) -> tuple[str | None, str | None, str | None, str, Backup | None]:
        """Resolve one explicit COPY source without contacting a remote."""
        if options.remote_name is not None and options.backup_id is not None:
            raise ConfigError("COPY accepts exactly one of remote_name or backup_id")
        if options.remote_name is not None and options.source_database is not None:
            raise ConfigError("--remote cannot be combined with --source-db")
        if options.backup_id is not None and options.source_database is not None:
            raise ConfigError("--backup cannot be combined with --source-db")

        if options.remote_name is not None:
            from odoo_instance_sdk.internal.dbprep.source import resolve_test_source

            source = resolve_test_source(
                project, DatabaseRefreshOptions(remote_name=options.remote_name)
            )
            assert source.config.database is not None
            return (
                source.source_name,
                source.config.base_url,
                source.branch,
                source.config.database,
                None,
            )

        if options.backup_id is not None:
            try:
                backup_id = uuid.UUID(str(options.backup_id))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ConfigError("catalogue backup identifier must be a complete UUID") from exc
            projection = self._client.get_catalog()._resolve_backup_projection(str(backup_id))
            if projection.state.value != "available":
                raise ConfigError("catalogue backup is not available")
            backup = projection.backup
            return (
                backup.source_name,
                backup.source_base_url,
                backup.source_git_branch,
                backup.database_name,
                backup,
            )

        return None, None, None, "", None

    def plan_checkout(
        self,
        project: ProjectConfig | Path,
        branch: str,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
    ) -> EnvironmentCheckoutPlan:
        """Return a secret-free checkout plan without performing mutations."""
        command = self.checkout_command(project, branch, options=options)
        return _checkout_public_plan(command)

    def checkout_command(
        self,
        project: ProjectConfig | Path,
        branch: str,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
    ) -> Command[DevelopmentEnvironment]:
        """Capture checkout inputs once and return the inspectable command."""
        snapshot = self._build_checkout_snapshot(project, branch, options=options)
        return self._command_from_snapshot(snapshot)

    @staticmethod
    def _adoption_fingerprint(
        project_id: str,
        checkout_path: Path,
        checkout_common: Path,
        options: EnvironmentCheckoutOptions,
        *,
        source_name: str | None,
        source_base_url: str | None,
        source_git_branch: str | None,
        source_database: str,
        selected_backup_id: uuid.UUID | None,
    ) -> str:
        """Hash requested, secret-free evidence that defines one adoption identity."""
        payload = {
            "project_id": project_id,
            "checkout_path": str(checkout_path),
            "checkout_git_common_dir": str(checkout_common),
            "base_ref": options.base_ref,
            "source_name": source_name,
            "source_base_url_digest": (
                hashlib.sha256(source_base_url.encode()).hexdigest()
                if source_base_url is not None
                else None
            ),
            "source_git_branch": source_git_branch,
            "resolved_source_database": source_database,
            "selected_backup_id": (
                str(selected_backup_id) if selected_backup_id is not None else None
            ),
            "db_mode": options.db_mode.value,
            "source_database": options.source_database,
            "target_database": options.target_database,
            "remote_name": options.remote_name,
            "backup_id": str(options.backup_id) if options.backup_id is not None else None,
            "python": str(options.python) if options.python is not None else None,
            "create_venv": options.create_venv,
            "odoo_bin": str(options.odoo_bin) if options.odoo_bin is not None else None,
            "config_path": str(options.config_path) if options.config_path is not None else None,
            "name": options.name,
            "http_port": options.http_port,
            "hash_lock": str(options.hash_lock) if options.hash_lock is not None else None,
            "hash_lock_sha256": options.hash_lock_sha256,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def adopt_command(
        self,
        project: ProjectConfig | Path,
        checkout_path: str | Path,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
    ) -> Command[DevelopmentEnvironment]:
        """Capture COPY preparation for an existing caller-owned checkout."""
        if options.db_mode is not EnvironmentDatabaseMode.COPY:
            raise ConfigError("adoption requires COPY mode")
        if options.base_ref is None:
            raise ConfigError("adoption requires an explicit base_ref")
        if (options.remote_name is None) == (options.backup_id is None):
            raise ConfigError("adoption requires exactly one of remote_name or backup_id")
        from odoo_instance_sdk.internal.git_worktree import (
            remote_url,
            rev_parse_branch,
            rev_parse_git_common_dir,
            rev_parse_toplevel,
        )

        external_path = Path(checkout_path).expanduser().resolve()
        if not external_path.is_dir():
            raise ConfigError(f"adoption checkout is not a directory: {external_path}")
        actual_root = rev_parse_toplevel(external_path)
        if actual_root != external_path:
            raise EnvironmentConflictError(
                "checkout_path_mismatch",
                "adoption requires the canonical Git working directory, not a child path",
            )
        branch = rev_parse_branch(actual_root)
        common = rev_parse_git_common_dir(actual_root)
        project_cfg = project if isinstance(project, ProjectConfig) else ProjectConfig.load(project)
        project_root = project_cfg.repository_root.resolve()
        configured_remote = remote_url(project_root)
        checkout_remote = remote_url(actual_root)
        if configured_remote is not None and checkout_remote is not None:
            if configured_remote != checkout_remote:
                raise EnvironmentConflictError(
                    "repository_identity_mismatch",
                    "caller-owned checkout remote does not match the selected project",
                )
        elif common != git_common_dir(project_root):
            raise EnvironmentConflictError(
                "repository_identity_unverified",
                "independent clone has no comparable configured origin remote",
            )
        project_id = f"project_{repo_key(project_root, git_common_dir(project_root))}"
        private = self._prepare_checkout(
            project_cfg,
            branch,
            options=options,
            dry_run_paths=True,
            checkout_path=actual_root,
        )
        fingerprint = self._adoption_fingerprint(
            project_id,
            actual_root,
            common,
            options,
            source_name=private.source_name,
            source_base_url=private.source_base_url,
            source_git_branch=private.source_git_branch,
            source_database=private.source_database or "",
            selected_backup_id=(
                private.selected_backup.id if private.selected_backup is not None else None
            ),
        )
        private = replace(
            private,
            project_id=project_id,
            adoption_input_fingerprint=fingerprint,
        )
        provenance, freshness, warnings = self._audit_checkout_plan(project_cfg, branch, options)
        public = _public_checkout_plan(private, provenance, freshness, warnings)
        execution = _execution_plan(private, provenance, freshness, warnings)
        snapshot = _CheckoutSnapshot(private=private, public=public, execution_plan=execution)
        return self._command_from_snapshot(snapshot)

    def adopt(
        self,
        project: ProjectConfig | Path,
        checkout_path: str | Path,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
    ) -> DevelopmentEnvironment:
        return self.adopt_command(project, checkout_path, options=options).run()

    def _checkout_command_with_branch_revalidation(
        self,
        project: ProjectConfig | Path,
        branch: str,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
        branch_revalidator: Callable[[RunContext[DevelopmentEnvironment]], None],
    ) -> Command[DevelopmentEnvironment]:
        snapshot = self._build_checkout_snapshot(project, branch, options=options)
        private = replace(snapshot.private, branch_revalidator=branch_revalidator)
        execution_plan = _checkout_execution_plan_with_private_steps(snapshot.execution_plan, private)  # fmt: skip
        return self._command_from_snapshot(replace(snapshot, private=private, execution_plan=execution_plan))  # fmt: skip

    def checkout_with_plan(
        self,
        project: ProjectConfig | Path,
        branch: str,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
    ) -> EnvironmentCheckoutResult:
        """Execute checkout and return its final secret-free typed plan."""
        command = self.checkout_command(project, branch, options=options)
        environment = command.run()
        return EnvironmentCheckoutResult(
            environment=environment,
            plan=_checkout_public_plan(command),
        )

    def checkout(
        self,
        project: ProjectConfig | Path,
        branch: str,
        *,
        options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
    ) -> DevelopmentEnvironment:
        return self.checkout_command(project, branch, options=options).run()

    def _get_env_row(self, cat: BackupCatalog, env_id: uuid.UUID) -> DevelopmentEnvironment:
        row = cat.get_environment(str(env_id))
        if row is None:
            raise RuntimeError("environment row disappeared after checkout")
        return _row_to_env(row)
