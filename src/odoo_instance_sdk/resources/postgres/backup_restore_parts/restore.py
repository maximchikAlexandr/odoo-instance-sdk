from __future__ import annotations  # noqa: I001 -- keep PostgreSQL restore lifecycle aliases grouped; remove when Ruff supports grouped aliases.

import json
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeVar, cast

import msgspec

from odoo_instance_sdk.exceptions import (
    LockConflictError,
    PostgresClusterError,
    PostgresClusterNotOwnedError,
    PostgresClusterTimeoutError,
    PostgresClusterUnhealthyError,
)
from odoo_instance_sdk.internal.locks import exclusive_lock_until, postgres_cluster_lock_path
from odoo_instance_sdk.internal.postgres_compose import (
    SubprocessComposeRunner,
    compose_project_name,
    compose_stop,
    compose_up,
    compose_volume_name,
    docker_available,
    ensure_docker_or_raise,
    inspect_container_identity,
    inspect_volume_identity,
)
from odoo_instance_sdk.models import ClusterResourceSnapshot, PostgresClusterState
from odoo_instance_sdk.resources.postgres.lifecycle import (
    _DEFAULT_STOP_TIMEOUT,
    _RESOURCE_SNAPSHOT_TIMEOUT as _RESOURCE_SNAPSHOT_TIMEOUT,
)
from odoo_instance_sdk.storage.backup_catalog import PostgresClusterClaim

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command, ExecutionPlan, JsonValue
    from odoo_instance_sdk.internal.postgres_compose import ComposeRunner
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        ProcessExecutor,
        RunContext,
    )

T = TypeVar("T")


class _RestoreMixin:
    if TYPE_CHECKING:
        _project_id: str
        _mode: Literal["external", "compose"]
        _endpoint_host: str
        _endpoint_port: int
        _image: str | None
        _user: str | None
        _compose_runner: ComposeRunner

        @property
        def owned(self) -> bool: ...
        @property
        def endpoint(self) -> str: ...
        @property
        def compose_project_name(self) -> str: ...

        def _compose_file(self) -> Path: ...
        def _cluster_claim(self) -> PostgresClusterClaim | None: ...
        def _ensure_pending_cluster_claim(self) -> PostgresClusterClaim: ...
        def _activate_cluster_claim(self, claim: PostgresClusterClaim) -> None: ...
        def _status_compose(
            self,
            *,
            timeout: float | None = None,
            health_step_id: str | None = None,
            ps_step_id: str | None = None,
        ) -> PostgresClusterState: ...
        def _ensure_artifacts(
            self,
            image: str,
            *,
            timeout: float | None = None,
            temporary_path: Path | None = None,
            step_id: str | None = None,
            cluster_id: str | None = None,
            validate: bool = True,
            publish: bool = True,
        ) -> None: ...
        def _approved_image_digest(self) -> str: ...
        def _require_trusted_image(
            self,
            timeout: float,
            *,
            pull_step_id: str | None = None,
            inspect_step_id: str | None = None,
        ) -> str: ...

    def _ensure_running_compose(  # noqa: C901
        self,
        timeout: float,
        *,
        temporary_path: Path | None = None,
        step_ids: Mapping[str, str] | None = None,
    ) -> None:
        """Start a managed cluster when status is STOPPED, STARTING, or UNKNOWN."""
        deadline = time.monotonic() + max(0.1, timeout)
        try:
            lock = exclusive_lock_until(postgres_cluster_lock_path(self._project_id), deadline)
            with lock:
                if self._compose_runner.requires_docker:
                    ensure_docker_or_raise()
                state = self._status_compose(
                    timeout=max(0.0, deadline - time.monotonic()),
                    ps_step_id=(step_ids or {}).get("postgres.ensure.status.ps"),
                    health_step_id=(step_ids or {}).get("postgres.ensure.status.health"),
                )
                if state is PostgresClusterState.HEALTHY:
                    # A healthy target without a claim is supported legacy
                    # Compose. An existing claim must still be re-inspected;
                    # declarative mode alone is never ownership evidence.
                    existing = self._cluster_claim()
                    if existing is None:
                        return
                    inspected = self._inspect_cluster_volume(
                        existing,
                        timeout=max(0.0, deadline - time.monotonic()),
                        step_id=(step_ids or {}).get("postgres.ensure.identity.volume"),
                        container_step_id=(step_ids or {}).get(
                            "postgres.ensure.identity.container"
                        ),
                    )
                    if inspected is False:
                        raise PostgresClusterError(
                            "managed postgres volume identity or attachment inspection failed"
                        )
                    if existing.state == "pending" and inspected is True:
                        self._activate_cluster_claim(existing)
                    return
                claim = self._ensure_pending_cluster_claim()
                remaining = max(0.0, deadline - time.monotonic())
                # Validate the generated compose document before resolving the
                # image digest.  Syntax/configuration failures are local and
                # deterministic; they must not lose the entire lifecycle
                # budget to the two image probes first.
                approved = self._approved_image_digest()
                self._ensure_artifacts(
                    approved,
                    timeout=remaining,
                    temporary_path=temporary_path,
                    step_id=(step_ids or {}).get("postgres.ensure.config"),
                    cluster_id=str(claim.cluster_id),
                    publish=False,
                )
                image = self._require_trusted_image(
                    max(0.0, deadline - time.monotonic()),
                    pull_step_id=(step_ids or {}).get("postgres.ensure.image.pull"),
                    inspect_step_id=(step_ids or {}).get("postgres.ensure.image.inspect"),
                )
                self._ensure_artifacts(
                    image,
                    cluster_id=str(claim.cluster_id),
                    validate=False,
                )
                if state is PostgresClusterState.UNHEALTHY:
                    raise PostgresClusterUnhealthyError(
                        f"compose postgres cluster unhealthy at {self.endpoint} "
                        f"(mode={self._mode}, state={state.value})"
                    )
                compose_up(
                    self._compose_runner,
                    self._compose_file(),
                    compose_project_name(self._project_id),
                    timeout=max(0.0, deadline - time.monotonic()),
                    step_id=(step_ids or {}).get("postgres.ensure.up"),
                )
                # ``compose up --wait`` owns the bounded readiness wait.  The
                # final pair is consequently a single captured observation;
                # polling it again would consume the same immutable step IDs a
                # second time and make the strict ledger report a duplicate.
                current = self._status_compose(
                    timeout=max(0.0, deadline - time.monotonic()),
                    ps_step_id=(step_ids or {}).get("postgres.ensure.final.ps"),
                    health_step_id=(step_ids or {}).get("postgres.ensure.final.health"),
                )
                if current is PostgresClusterState.HEALTHY:
                    inspected = self._inspect_cluster_volume(
                        claim,
                        timeout=max(0.0, deadline - time.monotonic()),
                        step_id=(step_ids or {}).get("postgres.ensure.identity.volume"),
                        container_step_id=(step_ids or {}).get(
                            "postgres.ensure.identity.container"
                        ),
                    )
                    if inspected is False:
                        raise PostgresClusterError(
                            "managed postgres volume identity or attachment inspection failed"
                        )
                    if inspected is True:
                        self._activate_cluster_claim(claim)
                    return
                if current is PostgresClusterState.UNHEALTHY:
                    raise PostgresClusterUnhealthyError(
                        f"compose postgres cluster unhealthy at {self.endpoint} "
                        f"(mode={self._mode}, state={current.value})"
                    )
                raise PostgresClusterTimeoutError(timeout)
        except LockConflictError as exc:
            raise PostgresClusterTimeoutError(timeout) from exc

    def _inspect_cluster_volume(
        self,
        claim: PostgresClusterClaim,
        *,
        timeout: float | None,
        step_id: str | None,
        container_step_id: str | None,
    ) -> bool | None:
        """Return exact inspection, or ``None`` for non-Docker test runners."""
        attachment = getattr(self._compose_runner, "inspect_cluster_attachment", None)
        if callable(attachment):
            return bool(
                attachment(
                    self.compose_project_name,
                    compose_volume_name(self._project_id),
                    str(claim.cluster_id),
                    self._project_id,
                )
            )
        custom = getattr(self._compose_runner, "inspect_volume_identity", None)
        if callable(custom):
            return bool(
                custom(
                    compose_volume_name(self._project_id),
                    str(claim.cluster_id),
                    self._project_id,
                )
            )
        if not isinstance(self._compose_runner, SubprocessComposeRunner):
            return None
        volume_ok = inspect_volume_identity(
            self._compose_runner,
            compose_volume_name(self._project_id),
            str(claim.cluster_id),
            project_id=self._project_id,
            timeout=timeout,
            step_id=step_id,
        )
        if not volume_ok:
            return False
        return inspect_container_identity(
            self._compose_runner,
            f"{self.compose_project_name}-postgres-1",
            str(claim.cluster_id),
            project_id=self._project_id,
            volume_name=compose_volume_name(self._project_id),
            timeout=timeout,
            step_id=container_step_id,
        )

    def stop(self, timeout: float = _DEFAULT_STOP_TIMEOUT) -> None:
        return self.stop_command(timeout).run()

    def stop_command(
        self, timeout: float = _DEFAULT_STOP_TIMEOUT, *, executor: ProcessExecutor | None = None
    ) -> Command[None]:
        from odoo_instance_sdk.execution import ExecutionPlan
        from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep, SubprocessExecutor

        compose_file = self._compose_file()
        prefix = (
            "docker",
            "compose",
            "--project-name",
            self.compose_project_name,
            "-f",
            str(compose_file),
        )
        if self._mode == "external":
            steps: tuple[PreparedStep | PreparedAction, ...] = (
                PreparedAction(
                    step_id="postgres.stop.external",
                    action="reject-external-stop",
                    description="Reject stopping an externally managed PostgreSQL cluster",
                    read_only=True,
                ),
            )
        elif not compose_file.is_file():
            steps = (
                PreparedAction(
                    step_id="postgres.stop.missing",
                    action="stop-noop",
                    description="No managed PostgreSQL compose file exists",
                    read_only=True,
                ),
            )
        else:
            steps = (
                PreparedStep(
                    step_id="postgres.stop.status.ps",
                    argv=(*prefix, "ps", "--format", "json"),
                    cwd=str(compose_file.parent),
                    timeout=timeout,
                    read_only=True,
                ),
                PreparedStep(
                    step_id="postgres.stop.status.health",
                    argv=(
                        *prefix,
                        "exec",
                        "-T",
                        "postgres",
                        "pg_isready",
                        "-U",
                        self._user or "",
                        "-d",
                        "postgres",
                    ),
                    cwd=str(compose_file.parent),
                    timeout=timeout,
                    read_only=True,
                ),
                PreparedStep(
                    step_id="postgres.stop",
                    argv=(*prefix, "stop", "--timeout", str(int(max(1, timeout)))),
                    cwd=str(compose_file.parent),
                    mutating=True,
                    timeout=timeout,
                ),
            )

        def run(context: RunContext[None]) -> None:
            if self._mode == "external":
                context.action("postgres.stop.external")
                self._stop_impl(
                    timeout,
                    command_timeout=int(max(1, timeout)),
                    status_ps_step_id="postgres.stop.status.ps",
                    status_health_step_id="postgres.stop.status.health",
                    stop_step_id="postgres.stop",
                )
            elif not compose_file.is_file():
                context.action("postgres.stop.missing")
            else:
                self._stop_impl(
                    timeout,
                    command_timeout=int(max(1, timeout)),
                    status_ps_step_id="postgres.stop.status.ps",
                    status_health_step_id="postgres.stop.status.health",
                    stop_step_id="postgres.stop",
                )
            self._account_optional_steps(context, steps)

        plan = ExecutionPlan(steps=tuple(step.public_projection() for step in steps))
        return self._make_command(plan, run, steps, executor=executor or SubprocessExecutor())

    def _stop_impl(
        self,
        timeout: float = _DEFAULT_STOP_TIMEOUT,
        *,
        command_timeout: int | None = None,
        status_ps_step_id: str | None = None,
        status_health_step_id: str | None = None,
        stop_step_id: str | None = None,
    ) -> None:
        if self._mode == "external":
            raise PostgresClusterNotOwnedError(
                f"cannot stop externally owned postgres cluster at {self.endpoint}"
            )
        deadline = time.monotonic() + max(0.1, timeout)
        try:
            lock = exclusive_lock_until(postgres_cluster_lock_path(self._project_id), deadline)
            with lock:
                compose_file = self._compose_file()
                if not compose_file.is_file():
                    return
                if self._compose_runner.requires_docker:
                    ensure_docker_or_raise()
                if (
                    self._status_compose(
                        timeout=max(0.0, deadline - time.monotonic()),
                        ps_step_id=status_ps_step_id,
                        health_step_id=status_health_step_id,
                    )
                    is PostgresClusterState.STOPPED
                ):
                    return
                compose_stop(
                    self._compose_runner,
                    compose_file,
                    compose_project_name(self._project_id),
                    timeout=max(0.0, deadline - time.monotonic()),
                    command_timeout=command_timeout,
                    step_id=stop_step_id,
                )
        except LockConflictError as exc:
            raise PostgresClusterTimeoutError(timeout) from exc

    def to_diagnostic_dict(self) -> Mapping[str, JsonValue]:
        """Read-only redacted diagnostic payload (no secrets)."""
        return {
            "mode": self._mode,
            "owned": self.owned,
            "endpoint": self.endpoint,
            "project_id": self._project_id,
            "image": self._image if self.owned else None,
            "user": self._user if self.owned else None,
        }

    def resource_snapshot(self) -> ClusterResourceSnapshot | None:
        """Read-only container identity + resource metrics. External → None.

        No lifecycle lock, no start/stop. Compose clusters resolve the
        container via `docker compose ps` then batch `docker inspect`/`docker
        stats --no-stream` through the internal cache helper.
        """
        return self.resource_snapshot_command().run()

    def resource_snapshot_command(  # noqa: C901
        self, *, executor: ProcessExecutor | None = None
    ) -> Command[ClusterResourceSnapshot | None]:
        from odoo_instance_sdk.execution import ExecutionPlan
        from odoo_instance_sdk.internal.proc import (
            PreparedAction,
            PreparedStep,
            ProcessExecutionError,
            ProcessResult,
            SubprocessExecutor,
        )

        process_executor = executor or SubprocessExecutor()

        step = PreparedAction(
            step_id="postgres.resource.snapshot",
            action="collect-resource-snapshot",
            description="Collect PostgreSQL container identity and resource metrics",
            read_only=True,
        )

        steps: list[PreparedStep | PreparedAction] = [step]
        captured_container_id: str | None = None
        observations: tuple[dict[str, JsonValue], ...] = ()
        if self._mode == "compose" and self._compose_file().is_file():
            from odoo_instance_sdk.internal.cluster_resources import container_id_from_rows

            prefix = (
                "docker",
                "compose",
                "--project-name",
                self.compose_project_name,
                "-f",
                str(self._compose_file()),
            )
            resource_ps = (*prefix, "ps", "--format", "json")
            planning_step = PreparedStep(
                step_id="postgres.resource.plan.ps",
                argv=resource_ps,
                cwd=str(self._compose_file().parent),
                timeout=5.0,
                read_only=True,
            )

            def planning_observation(
                result: ProcessResult | None = None, *, diagnostic: str | None = None
            ) -> dict[str, JsonValue]:
                from odoo_instance_sdk.internal.proc.redaction import redacted_projection

                value: dict[str, JsonValue] = {
                    "step_id": planning_step.step_id,
                    "process": cast(
                        "JsonValue", msgspec.to_builtins(planning_step.public_projection())
                    ),
                    "read_only": True,
                    "executed_during_planning": True,
                }
                if result is not None:
                    stdout = (
                        result.stdout
                        if isinstance(result.stdout, str)
                        else (
                            result.stdout.decode(errors="replace")
                            if isinstance(result.stdout, bytes)
                            else ""
                        )
                    )
                    value["result"] = {
                        "returncode": result.returncode,
                        "stdout": cast("str", redacted_projection(stdout, field="stdout")),
                    }
                if diagnostic is not None:
                    value["diagnostic"] = diagnostic
                return value

            if not self._compose_runner.requires_docker or docker_available():
                try:
                    planning_result = process_executor.execute(planning_step)
                except ProcessExecutionError as error:
                    observations = (planning_observation(diagnostic=error.__class__.__name__),)
                else:
                    if isinstance(planning_result, ProcessResult):
                        stdout = (
                            planning_result.stdout
                            if isinstance(planning_result.stdout, str)
                            else ""
                        )
                        rows: list[dict[str, JsonValue]] = []
                        for line in stdout.splitlines():
                            try:
                                parsed = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if isinstance(parsed, dict):
                                rows.append(parsed)
                        captured_container_id = container_id_from_rows(rows, "postgres")
                        observation = planning_observation(planning_result)
                        observation["container_found"] = captured_container_id is not None
                        observations = (observation,)
                    else:
                        observations = (
                            planning_observation(diagnostic="planning executor returned no result"),
                        )
            else:
                observations = (planning_observation(diagnostic="docker unavailable"),)
            steps.extend(
                (
                    PreparedStep(
                        step_id="postgres.resource.status.ps",
                        argv=resource_ps,
                        cwd=str(self._compose_file().parent),
                        timeout=5.0,
                        read_only=True,
                    ),
                    PreparedStep(
                        step_id="postgres.resource.status.health",
                        argv=(
                            *prefix,
                            "exec",
                            "-T",
                            "postgres",
                            "pg_isready",
                            "-U",
                            self._user or "",
                            "-d",
                            "postgres",
                        ),
                        cwd=str(self._compose_file().parent),
                        timeout=5.0,
                        read_only=True,
                    ),
                )
            )
            if captured_container_id is not None:
                steps.extend(
                    (
                        PreparedStep(
                            step_id="postgres.resource.inspect",
                            argv=("docker", "inspect", "--format", "json", captured_container_id),
                            timeout=_RESOURCE_SNAPSHOT_TIMEOUT,
                            read_only=True,
                        ),
                        PreparedStep(
                            step_id="postgres.resource.stats",
                            argv=(
                                "docker",
                                "stats",
                                "--no-stream",
                                "--format",
                                "json",
                                captured_container_id,
                            ),
                            timeout=_RESOURCE_SNAPSHOT_TIMEOUT,
                            read_only=True,
                        ),
                    )
                )
            steps.append(
                PreparedStep(
                    step_id="postgres.resource.volume-df",
                    argv=("docker", "system", "df", "-v", "--format", "{{json .}}"),
                    timeout=_RESOURCE_SNAPSHOT_TIMEOUT,
                    read_only=True,
                )
            )

        captured_steps = tuple(steps)

        def run(
            context: RunContext[ClusterResourceSnapshot | None],
        ) -> ClusterResourceSnapshot | None:
            context.action(step.step_id)
            if self._mode == "external":
                self._account_optional_steps(context, captured_steps)
                return None
            state = self._status_compose(
                ps_step_id="postgres.resource.status.ps",
                health_step_id="postgres.resource.status.health",
            )
            result = self._resource_snapshot_impl(
                state=state,
                container_id=captured_container_id,
                step_ids={
                    "inspect": "postgres.resource.inspect",
                    "stats": "postgres.resource.stats",
                    "volume-df": "postgres.resource.volume-df",
                },
            )
            self._account_legacy_steps(context, captured_steps)
            self._account_optional_steps(context, captured_steps)
            return result

        plan = ExecutionPlan(
            steps=tuple(item.public_projection() for item in captured_steps),
            observations=tuple(observation for observation in observations),
        )
        return self._make_command(plan, run, captured_steps, executor=process_executor)

    def _make_command(
        self,
        plan: ExecutionPlan,
        callback: Callable[[RunContext[T]], T],
        steps: Sequence[PreparedStep | PreparedAction],
        *,
        executor: ProcessExecutor,
    ) -> Command[T]:
        """Bind a lifecycle callback to one strict shared-process snapshot.

        The legacy ``ComposeRunner`` remains supported for callers that inject a
        runner in compatibility tests.  Real subprocess runners use strict
        matching, so a callback cannot silently replace an inspected process
        step with another child invocation.
        """
        from odoo_instance_sdk.execution import Command
        from odoo_instance_sdk.internal.proc import prepared_command

        prepared = prepared_command(
            callback,
            steps,
            executor=executor,
        )
        return Command.from_prepared(plan, prepared)

    def _account_legacy_steps(
        self, context: RunContext[T], steps: Sequence[PreparedStep | PreparedAction]
    ) -> None:
        """Account for steps owned by an injected legacy runner.

        A real ``SubprocessComposeRunner`` consumes each step through the active
        context.  An injected test/compatibility runner owns its own execution;
        only that path may explicitly account for steps it already performed.
        """
        if isinstance(self._compose_runner, SubprocessComposeRunner):
            return
        for step in steps:
            if not context.consumed(step.step_id):
                context.skip(step.step_id)

    @staticmethod
    def _account_optional_steps(
        context: RunContext[T], steps: Sequence[PreparedStep | PreparedAction]
    ) -> None:
        """Account for a declared branch that the lifecycle made unnecessary."""
        for step in steps:
            # A dependency preflight can run inside a different strict
            # command (for example an Odoo foreground command).  Its private
            # manifest is not part of that outer command, so it must not try
            # to consume or skip steps that were never captured there.
            if context.planned(step.step_id) and not context.consumed(step.step_id):
                context.skip(step.step_id)

    def _resource_snapshot_impl(
        self,
        *,
        state: PostgresClusterState,
        container_id: str | None,
        step_ids: Mapping[str, str],
    ) -> ClusterResourceSnapshot:
        from odoo_instance_sdk.internal.cluster_resources import cluster_resource_snapshot

        return cluster_resource_snapshot(
            compose_file=self._compose_file(),
            compose_project_name=self.compose_project_name,
            service="postgres",
            runner=self._compose_runner,
            state=state,
            container_id=container_id,
            # The command's planning observation is the only allowed
            # ``compose ps`` probe.  A missing planning result is a bounded
            # unavailable observation, never permission to launch a second
            # unplanned resolver during execution.
            resolve_container=False,
            step_ids=step_ids,
            timeout=_RESOURCE_SNAPSHOT_TIMEOUT,
        )
