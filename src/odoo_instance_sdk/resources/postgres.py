from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeVar, cast

import msgspec

from odoo_instance_sdk.exceptions import (
    LockConflictError,
    PostgresClusterError,
    PostgresClusterNotOwnedError,
    PostgresClusterTimeoutError,
    PostgresClusterUnhealthyError,
    PostgresClusterUnreachableError,
    PostgresImageNotTrustedError,
)
from odoo_instance_sdk.internal.address import AddressState, probe_address
from odoo_instance_sdk.internal.git_worktree import (
    rev_parse_git_common_dir,
    rev_parse_toplevel,
)
from odoo_instance_sdk.internal.locks import exclusive_lock_until, postgres_cluster_lock_path
from odoo_instance_sdk.internal.paths import get_project_postgres_dir
from odoo_instance_sdk.internal.postgres_compose import (
    ComposeRunner,
    SubprocessComposeRunner,
    compose_project_name,
    compose_stop,
    compose_up,
    derive_state,
    docker_available,
    ensure_docker_or_raise,
    ensure_password_file,
    is_oci_digest,
    render_compose_yaml,
    resolve_image_digest,
    write_compose_file_atomic,
)
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.models import ClusterResourceSnapshot, PostgresClusterState, StartConfig
from odoo_instance_sdk.project import ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command, ExecutionPlan, JsonValue, PlanObservation
    from odoo_instance_sdk.internal.pg.server import ServerSummary
    from odoo_instance_sdk.internal.proc import (
        DeadlineProcessExecutor,
        PreparedAction,
        PreparedStep,
        ProcessExecutor,
        RunContext,
    )
    from odoo_instance_sdk.models import ServerUnavailabilityReason
T = TypeVar("T")

_DEFAULT_TIMEOUT = 60.0
_DEFAULT_STOP_TIMEOUT = 30.0
_RESOURCE_SNAPSHOT_TIMEOUT = 5.0


def _resolve_project_id(repository_root: Path) -> str:
    """Return Git identity; retain the documented non-Git project fallback."""
    try:
        toplevel = rev_parse_toplevel(repository_root)
        return repo_key(toplevel, rev_parse_git_common_dir(toplevel))
    except Exception:
        resolved = repository_root.resolve()
        return f"{resolved.name or 'repo'}_{hashlib.sha256(str(resolved).encode()).hexdigest()[:8]}"


def _resolve_endpoint_external(source_config: Path | None) -> tuple[str, int]:
    if source_config is None:
        raise PostgresClusterError(
            "external postgres mode requires source_config in manifest; rerun init --config"
        )
    start_cfg = StartConfig.from_odoo_config(source_config)
    host = start_cfg.db_host or "127.0.0.1"
    port = start_cfg.db_port or 5432
    return host, port


@dataclass(frozen=True, slots=True, kw_only=True)
class PostgresCluster:
    """Project-level PostgreSQL cluster: ownership, status, readiness, managed lifecycle.

    This is the single operational abstraction — no Resource, no factory, no
    ``client.postgres`` facade. ``ensure_running()`` is the required idempotent
    operation; ``start()`` deliberately does not exist.
    """

    _repository_root: Path
    _project_id: str
    _mode: Literal["external", "compose"]
    _endpoint_host: str
    _endpoint_port: int
    _image: str | None = None
    _user: str | None = None
    _compose_runner: ComposeRunner = field(default_factory=SubprocessComposeRunner)

    @classmethod
    def from_project(
        cls,
        project_path: str | Path,
        *,
        compose_runner: ComposeRunner | None = None,
    ) -> PostgresCluster:
        root = Path(project_path).resolve()
        cfg = ProjectConfig.load(root)
        return cls._from_config(cfg, repository_root=root, compose_runner=compose_runner)

    @classmethod
    def _from_config(
        cls,
        cfg: ProjectConfig,
        *,
        repository_root: Path,
        compose_runner: ComposeRunner | None,
        project_id: str | None = None,
    ) -> PostgresCluster:
        project_id = project_id or _resolve_project_id(repository_root)
        postgres = cfg.postgres
        mode: Literal["external", "compose"] = (
            "compose" if postgres is not None and postgres.mode == "compose" else "external"
        )
        if mode == "compose":
            if postgres is None or postgres.image is None:
                raise PostgresClusterError(
                    "compose postgres mode requires image in manifest; rerun init --postgres-image"
                )
            if postgres.port is None:
                raise PostgresClusterError(
                    "compose postgres mode requires port in manifest; rerun init --postgres-port"
                )
            host = "127.0.0.1"
            port = postgres.port
            image = postgres.image
            user = postgres.user or "odoo"
            return cls(
                _repository_root=repository_root,
                _project_id=project_id,
                _mode=mode,
                _endpoint_host=host,
                _endpoint_port=port,
                _image=image,
                _user=user,
                _compose_runner=compose_runner or SubprocessComposeRunner(),
            )
        host, port = _resolve_endpoint_external(cfg.source_config)
        return cls(
            _repository_root=repository_root,
            _project_id=project_id,
            _mode="external",
            _endpoint_host=host,
            _endpoint_port=port,
            _compose_runner=compose_runner or SubprocessComposeRunner(),
        )

    @property
    def mode(self) -> Literal["external", "compose"]:
        return self._mode

    @property
    def owned(self) -> bool:
        return self._mode == "compose"

    @property
    def endpoint(self) -> str:
        host = self._endpoint_host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"{host}:{self._endpoint_port}"

    @property
    def endpoint_host(self) -> str:
        return self._endpoint_host

    @property
    def endpoint_port(self) -> int:
        return self._endpoint_port

    @property
    def compose_file(self) -> Path:
        """Managed compose artifact path, exposed for operational cleanup tooling."""
        return self._compose_file()

    @property
    def compose_project_name(self) -> str:
        return compose_project_name(self._project_id)

    @property
    def compose_runner(self) -> ComposeRunner:
        """Read-only command boundary used by monitoring collectors."""
        return self._compose_runner

    @property
    def password_file(self) -> Path:
        return self.compose_file.parent / "postgres-password"

    def __repr__(self) -> str:
        return (
            f"PostgresCluster(mode={self._mode!r}, owned={self.owned!r}, "
            f"endpoint={self.endpoint!r})"
        )

    def _compose_dir(self) -> Path:
        return get_project_postgres_dir(self._project_id)

    def _compose_file(self) -> Path:
        return self._compose_dir() / "compose.yaml"

    def _password_file(self) -> Path:
        return self._compose_dir() / "postgres-password"

    def _trust_file(self) -> Path:
        """User-owned approval store; it is intentionally outside the repository."""
        return self._compose_dir().parent / "approved-images.json"

    def _resolve_image_digest(
        self,
        timeout: float | None = None,
        *,
        pull_step_id: str | None = None,
        inspect_step_id: str | None = None,
    ) -> str:
        assert self._image is not None
        return resolve_image_digest(
            self._compose_runner,
            self._image,
            timeout=timeout,
            pull_step_id=pull_step_id,
            inspect_step_id=inspect_step_id,
        )

    def resolve_image_digest(self, timeout: float | None = None) -> str:
        """Resolve the manifest image to the OCI RepoDigest to be explicitly approved."""
        return self.resolve_image_digest_command(timeout).run()

    def resolve_image_digest_command(
        self, timeout: float | None = None, *, executor: ProcessExecutor | None = None
    ) -> Command[str]:
        if not self.owned:
            raise PostgresClusterNotOwnedError("external postgres clusters have no image digest")
        assert self._image is not None
        from odoo_instance_sdk.execution import ExecutionPlan
        from odoo_instance_sdk.internal.proc import PreparedStep, SubprocessExecutor

        steps = (
            PreparedStep(
                step_id="postgres.image.pull",
                argv=("docker", "image", "pull", self._image),
                timeout=timeout,
                read_only=True,
            ),
            PreparedStep(
                step_id="postgres.image.inspect",
                argv=(
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{index .RepoDigests 0}}",
                    self._image,
                ),
                timeout=timeout,
                read_only=True,
            ),
        )

        def run(context: RunContext[str]) -> str:
            result = self._resolve_image_digest(
                timeout,
                pull_step_id="postgres.image.pull",
                inspect_step_id="postgres.image.inspect",
            )
            self._account_legacy_steps(context, steps)
            return result

        plan = ExecutionPlan(steps=tuple(step.public_projection() for step in steps))
        return self._make_command(plan, run, steps, executor=executor or SubprocessExecutor())

    def approve_image(self, image_digest: str, *, timeout: float | None = None) -> None:
        """Approve the exact OCI digest currently resolved for the manifest reference."""
        return self.approve_image_command(image_digest, timeout=timeout).run()

    def approve_image_command(
        self,
        image_digest: str,
        *,
        timeout: float | None = None,
        executor: ProcessExecutor | None = None,
    ) -> Command[None]:
        if not self.owned:
            raise PostgresClusterNotOwnedError(
                "external postgres clusters have no image to approve"
            )
        assert self._image is not None
        from odoo_instance_sdk.execution import ExecutionPlan
        from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep, SubprocessExecutor

        steps = (
            PreparedStep(
                step_id="postgres.image.pull",
                argv=("docker", "image", "pull", self._image),
                timeout=timeout,
                read_only=True,
            ),
            PreparedStep(
                step_id="postgres.image.inspect",
                argv=(
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{index .RepoDigests 0}}",
                    self._image,
                ),
                timeout=timeout,
                read_only=True,
            ),
            PreparedAction(
                step_id="postgres.image.approve",
                action="write-trust-record",
                description="Persist the approved PostgreSQL image digest",
                mutating=True,
            ),
        )

        def run(context: RunContext[None]) -> None:
            resolved = self._resolve_image_digest(
                timeout,
                pull_step_id="postgres.image.pull",
                inspect_step_id="postgres.image.inspect",
            )
            if image_digest != resolved:
                raise PostgresImageNotTrustedError(
                    "image digest does not match the resolved OCI RepoDigest"
                )
            context.action("postgres.image.approve")
            self._approve_image(resolved)
            self._account_legacy_steps(context, steps)

        plan = ExecutionPlan(steps=tuple(step.public_projection() for step in steps))
        return self._make_command(plan, run, steps, executor=executor or SubprocessExecutor())

    def _approve_image(self, resolved: str) -> None:
        trust_file = self._trust_file()
        trust_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            current = json.loads(trust_file.read_text(encoding="utf-8"))
            images = current.get("images", {})
        except (OSError, ValueError, AttributeError):
            images = {}
        if not isinstance(images, dict):
            images = {}
        images[self._image] = resolved
        payload = {"version": 1, "images": images}
        fd, tmp_name = tempfile.mkstemp(dir=trust_file.parent, prefix=".trust-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, sort_keys=True)
                stream.write("\n")
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, trust_file)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def _require_trusted_image(
        self,
        timeout: float,
        *,
        pull_step_id: str | None = None,
        inspect_step_id: str | None = None,
    ) -> str:
        trust_file = self._trust_file()
        try:
            data = json.loads(trust_file.read_text(encoding="utf-8"))
            approved = data["images"]
        except (OSError, ValueError, KeyError, TypeError):
            approved = {}
        expected = approved.get(self._image) if isinstance(approved, dict) else None
        # Do not permit a repository-controlled selector to trigger a pull before
        # an already persisted, syntactically immutable approval is established.
        if not isinstance(expected, str) or not is_oci_digest(expected):
            raise PostgresImageNotTrustedError(
                "postgres image digest is not approved for this user; run 'odcli postgres approve-image --image-digest <resolved-digest>'"
            )
        resolved = self._resolve_image_digest(
            timeout, pull_step_id=pull_step_id, inspect_step_id=inspect_step_id
        )
        if expected != resolved:
            raise PostgresImageNotTrustedError(
                "postgres image digest changed since explicit approval"
            )
        return resolved

    def _ensure_artifacts(
        self,
        image: str,
        *,
        timeout: float | None = None,
        temporary_path: Path | None = None,
        step_id: str | None = None,
    ) -> None:
        """Lazily create compose artifacts (idempotent)."""
        if not self.owned:
            return
        compose_dir = self._compose_dir()
        compose_dir.mkdir(parents=True, exist_ok=True)
        password_path = self._password_file()
        ensure_password_file(password_path)
        assert self._user is not None
        content = render_compose_yaml(
            image=image,
            port=self._endpoint_port,
            user=self._user,
            project_id=self._project_id,
            password_file=str(password_path),
        )
        write_compose_file_atomic(
            self._compose_file(),
            content,
            runner=self._compose_runner,
            project_name=compose_project_name(self._project_id),
            timeout=timeout,
            temporary_path=temporary_path,
            step_id=step_id,
        )

    def status(self) -> PostgresClusterState:
        return self.status_command().run()

    def _server_summary_plan(
        self, executor: DeadlineProcessExecutor | None
    ) -> tuple[tuple[PreparedStep, ...], ServerUnavailabilityReason | None]:
        if not isinstance(self._compose_runner, SubprocessComposeRunner) and executor is None:
            return (), None
        from odoo_instance_sdk.internal.pg.server import build_server_summary_plan

        plan = build_server_summary_plan(self)
        return plan.steps, plan.reason

    def status_command(
        self,
        *,
        executor: DeadlineProcessExecutor | None = None,
        server_summary_sink: Callable[[ServerSummary], None] | None = None,
    ) -> Command[PostgresClusterState]:
        from odoo_instance_sdk.execution import ExecutionPlan
        from odoo_instance_sdk.internal.proc import (
            PreparedAction,
            PreparedStep,
            SubprocessExecutor,
            require_deadline_executor,
        )

        steps: tuple[PreparedStep | PreparedAction, ...]
        if self._mode == "external":
            action = PreparedAction(
                step_id="postgres.status.external",
                action="probe-address",
                description="Probe the externally managed PostgreSQL endpoint",
                read_only=True,
            )
            steps = (action,)
        elif not self._compose_file().is_file() or (
            self._compose_runner.requires_docker and not docker_available()
        ):
            action = PreparedAction(
                step_id="postgres.status.unavailable",
                action="status-unavailable",
                description="Determine PostgreSQL availability without launching a child",
                read_only=True,
            )
            steps = (action,)
        else:
            compose_file = self._compose_file()
            prefix = (
                "docker",
                "compose",
                "--project-name",
                self.compose_project_name,
                "-f",
                str(compose_file),
            )
            steps = (
                PreparedStep(
                    step_id="postgres.status.ps",
                    argv=(*prefix, "ps", "--format", "json"),
                    cwd=str(compose_file.parent),
                    read_only=True,
                    text=True,
                ),
                PreparedStep(
                    step_id="postgres.status.health",
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
                    read_only=True,
                    text=True,
                ),
            )

        server_steps: tuple[PreparedStep, ...] = ()
        server_steps, server_summary_eligibility = self._server_summary_plan(executor)
        process_executor = executor or SubprocessExecutor()
        if server_steps:
            require_deadline_executor(process_executor)
        all_steps: tuple[PreparedStep | PreparedAction, ...] = (*steps, *server_steps)

        unavailable_state = (
            PostgresClusterState.UNKNOWN
            if self._compose_runner.requires_docker and not docker_available()
            else PostgresClusterState.STOPPED
        )

        def run(context: RunContext[PostgresClusterState]) -> PostgresClusterState:
            if self._mode == "external":
                context.action("postgres.status.external")
                state = self._status_external()
            elif len(steps) == 1:
                context.action(steps[0].step_id)
                state = unavailable_state
            else:
                state = self._status_compose(
                    health_step_id="postgres.status.health",
                    ps_step_id="postgres.status.ps",
                )
            if state is PostgresClusterState.HEALTHY:
                from odoo_instance_sdk.internal.pg.server import collect_server_summary

                summary = collect_server_summary(
                    context=context,
                    steps=server_steps,
                    eligibility=server_summary_eligibility,
                )
                if server_summary_sink is not None:
                    server_summary_sink(summary)
            else:
                self._account_optional_steps(context, server_steps)
            self._account_legacy_steps(context, all_steps)
            return state

        observations: tuple[PlanObservation, ...] = ()
        if server_steps:
            from odoo_instance_sdk.internal.pg.server import server_summary_deadline_observation

            observations = (server_summary_deadline_observation(server_steps),)
        plan = ExecutionPlan(
            steps=tuple(step.public_projection() for step in all_steps),
            observations=observations,
        )
        return self._make_command(plan, run, all_steps, executor=process_executor)

    def _status_impl(self) -> PostgresClusterState:
        if self._mode == "external":
            return self._status_external()
        return self._status_compose()

    def _status_external(self) -> PostgresClusterState:
        state = probe_address(self._endpoint_host, self._endpoint_port)
        if state is AddressState.FREE:
            return PostgresClusterState.UNREACHABLE
        if state is AddressState.OCCUPIED:
            return PostgresClusterState.HEALTHY
        return PostgresClusterState.UNREACHABLE

    def _status_compose(
        self,
        *,
        timeout: float | None = None,
        health_step_id: str | None = None,
        ps_step_id: str | None = None,
    ) -> PostgresClusterState:
        if self._compose_runner.requires_docker and not docker_available():
            return PostgresClusterState.UNKNOWN
        compose_file = self._compose_file()
        if not compose_file.is_file():
            return PostgresClusterState.STOPPED
        assert self._user is not None
        return derive_state(
            self._compose_runner,
            compose_file,
            compose_project_name(self._project_id),
            user=self._user,
            timeout=timeout,
            health_step_id=health_step_id,
            ps_step_id=ps_step_id,
        )

    def ensure_running(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        # Preparation and lifecycle commands may already own a strict ledger.
        # Re-entering ``ensure_running_command`` here would create a second
        # executor and make the inspected outer plan decorative.
        from odoo_instance_sdk.internal.proc import active_context

        context = active_context()
        if context is not None:
            temporary_path = None
            if self._mode != "external" and context.planned("postgres.ensure.config"):
                config_step = context.prepared("postgres.ensure.config")
                try:
                    config_index = len(config_step.argv) - 1 - config_step.argv[::-1].index("-f")
                    temporary_path = Path(config_step.argv[config_index + 1])
                except (ValueError, IndexError):
                    raise PostgresClusterError(
                        "captured postgres ensure config step has no temporary compose path"
                    ) from None
            step_ids = {
                step_id: step_id
                for step_id in (
                    "postgres.ensure.image.pull",
                    "postgres.ensure.image.inspect",
                    "postgres.ensure.status.ps",
                    "postgres.ensure.status.health",
                    "postgres.ensure.config",
                    "postgres.ensure.up",
                    "postgres.ensure.final.ps",
                    "postgres.ensure.final.health",
                )
                if context.planned(step_id)
            }
            self._ensure_running_impl(timeout, temporary_path=temporary_path, step_ids=step_ids)
            if self._mode != "external":
                self._account_optional_steps(
                    context, self._ensure_running_steps(timeout, temporary_path=temporary_path)
                )
            return None
        return self.ensure_running_command(timeout).run()

    def _ensure_running_steps(
        self,
        timeout: float,
        *,
        temporary_path: Path | None = None,
    ) -> tuple[PreparedStep | PreparedAction, ...]:
        """Return the exact process/action manifest used by ensure-running."""
        from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep

        if self._mode == "external":
            return (
                PreparedAction(
                    step_id="postgres.ensure.external",
                    action="ensure-external",
                    description="Verify the externally managed PostgreSQL endpoint",
                    read_only=True,
                ),
            )
        compose_file = self._compose_file()
        prefix = (
            "docker",
            "compose",
            "--project-name",
            self.compose_project_name,
            "-f",
            str(compose_file),
        )
        config_path = temporary_path or (
            compose_file.parent / f".compose-{uuid.uuid4().hex}.yaml.tmp"
        )
        return (
            PreparedStep(
                step_id="postgres.ensure.image.pull",
                argv=("docker", "image", "pull", self._image or ""),
                timeout=timeout,
                read_only=True,
            ),
            PreparedStep(
                step_id="postgres.ensure.image.inspect",
                argv=(
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{index .RepoDigests 0}}",
                    self._image or "",
                ),
                timeout=timeout,
                read_only=True,
            ),
            PreparedStep(
                step_id="postgres.ensure.status.ps",
                argv=(*prefix, "ps", "--format", "json"),
                cwd=str(compose_file.parent),
                timeout=timeout,
                read_only=True,
            ),
            PreparedStep(
                step_id="postgres.ensure.status.health",
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
                step_id="postgres.ensure.config",
                argv=(
                    *prefix[:-2],
                    "-f",
                    str(config_path),
                    "config",
                    "--quiet",
                ),
                cwd=str(compose_file.parent),
                timeout=timeout,
                mutating=True,
            ),
            PreparedStep(
                step_id="postgres.ensure.up",
                argv=(*prefix, "up", "--detach", "--wait"),
                cwd=str(compose_file.parent),
                timeout=timeout,
                mutating=True,
            ),
            PreparedStep(
                step_id="postgres.ensure.final.ps",
                argv=(*prefix, "ps", "--format", "json"),
                cwd=str(compose_file.parent),
                timeout=timeout,
                read_only=True,
            ),
            PreparedStep(
                step_id="postgres.ensure.final.health",
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
        )

    def ensure_running_command(
        self, timeout: float = _DEFAULT_TIMEOUT, *, executor: ProcessExecutor | None = None
    ) -> Command[None]:
        from odoo_instance_sdk.execution import ExecutionPlan
        from odoo_instance_sdk.internal.proc import SubprocessExecutor

        compose_file = self._compose_file()
        temporary_path = compose_file.parent / f".compose-{uuid.uuid4().hex}.yaml.tmp"
        steps = self._ensure_running_steps(timeout, temporary_path=temporary_path)

        def run(context: RunContext[None]) -> None:
            if self._mode == "external":
                context.action("postgres.ensure.external")
                self._ensure_running_external()
            else:
                self._ensure_running_compose(
                    timeout,
                    temporary_path=temporary_path,
                    step_ids={step.step_id: step.step_id for step in steps},
                )
            self._account_optional_steps(context, steps)

        plan = ExecutionPlan(steps=tuple(step.public_projection() for step in steps))
        return self._make_command(plan, run, steps, executor=executor or SubprocessExecutor())

    def _ensure_running_impl(
        self,
        timeout: float = _DEFAULT_TIMEOUT,
        *,
        temporary_path: Path | None = None,
        step_ids: Mapping[str, str] | None = None,
    ) -> None:
        if self._mode == "external":
            self._ensure_running_external()
            return
        self._ensure_running_compose(timeout, temporary_path=temporary_path, step_ids=step_ids)

    def _ensure_running_external(self) -> None:
        state = self._status_impl()
        if state is PostgresClusterState.HEALTHY:
            return
        raise PostgresClusterUnreachableError(
            f"external postgres cluster not reachable at {self.endpoint} "
            f"(mode={self._mode}, state={state.value})"
        )

    def _ensure_running_compose(
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
                remaining = max(0.0, deadline - time.monotonic())
                image = self._require_trusted_image(
                    remaining,
                    pull_step_id=(step_ids or {}).get("postgres.ensure.image.pull"),
                    inspect_step_id=(step_ids or {}).get("postgres.ensure.image.inspect"),
                )
                state = self._status_compose(
                    timeout=max(0.0, deadline - time.monotonic()),
                    ps_step_id=(step_ids or {}).get("postgres.ensure.status.ps"),
                    health_step_id=(step_ids or {}).get("postgres.ensure.status.health"),
                )
                # Do not rewrite secret/config artifacts on a healthy fast path.
                if state is PostgresClusterState.HEALTHY:
                    return
                self._ensure_artifacts(
                    image,
                    timeout=max(0.0, deadline - time.monotonic()),
                    temporary_path=temporary_path,
                    step_id=(step_ids or {}).get("postgres.ensure.config"),
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
                    return
                if current is PostgresClusterState.UNHEALTHY:
                    raise PostgresClusterUnhealthyError(
                        f"compose postgres cluster unhealthy at {self.endpoint} "
                        f"(mode={self._mode}, state={current.value})"
                    )
                raise PostgresClusterTimeoutError(timeout)
        except LockConflictError as exc:
            raise PostgresClusterTimeoutError(timeout) from exc

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


__all__ = ["PostgresCluster"]
