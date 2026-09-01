"""Public pgAdmin lifecycle orchestration."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

import msgspec

from odoo_instance_sdk.exceptions import PgAdminUnavailableError, StalePlanError
from odoo_instance_sdk.execution import Command
from odoo_instance_sdk.internal import pgadmin_container, pgadmin_files
from odoo_instance_sdk.internal.address import AddressState, probe_address
from odoo_instance_sdk.internal.pgadmin_container import PostgresIdentityCluster
from odoo_instance_sdk.models import PgAdminOpenResult

if TYPE_CHECKING:
    from odoo_instance_sdk.config import InstanceConfig
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.internal.pgadmin_files import (
        PgAdminFingerprintInputs,
        PgAdminPaths,
        PgAdminPreparation,
        PostgresIdentity,
    )
    from odoo_instance_sdk.internal.postgres_compose import ComposeRunner
    from odoo_instance_sdk.internal.proc import (
        ProcessExecutor,
        RunContext,
        Step,
    )


class _PgAdminInstance(Protocol):
    config: InstanceConfig


@dataclass(frozen=True, slots=True)
class _PgAdminReconciliationCarrier:
    """Private carrier for the command returned by locked provisioning."""

    preparation: PgAdminPreparation
    runner: ComposeRunner
    network: str
    database: str
    timeout: float
    steps: tuple[Step, ...]
    executor: ProcessExecutor
    fingerprint_key: bytes

    def reconcile(
        self,
        context: RunContext[PgAdminOpenResult],
        *,
        lock_held: bool = False,
    ) -> PgAdminOpenResult:
        """Execute one reconciliation run with a fresh configured deadline."""
        deadline = time.monotonic() + max(0.1, self.timeout)

        def execute_locked() -> PgAdminOpenResult:
            try:
                preparation_matches = pgadmin_files.revalidate_preparation(
                    self.preparation,
                    expected_fingerprint_key=self.fingerprint_key,
                )
            except PgAdminUnavailableError:
                raise StalePlanError(
                    "captured pgAdmin preparation changed before execution"
                ) from None
            if not preparation_matches:
                raise StalePlanError("captured pgAdmin fingerprint key changed before execution")
            current = pgadmin_container.inspect_container(
                self.runner,
                pgadmin_files.PGADMIN_CONTAINER_NAME,
                deadline=deadline,
                missing_ok=True,
                step_id="pgadmin.reconciliation.inspect.0",
            )
            _revalidate_port(self.preparation.port, current)
            context.action("pgadmin.reconciliation.port.revalidate")
            return pgadmin_container.reconcile_container(
                self.preparation,
                runner=self.runner,
                network=self.network,
                database=self.database,
                deadline=deadline,
                planned=True,
                current_container=current,
            )

        if lock_held:
            return execute_locked()
        with pgadmin_files.pgadmin_lock(
            path=self.preparation.paths.lock,
            timeout=max(0.1, self.timeout),
        ):
            return execute_locked()

    def reconciliation_command(self) -> Command[PgAdminOpenResult]:
        """Capture the exact post-provisioning command for one immutable run."""
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.proc import prepared_command

        def reconcile(context: RunContext[PgAdminOpenResult]) -> PgAdminOpenResult:
            return self.reconcile(context)

        plan = ExecutionPlan(steps=tuple(step.public_projection() for step in self.steps))
        return Command.from_prepared(
            plan,
            prepared_command(reconcile, self.steps, executor=self.executor),
        )


class PgAdminPhaseHandle(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Public frozen handle for the private post-provisioning command.

    ``Command`` is already the repository's safe public/private boundary: its
    plan is serializable and secret-free while its prepared callback remains
    private.  Keeping that command directly avoids a second process-local
    registry and makes the phase boundary explicit.
    """

    reconciliation: Command[PgAdminOpenResult]

    def reconciliation_command(self) -> Command[PgAdminOpenResult]:
        """Return the explicitly captured reconciliation command."""
        return self.reconciliation


@dataclass(frozen=True, slots=True)
class PgAdminProvisioningPhase:
    """Explicit locked domain phase preceding Docker reconciliation."""

    instance: _PgAdminInstance
    cluster: PostgresIdentityCluster
    database: str
    timeout: float = 60.0
    captured_identity: PostgresIdentity | None = None
    captured_paths: PgAdminPaths | None = None
    captured_port: int | None = None
    captured_fingerprint: PgAdminFingerprintInputs | None = None
    executor: ProcessExecutor | None = None

    def provision(self) -> PgAdminPhaseHandle:
        """Provision private inputs under the lifecycle lock and return a handle."""
        return PgAdminPhaseHandle(reconciliation=self._provision_carrier().reconciliation_command())

    def _provision_carrier(
        self,
        *,
        lock_held: bool = False,
    ) -> _PgAdminReconciliationCarrier:
        """Capture the private continuation while holding the lifecycle lock."""
        from odoo_instance_sdk.internal.proc import active_context

        paths = self.captured_paths or pgadmin_files.PgAdminPaths.from_defaults()
        context = cast("RunContext[PgAdminOpenResult] | None", active_context())
        if lock_held:
            return self._run_locked(context, paths)
        with pgadmin_files.pgadmin_lock(path=paths.lock, timeout=self.timeout):
            return self._run_locked(context, paths)

    def _run_locked(
        self,
        context: RunContext[PgAdminOpenResult] | None,
        paths: PgAdminPaths,
    ) -> _PgAdminReconciliationCarrier:
        """Run the phase body while its caller owns the lifecycle lock."""
        from odoo_instance_sdk.internal.proc import PreparedAction

        runner = self.cluster.compose_runner
        if runner is None:
            raise PgAdminUnavailableError()
        from odoo_instance_sdk.internal.proc import SubprocessExecutor

        executor = (
            context.executor if context is not None else self.executor or SubprocessExecutor()
        )
        planned = context is not None
        deadline = time.monotonic() + max(0.1, self.timeout)
        identity = pgadmin_container.resolve_postgres_identity(
            self.cluster, deadline=deadline, planned=planned
        )
        if self.captured_identity is not None and identity != self.captured_identity:
            raise StalePlanError("captured pgAdmin identity changed before execution")
        if self.captured_identity is not None:
            identity = self.captured_identity
        password = self.instance.config.db_password or ""
        fingerprint_inputs = (
            self.captured_fingerprint
            or pgadmin_files.execution_fingerprint_inputs(paths, identity, self.database, password)
        )
        _assert_persisted_fingerprint_key(
            paths,
            fingerprint_inputs.key,
            allow_missing=True,
        )
        if context is not None and not context.planned("pgadmin.container.inspect.0"):
            raise PgAdminUnavailableError()
        current = pgadmin_container.inspect_container(
            runner,
            pgadmin_files.PGADMIN_CONTAINER_NAME,
            deadline=deadline,
            missing_ok=True,
            step_id="pgadmin.container.inspect.0" if context is not None else None,
        )
        if current is not None:
            # Do not expose any credential files to an unowned or malformed
            # container.  Reconciliation performs this check too, but it must
            # happen before preparation writes or validates the host files.
            pgadmin_container.assert_owned_container(current)
        port = self.captured_port or pgadmin_files.select_port(paths)
        _revalidate_port(port, current)
        if context is not None:
            if not context.planned("pgadmin.port.revalidate"):
                raise PgAdminUnavailableError()
            context.action("pgadmin.port.revalidate")
            if not context.planned("pgadmin.prepare"):
                raise PgAdminUnavailableError()
            context.action("pgadmin.prepare")
        preparation = pgadmin_files.prepare_files(
            servers_json=pgadmin_files.server_json(identity, self.database),
            pgpass=pgadmin_files.pgpass_line(identity, password),
            fingerprint=fingerprint_inputs.fingerprint,
            port=port,
            paths=paths,
            fingerprint_key=fingerprint_inputs.key,
        )
        return _PgAdminReconciliationCarrier(
            preparation=preparation,
            runner=runner,
            network=identity.network,
            database=self.database,
            timeout=self.timeout,
            steps=(
                *pgadmin_files.preparation_revalidation_steps(paths),
                pgadmin_container.reconciliation_inspect_step(),
                PreparedAction(
                    step_id="pgadmin.reconciliation.port.revalidate",
                    action="pgadmin_reconciliation_port_revalidate",
                    description="Revalidate the captured loopback port under the lifecycle lock",
                    read_only=True,
                ),
                *pgadmin_container.reconciliation_steps(
                    paths=paths,
                    port=port,
                    network=identity.network,
                    fingerprint=fingerprint_inputs.fingerprint,
                    secret_values=(fingerprint_inputs.fingerprint, password),
                ),
            ),
            executor=executor,
            fingerprint_key=fingerprint_inputs.key,
        )


def open_pgadmin_phase(
    *,
    instance: _PgAdminInstance,
    cluster: PostgresIdentityCluster,
    database: str,
    timeout: float = 60.0,
    captured_identity: PostgresIdentity | None = None,
    captured_paths: PgAdminPaths | None = None,
    captured_port: int | None = None,
    executor: ProcessExecutor | None = None,
) -> PgAdminPhaseHandle:
    """Run only locked provisioning and return a safe typed phase handle."""
    return PgAdminProvisioningPhase(
        instance=instance,
        cluster=cluster,
        database=database,
        timeout=timeout,
        captured_identity=captured_identity,
        captured_paths=captured_paths,
        captured_port=captured_port,
        executor=executor,
    ).provision()


def _revalidate_port(port: int, current: dict[str, JsonValue] | None) -> None:
    """Reject a captured port conflict before any pgAdmin file mutation."""
    state = probe_address("127.0.0.1", port)
    if state is AddressState.FREE:
        return
    if (
        state is AddressState.OCCUPIED
        and current is not None
        and pgadmin_container.owned_container_uses_port(current, port)
    ):
        return
    raise StalePlanError(
        "captured pgAdmin loopback port is no longer available",
        expected=port,
        actual=state.value,
    )


def _assert_persisted_fingerprint_key(
    paths: PgAdminPaths,
    expected_key: bytes,
    *,
    allow_missing: bool,
) -> None:
    """Reject a changed private HMAC key without disclosing either key."""
    persisted = pgadmin_files._existing_fingerprint_key(paths)
    if persisted == expected_key:
        return
    key_path = paths.private_dir / ".fingerprint-key"
    if allow_missing and not key_path.exists() and not key_path.is_symlink():
        return
    raise StalePlanError("captured pgAdmin fingerprint key changed before execution")


__all__ = [
    "PgAdminPhaseHandle",
    "PgAdminProvisioningPhase",
    "open_pgadmin_phase",
]
