from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.exceptions import (
    BackupCatalogError,
    MonitorError,
    PostgresClusterError,
    ProjectManifestNotFoundError,
)
from odoo_instance_sdk.internal import paths as _paths
from odoo_instance_sdk.internal.db_name import validate_filestore_containment
from odoo_instance_sdk.internal.git_activity import (
    _validated_base_ref,
)
from odoo_instance_sdk.internal.postgres_compose import (
    SubprocessComposeRunner,
)
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.models import (
    SNAPSHOT_SECTIONS,
    CheckoutInventory,
    ProcessInventory,
    Snapshot,
    SnapshotRequest,
)
from odoo_instance_sdk.resources.monitor.planning import (
    _PROBE_TIMEOUT_SECONDS,
    _SCHEMA_VERSION,
)
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog, MonitorCatalogSnapshot

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.postgres_compose import ComposeRunner
    from odoo_instance_sdk.internal.proc import (
        PreparedStep,
        ProcessExecutor,
        ProcessResult,
        RunContext,
    )
    from odoo_instance_sdk.internal.process_inventory import DatabaseCredentials
    from odoo_instance_sdk.models import (
        ClusterResourceSnapshot,
        EnvironmentSnapshot,
        PostgresClusterState,
        ProjectSummary,
        SnapshotObservation,
        SnapshotSection,
    )
    from odoo_instance_sdk.resources.monitor.planning import (
        _DockerProvider,
        _GitProvider,
        _ProjectPlan,
        _SnapshotPlan,
    )


class _CollectMixin:
    if TYPE_CHECKING:
        catalog_path: Path | None
        _executor: ProcessExecutor
        git_provider: _GitProvider | None
        docker_provider: _DockerProvider | None
        _docker_runner: ComposeRunner

        def _collect_cluster_resources(
            self,
            plans: list[_ProjectPlan],
            *,
            probe_results: dict[str, ProcessResult] | None = None,
        ) -> tuple[dict[str, ClusterResourceSnapshot], set[str]]: ...

        def _collect_snapshot_rows(
            self,
            plans: tuple[_ProjectPlan, ...],
            resources: dict[str, ClusterResourceSnapshot],
            *,
            probe_results: dict[str, ProcessResult] | None = None,
            sections: frozenset[SnapshotSection] | None = None,
        ) -> tuple[
            tuple[ProjectSummary, ...],
            tuple[EnvironmentSnapshot, ...],
            dict[SnapshotSection, str],
        ]: ...

        def _prune_caches(
            self,
            environment_ids: set[str],
            worktrees: set[Path],
            clusters: set[str],
            statuses: set[str],
            cpu_points: set[tuple[int, float]],
        ) -> None: ...

        def _cached_status(
            self,
            cluster: PostgresCluster,
            *,
            probe_results: dict[str, ProcessResult] | None = None,
        ) -> PostgresClusterState: ...

        def _plan_snapshot(
            self,
            catalog: BackupCatalog,
            *,
            project_id: str | None = None,
            include_removed: bool = False,
            probe_results: dict[str, ProcessResult] | None = None,
            catalog_rows: MonitorCatalogSnapshot | None = None,
            request: SnapshotRequest | None = None,
        ) -> _SnapshotPlan: ...

        @staticmethod
        def _snapshot_observation(
            observed_at: datetime,
            sections: frozenset[SnapshotSection],
            projects: tuple[ProjectSummary, ...],
            environments: tuple[EnvironmentSnapshot, ...],
            resources: dict[str, ClusterResourceSnapshot],
            section_outcomes: Mapping[SnapshotSection, str] | None = None,
        ) -> SnapshotObservation: ...

    def snapshot(
        self,
        project_id: str | None = None,
        *,
        include_removed: bool = False,
        request: SnapshotRequest | None = None,
    ) -> Snapshot:
        """Build one immutable snapshot command and execute it."""
        if request is None:
            return self.snapshot_command(project_id, include_removed=include_removed).run()
        return self.snapshot_command(
            project_id, include_removed=include_removed, request=request
        ).run()

    def processes(self, project_id: str | None = None) -> ProcessInventory:
        """Project one ``ProcessInventory`` from a single canonical snapshot.

        Delegates to ``processes_command`` and does not perform a second sample.
        """
        return self.processes_command(project_id=project_id).run()

    def checkout_inventory(
        self, project_id: str | None = None, *, include_removed: bool = False
    ) -> CheckoutInventory:
        """Project one ``CheckoutInventory`` from a single canonical snapshot.

        Delegates to ``checkout_inventory_command`` and does not perform a
        second sample. The main checkout of each project is the first row of
        its group with ``kind = main``; environment rows follow with
        ``kind = environment``.
        """
        return self.checkout_inventory_command(
            project_id=project_id, include_removed=include_removed
        ).run()

    def checkout_inventory_command(
        self, project_id: str | None = None, *, include_removed: bool = False
    ) -> Command[CheckoutInventory]:
        """Capture one ``CheckoutInventory`` projection from a single snapshot.

        The captured command runs ``snapshot_command`` exactly once and projects
        the result plus Git facts of the main checkout into the frozen
        ``CheckoutInventory`` model. The action is read-only and never spawns a
        process outside the bounded Git facts collector.
        """
        from odoo_instance_sdk.execution import Command as _Command, ExecutionPlan
        from odoo_instance_sdk.internal.checkout_inventory import build_checkout_inventory
        from odoo_instance_sdk.internal.proc import (
            PreparedAction,
            prepared_command,
        )

        action = PreparedAction(
            step_id="monitor.checkout_inventory",
            action="project_checkout_inventory",
            description="Project one CheckoutInventory from a single monitor snapshot",
            details={"project_id": project_id, "include_removed": include_removed},
            read_only=True,
        )

        def execute(context: RunContext[CheckoutInventory]) -> CheckoutInventory:
            context.action(action.step_id)
            try:
                snapshot = self.snapshot_command(
                    project_id=project_id, include_removed=include_removed
                ).run()
                base_ref_resolver = self._checkout_inventory_base_ref_resolver()
                worktree_paths = self._checkout_inventory_worktree_paths(
                    project_id=project_id,
                    snapshot=snapshot,
                    include_removed=include_removed,
                )
                inventory = build_checkout_inventory(
                    snapshot,
                    project_id=project_id,
                    include_removed=include_removed,
                    worktree_paths=worktree_paths,
                    base_ref_resolver=base_ref_resolver,
                )
                context.complete_action(action.step_id)
                return inventory
            finally:
                context.skip_remaining()

        plan = ExecutionPlan(steps=(action.public_projection(),)).with_fingerprint()
        return _Command.from_prepared(
            plan,
            prepared_command(execute, (action,), executor=self._executor),
        )

    def _checkout_inventory_base_ref_resolver(self) -> Callable[[str], str | None] | None:
        """Build a base-ref resolver from catalogue project manifests.

        ponytail: reads each project manifest once; the project count is small.
        Returns ``None`` when the catalogue is unavailable so the projection
        degrades to the ``main`` default.
        """
        import sqlite3

        from odoo_instance_sdk.project import ProjectConfig

        db_path = self.catalog_path if self.catalog_path is not None else _paths.get_catalog_path()
        try:
            catalog = BackupCatalog(db_path=db_path)
        except (BackupCatalogError, sqlite3.Error, OSError):
            return None
        base_refs: dict[str, str | None] = {}
        try:
            rows = catalog._monitor_snapshot_rows(include_removed=False)
            for project in rows.projects:
                project_id = str(project["project_id"])
                if project_id in base_refs:
                    continue
                repo_root = Path(str(project["repository_root"]))
                try:
                    config = ProjectConfig.load(repo_root)
                    base_refs[project_id] = config.default_base_ref
                except Exception:
                    base_refs[project_id] = None
        finally:
            catalog.close()
        if not base_refs:
            return None

        def resolve(project_id: str) -> str | None:
            return base_refs.get(project_id)

        return resolve

    def _checkout_inventory_worktree_paths(
        self, *, project_id: str | None, snapshot: Snapshot, include_removed: bool = False
    ) -> dict[str, str]:
        """Read stored worktree paths after the monitor's single snapshot pass.

        ponytail: one catalogue read per sample; the environment count is small.
        Returns an empty mapping when the catalogue is unavailable so the
        projection degrades to empty worktree paths.
        """
        import sqlite3

        db_path = self.catalog_path if self.catalog_path is not None else _paths.get_catalog_path()
        try:
            catalog = BackupCatalog(db_path=db_path)
        except (BackupCatalogError, sqlite3.Error, OSError):
            return {}
        paths: dict[str, str] = {}
        try:
            rows = catalog.list_environments(include_removed=include_removed)
        except (BackupCatalogError, OSError):
            catalog.close()
            return {}
        finally:
            catalog.close()
        from odoo_instance_sdk.resources.environment.checkout_artifacts import _row_to_env

        environment_ids = {env.id for env in snapshot.environments}
        for row in rows:
            env_id = str(row["id"])
            if env_id not in environment_ids:
                continue
            if project_id is not None:
                resolved_project = str(
                    row["project_id"]
                    or f"project_{repo_key(Path(str(row['repository_root'])), Path(str(row['git_common_dir'])))}"
                )
                if resolved_project != project_id:
                    continue
            paths[env_id] = str(_row_to_env(row).worktree_path)
        return paths

    def processes_command(self, project_id: str | None = None) -> Command[ProcessInventory]:
        """Capture one ``ProcessInventory`` projection from a single snapshot.

        The captured command runs ``snapshot_command`` exactly once and projects
        the result into the frozen ``ProcessInventory`` model. The action is
        read-only and never spawns a process outside the shared PostgreSQL
        backend attribution boundary.
        """
        from odoo_instance_sdk.execution import Command as _Command, ExecutionPlan
        from odoo_instance_sdk.internal.pg.builder import (
            PsqlSpecification,
            build_psql_specification,
        )
        from odoo_instance_sdk.internal.proc import (
            PreparedAction,
            prepared_command,
        )
        from odoo_instance_sdk.internal.process_inventory import (
            _BackendAttributionInput,
            _pg_stat_activity_query,
            build_process_inventory,
        )

        snapshot_command = self.snapshot_command(project_id=project_id, include_removed=False)
        credentials = self._process_inventory_credentials(project_id=project_id)
        credentials_by_database = {item.database: item for item in credentials}
        psql_specifications: dict[str, PsqlSpecification] = {
            database: build_psql_specification(
                host=item.host,
                port=item.port or 5432,
                user=item.user,
                password=item.password,
                database="postgres",
                args=("-c", _pg_stat_activity_query()),
                _trusted_args=("-t", "-A"),
                timeout=5.0,
                max_output_bytes=1024 * 1024,
                max_combined_output_bytes=1024 * 1024,
                step_id=f"process_inventory.{project_id or ''}.{database}.pg_stat_activity",
                _require_binary=False,
            )
            for database, item in sorted(credentials_by_database.items())
            if item.user
        }

        action = PreparedAction(
            step_id="monitor.processes",
            action="project_process_inventory",
            description="Project one ProcessInventory from a single monitor snapshot",
            details={"project_id": project_id},
            read_only=True,
        )

        def backend_runner(request: _BackendAttributionInput) -> tuple[int, str, str]:
            from odoo_instance_sdk.internal.pg.transport import execute_psql
            from odoo_instance_sdk.internal.proc import (
                ProcessOutputLimitError,
                ProcessSpawnError,
                ProcessTimeoutError,
            )

            specification = psql_specifications.get(request.database)
            if specification is None:
                return 127, "", "psql missing: attribution step was not captured"
            try:
                result = execute_psql(specification)
            except ProcessSpawnError:
                return 127, "", "psql missing"
            except ProcessTimeoutError:
                return 12, "", "timeout"
            except ProcessOutputLimitError:
                return 1, "", "output exceeded configured limit"
            stdout = result.stdout if isinstance(result.stdout, str) else ""
            stderr = result.stderr if isinstance(result.stderr, str) else ""
            return result.returncode, stdout, stderr

        def execute(context: RunContext[ProcessInventory]) -> ProcessInventory:
            context.action(action.step_id)
            try:
                snapshot = snapshot_command.run()

                def credential_resolver(database: str) -> DatabaseCredentials | None:
                    return credentials_by_database.get(database)

                inventory = build_process_inventory(
                    snapshot,
                    project_id=project_id,
                    credential_resolver=credential_resolver,
                    backend_runner=backend_runner,
                )
                context.complete_action(action.step_id)
                return inventory
            finally:
                context.skip_remaining()

        captured_steps: tuple[PreparedAction | PreparedStep, ...] = (
            action,
            *(spec.prepared_step for spec in psql_specifications.values()),
        )
        plan = ExecutionPlan(
            steps=tuple(step.public_projection() for step in captured_steps)
        ).with_fingerprint()
        return _Command.from_prepared(
            plan,
            prepared_command(execute, captured_steps, executor=self._executor),
        )

    def _process_inventory_credentials(
        self, *, project_id: str | None
    ) -> tuple[DatabaseCredentials, ...]:
        """Capture one deterministic credential record for each selected database.

        ponytail: reads the generated config once per database; the per-project
        environment count is small. Missing config values remain typed records
        so attribution can degrade as ``credentials_missing``.
        """
        import sqlite3

        from odoo_instance_sdk.internal.process_inventory import DatabaseCredentials
        from odoo_instance_sdk.models import StartConfig

        db_path = self.catalog_path if self.catalog_path is not None else _paths.get_catalog_path()
        try:
            catalog = BackupCatalog(db_path=db_path)
        except (BackupCatalogError, sqlite3.Error, OSError):
            return ()
        credentials_by_database: dict[str, DatabaseCredentials] = {}
        try:
            rows = catalog._monitor_snapshot_rows(include_removed=False)
            for row, _runtime in rows.environments:
                resolved_project_id = str(
                    row["project_id"]
                    or f"project_{repo_key(Path(str(row['repository_root'])), Path(str(row['git_common_dir'])))}"
                )
                if project_id is not None and resolved_project_id != project_id:
                    continue
                database_value = (
                    row["target_db_name"]
                    if str(row["db_mode"]) == "copy"
                    else row["source_db_name"]
                )
                if database_value is None:
                    continue
                database_name = str(database_value)
                if database_name in credentials_by_database:
                    continue
                try:
                    config = StartConfig.from_odoo_config(str(row["generated_config_path"]))
                except Exception:
                    credentials_by_database[database_name] = DatabaseCredentials(
                        database=database_name
                    )
                else:
                    credentials_by_database[database_name] = DatabaseCredentials(
                        database=database_name,
                        host=config.db_host,
                        port=config.db_port,
                        user=config.db_user,
                        password=config.db_password,
                    )
        finally:
            catalog.close()
        return tuple(credentials_by_database[name] for name in sorted(credentials_by_database))

    def snapshot_command(
        self,
        project_id: str | None = None,
        *,
        include_removed: bool = False,
        request: SnapshotRequest | None = None,
    ) -> Command[Snapshot]:
        """Capture one finite monitor collection operation.

        The monitor's catalog/cache reads and bounded probes remain in the
        operation callback.  The action is deliberately consumed after the
        collection so the command ledger still records a complete finite run;
        ``watch`` constructs a fresh command for every tick.
        """
        if request is not None:
            include_removed = request.include_removed
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.proc import (
            PreparedAction,
            ProcessExecutionError,
            ProcessResult,
            prepared_command,
        )

        action = PreparedAction(
            step_id="monitor.snapshot",
            action="collect_snapshot",
            description="Collect one finite environment monitor snapshot",
            details={
                "project_id": project_id,
                "include_removed": include_removed,
            },
            read_only=True,
        )
        probe_steps, catalog_rows = self._capture_probe_steps(
            project_id=project_id, include_removed=include_removed, request=request
        )
        captured_steps: tuple[PreparedStep | PreparedAction, ...] = (*probe_steps, action)

        def execute(context: RunContext[Snapshot]) -> Snapshot:
            context.action(action.step_id)
            probe_results = cast("dict[str, ProcessResult]", context.results)
            try:
                for probe in probe_steps:
                    try:
                        probe_results[probe.step_id] = cast(
                            "ProcessResult", context.process(probe.step_id)
                        )
                    except ProcessExecutionError as error:
                        # Keep a typed failed observation in the same ledger.
                        # Collectors must not retry a failed captured probe via
                        # their legacy provider path.
                        probe_results[probe.step_id] = ProcessResult(
                            argv=error.argv,
                            returncode=127,
                            stdout="",
                            stderr=str(error),
                            duration=error.duration,
                            cwd=probe.cwd,
                            environment=probe.environment,
                        )
                if probe_steps:
                    snapshot = self._snapshot_impl(
                        project_id=project_id,
                        include_removed=include_removed,
                        probe_results=probe_results,
                        catalog_rows=catalog_rows,
                        request=request,
                    )
                else:
                    if request is not None:
                        snapshot = self._snapshot_impl(
                            project_id=project_id,
                            include_removed=include_removed,
                            probe_results={},
                            catalog_rows=catalog_rows,
                            request=request,
                        )
                    else:
                        snapshot = self._snapshot_impl(
                            project_id=project_id,
                            include_removed=include_removed,
                        )
                context.complete_action(action.step_id)
                return snapshot
            finally:
                # Optional branches (for example an upstream Git ref that is
                # not present) are accounted for without launching a second
                # process.  Required probes are consumed by the collectors
                # through the active RunContext.
                context.skip_remaining()

        plan = ExecutionPlan(
            steps=tuple(step.public_projection() for step in captured_steps)
        ).with_fingerprint()
        return Command.from_prepared(
            plan,
            prepared_command(
                execute,
                captured_steps,
                executor=self._executor,
            ),
        )

    def _capture_probe_steps(  # noqa: C901
        self,
        *,
        project_id: str | None,
        include_removed: bool,
        request: SnapshotRequest | None = None,
    ) -> tuple[tuple[PreparedStep, ...], MonitorCatalogSnapshot]:
        """Capture the finite process probe manifest for one snapshot command.

        Catalog discovery is deliberately best-effort here: it is construction
        of an inspectable manifest, not snapshot collection.  The callback
        remains the authoritative domain collector, while this manifest keeps
        Git, storage, Docker and PostgreSQL child-process inputs in the same
        command ledger as the resulting snapshot.
        """
        from odoo_instance_sdk.internal.proc import PreparedStep

        db_path = self.catalog_path if self.catalog_path is not None else _paths.get_catalog_path()
        try:
            catalog = BackupCatalog(db_path=db_path)
            try:
                catalog_rows = catalog._monitor_snapshot_rows(include_removed=include_removed)
            finally:
                catalog.close()
        except (BackupCatalogError, sqlite3.Error, OSError):
            return (), MonitorCatalogSnapshot((), (), ())

        selected = frozenset(request.sections if request is not None else SNAPSHOT_SECTIONS)
        rows = catalog_rows.environments
        registered_projects = catalog_rows.projects
        steps: list[PreparedStep] = []
        projects: set[tuple[Path, str]] = set()
        repositories: set[tuple[Path, str]] = set()
        for row, _runtime in rows:
            worktree = Path(str(row["worktree_path"])).resolve()
            env_id = str(row["id"])
            repository = Path(str(row["repository_root"])).resolve()
            resolved_project = str(
                row["project_id"]
                or f"project_{repo_key(repository, Path(str(row['git_common_dir'])))}"
            )
            if project_id is not None and resolved_project != project_id:
                continue
            if (
                request is not None
                and request.project_ids
                and resolved_project not in request.project_ids
            ):
                continue
            if (
                request is not None
                and request.environment_ids
                and env_id not in request.environment_ids
            ):
                continue
            base_ref = _validated_base_ref(row["base_ref"])
            git_commands: tuple[tuple[str, tuple[str, ...]], ...] = ()
            if "git" in selected and self.git_provider is None and base_ref is not None:
                upstream_ref = f"{base_ref}@{{upstream}}"
                local_ref = f"refs/heads/{base_ref}"
                git_commands = (
                    ("branch", ("rev-parse", "--abbrev-ref", "HEAD")),
                    ("upstream", ("rev-parse", "--verify", upstream_ref)),
                    ("local_main", ("rev-parse", "--verify", local_ref)),
                    ("local_exact", ("rev-parse", "--verify", base_ref)),
                    (
                        "upstream_merge_base",
                        ("merge-base", upstream_ref, "HEAD"),
                    ),
                    ("upstream_ahead", ("rev-list", "--count", f"{upstream_ref}..HEAD")),
                    ("upstream_behind", ("rev-list", "--count", f"HEAD..{upstream_ref}")),
                    ("upstream_diff", ("diff", "--numstat", f"{upstream_ref}...HEAD")),
                    (
                        "local_merge_base",
                        ("merge-base", local_ref, "HEAD"),
                    ),
                    ("local_ahead", ("rev-list", "--count", f"{local_ref}..HEAD")),
                    ("local_behind", ("rev-list", "--count", f"HEAD..{local_ref}")),
                    ("local_diff", ("diff", "--numstat", f"{local_ref}...HEAD")),
                    (
                        "local_exact_merge_base",
                        ("merge-base", base_ref, "HEAD"),
                    ),
                    ("local_exact_ahead", ("rev-list", "--count", f"{base_ref}..HEAD")),
                    ("local_exact_behind", ("rev-list", "--count", f"HEAD..{base_ref}")),
                    ("local_exact_diff", ("diff", "--numstat", f"{base_ref}...HEAD")),
                )
                steps.append(
                    PreparedStep(
                        step_id=f"monitor.{env_id}.git.head",
                        argv=("git", "-C", str(worktree), "rev-parse", "--verify", "HEAD"),
                        cwd=str(worktree),
                        timeout=_PROBE_TIMEOUT_SECONDS,
                        read_only=True,
                        text=True,
                    )
                )
            du = shutil.which("du") or "du"
            if "storage" in selected:
                steps.append(
                    PreparedStep(
                        step_id=f"monitor.{env_id}.storage.worktree",
                        argv=(du, "-sb", str(worktree)),
                        cwd=str(worktree.parent),
                        timeout=_PROBE_TIMEOUT_SECONDS,
                        read_only=True,
                        text=True,
                    )
                )
            database = (
                row["target_db_name"] if str(row["db_mode"]) == "copy" else row["source_db_name"]
            )
            db_config = None
            if database and "postgresql" in selected:
                try:
                    from odoo_instance_sdk.models import StartConfig

                    db_config = StartConfig.from_odoo_config(str(row["generated_config_path"]))
                except Exception:
                    db_config = None
            if database and "postgresql" in selected:
                from odoo_instance_sdk.internal.pg.builder import build_psql_specification

                steps.append(
                    build_psql_specification(
                        step_id=f"monitor.{env_id}.postgres.identity",
                        host=db_config.db_host if db_config is not None else None,
                        port=db_config.db_port or 5432 if db_config is not None else 5432,
                        user=db_config.db_user if db_config is not None else None,
                        password=db_config.db_password if db_config is not None else None,
                        database=str(database),
                        args=("-w", "-c", "SELECT 1"),
                        _trusted_args=("-t", "-A"),
                        timeout=_PROBE_TIMEOUT_SECONDS,
                        _allow_missing_user=True,
                        _require_binary=False,
                    ).prepared_step
                )
            if "storage" in selected and bool(int(row["python_environment_owned"])):
                steps.append(
                    PreparedStep(
                        step_id=f"monitor.{env_id}.storage.python",
                        argv=(du, "-sb", str(row["python_environment_path"])),
                        cwd=str(Path(str(row["python_environment_path"])).parent),
                        timeout=_PROBE_TIMEOUT_SECONDS,
                        read_only=True,
                        text=True,
                    )
                )
            environment_root = Path(str(row["generated_config_path"])).parent
            if "storage" in selected:
                for suffix, path in (
                    ("cache", environment_root / "cache"),
                    ("artifacts", environment_root / "artifacts"),
                ):
                    steps.append(
                        PreparedStep(
                            step_id=f"monitor.{env_id}.storage.{suffix}",
                            argv=(du, "-sb", str(path)),
                            cwd=str(path.parent),
                            timeout=_PROBE_TIMEOUT_SECONDS,
                            read_only=True,
                            text=True,
                        )
                    )
            if (
                "storage" in selected
                and "postgresql" in selected
                and str(row["db_mode"]) == "copy"
                and row["target_db_name"]
            ):
                database_name = str(row["target_db_name"]).replace("'", "''")
                if row["target_db_name"] is not None:
                    from odoo_instance_sdk.internal.pg.builder import build_psql_specification

                    steps.append(
                        build_psql_specification(
                            step_id=f"monitor.{env_id}.storage.postgres",
                            host=db_config.db_host if db_config is not None else None,
                            port=db_config.db_port or 5432 if db_config is not None else 5432,
                            user=db_config.db_user if db_config is not None else None,
                            password=db_config.db_password if db_config is not None else None,
                            database=str(row["target_db_name"]),
                            args=(
                                "-w",
                                "-c",
                                f"SELECT pg_database_size('{database_name}')",
                            ),
                            _trusted_args=("-t", "-A"),
                            timeout=_PROBE_TIMEOUT_SECONDS,
                            _allow_missing_user=True,
                            _require_binary=False,
                        ).prepared_step
                    )
                try:
                    from odoo_instance_sdk.models import StartConfig

                    cfg = StartConfig.from_odoo_config(str(row["generated_config_path"]))
                    data_dir = Path(cfg.data_dir) if cfg.data_dir is not None else None
                    filestore = (
                        validate_filestore_containment(data_dir, str(row["target_db_name"]))
                        if data_dir is not None
                        else None
                    )
                except Exception:
                    filestore = None
                if filestore is not None:
                    steps.append(
                        PreparedStep(
                            step_id=f"monitor.{env_id}.storage.filestore",
                            argv=(du, "-sb", str(filestore)),
                            cwd=str(filestore.parent),
                            timeout=_PROBE_TIMEOUT_SECONDS,
                            read_only=True,
                            text=True,
                        )
                    )
            for suffix, args in git_commands:
                steps.append(
                    PreparedStep(
                        step_id=f"monitor.{env_id}.git.{suffix}",
                        argv=("git", "-C", str(worktree), *args),
                        cwd=str(worktree),
                        timeout=_PROBE_TIMEOUT_SECONDS,
                        read_only=True,
                        text=True,
                    )
                )
            projects.add((repository, resolved_project))
            if (repository / ".git").exists():
                repositories.add((repository, resolved_project))

        for project in registered_projects:
            repository = Path(str(project["repository_root"])).resolve()
            resolved_project = str(project["project_id"])
            if project_id is None or resolved_project == project_id:
                projects.add((repository, resolved_project))

        for repository, project_name in sorted(projects, key=lambda item: (str(item[0]), item[1])):
            if "docker" not in selected or self.docker_provider is not None:
                continue
            from odoo_instance_sdk.internal.proc import SubprocessExecutor

            if isinstance(self._executor, SubprocessExecutor) and shutil.which("docker") is None:
                continue
            compose_file = repository / "docker-compose.yml"
            try:
                cluster = PostgresCluster.from_project(repository)
                if cluster.mode != "compose" or not isinstance(
                    cluster.compose_runner, SubprocessComposeRunner
                ):
                    continue
                if SubprocessComposeRunner.run.__module__ != (
                    "odoo_instance_sdk.internal.postgres_compose"
                ):
                    continue
                compose_file = cluster.compose_file
            except (ProjectManifestNotFoundError, PostgresClusterError, OSError, AssertionError):
                # The command still exposes the canonical production probe
                # when construction cannot inspect a manifest.  If a custom
                # runner was available, the branch above deliberately omitted
                # it so injected process seams retain their old behavior.
                pass
            steps.append(
                PreparedStep(
                    step_id=f"monitor.{project_name}.docker.resources",
                    argv=(
                        "docker",
                        "compose",
                        "-f",
                        str(compose_file),
                        "-p",
                        project_name,
                        "ps",
                        "--format",
                        "json",
                    ),
                    cwd=str(repository),
                    timeout=_PROBE_TIMEOUT_SECONDS,
                    read_only=True,
                    text=True,
                )
            )
        for repository, project_name in sorted(
            repositories, key=lambda item: (str(item[0]), item[1])
        ):
            if "artifact" not in selected:
                continue
            steps.append(
                PreparedStep(
                    step_id=f"monitor.{project_name}.git.worktrees",
                    argv=(
                        "git",
                        "-C",
                        str(repository),
                        "worktree",
                        "list",
                        "--porcelain",
                        "-z",
                    ),
                    cwd=str(repository),
                    timeout=_PROBE_TIMEOUT_SECONDS,
                    read_only=True,
                    text=True,
                )
            )
        return tuple(steps), catalog_rows

    def _snapshot_impl(
        self,
        project_id: str | None = None,
        *,
        include_removed: bool = False,
        probe_results: dict[str, ProcessResult] | None = None,
        catalog_rows: MonitorCatalogSnapshot | None = None,
        request: SnapshotRequest | None = None,
    ) -> Snapshot:
        """Perform one coherent collection pass and return an immutable snapshot."""
        generated_at = datetime.now(UTC)
        db_path = self.catalog_path if self.catalog_path is not None else _paths.get_catalog_path()
        try:
            catalog = BackupCatalog(db_path=db_path)
        except (BackupCatalogError, sqlite3.Error) as exc:
            raise MonitorError("monitor catalog unavailable") from exc

        try:
            plan = self._plan_snapshot(
                catalog,
                project_id=project_id,
                include_removed=include_removed,
                probe_results=probe_results,
                catalog_rows=catalog_rows,
                request=request,
            )
        finally:
            catalog.close()
        resources, active_clusters = (
            self._collect_cluster_resources(list(plan.projects), probe_results=probe_results)
            if "docker" in plan.sections
            else ({}, set())
        )
        projects, environments, section_outcomes = self._collect_snapshot_rows(
            plan.projects, resources, probe_results=probe_results, sections=plan.sections
        )
        self._prune_caches(
            set(plan.environment_ids),
            set(plan.worktrees),
            active_clusters,
            set(plan.statuses),
            set(plan.cpu_points),
        )
        observed_at = datetime.now(UTC)
        observation = self._snapshot_observation(
            observed_at,
            plan.sections,
            projects,
            environments,
            resources,
            section_outcomes,
        )
        return Snapshot(
            schema_version=_SCHEMA_VERSION,
            generated_at=generated_at,
            projects=projects,
            environments=environments,
            observation=observation,
        )
