"""Read-only database evidence and explicit reconciliation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, TypeVar

from odoo_instance_sdk.exceptions import (
    DatabaseManagerUnavailableError,
    DatabaseReconciliationError,
)
from odoo_instance_sdk.models import DatabaseObservation, DatabaseReconciliationResult

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        ProcessExecutor,
        RunContext,
    )
    from odoo_instance_sdk.resources.instance import OdooInstance


_T = TypeVar("_T")


class _ObservationMixin:
    if TYPE_CHECKING:
        _instance: OdooInstance

        @property
        def _cluster(self) -> tuple[str | None, int] | None: ...

        def names(self) -> tuple[str, ...]: ...

        from odoo_instance_sdk.models import Database

        def list(self) -> tuple[Database, ...]: ...

        def _action_command(
            self,
            step_id: str,
            description: str,
            callback: Callable[[], _T],
            *,
            executor: ProcessExecutor | None,
            read_only: bool = False,
            mutating: bool = False,
            action_steps: Sequence[PreparedAction] = (),
            steps: Sequence[PreparedStep] = (),
            optional_steps: Sequence[str] = (),
        ) -> Command[_T]: ...

        def _planned_exists_result(self, name: str, step_id: str | None) -> bool | None: ...

    def observe(self) -> DatabaseObservation:
        """Read live database names and catalogue differences without writes."""
        return self.observe_command().run()

    def observe_command(
        self, *, executor: ProcessExecutor | None = None
    ) -> Command[DatabaseObservation]:
        return self._action_command(
            "database.observe",
            "Observe live database identities",
            self._observe_impl,
            executor=executor,
            read_only=True,
        )

    def _observe_impl(self) -> DatabaseObservation:
        names = self.names()
        cluster = self._cluster
        host = port = None
        tracked: tuple[str, ...] = ()
        missing: tuple[str, ...] = ()
        if cluster is not None:
            host, port = cluster
            tracked = tuple(
                sorted(
                    self._instance._client.get_catalog().distinct_restored_database_names(
                        host, port
                    )
                )
            )
            live = set(names)
            missing = tuple(name for name in tracked if name not in live)
        return DatabaseObservation(
            names=names,
            evidence_source="odoo",
            tracked_names=tracked,
            missing_names=missing,
            cluster_host=host,
            cluster_port=port,
        )

    def observe_exists(self, name: str) -> DatabaseObservation:
        """Observe one exact database, using the bounded psql fallback."""
        return self._observe_exists_impl(name)

    def _observe_exists_impl(
        self, name: str, *, psql_step_id: str | None = None
    ) -> DatabaseObservation:
        direct_result = self._planned_exists_result(name, psql_step_id)
        if direct_result is not None:
            cluster = self._cluster
            host: str | None
            port: int | None
            host, port = cluster if cluster is not None else (None, None)
            tracked: tuple[str, ...] = ()
            if not direct_result and cluster is not None:
                assert port is not None
                tracked = tuple(
                    sorted(
                        self._instance._client.get_catalog().distinct_restored_database_names(
                            host, port
                        )
                    )
                )
            return DatabaseObservation(
                names=(name,) if direct_result else (),
                evidence_source="psql",
                tracked_names=tracked,
                missing_names=(name,) if not direct_result and name in tracked else (),
                cluster_host=host,
                cluster_port=port,
            )

        try:
            databases = self.list()
        except DatabaseManagerUnavailableError:
            cluster = self._cluster
            if cluster is None or self._instance.config.db_user is None:
                raise
            db_host, db_port = cluster
            from odoo_instance_sdk.resources.database.backup_restore_parts.queries import (
                _verify_database_via_psql,
            )

            result = _verify_database_via_psql(
                db_host,
                db_port,
                self._instance.config.db_user,
                self._instance.config.db_password,
                name,
                step_id=psql_step_id,
            )
            if result is None:
                raise
            tracked = tuple(
                sorted(
                    self._instance._client.get_catalog().distinct_restored_database_names(
                        db_host, db_port
                    )
                )
            )
            return DatabaseObservation(
                names=(name,) if result else (),
                evidence_source="psql",
                tracked_names=tracked,
                missing_names=(name,) if not result and name in tracked else (),
                cluster_host=db_host,
                cluster_port=db_port,
            )

        names = tuple(db.name for db in databases)
        cluster = self._cluster
        live_host: str | None
        live_port: int | None
        live_host, live_port = cluster if cluster is not None else (None, None)
        live_tracked: tuple[str, ...] = ()
        if cluster is not None:
            assert live_port is not None
            live_tracked = tuple(
                sorted(
                    self._instance._client.get_catalog().distinct_restored_database_names(
                        live_host, live_port
                    )
                )
            )
        return DatabaseObservation(
            names=names,
            evidence_source="odoo",
            tracked_names=live_tracked,
            missing_names=(name,) if name not in names and name in live_tracked else (),
            cluster_host=live_host,
            cluster_port=live_port,
        )

    def reconcile_databases(
        self, observation: DatabaseObservation | None = None
    ) -> DatabaseReconciliationResult:
        """Revalidate and explicitly publish tracked database absences."""
        return self.reconcile_databases_command(observation).run()

    def reconcile_databases_command(
        self,
        observation: DatabaseObservation | None = None,
        *,
        executor: ProcessExecutor | None = None,
    ) -> Command[DatabaseReconciliationResult]:
        captured = observation if observation is not None else self.observe()
        host, port = self._require_reconciliation_observation(captured)
        candidates = captured.missing_names
        from odoo_instance_sdk.execution import (
            Command,
            ExecutionPlan,
            PlanPrecondition,
            SemanticPlanObservation,
        )
        from odoo_instance_sdk.internal.proc import (
            PreparedAction,
            SubprocessExecutor,
            prepared_command,
        )

        action = PreparedAction(
            step_id="database.reconcile",
            action="database.reconcile",
            description="Publish explicitly revalidated dropped database events",
            read_only=False,
            mutating=True,
        )
        semantic = SemanticPlanObservation(
            kind="semantic",
            goal="Reconcile databases absent from the selected cluster",
            targets=(*tuple(f"database={name}" for name in candidates), f"cluster={host}:{port}"),
            mutations=("append idempotent dropped database events",),
            preconditions=(
                PlanPrecondition(
                    name="exact-database-observation",
                    status="passed",
                    detail="captured database evidence proves tracked names absent",
                ),
            ),
        )

        def run(context: RunContext[DatabaseReconciliationResult]) -> DatabaseReconciliationResult:
            context.action(action.step_id)
            if candidates:
                current = (
                    self._observe_exists_impl(candidates[0])
                    if captured.evidence_source == "psql" and len(candidates) == 1
                    else self._observe_impl()
                )
                if current.inconclusive:
                    raise DatabaseReconciliationError("inconclusive")
                if (current.cluster_host, current.cluster_port) != (host, port):
                    raise DatabaseReconciliationError("foreign-cluster")
                if set(candidates) - set(current.missing_names):
                    raise DatabaseReconciliationError("stale-observation")
                self._instance._client.get_catalog().record_databases_dropped(
                    host, port, candidates
                )
            return DatabaseReconciliationResult(
                cluster_host=host,
                cluster_port=port,
                reconciled_names=candidates,
            )

        return Command.from_prepared(
            ExecutionPlan(steps=(action.public_projection(),), observations=(semantic,)),
            prepared_command(run, (action,), executor=executor or SubprocessExecutor()),
        )

    def _require_reconciliation_observation(
        self, observation: DatabaseObservation
    ) -> tuple[str, int]:
        cluster = self._cluster
        if observation.inconclusive:
            raise DatabaseReconciliationError("inconclusive")
        if not observation.missing_names:
            if cluster is None or cluster[0] is None:
                raise DatabaseReconciliationError("cluster-unavailable")
            return cluster[0], cluster[1]
        if (
            cluster is None
            or cluster[0] is None
            or (observation.cluster_host, observation.cluster_port) != cluster
        ):
            raise DatabaseReconciliationError("foreign-cluster")
        if not set(observation.missing_names).issubset(set(observation.tracked_names)):
            raise DatabaseReconciliationError("untracked-database")
        return cluster[0], cluster[1]
