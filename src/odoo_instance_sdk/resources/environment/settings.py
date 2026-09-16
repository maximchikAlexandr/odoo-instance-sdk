from __future__ import annotations

# ruff: noqa: F821
import shutil
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.exceptions import (
    ConfigError,
    DatabaseAlreadyExistsError,
    EnvironmentNotFoundError,
    EnvironmentResolutionError,
    InstanceConfigurationError,
    MasterPasswordRequiredError,
    PgAdminDatabaseNotFoundError,
    PgAdminEnvironmentNotFoundError,
    PgAdminError,
    PgAdminNotEligibleError,
    PgAdminUnavailableError,
    PostgresClusterError,
    RestoreFailedError,
    StalePlanError,
)
from odoo_instance_sdk.internal.dependency_sync import (
    build_trusted_sync_argv,
    resolve_hash_lock,
    revalidate_hash_lock,
)
from odoo_instance_sdk.internal.locks import (
    environment_lock_path,
    exclusive_lock,
    python_env_lock_path,
)
from odoo_instance_sdk.internal.odoo_config import (
    get_admin_passwd,
    infer_base_url,
    parse_odoo_config,
)
from odoo_instance_sdk.internal.pgadmin import PgAdminPhaseHandle
from odoo_instance_sdk.internal.sanitize import sanitize_last_error
from odoo_instance_sdk.internal.urls import assert_local
from odoo_instance_sdk.models import (
    BackupFormat,
    PgAdminOpenResult,
    PostgresClusterState,
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.storage.backup_catalog import CopyJournalStage

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.pgadmin import _PgAdminReconciliationCarrier
    from odoo_instance_sdk.internal.proc import (
        ProcessExecutor,
        ProcessResult,
        RunContext,
        Step,
    )
    from odoo_instance_sdk.resources.instance import OdooInstance
    from odoo_instance_sdk.resources.postgres import PostgresCluster
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

from odoo_instance_sdk.models.backup import DevelopmentEnvironment
from odoo_instance_sdk.resources.environment import helpers as _helpers

globals().update(
    {name: value for name, value in _helpers.__dict__.items() if not name.startswith("__")}
)


class _SettingsMixin:
    def _do_copy_restore(
        self,
        *,
        context: RunContext[DevelopmentEnvironment],
        cat: BackupCatalog,
        env_id: uuid.UUID,
        source_config: Path | None,
        cfg_dict: Mapping[str, str],
        source_db: str,
        target_db: str,
    ) -> uuid.UUID:

        catalog = cat
        if source_config is None:
            raise ConfigError("copy mode requires a source config")
        base_url = infer_base_url(cfg_dict)
        assert_local(base_url)
        master_pwd = get_admin_passwd(cfg_dict)
        if master_pwd is None:
            raise MasterPasswordRequiredError("copy mode requires admin_passwd in source config")

        instance = self._client.instance.from_config(source_config, master_password=master_pwd)
        db_port = instance.config.db_port or 5432
        catalog.upsert_copy_journal(
            str(env_id),
            target_database=target_db,
            db_host=instance.config.db_host,
            db_port=db_port,
            db_user=instance.config.db_user,
            backup_id=None,
            stage=CopyJournalStage.PREPARED,
        )

        try:
            existing_dbs = instance.databases.list()
        except Exception as e:
            raise InstanceConfigurationError(
                f"Source Odoo HTTP endpoint unavailable for copy mode: {e}"
            ) from e

        if target_db in {db.name for db in existing_dbs}:
            raise DatabaseAlreadyExistsError(
                f"Target database {target_db!r} already exists on {base_url}"
            )

        backup = instance.databases.backup(source_db, format=BackupFormat.ZIP, filestore=True)
        catalog.update_environment(str(env_id), {"backup_id": str(backup.id)})
        catalog.upsert_copy_journal(
            str(env_id),
            target_database=target_db,
            db_host=instance.config.db_host,
            db_port=db_port,
            db_user=instance.config.db_user,
            backup_id=str(backup.id),
            stage=CopyJournalStage.BACKED_UP,
        )

        self._consume_copy_database_probe(
            context,
            "database.restore.exists-before",
            target_db,
            expected_exists=False,
        )
        # The restore endpoint can create a database and then fail.  Persist
        # uncertainty only after the target is proven absent, so compensation
        # never treats a pre-existing database as restore-owned.
        catalog.upsert_copy_journal(
            str(env_id),
            target_database=target_db,
            db_host=instance.config.db_host,
            db_port=db_port,
            db_user=instance.config.db_user,
            backup_id=str(backup.id),
            stage=CopyJournalStage.RESTORE_PENDING,
        )
        instance.databases._restore_after_verified_absence(
            backup,
            target_db,
            copy=True,
            neutralize_database=True,
        )
        self._consume_copy_database_probe(
            context,
            "database.restore.exists-after",
            target_db,
            expected_exists=True,
        )
        catalog.upsert_copy_journal(
            str(env_id),
            target_database=target_db,
            db_host=instance.config.db_host,
            db_port=db_port,
            db_user=instance.config.db_user,
            backup_id=str(backup.id),
            stage=CopyJournalStage.RESTORED,
        )

        return backup.id

    @staticmethod
    def _consume_copy_database_probe(
        context: RunContext[DevelopmentEnvironment],
        step_id: str,
        target_db: str,
        *,
        expected_exists: bool,
    ) -> None:
        result = cast("ProcessResult", context.process(step_id))
        if result.returncode != 0:
            detail = _process_stderr(result).strip()
            suffix = f": {detail}" if detail else ""
            raise InstanceConfigurationError(
                f"PostgreSQL database existence probe failed for {target_db!r}"
                f" (returncode={result.returncode}){suffix}"
            )
        stdout = result.stdout
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        exists = bool(str(stdout or "").strip())
        if exists == expected_exists:
            return
        if expected_exists:
            raise RestoreFailedError(f"Database {target_db!r} was not created after restore")
        raise DatabaseAlreadyExistsError(f"Target database {target_db!r} already exists")

    def _preflight_copy_checkout(self, plan: _CheckoutPlan) -> None:
        """Perform every COPY rejection check before creating owned artifacts."""
        if plan.source_config is None:
            raise ConfigError("copy mode requires a source config")
        if plan.source_database is None or plan.target_database is None:
            raise ConfigError("copy mode requires source and target databases")
        base_url = infer_base_url(plan.config_values)
        assert_local(base_url)
        master_pwd = get_admin_passwd(plan.config_values)
        if master_pwd is None:
            raise MasterPasswordRequiredError("copy mode requires admin_passwd in source config")
        instance = self._client.instance.from_config(plan.source_config, master_password=master_pwd)
        try:
            existing = instance.databases.names()
        except Exception as exc:
            raise InstanceConfigurationError(
                f"Source Odoo HTTP endpoint unavailable for copy mode: {exc}"
            ) from exc
        if plan.target_database in set(existing):
            raise DatabaseAlreadyExistsError(
                f"Target database {plan.target_database!r} already exists on {base_url}"
            )

    def _cleanup_on_failure(
        self,
        *,
        cat: BackupCatalog,
        env_id: uuid.UUID,
        repo_root: Path,
        created_paths: list[Path],
        env_root: Path,
        backup_id: uuid.UUID | None,
        error: BaseException,
        context: RunContext[DevelopmentEnvironment],
    ) -> None:

        catalog = cat
        if backup_id is None:
            row = catalog.get_environment(str(env_id))
            if row is not None and row["backup_id"] is not None:
                backup_id = uuid.UUID(str(row["backup_id"]))
        cleanup_failed = self._rollback_copy_checkout(catalog, env_id, backup_id)
        # A restored copy must remain diagnosable when compensation cannot prove
        # that the target database is gone.  In particular, do not delete the
        # generated config (the cluster identity) or its owned backup first.
        if not cleanup_failed:
            cleanup_failed = self._cleanup_created_paths(repo_root, created_paths, context=context)

        msg = sanitize_last_error(str(error)) or type(error).__name__
        if cleanup_failed:
            catalog.update_environment_state(
                str(env_id), EnvironmentState.CLEANUP_FAILED, last_error=msg
            )
        else:
            catalog.update_environment_state(str(env_id), EnvironmentState.FAILED, last_error=msg)
        catalog.add_environment_event(str(env_id), "checkout", "failed", message=msg)

    def _rollback_copy_checkout(  # noqa: C901
        self, catalog: BackupCatalog, env_id: uuid.UUID, backup_id: uuid.UUID | None
    ) -> bool:
        """Compensate a failed COPY checkout without losing the deletion capability."""
        journal = catalog.get_copy_journal(str(env_id))
        if journal is None:
            return self._cleanup_backup(catalog, backup_id) if backup_id is not None else False

        stage = CopyJournalStage(str(journal["stage"]))
        if stage is CopyJournalStage.RESTORE_PENDING:
            return True

        if stage is CopyJournalStage.RESTORED:
            row = catalog.get_environment(str(env_id))
            if row is None:
                return True
            config_path = Path(str(row["generated_config_path"]))
            if not config_path.is_file():
                return True
            try:
                cfg = parse_odoo_config(config_path)
                master_pwd = get_admin_passwd(cfg)
                if master_pwd is None:
                    return True
                instance = self._client.instance.from_config(
                    config_path, master_password=master_pwd
                )
                target = str(journal["target_database"])
                exists = instance.databases.exists(target)
                if exists:
                    instance.databases.drop(target)
                    if instance.databases.exists(target):
                        return True
            except Exception:
                return True
            catalog.upsert_copy_journal(
                str(env_id),
                target_database=str(journal["target_database"]),
                db_host=str(journal["db_host"]),
                db_port=int(journal["db_port"]),
                db_user=str(journal["db_user"]) if journal["db_user"] is not None else None,
                backup_id=str(journal["backup_id"]) if journal["backup_id"] is not None else None,
                stage=CopyJournalStage.DROPPED,
            )
            stage = CopyJournalStage.DROPPED

        if stage in (
            CopyJournalStage.PREPARED,
            CopyJournalStage.BACKED_UP,
            CopyJournalStage.DROPPED,
        ):
            journal_backup_id = journal["backup_id"]
            resolved_backup_id = (
                uuid.UUID(str(journal_backup_id)) if journal_backup_id is not None else backup_id
            )
            if resolved_backup_id is not None and self._cleanup_backup(catalog, resolved_backup_id):
                return True
            if stage is CopyJournalStage.DROPPED:
                catalog.upsert_copy_journal(
                    str(env_id),
                    target_database=str(journal["target_database"]),
                    db_host=str(journal["db_host"]),
                    db_port=int(journal["db_port"]),
                    db_user=str(journal["db_user"]) if journal["db_user"] is not None else None,
                    backup_id=str(journal_backup_id) if journal_backup_id is not None else None,
                    stage=CopyJournalStage.BACKUP_DELETED,
                )
        return False

    def _cleanup_created_paths(
        self,
        repo_root: Path,
        created_paths: list[Path],
        *,
        context: RunContext[DevelopmentEnvironment] | None = None,
    ) -> bool:
        cleanup_failed = False
        for p in created_paths:
            if p.name == "worktree":
                from odoo_instance_sdk.internal.git_worktree import worktree_remove

                if not p.exists():
                    if context is not None and context.planned("checkout.cleanup.worktree"):
                        context.skip("checkout.cleanup.worktree")
                    continue
                try:
                    if context is None:
                        worktree_remove(repo_root, p)
                    else:
                        result = cast("ProcessResult", context.process("checkout.cleanup.worktree"))
                        if result.returncode != 0:
                            cleanup_failed = True
                except Exception:
                    cleanup_failed = True
            else:
                try:
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=False)
                    else:
                        p.unlink(missing_ok=True)
                except OSError:
                    cleanup_failed = True
        return cleanup_failed

    def _cleanup_backup(self, catalog: BackupCatalog, backup_id: uuid.UUID) -> bool:

        cat = catalog
        try:
            row = cat.get_by_id(str(backup_id))
            if row is not None:
                backup = _row_to_backup(row)
                if backup is not None:
                    self._client.backups.delete(backup)
        except Exception:
            return True
        return False

    def sync_python(
        self,
        selector: EnvironmentSelector,
        *,
        upgrade: bool = False,
        hash_lock: str | Path | None = None,
        hash_lock_sha256: str | None = None,
    ) -> DevelopmentEnvironment:
        return self.sync_python_command(
            selector,
            upgrade=upgrade,
            hash_lock=hash_lock,
            hash_lock_sha256=hash_lock_sha256,
        ).run()

    def sync_python_command(  # noqa: C901
        self,
        selector: EnvironmentSelector,
        *,
        upgrade: bool = False,
        hash_lock: str | Path | None = None,
        hash_lock_sha256: str | None = None,
    ) -> Command[DevelopmentEnvironment]:
        """Capture one immutable uv synchronization operation."""
        from odoo_instance_sdk.execution import Command
        from odoo_instance_sdk.internal.proc import (
            PreparedAction,
            PreparedStep,
            SubprocessExecutor,
            prepared_command,
        )

        env = (
            self._resolve_selector(selector, include_removed=False)
            if isinstance(selector, str)
            else selector
        )
        project = _load_project(env)
        worktree = Path(env.worktree_path)
        repo_root = Path(env.repository_root)
        trusted_lock = resolve_hash_lock(hash_lock, hash_lock_sha256, base_dir=repo_root)
        if trusted_lock is not None:
            if not env.python_environment_owned:
                raise ConfigError("hash-locked dependency sync requires an owned environment")
            if upgrade:
                raise ConfigError("upgrade cannot be combined with a hash-locked sync")
            inputs: list[str] = []
        else:
            inputs = _rebase_requirement_paths(list(project.requirements), repo_root, worktree)
            odoo_req = _find_odoo_requirements(worktree)
            if odoo_req is not None and str(odoo_req) not in inputs:
                inputs.append(str(odoo_req))
        steps: list[Step] = []
        if trusted_lock is not None:
            install_argv = build_trusted_sync_argv(
                _owned_python_executable(Path(env.python_environment_path)), trusted_lock
            )
            steps.append(
                PreparedStep(
                    step_id="environment.sync.install",
                    argv=install_argv,
                    cwd=str(worktree),
                    mutating=True,
                )
            )
        elif inputs:
            compile_argv = ["uv", "pip", "compile", *inputs]
            if upgrade:
                compile_argv.append("--upgrade")
            compile_argv.extend(("-o", env.dependency_lock_path))
            steps.append(
                PreparedStep(
                    step_id="environment.sync.compile",
                    argv=tuple(compile_argv),
                    cwd=str(worktree),
                    mutating=True,
                )
            )
            if env.python_environment_owned:
                install_argv = (
                    "uv",
                    "pip",
                    "sync",
                    "--python",
                    str(Path(env.python_environment_path) / "bin" / "python"),
                    env.dependency_lock_path,
                )
            else:
                install_argv = (
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    env.python_environment_path,
                    "-r",
                    env.dependency_lock_path,
                )
            steps.append(
                PreparedStep(
                    step_id="environment.sync.install",
                    argv=install_argv,
                    cwd=str(worktree),
                    mutating=True,
                )
            )
        steps.append(
            PreparedAction(
                step_id="environment.sync",
                action="sync_python",
                description="Record Python dependency synchronization",
                mutating=trusted_lock is not None or bool(inputs),
            )
        )
        prepared_steps = tuple(steps)

        def execute(context: RunContext[DevelopmentEnvironment]) -> DevelopmentEnvironment:
            context.action("environment.sync")
            catalog = self._client.get_catalog()
            completed = False
            catalog.add_environment_event(str(env.id), "sync", "started")
            try:
                with (
                    exclusive_lock(environment_lock_path(str(env.id))),
                    exclusive_lock(python_env_lock_path(env.python_environment_path)),
                ):
                    if trusted_lock is not None:
                        revalidate_hash_lock(trusted_lock, hash_lock_sha256)
                        install_result = cast(
                            "ProcessResult", context.process("environment.sync.install")
                        )
                        if install_result.returncode != 0:
                            raise ConfigError(
                                f"uv pip sync failed: {_process_stderr(install_result)}".strip()
                            )
                    elif inputs:
                        compile_result = cast(
                            "ProcessResult", context.process("environment.sync.compile")
                        )
                        if compile_result.returncode != 0:
                            context.skip("environment.sync.install")
                            if not Path(env.dependency_lock_path).is_file():
                                raise ConfigError(
                                    "uv pip compile failed and no prior lock: "
                                    f"{_process_stderr(compile_result)}"
                                )
                            catalog.add_environment_event(
                                str(env.id),
                                "sync",
                                "failed",
                                message="uv pip compile failed; kept existing lock",
                            )
                            completed = True
                            return self._get_env_row(catalog, env.id)
                        install_result = cast(
                            "ProcessResult", context.process("environment.sync.install")
                        )
                        if install_result.returncode != 0:
                            raise ConfigError(
                                f"uv pip install failed: {_process_stderr(install_result)}".strip()
                            )
                    catalog._record_environment_sync_success(
                        str(env.id),
                        _sync_applied_settings(
                            catalog,
                            env,
                            project,
                            [str(trusted_lock)] if trusted_lock is not None else inputs,
                        ),
                    )
                    completed = True
                    return self._get_env_row(catalog, env.id)
            finally:
                if completed:
                    # Successful and intentionally retained-lock outcomes are
                    # complete at this point; callback exceptions are closed
                    # by RunContext.fail_actions.
                    context.complete_action("environment.sync")

        from odoo_instance_sdk.execution import ExecutionPlan

        return Command.from_prepared(
            ExecutionPlan(
                steps=tuple(step.public_projection() for step in prepared_steps)
            ).with_fingerprint(),
            prepared_command(
                execute,
                prepared_steps,
                executor=SubprocessExecutor(),
            ),
        )

    def record_use(self, env: DevelopmentEnvironment) -> None:
        catalog = self._client.get_catalog()
        now = datetime.now(UTC).isoformat()
        catalog.record_environment_use(str(env.id), now)

    def get(self, selector: EnvironmentSelector) -> DevelopmentEnvironment:
        if isinstance(selector, DevelopmentEnvironment):
            return selector
        return self._resolve_selector(selector, include_removed=True)

    def open_pgadmin(self, selector: EnvironmentSelector) -> PgAdminOpenResult:
        return self.open_pgadmin_command(selector).run()

    def open_pgadmin_command(
        self,
        selector: EnvironmentSelector,
        *,
        executor: ProcessExecutor | None = None,
    ) -> Command[PgAdminOpenResult]:
        captured_selector: EnvironmentSelector = selector
        if isinstance(selector, str):
            try:
                captured_selector = self.get(selector)
            except Exception:
                # Preserve the existing typed selector error in the callback.
                captured_selector = selector
        planned_cluster = _pgadmin_cluster_snapshot(captured_selector)
        captured_inputs = self._capture_pgadmin_command_inputs(captured_selector, planned_cluster)
        steps = _pgadmin_command_steps(
            captured_selector,
            cluster=planned_cluster,
            inputs=captured_inputs,
            include_reconciliation=True,
        )

        def open_pgadmin() -> PgAdminOpenResult:
            from odoo_instance_sdk.internal import pgadmin_files

            try:
                paths = (
                    captured_inputs.paths
                    if captured_inputs is not None
                    else pgadmin_files.PgAdminPaths.from_defaults()
                )
                # The full finite operation owns one lock-atomic lifecycle:
                # no other first-run command can observe a key write before
                # this command has reconciled its container.
                with pgadmin_files.pgadmin_lock(
                    path=paths.lock,
                    timeout=_PGADMIN_LIFECYCLE_TIMEOUT,
                ):
                    carrier = self._open_pgadmin_impl(
                        captured_selector,
                        cluster=planned_cluster,
                        captured_inputs=captured_inputs,
                        lock_held=True,
                    )
                    from odoo_instance_sdk.internal.proc import active_context

                    context = cast("RunContext[PgAdminOpenResult] | None", active_context())
                    if context is None:
                        raise PgAdminUnavailableError()
                    return carrier.reconcile(context, lock_held=True)
            finally:
                _skip_planned_pgadmin_database_probe()

        return self._action_command(
            "environment.open-pgadmin",
            "Open the environment's owned pgAdmin container",
            open_pgadmin,
            executor=executor,
            mutating=True,
            steps=steps,
        )

    def open_pgadmin_phase(self, selector: EnvironmentSelector) -> PgAdminPhaseHandle:
        """Run only provisioning and return the explicit reconciliation phase."""
        return self.open_pgadmin_phase_command(selector).run()

    def open_pgadmin_phase_command(
        self,
        selector: EnvironmentSelector,
        *,
        executor: ProcessExecutor | None = None,
    ) -> Command[PgAdminPhaseHandle]:
        """Capture the finite provisioning phase without hiding reconciliation."""
        captured_selector: EnvironmentSelector = selector
        if isinstance(selector, str):
            try:
                captured_selector = self.get(selector)
            except Exception:
                captured_selector = selector
        planned_cluster = _pgadmin_cluster_snapshot(captured_selector)
        captured_inputs = self._capture_pgadmin_command_inputs(captured_selector, planned_cluster)
        steps = _pgadmin_command_steps(
            captured_selector,
            cluster=planned_cluster,
            inputs=captured_inputs,
        )

        def provision() -> PgAdminPhaseHandle:
            try:
                carrier = self._open_pgadmin_impl(
                    captured_selector,
                    cluster=planned_cluster,
                    captured_inputs=captured_inputs,
                )
                return PgAdminPhaseHandle(reconciliation=carrier.reconciliation_command())
            finally:
                _skip_planned_pgadmin_database_probe()

        return self._action_command(
            "environment.open-pgadmin-phase",
            "Provision pgAdmin and return its explicit reconciliation command",
            provision,
            executor=executor,
            mutating=True,
            steps=steps,
        )

    def _capture_pgadmin_command_inputs(
        self,
        selector: EnvironmentSelector,
        cluster: PostgresCluster | None,
    ) -> _PgAdminCommandInputs | None:
        """Capture every immutable input needed by the pgAdmin command.

        Docker identity is derived from the managed Compose project name, not
        from a runtime container id.  This keeps the later inspect/run argv
        stable while the identity probe still validates the live object.
        """
        from odoo_instance_sdk.resources.postgres import PostgresCluster as _PostgresCluster

        if (
            not isinstance(cluster, _PostgresCluster)
            or cluster.mode != "compose"
            or not cluster.owned
        ):
            return None
        try:
            env = self.get(selector)
            self._require_pgadmin_environment(env)
            database = self._pgadmin_database(env)
            instance = self._configured_pgadmin_instance(env)
            password = instance.config.db_password or ""
            if not isinstance(password, str):
                return None
            from odoo_instance_sdk.internal.pgadmin_files import (
                PgAdminPaths,
                PostgresIdentity,
                execution_fingerprint_inputs,
                select_port,
            )

            paths = PgAdminPaths.from_defaults()
            identity = PostgresIdentity(
                container_name=f"{cluster.compose_project_name}-postgres-1",
                network=f"{cluster.compose_project_name}_default",
                user=cluster._user or "odoo",
                host=f"{cluster.compose_project_name}-postgres-1",
                # This identity is used from pgAdmin's Docker network.  The
                # Compose endpoint is the host-published port and is not
                # reachable as the container-side PostgreSQL service port.
                port=5432,
            )
            database_probe = instance.databases._psql_probe_for(
                database, "pgadmin.database.exists.psql"
            )
            fingerprint_inputs = execution_fingerprint_inputs(paths, identity, database, password)
            return _PgAdminCommandInputs(
                identity=identity,
                paths=paths,
                port=select_port(paths),
                password=password,
                database=database,
                database_probe=database_probe,
                fingerprint_inputs=fingerprint_inputs,
            )
        except Exception:
            return None

    def _open_pgadmin_impl(
        self,
        selector: EnvironmentSelector,
        *,
        cluster: PostgresCluster | None = None,
        captured_inputs: _PgAdminCommandInputs | None = None,
        lock_held: bool = False,
    ) -> _PgAdminReconciliationCarrier:
        """Run preflight and the locked phase, returning its private carrier.

        Resolution and all security-critical preconditions live here so callers
        cannot supply browser-controlled database or endpoint values. The
        caller chooses whether the phase owns its lock or participates in the
        full operation's already-held lifecycle lock.
        """
        try:
            env = self.get(selector)
        except EnvironmentNotFoundError:
            raise PgAdminEnvironmentNotFoundError() from None
        except EnvironmentResolutionError:
            raise PgAdminUnavailableError() from None
        self._require_pgadmin_environment(env)
        database = self._pgadmin_database(env)
        cluster = (
            self._healthy_owned_compose(env)
            if cluster is None
            else self._validate_healthy_owned_compose(cluster)
        )
        from odoo_instance_sdk.internal.proc import active_context

        context = active_context()
        if (
            context is not None
            and context.planned("pgadmin.postgres.status.ps")
            and not context.consumed("pgadmin.postgres.status.ps")
        ):
            # A captured domain double may provide its own health decision;
            # it still has to account for the exact finite status phase.
            _pgadmin_captured_cluster_state(context)
        instance = self._configured_pgadmin_instance(env)
        self._require_pgadmin_database(instance, database)

        if captured_inputs is not None and (
            captured_inputs.database != database
            or captured_inputs.password != (instance.config.db_password or "")
        ):
            raise StalePlanError("captured pgAdmin inputs changed before execution")

        try:
            from odoo_instance_sdk.internal.pgadmin import PgAdminProvisioningPhase
            from odoo_instance_sdk.internal.proc import active_context

            context = active_context()
            return PgAdminProvisioningPhase(
                instance=instance,
                cluster=cluster,
                database=database,
                captured_identity=(captured_inputs.identity if captured_inputs else None),
                captured_paths=(captured_inputs.paths if captured_inputs else None),
                captured_port=(captured_inputs.port if captured_inputs else None),
                captured_fingerprint=(
                    captured_inputs.fingerprint_inputs if captured_inputs else None
                ),
                executor=context.executor if context is not None else None,
            )._provision_carrier(lock_held=lock_held)
        except (PgAdminError, StalePlanError):
            raise
        except Exception:
            raise PgAdminUnavailableError() from None

    def _require_pgadmin_environment(self, env: DevelopmentEnvironment) -> None:
        if env.state is not EnvironmentState.READY:
            raise PgAdminNotEligibleError()

    def _pgadmin_database(self, env: DevelopmentEnvironment) -> str:
        database = (
            env.target_db_name
            if env.db_mode is EnvironmentDatabaseMode.COPY
            else env.source_db_name
        )
        if database is None:
            raise PgAdminNotEligibleError()
        return database

    def _healthy_owned_compose(self, env: DevelopmentEnvironment) -> PostgresCluster:
        from odoo_instance_sdk.resources.postgres import PostgresCluster

        try:
            cluster = PostgresCluster.from_project(Path(env.repository_root))
        except (ConfigError, PostgresClusterError):
            raise PgAdminNotEligibleError() from None
        except Exception:
            raise PgAdminUnavailableError() from None
        return self._validate_healthy_owned_compose(cluster)

    def _validate_healthy_owned_compose(self, cluster: PostgresCluster) -> PostgresCluster:
        if cluster.mode != "compose" or not cluster.owned:
            raise PgAdminNotEligibleError()
        try:
            from odoo_instance_sdk.internal.proc import active_context

            context = active_context()
            if context is not None and context.planned("pgadmin.postgres.status.ps"):
                state = _pgadmin_captured_cluster_state(context)
            elif context is not None and callable(getattr(cluster, "_status_compose", None)):
                state = cluster._status_compose(
                    ps_step_id="pgadmin.postgres.status.ps",
                    health_step_id="pgadmin.postgres.status.health",
                )
            else:
                state = cluster.status()
        except Exception:
            raise PgAdminUnavailableError() from None
        if state is PostgresClusterState.HEALTHY:
            return cluster
        if state in {PostgresClusterState.UNKNOWN, PostgresClusterState.UNREACHABLE}:
            raise PgAdminUnavailableError()
        raise PgAdminNotEligibleError()

    def _configured_pgadmin_instance(self, env: DevelopmentEnvironment) -> OdooInstance:
        try:
            return self._client.instance.from_environment(env)
        except Exception:
            raise PgAdminUnavailableError() from None

    def _require_pgadmin_database(self, instance: OdooInstance, database: str) -> None:
        try:
            exists = instance.databases.exists(database)
        except Exception:
            raise PgAdminUnavailableError() from None
        if not exists:
            raise PgAdminDatabaseNotFoundError()

    def list(
        self,
        *,
        project: ProjectConfig | Path | None = None,
        include_removed: bool = False,
    ) -> list[DevelopmentEnvironment]:
        return self.list_command(
            project=project,
            include_removed=include_removed,
        ).run()
