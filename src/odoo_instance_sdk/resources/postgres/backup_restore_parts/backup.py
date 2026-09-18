from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeVar, cast

import odoo_instance_sdk.resources.postgres as _postgres_shim
from odoo_instance_sdk.exceptions import (
    PostgresClusterError,
    PostgresClusterNotOwnedError,
    PostgresClusterUnreachableError,
    PostgresImageNotTrustedError,
)
from odoo_instance_sdk.internal import paths as _paths
from odoo_instance_sdk.internal.address import AddressState
from odoo_instance_sdk.internal.postgres_compose import (
    ComposeRunner,
    SubprocessComposeRunner,
    compose_project_name,
    compose_volume_name,
    derive_state,
    ensure_password_file,
    is_oci_digest,
    render_compose_yaml,
    resolve_image_digest,
    write_compose_file_atomic,
)
from odoo_instance_sdk.models import PostgresClusterState
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.postgres.lifecycle import _DEFAULT_TIMEOUT
from odoo_instance_sdk.resources.postgres.lifecycle import (
    _RESOURCE_SNAPSHOT_TIMEOUT as _RESOURCE_SNAPSHOT_TIMEOUT,
)
from odoo_instance_sdk.resources.postgres.lifecycle import (
    _resolve_endpoint_external as _resolve_endpoint_external,
)
from odoo_instance_sdk.resources.postgres.lifecycle import (
    _resolve_project_id as _resolve_project_id,
)
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog, PostgresClusterClaim

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from odoo_instance_sdk.execution import Command, ExecutionPlan, PlanObservation
    from odoo_instance_sdk.internal.pg.server import ServerSummary
    from odoo_instance_sdk.internal.proc import (
        DeadlineProcessExecutor,
        PreparedAction,
        PreparedStep,
        ProcessExecutor,
        RunContext,
    )
    from odoo_instance_sdk.models import ServerUnavailabilityReason
    from odoo_instance_sdk.resources.postgres.backup_restore_parts import PostgresCluster

T = TypeVar("T")


class _BackupMixin:
    if TYPE_CHECKING:
        _repository_root: Path
        _project_id: str
        _mode: Literal["external", "compose"]
        _endpoint_host: str
        _endpoint_port: int
        _image: str | None
        _user: str | None
        _compose_runner: ComposeRunner

        def __init__(
            self,
            *,
            _repository_root: Path,
            _project_id: str,
            _mode: Literal["external", "compose"],
            _endpoint_host: str,
            _endpoint_port: int,
            _image: str | None = None,
            _user: str | None = None,
            _compose_runner: ComposeRunner = ...,
        ) -> None: ...

        def _ensure_running_compose(
            self,
            timeout: float,
            *,
            temporary_path: Path | None = None,
            step_ids: Mapping[str, str] | None = None,
        ) -> None: ...
        def _make_command(
            self,
            plan: ExecutionPlan,
            callback: Callable[[RunContext[T]], T],
            steps: Sequence[PreparedStep | PreparedAction],
            *,
            executor: ProcessExecutor,
        ) -> Command[T]: ...
        def _account_legacy_steps(
            self, context: RunContext[T], steps: Sequence[PreparedStep | PreparedAction]
        ) -> None: ...
        @staticmethod
        def _account_optional_steps(
            context: RunContext[T], steps: Sequence[PreparedStep | PreparedAction]
        ) -> None: ...

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
            return cast(
                "PostgresCluster",
                cls(
                    _repository_root=repository_root,
                    _project_id=project_id,
                    _mode=mode,
                    _endpoint_host=host,
                    _endpoint_port=port,
                    _image=image,
                    _user=user,
                    _compose_runner=compose_runner or SubprocessComposeRunner(),
                ),
            )
        host, port = _resolve_endpoint_external(cfg.source_config)
        return cast(
            "PostgresCluster",
            cls(
                _repository_root=repository_root,
                _project_id=project_id,
                _mode="external",
                _endpoint_host=host,
                _endpoint_port=port,
                _compose_runner=compose_runner or SubprocessComposeRunner(),
            ),
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
        return _paths.get_project_postgres_dir(self._project_id)

    def _compose_file(self) -> Path:
        return self._compose_dir() / "compose.yaml"

    def _password_file(self) -> Path:
        return self._compose_dir() / "postgres-password"

    def _trust_file(self) -> Path:
        """User-owned approval store; it is intentionally outside the repository."""
        return self._compose_dir().parent / "approved-images.json"

    def _cluster_claim(self) -> PostgresClusterClaim | None:
        """Read this project's claim; an absent row denotes legacy/external state."""
        catalog = BackupCatalog(db_path=_paths.get_catalog_path())
        try:
            return catalog._get_postgres_cluster(self._project_id)
        finally:
            catalog.close()

    def _ensure_pending_cluster_claim(self) -> PostgresClusterClaim:
        catalog = BackupCatalog(db_path=_paths.get_catalog_path())
        try:
            return catalog._ensure_postgres_cluster_pending(
                self._project_id,
                self.compose_project_name,
                compose_volume_name(self._project_id),
            )
        finally:
            catalog.close()

    def _activate_cluster_claim(self, claim: PostgresClusterClaim) -> None:
        catalog = BackupCatalog(db_path=_paths.get_catalog_path())
        try:
            catalog._activate_postgres_cluster(
                claim.cluster_id,
                self._project_id,
                self.compose_project_name,
                compose_volume_name(self._project_id),
            )
        finally:
            catalog.close()

    def _restore_provenance(self) -> tuple[str | None, str | None]:
        """Return verified ownership/data-dir context for a completed restore."""
        if not self.owned:
            return None, None
        claim = self._cluster_claim()
        if claim is None:
            return None, None
        if claim.state != "active":
            raise PostgresClusterError("postgres cluster claim is not active")
        if (
            claim.project_id != self._project_id
            or claim.compose_project != self.compose_project_name
            or claim.volume_name != compose_volume_name(self._project_id)
        ):
            raise PostgresClusterError("postgres cluster claim identity does not match target")
        data_dir = None
        return str(claim.cluster_id), data_dir

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
            context.complete_action("postgres.image.approve")
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
        expected = self._approved_image_digest()
        resolved = self._resolve_image_digest(
            timeout, pull_step_id=pull_step_id, inspect_step_id=inspect_step_id
        )
        if expected != resolved:
            raise PostgresImageNotTrustedError(
                "postgres image digest changed since explicit approval"
            )
        return resolved

    def _approved_image_digest(self) -> str:
        """Read the immutable local approval without probing Docker."""
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
        return expected

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
    ) -> None:
        """Validate or atomically publish the compose artifacts."""
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
            cluster_id=cluster_id,
        )
        write_compose_file_atomic(
            self._compose_file(),
            content,
            runner=self._compose_runner,
            project_name=compose_project_name(self._project_id),
            timeout=timeout,
            temporary_path=temporary_path,
            step_id=step_id,
            validate=validate,
            publish=publish,
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
            self._compose_runner.requires_docker and not _postgres_shim.docker_available()
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
            if self._compose_runner.requires_docker and not _postgres_shim.docker_available()
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
        state = _postgres_shim.probe_address(self._endpoint_host, self._endpoint_port)
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
        if self._compose_runner.requires_docker and not _postgres_shim.docker_available():
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
                    "postgres.ensure.identity.volume",
                    "postgres.ensure.identity.container",
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
            PreparedStep(
                step_id="postgres.ensure.identity.volume",
                argv=(
                    "docker",
                    "volume",
                    "inspect",
                    "--format",
                    "{{json .}}",
                    compose_volume_name(self._project_id),
                ),
                timeout=timeout,
                read_only=True,
            ),
            PreparedStep(
                step_id="postgres.ensure.identity.container",
                argv=(
                    "docker",
                    "inspect",
                    "--format",
                    "{{json .}}",
                    f"{self.compose_project_name}-postgres-1",
                ),
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
