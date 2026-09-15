from __future__ import annotations

# ruff: noqa: F821
import contextlib
import shutil
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

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
    CheckoutInventory,
    PostgresClusterState,
    ProcessInventory,
    Snapshot,
)
from odoo_instance_sdk.resources.environment import EnvironmentState
from odoo_instance_sdk.resources.monitor.planning import (
    _PROBE_TIMEOUT_SECONDS,
    _SCHEMA_VERSION,
    _EnvironmentPlan,
    _ProjectPlan,
    _SnapshotPlan,
)
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog, MonitorCatalogSnapshot


class _CollectMixin:
    def snapshot(self, project_id: str | None = None, *, include_removed: bool = False) -> Snapshot:
        """Build one immutable snapshot command and execute it."""
        return self.snapshot_command(project_id, include_removed=include_removed).run()

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
        from odoo_instance_sdk.execution import Command as _Command
        from odoo_instance_sdk.execution import ExecutionPlan
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
        environment_ids = {env.id for env in snapshot.environments}
        for row in rows:
            env_id = str(row["id"])
            if env_id not in environment_ids:
                continue
            if project_id is not None:
                resolved_project = f"project_{repo_key(Path(str(row['repository_root'])), Path(str(row['git_common_dir'])))}"
                if resolved_project != project_id:
                    continue
            worktree_value = row["worktree_path"]
            if isinstance(worktree_value, str) and worktree_value.strip():
                paths[env_id] = worktree_value
        return paths

    def processes_command(self, project_id: str | None = None) -> Command[ProcessInventory]:
        """Capture one ``ProcessInventory`` projection from a single snapshot.

        The captured command runs ``snapshot_command`` exactly once and projects
        the result into the frozen ``ProcessInventory`` model. The action is
        read-only and never spawns a process outside the shared PostgreSQL
        backend attribution boundary.
        """
        from odoo_instance_sdk.execution import Command as _Command
        from odoo_instance_sdk.execution import ExecutionPlan
        from odoo_instance_sdk.internal.proc import (
            PreparedAction,
            prepared_command,
        )
        from odoo_instance_sdk.internal.process_inventory import build_process_inventory

        action = PreparedAction(
            step_id="monitor.processes",
            action="project_process_inventory",
            description="Project one ProcessInventory from a single monitor snapshot",
            details={"project_id": project_id},
            read_only=True,
        )

        def execute(context: RunContext[ProcessInventory]) -> ProcessInventory:
            context.action(action.step_id)
            try:
                snapshot = self.snapshot_command(project_id=project_id, include_removed=False).run()
                credential_resolver = self._process_inventory_credential_resolver(
                    project_id=project_id
                )
                inventory = build_process_inventory(
                    snapshot,
                    project_id=project_id,
                    credential_resolver=credential_resolver,
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

    def _process_inventory_credential_resolver(
        self, *, project_id: str | None
    ) -> Callable[[str], DatabaseCredentials | None] | None:
        """Build a credential resolver from catalogue environment config paths.

        ponytail: reads the generated config once per database; the per-project
        environment count is small. Returns ``None`` when the catalogue is
        unavailable so backend attribution degrades gracefully.
        """
        import sqlite3

        from odoo_instance_sdk.internal.process_inventory import DatabaseCredentials
        from odoo_instance_sdk.models import StartConfig

        db_path = self.catalog_path if self.catalog_path is not None else _paths.get_catalog_path()
        try:
            catalog = BackupCatalog(db_path=db_path)
        except (BackupCatalogError, sqlite3.Error, OSError):
            return None
        credentials_by_database: dict[str, DatabaseCredentials] = {}
        try:
            rows = catalog._monitor_snapshot_rows(include_removed=False)
            for row, _runtime in rows.environments:
                resolved_project_id = f"project_{repo_key(Path(str(row['repository_root'])), Path(str(row['git_common_dir'])))}"
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
                    continue
                credentials_by_database[database_name] = DatabaseCredentials(
                    database=database_name,
                    host=config.db_host,
                    port=config.db_port,
                    user=config.db_user,
                    password=config.db_password,
                )
        finally:
            catalog.close()
        if not credentials_by_database:
            return None

        def resolve(database: str) -> DatabaseCredentials | None:
            return credentials_by_database.get(database)

        return resolve

    def snapshot_command(
        self, project_id: str | None = None, *, include_removed: bool = False
    ) -> Command[Snapshot]:
        """Capture one finite monitor collection operation.

        The monitor's catalog/cache reads and bounded probes remain in the
        operation callback.  The action is deliberately consumed after the
        collection so the command ledger still records a complete finite run;
        ``watch`` constructs a fresh command for every tick.
        """
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
            project_id=project_id, include_removed=include_removed
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
        self, *, project_id: str | None, include_removed: bool
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

        rows = catalog_rows.environments
        registered_projects = catalog_rows.projects
        steps: list[PreparedStep] = []
        projects: set[tuple[Path, str]] = set()
        repositories: set[tuple[Path, str]] = set()
        for row, _runtime in rows:
            worktree = Path(str(row["worktree_path"])).resolve()
            env_id = str(row["id"])
            repository = Path(str(row["repository_root"])).resolve()
            resolved_project = f"project_{repo_key(repository, Path(str(row['git_common_dir'])))}"
            if project_id is not None and resolved_project != project_id:
                continue
            base_ref = _validated_base_ref(row["base_ref"])
            git_commands: tuple[tuple[str, tuple[str, ...]], ...] = ()
            if self.git_provider is None and base_ref is not None:
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
            if database:
                try:
                    from odoo_instance_sdk.models import StartConfig

                    db_config = StartConfig.from_odoo_config(str(row["generated_config_path"]))
                except Exception:
                    db_config = None
            if database:
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
            if bool(int(row["python_environment_owned"])):
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
            if str(row["db_mode"]) == "copy" and row["target_db_name"]:
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
            if self.docker_provider is not None:
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
            )
        finally:
            catalog.close()
        resources, active_clusters = self._collect_cluster_resources(
            list(plan.projects), probe_results=probe_results
        )
        projects, environments = self._collect_snapshot_rows(
            plan.projects, resources, probe_results=probe_results
        )
        self._prune_caches(
            set(plan.environment_ids),
            set(plan.worktrees),
            active_clusters,
            set(plan.statuses),
            set(plan.cpu_points),
        )
        return Snapshot(
            schema_version=_SCHEMA_VERSION,
            generated_at=generated_at,
            projects=projects,
            environments=environments,
        )

    def _plan_snapshot(  # noqa: C901
        self,
        catalog: BackupCatalog,
        *,
        project_id: str | None = None,
        include_removed: bool = False,
        probe_results: dict[str, ProcessResult] | None = None,
        catalog_rows: MonitorCatalogSnapshot | None = None,
    ) -> _SnapshotPlan:
        """Read catalog runtime once and derive deterministic project plans."""
        if catalog_rows is None:
            try:
                snapshot_rows = catalog._monitor_snapshot_rows(include_removed=include_removed)
            except (BackupCatalogError, sqlite3.Error) as exc:
                raise MonitorError("monitor catalog unavailable") from exc
        else:
            snapshot_rows = catalog_rows
        rows = snapshot_rows.environments
        registered_projects = snapshot_rows.projects
        project_runtimes = snapshot_rows.project_runtimes
        groups: dict[str, list[_EnvironmentPlan]] = {}
        project_details: dict[str, Path] = {
            str(row["project_id"]): Path(str(row["repository_root"])).resolve()
            for row in registered_projects
        }
        project_runtime_by_id = {str(row["owner_id"]): row for row in project_runtimes}
        environment_ids: set[str] = set()
        worktrees: set[Path] = set()
        cpu_points: set[tuple[int, float]] = set()
        for row, runtime in rows:
            repository = Path(str(row["repository_root"])).resolve()
            git_common = Path(str(row["git_common_dir"])).resolve()
            resolved_project_id = f"project_{repo_key(repository, git_common)}"
            groups.setdefault(resolved_project_id, []).append(_EnvironmentPlan(row, runtime))
            project_details.setdefault(resolved_project_id, repository)
            environment_ids.add(str(row["id"]))
            worktrees.add(Path(str(row["worktree_path"])).resolve())
            if runtime is not None:
                with contextlib.suppress(TypeError, ValueError):
                    cpu_points.add((int(runtime["root_pid"]), float(runtime["create_time"])))
        for runtime in project_runtimes:
            with contextlib.suppress(TypeError, ValueError):
                cpu_points.add((int(runtime["root_pid"]), float(runtime["create_time"])))
        plans: list[_ProjectPlan] = []
        statuses: set[str] = set()
        for resolved_project_id, environments in groups.items():
            first = environments[0].row
            repo_root = Path(str(first["repository_root"]))
            # Filtering is intentionally before manifest/status/Docker work.
            # The catalog grouping and cache-pruning inputs remain cheap.
            if project_id is not None and resolved_project_id != project_id:
                continue
            if all(
                str(item.row["state"]) == EnvironmentState.REMOVED.value for item in environments
            ):
                cluster, state = None, None
            else:
                cluster, state = self._project_cluster(
                    repo_root,
                    statuses,
                    project_id=resolved_project_id,
                    probe_results=probe_results,
                )
            plans.append(
                _ProjectPlan(
                    resolved_project_id,
                    repo_root,
                    cluster,
                    state,
                    tuple(environments),
                    project_runtime=project_runtime_by_id.get(resolved_project_id),
                )
            )
        for resolved_project_id, repo_root in project_details.items():
            if resolved_project_id in groups:
                continue
            if project_id is not None and resolved_project_id != project_id:
                continue
            cluster, state = self._project_cluster(
                repo_root,
                statuses,
                project_id=resolved_project_id,
                probe_results=probe_results,
            )
            plans.append(
                _ProjectPlan(
                    resolved_project_id,
                    repo_root,
                    cluster,
                    state,
                    (),
                    project_runtime=project_runtime_by_id.get(resolved_project_id),
                )
            )
        return _SnapshotPlan(
            tuple(sorted(plans, key=lambda item: item.project_id)),
            frozenset(environment_ids),
            frozenset(worktrees),
            frozenset(statuses),
            frozenset(cpu_points),
        )

    def _project_cluster(
        self,
        repo_root: Path,
        statuses: set[str],
        *,
        project_id: str,
        probe_results: dict[str, ProcessResult] | None = None,
    ) -> tuple[PostgresCluster | None, PostgresClusterState | None]:
        try:
            if (
                probe_results is None
                or PostgresCluster.from_project.__module__ != "odoo_instance_sdk.resources.postgres"
            ):
                cluster = PostgresCluster.from_project(repo_root)
            else:
                from odoo_instance_sdk.project import ProjectConfig

                cluster = PostgresCluster._from_config(
                    ProjectConfig.load(repo_root),
                    repository_root=repo_root,
                    compose_runner=self._docker_runner,
                    project_id=project_id.removeprefix("project_"),
                )
            statuses.add(str(cluster.compose_file.resolve()))
            return cluster, self._cached_status(cluster, probe_results=probe_results)
        except (ProjectManifestNotFoundError, PostgresClusterError, OSError):
            return None, None
