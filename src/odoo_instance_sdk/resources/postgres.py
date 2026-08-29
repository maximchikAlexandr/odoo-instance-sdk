from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

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
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import ProcessExecutor, RunContext

_DEFAULT_TIMEOUT = 60.0
_DEFAULT_STOP_TIMEOUT = 30.0


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
    ) -> PostgresCluster:
        project_id = _resolve_project_id(repository_root)
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

    def _resolve_image_digest(self, timeout: float | None = None) -> str:
        assert self._image is not None
        return resolve_image_digest(self._compose_runner, self._image, timeout=timeout)

    def resolve_image_digest(self, timeout: float | None = None) -> str:
        """Resolve the manifest image to the OCI RepoDigest to be explicitly approved."""
        return self.resolve_image_digest_command(timeout).run()

    def resolve_image_digest_command(
        self, timeout: float | None = None, *, executor: ProcessExecutor | None = None
    ) -> Command[str]:
        if not self.owned:
            raise PostgresClusterNotOwnedError("external postgres clusters have no image digest")
        assert self._image is not None
        from odoo_instance_sdk.execution import Command, ExecutionPlan
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
            result = self._resolve_image_digest(timeout)
            context.skip_remaining()
            return result

        plan = ExecutionPlan(steps=tuple(step.public_projection() for step in steps))
        return Command.create(
            plan,
            run,
            steps,
            executor=executor or SubprocessExecutor(),
            private_projection=None,
        )

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
        from odoo_instance_sdk.execution import Command, ExecutionPlan
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
            resolved = self._resolve_image_digest(timeout)
            if image_digest != resolved:
                raise PostgresImageNotTrustedError(
                    "image digest does not match the resolved OCI RepoDigest"
                )
            context.action("postgres.image.approve")
            self._approve_image(resolved)
            context.skip_remaining()

        plan = ExecutionPlan(steps=tuple(step.public_projection() for step in steps))
        return Command.create(
            plan,
            run,
            steps,
            executor=executor or SubprocessExecutor(),
        )

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

    def _require_trusted_image(self, timeout: float) -> str:
        trust_file = self._trust_file()
        try:
            data = json.loads(trust_file.read_text(encoding="utf-8"))
            approved = data["images"]
        except (OSError, ValueError, KeyError, TypeError):
            approved = {}
        expected = approved.get(self._image) if isinstance(approved, dict) else None
        # Do not permit a repository-controlled selector to trigger a pull before
        # an already persisted, syntactically immutable approval is established.
        if not is_oci_digest(expected):
            raise PostgresImageNotTrustedError(
                "postgres image digest is not approved for this user; run 'odcli postgres approve-image --image-digest <resolved-digest>'"
            )
        resolved = self._resolve_image_digest(timeout)
        if expected != resolved:
            raise PostgresImageNotTrustedError(
                "postgres image digest changed since explicit approval"
            )
        return resolved

    def _ensure_artifacts(self, image: str, *, timeout: float | None = None) -> None:
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
        )

    def status(self) -> PostgresClusterState:
        return self.status_command().run()

    def status_command(
        self, *, executor: ProcessExecutor | None = None
    ) -> Command[PostgresClusterState]:
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep, SubprocessExecutor

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

        unavailable_state = (
            PostgresClusterState.UNKNOWN
            if self._compose_runner.requires_docker and not docker_available()
            else PostgresClusterState.STOPPED
        )

        def run(context: RunContext[PostgresClusterState]) -> PostgresClusterState:
            if self._mode == "external":
                context.action("postgres.status.external")
                return self._status_external()
            if len(steps) == 1:
                context.action(steps[0].step_id)
                return unavailable_state
            result = self._status_compose(health_step_id="postgres.status.health")
            context.skip_remaining()
            return result

        plan = ExecutionPlan(steps=tuple(step.public_projection() for step in steps))
        return Command.create(
            plan,
            run,
            steps,
            executor=executor or SubprocessExecutor(),
        )

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
        self, *, timeout: float | None = None, health_step_id: str | None = None
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
        )

    def ensure_running(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        return self.ensure_running_command(timeout).run()

    def ensure_running_command(
        self, timeout: float = _DEFAULT_TIMEOUT, *, executor: ProcessExecutor | None = None
    ) -> Command[None]:
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.proc import PreparedAction, PreparedStep, SubprocessExecutor

        if self._mode == "external":
            steps: tuple[PreparedStep | PreparedAction, ...] = (
                PreparedAction(
                    step_id="postgres.ensure.external",
                    action="ensure-external",
                    description="Verify the externally managed PostgreSQL endpoint",
                    read_only=True,
                ),
            )
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
                    read_only=True,
                ),
                PreparedStep(
                    step_id="postgres.ensure.config",
                    argv=(*prefix, "config", "--quiet"),
                    cwd=str(compose_file.parent),
                    mutating=True,
                ),
                PreparedStep(
                    step_id="postgres.ensure.up",
                    argv=(*prefix, "up", "--detach", "--wait"),
                    cwd=str(compose_file.parent),
                    mutating=True,
                ),
                PreparedStep(
                    step_id="postgres.ensure.final.ps",
                    argv=(*prefix, "ps", "--format", "json"),
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
                    read_only=True,
                ),
            )

        def run(context: RunContext[None]) -> None:
            if self._mode == "external":
                context.action("postgres.ensure.external")
                self._ensure_running_external()
            else:
                self._ensure_running_compose(timeout)
            context.skip_remaining()

        plan = ExecutionPlan(steps=tuple(step.public_projection() for step in steps))
        return Command.create(
            plan,
            run,
            steps,
            executor=executor or SubprocessExecutor(),
        )

    def _ensure_running_impl(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        if self._mode == "external":
            self._ensure_running_external()
            return
        self._ensure_running_compose(timeout)

    def _ensure_running_external(self) -> None:
        state = self._status_impl()
        if state is PostgresClusterState.HEALTHY:
            return
        raise PostgresClusterUnreachableError(
            f"external postgres cluster not reachable at {self.endpoint} "
            f"(mode={self._mode}, state={state.value})"
        )

    def _ensure_running_compose(self, timeout: float) -> None:
        """Start a managed cluster when status is STOPPED, STARTING, or UNKNOWN."""
        deadline = time.monotonic() + max(0.1, timeout)
        try:
            lock = exclusive_lock_until(postgres_cluster_lock_path(self._project_id), deadline)
            with lock:
                if self._compose_runner.requires_docker:
                    ensure_docker_or_raise()
                remaining = max(0.0, deadline - time.monotonic())
                image = self._require_trusted_image(remaining)
                state = self._status_compose(timeout=max(0.0, deadline - time.monotonic()))
                # Do not rewrite secret/config artifacts on a healthy fast path.
                if state is PostgresClusterState.HEALTHY:
                    return
                self._ensure_artifacts(image, timeout=max(0.0, deadline - time.monotonic()))
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
                )
                while True:
                    current = self._status_compose(timeout=max(0.0, deadline - time.monotonic()))
                    if current is PostgresClusterState.HEALTHY:
                        return
                    if current is PostgresClusterState.UNHEALTHY:
                        raise PostgresClusterUnhealthyError(
                            f"compose postgres cluster unhealthy at {self.endpoint} "
                            f"(mode={self._mode}, state={current.value})"
                        )
                    if time.monotonic() >= deadline:
                        raise PostgresClusterTimeoutError(timeout)
                    time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
        except LockConflictError as exc:
            raise PostgresClusterTimeoutError(timeout) from exc

    def stop(self, timeout: float = _DEFAULT_STOP_TIMEOUT) -> None:
        return self.stop_command(timeout).run()

    def stop_command(
        self, timeout: float = _DEFAULT_STOP_TIMEOUT, *, executor: ProcessExecutor | None = None
    ) -> Command[None]:
        from odoo_instance_sdk.execution import Command, ExecutionPlan
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
                self._stop_impl(timeout)
            elif not compose_file.is_file():
                context.action("postgres.stop.missing")
            else:
                self._stop_impl(timeout)
            context.skip_remaining()

        plan = ExecutionPlan(steps=tuple(step.public_projection() for step in steps))
        return Command.create(plan, run, steps, executor=executor or SubprocessExecutor())

    def _stop_impl(self, timeout: float = _DEFAULT_STOP_TIMEOUT) -> None:
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
                    self._status_compose(timeout=max(0.0, deadline - time.monotonic()))
                    is PostgresClusterState.STOPPED
                ):
                    return
                compose_stop(
                    self._compose_runner,
                    compose_file,
                    compose_project_name(self._project_id),
                    timeout=max(0.0, deadline - time.monotonic()),
                )
        except LockConflictError as exc:
            raise PostgresClusterTimeoutError(timeout) from exc

    def to_diagnostic_dict(self) -> Mapping[str, object]:
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

    def resource_snapshot_command(
        self, *, executor: ProcessExecutor | None = None
    ) -> Command[ClusterResourceSnapshot | None]:
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.internal.proc import PreparedAction, SubprocessExecutor

        step = PreparedAction(
            step_id="postgres.resource.snapshot",
            action="collect-resource-snapshot",
            description="Collect PostgreSQL container identity and resource metrics",
            read_only=True,
        )

        def run(
            context: RunContext[ClusterResourceSnapshot | None],
        ) -> ClusterResourceSnapshot | None:
            context.action(step.step_id)
            result = self._resource_snapshot_impl()
            context.skip_remaining()
            return result

        plan = ExecutionPlan(steps=(step.public_projection(),))
        return Command.create(
            plan,
            run,
            (step,),
            executor=executor or SubprocessExecutor(),
        )

    def _resource_snapshot_impl(self) -> ClusterResourceSnapshot | None:
        if self._mode == "external":
            return None
        from odoo_instance_sdk.internal.cluster_resources import cluster_resource_snapshot

        return cluster_resource_snapshot(
            compose_file=self._compose_file(),
            compose_project_name=self.compose_project_name,
            service="postgres",
            runner=self._compose_runner,
            state=self._status_impl(),
        )


__all__ = ["PostgresCluster"]
