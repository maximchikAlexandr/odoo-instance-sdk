from __future__ import annotations

import contextlib
import copy
import os
import subprocess
import sys
import tempfile
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, TextIO, TypeVar, cast

import psutil

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import (
    InstanceConfigurationError,
    LogfileAccessError,
    NonLocalInstanceError,
)
from odoo_instance_sdk.internal.locks import environment_lock_path, exclusive_lock, shared_lock
from odoo_instance_sdk.internal.odoo_config import (
    infer_base_url,
    parse_db_names,
    parse_odoo_config,
)
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    ProcessExecutor,
    ProcessHandle,
    ProcessResult,
    SubprocessExecutor,
    terminate,
    wait_foreground,
)
from odoo_instance_sdk.internal.process_env import (
    captured_child_environment,
    sanitized_child_environment,
)
from odoo_instance_sdk.internal.project_env import (
    MASTER_PASSWORD_KEY,
    load_project_environment,
    project_environment_secret_values,
)
from odoo_instance_sdk.internal.repo_key import git_common_dir, repo_key
from odoo_instance_sdk.internal.server import (
    _write_secret_config,
    cleanup_secret_config,
    get_process_status,
)
from odoo_instance_sdk.internal.urls import assert_local, normalize_base_url
from odoo_instance_sdk.models import (
    CommandResult,
    OdooProcess,
    ProcessStatus,
    ReadinessResult,
    StartConfig,
)
from odoo_instance_sdk.resources.database import DatabaseResource

T = TypeVar("T")


def _build_cli_args(config: StartConfig, *, secret_config_path: str | None = None) -> list[str]:
    """Resolve the shared non-launching CLI argument builder at the boundary."""
    from odoo_instance_sdk.internal.server import _build_cli_args as build_args

    if secret_config_path is None:
        return build_args(config)
    return build_args(config, secret_config_path=secret_config_path)


if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import (
        Command,
        ExecutionPlan,
        PlanObservation,
        SemanticPlanObservation,
    )
    from odoo_instance_sdk.internal.proc import PrivateJsonValue, RunContext
    from odoo_instance_sdk.internal.project_runtime import DeferredProjectRuntime
    from odoo_instance_sdk.project import ProjectConfig
    from odoo_instance_sdk.resources.environment import DevelopmentEnvironment
    from odoo_instance_sdk.resources.postgres import PostgresCluster


@dataclass(frozen=True, slots=True)
class _RuntimeBinding:
    """Private owner-neutral identity shared by environment and project instances."""

    owner_kind: Literal["environment", "project"]
    owner_id: str
    project_id: str
    repository_root: Path
    git_common_dir: Path


class _RuntimeCatalog(Protocol):
    def _register_project(
        self, project_id: str, repository_root: str | Path, git_common_dir: str | Path
    ) -> None: ...

    def _upsert_runtime(
        self,
        owner_kind: str,
        owner_id: str,
        *,
        root_pid: int,
        create_time: float,
        started_at: str,
        checkout_branch: str,
        commit_sha: str,
        http_url: str,
        http_port: int,
        database_name: str,
    ) -> None: ...

    def _clear_runtime(self, owner_kind: str, owner_id: str) -> None: ...


@dataclass(slots=True, kw_only=True)
class InstanceFactory:
    _client: OdooClient

    def __call__(self, base_url: str, *, master_password: str | None = None) -> OdooInstance:
        normalized = normalize_base_url(base_url)
        return OdooInstance(
            config=InstanceConfig(
                base_url=normalized,
                master_password=master_password,
            ),
            _client=self._client,
        )

    def from_config(
        self,
        path: str | Path,
        *,
        base_url: str | None = None,
        master_password: str | None = None,
    ) -> OdooInstance:
        config = parse_odoo_config(path)
        url = infer_base_url(config, base_url=base_url)
        normalized = normalize_base_url(url)
        if master_password is None:
            raw_passwd = config.get("admin_passwd")
            master_password = raw_passwd if raw_passwd else None
        db_names = parse_db_names(config.get("db_name"))
        try:
            assert_local(normalized)
        except NonLocalInstanceError as e:
            raise InstanceConfigurationError(
                f"from_config requires a local instance; {normalized} is remote"
            ) from e
        start_cfg = StartConfig.from_odoo_config(path)
        db_port = start_cfg.db_port
        if start_cfg.db_host and db_port is None:
            db_port = 5432
        return OdooInstance(
            config=InstanceConfig(
                base_url=normalized,
                master_password=master_password,
                configured_database_names=db_names,
                start_config=start_cfg,
                db_host=start_cfg.db_host,
                db_port=db_port,
                db_user=start_cfg.db_user,
                db_password=start_cfg.db_password,
            ),
            _client=self._client,
        )

    def from_environment(self, environment: DevelopmentEnvironment) -> OdooInstance:
        from odoo_instance_sdk.resources.environment import (
            EnvironmentState,
            _decode_runtime_json,
        )
        from odoo_instance_sdk.resources.postgres import PostgresCluster

        if environment.state != EnvironmentState.READY:
            raise InstanceConfigurationError(
                f"from_environment requires a ready environment; "
                f"state={environment.state} for {environment.id}"
            )
        config_path = Path(environment.generated_config_path)
        if not config_path.is_file():
            raise InstanceConfigurationError(f"Generated config not found: {config_path}")
        cfg = parse_odoo_config(config_path)
        url = infer_base_url(cfg)
        normalized = normalize_base_url(url)
        try:
            assert_local(normalized)
        except NonLocalInstanceError as e:
            raise InstanceConfigurationError(
                f"from_environment requires a local instance; {normalized} is remote"
            ) from e

        runtime = _decode_runtime_json(_runtime_json_for(self._client, environment))
        odoo_bin = runtime.get("odoo_bin")
        if odoo_bin is None:
            raise InstanceConfigurationError(
                f"No odoo_bin recorded in runtime_json for environment {environment.id}"
            )
        python_bin = _resolve_python_binary(environment)
        command_prefix: tuple[str, ...] = (python_bin, odoo_bin)

        runtime_cwd = runtime.get("runtime_cwd") or environment.worktree_path
        default_cwd = Path(runtime_cwd)

        start_cfg = StartConfig.from_odoo_config(config_path)
        db_port = start_cfg.db_port
        if start_cfg.db_host and db_port is None:
            db_port = 5432
        db_names = parse_db_names(cfg.get("db_name"))
        project_environment = load_project_environment(Path(environment.repository_root))

        # Bind the project-level PostgresCluster for dependency preflight.
        # Bind does not start the cluster; readiness is checked in preflight.
        # We only swallow SDK-level config/manifest errors: a missing manifest
        # or unparseable config disables preflight rather than crashing spawn,
        # matching the "fail-fast" intent only when the cluster is actually
        # consulted. Unexpected errors propagate.
        from odoo_instance_sdk.exceptions import (
            PostgresClusterError,
            ProjectManifestNotFoundError,
        )

        try:
            cluster = PostgresCluster.from_project(Path(environment.repository_root))
        except (ProjectManifestNotFoundError, PostgresClusterError):
            cluster = None

        return OdooInstance(
            config=InstanceConfig(
                base_url=normalized,
                master_password=None,
                configured_database_names=db_names,
                start_config=start_cfg,
                command_prefix=command_prefix,
                default_cwd=default_cwd,
                db_host=start_cfg.db_host,
                db_port=db_port,
                db_user=start_cfg.db_user,
                db_password=start_cfg.db_password,
                project_environment=project_environment,
            ),
            _client=self._client,
            _artifact_lock_path=environment_lock_path(str(environment.id)),
            _postgres_cluster=cluster,
            _environment_id=str(environment.id),
            _runtime_binding=_RuntimeBinding(
                owner_kind="environment",
                owner_id=str(environment.id),
                project_id=f"project_{repo_key(Path(environment.repository_root), Path(environment.git_common_dir))}",
                repository_root=Path(environment.repository_root).resolve(),
                git_common_dir=Path(environment.git_common_dir).resolve(),
            ),
        )

    def from_project(self, project: ProjectConfig) -> OdooInstance:
        """Construct a local instance from an initialized project manifest."""
        from odoo_instance_sdk.resources.postgres import PostgresCluster

        root = project.repository_root.resolve()
        project_environment = load_project_environment(root)
        config_path = _project_path(root, project.source_config, field="source_config")
        odoo_bin = _project_path(root, project.odoo_bin, field="odoo_bin")
        python_bin, deferred_runtime = _project_runtime_binding(root, project, odoo_bin)
        default_cwd = (
            _project_path(root, project.runtime_cwd, field="runtime_cwd", directory=True)
            if project.runtime_cwd is not None
            else root
        )

        start_cfg = StartConfig.from_odoo_config(config_path)
        if project.preferred_http_port is not None:
            start_cfg.http_port = project.preferred_http_port
        if project.default_source_database is not None:
            start_cfg.db_name = project.default_source_database
        normalized = normalize_base_url(f"http://{start_cfg.http_interface}:{start_cfg.http_port}")
        try:
            assert_local(normalized)
        except NonLocalInstanceError as e:
            raise InstanceConfigurationError(
                f"from_project requires a local instance; {normalized} is remote"
            ) from e

        db_port = start_cfg.db_port
        if start_cfg.db_host and db_port is None:
            db_port = 5432
        cluster = PostgresCluster.from_project(root)
        return OdooInstance(
            config=InstanceConfig(
                base_url=normalized,
                master_password=None,
                configured_database_names=parse_db_names(start_cfg.db_name),
                start_config=start_cfg,
                command_prefix=(str(python_bin), str(odoo_bin)) if python_bin is not None else None,
                deferred_runtime=deferred_runtime,
                default_cwd=default_cwd,
                default_run_args=project.default_run_args,
                db_host=start_cfg.db_host,
                db_port=db_port,
                db_user=start_cfg.db_user,
                db_password=start_cfg.db_password,
                project_environment=project_environment,
            ),
            _client=self._client,
            _postgres_cluster=cluster,
            _runtime_binding=_RuntimeBinding(
                owner_kind="project",
                owner_id=f"project_{repo_key(root, git_common_dir(root))}",
                project_id=f"project_{repo_key(root, git_common_dir(root))}",
                repository_root=root,
                git_common_dir=git_common_dir(root),
            ),
        )


def _runtime_json_for(client: OdooClient, env: DevelopmentEnvironment) -> str | None:
    row = client.get_catalog().get_environment(str(env.id))
    if row is None:
        return None
    try:
        return cast("str | None", row["runtime_json"])
    except (KeyError, IndexError):
        return None


def _resolve_python_binary(env: DevelopmentEnvironment) -> str:
    py_path = Path(env.python_environment_path)
    if py_path.is_dir():
        return str(py_path / "bin" / "python")
    return str(py_path)


def _project_path(
    root: Path,
    value: str | Path | None,
    *,
    field: str,
    directory: bool = False,
) -> Path:
    if value is None:
        raise InstanceConfigurationError(f"Project manifest requires {field}")
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    path = Path(os.path.abspath(path))
    valid = path.is_dir() if directory else path.is_file()
    if not valid:
        kind = "directory" if directory else "file"
        raise InstanceConfigurationError(f"Project {field} {kind} not found: {path}")
    return path


def _iter_logfile(path: Path, *, tail: int, follow: bool) -> Iterator[str]:
    handle = _open_logfile(path)
    try:
        lines = deque(handle, maxlen=tail)
        cursor, sentinel = _logfile_cursor_snapshot(handle)
        yield from lines
        while follow:
            try:
                path_stat = path.stat()
                descriptor_stat = os.fstat(handle.fileno())
                replaced = (path_stat.st_dev, path_stat.st_ino) != (
                    descriptor_stat.st_dev,
                    descriptor_stat.st_ino,
                )
                truncated = descriptor_stat.st_size < cursor
                rewritten = (
                    descriptor_stat.st_size >= cursor
                    and _logfile_sentinel(handle.fileno(), cursor) != sentinel
                )
                if replaced or truncated or rewritten:
                    new_handle = _open_logfile(path)
                    handle.close()
                    handle = new_handle
                    cursor, sentinel = _logfile_cursor_snapshot(handle)
            except OSError:
                time.sleep(0.2)
                continue
            line = handle.readline()
            if line:
                cursor, sentinel = _logfile_cursor_snapshot(handle)
                yield line
                continue
            time.sleep(0.2)
    finally:
        handle.close()


_LOGFILE_SENTINEL_BYTES = 4096


def _open_logfile(path: Path) -> TextIO:
    try:
        return path.open(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise LogfileAccessError(str(path), exc.strerror or type(exc).__name__) from exc


def _logfile_sentinel(fd: int, cursor: int) -> bytes:
    """Read a fixed window immediately before the current follow cursor."""
    length = min(cursor, _LOGFILE_SENTINEL_BYTES)
    return os.pread(fd, length, cursor - length)


def _logfile_cursor_snapshot(handle: TextIO) -> tuple[int, bytes]:
    fd = handle.fileno()
    cursor = handle.tell()
    return cursor, _logfile_sentinel(fd, cursor)


_PROTECTED_RUNTIME_OPTIONS = (
    "--config",
    "--database",
    "--db-filter",
    "--db_user",
    "--db_password",
    "--db_host",
    "--db_port",
    "--db_sslmode",
    "--addons-path",
    "--upgrade-path",
    "--data-dir",
    "--http-interface",
    "--http-port",
    "--gevent-port",
    "--longpolling-port",
    "--logfile",
    "-c",
    "-d",
    "-r",
    "-w",
)


def _process_create_time(pid: int) -> float:
    """Return the exact process identity used by runtime reconciliation."""
    try:
        return float(psutil.Process(pid).create_time())
    except (psutil.Error, OSError):
        # Test/injected process seams may expose a synthetic PID.  Real
        # subprocesses take the exact psutil path; the timestamp fallback keeps
        # persistence best-effort without making foreground execution fail.
        return time.time()


def _worktree_ref(
    cwd: str | Path | None,
    *,
    context: RunContext[T] | None = None,
) -> tuple[str, str]:
    """Return ``(branch, commit_sha)`` for the worktree at ``cwd``.

    Best-effort: on any git failure returns ``("unknown", "")``.
    """
    if cwd is None:
        return "unknown", ""
    target = str(cwd)
    try:
        if context is None:
            from odoo_instance_sdk.internal.proc import run_captured

            branch_proc = run_captured(
                ("git", "-C", target, "rev-parse", "--abbrev-ref", "HEAD"),
                env=sanitized_child_environment(),
                timeout=10,
                text=True,
            )
            sha_proc = run_captured(
                ("git", "-C", target, "rev-parse", "HEAD"),
                env=sanitized_child_environment(),
                timeout=10,
                text=True,
            )
        else:
            branch_proc = cast("ProcessResult", context.process("instance.foreground.git.branch"))
            sha_proc = cast("ProcessResult", context.process("instance.foreground.git.commit"))
    except (subprocess.SubprocessError, OSError):
        return "unknown", ""
    branch_output = branch_proc.stdout if isinstance(branch_proc.stdout, str) else ""
    sha_output = sha_proc.stdout if isinstance(sha_proc.stdout, str) else ""
    branch = branch_output.strip() if branch_proc.returncode == 0 else "unknown"
    sha = sha_output.strip() if sha_proc.returncode == 0 else ""
    return branch or "unknown", sha


def _validate_runtime_args(args: Sequence[str]) -> tuple[str, ...]:
    captured = tuple(args)
    for token in captured:
        if token.startswith("--"):
            option = token.split("=", 1)[0]
            long_name = option[2:]
            if long_name:
                for protected in _PROTECTED_RUNTIME_OPTIONS:
                    if protected.startswith("--") and (
                        option == protected or protected.startswith(option)
                    ):
                        raise InstanceConfigurationError(
                            f"runtime argument override {option!r} is forbidden; "
                            "managed environment binding cannot be changed"
                        )
        else:
            for protected in _PROTECTED_RUNTIME_OPTIONS:
                if not protected.startswith("--") and (
                    token == protected or token.startswith(protected)
                ):
                    raise InstanceConfigurationError(
                        f"runtime argument override {protected!r} is forbidden; "
                        "managed environment binding cannot be changed"
                    )
    return captured


def _command_plan(
    steps: tuple[PreparedStep | PreparedAction, ...],
    *,
    secrets: Sequence[str] = (),
    observations: Sequence[PlanObservation] = (),
) -> ExecutionPlan:
    from odoo_instance_sdk.execution import ExecutionPlan

    plan = ExecutionPlan(
        steps=tuple(step.public_projection() for step in steps),
        observations=tuple(observations),
    )
    return plan.with_fingerprint(secrets=secrets)


def _http_port_observation(config: StartConfig) -> SemanticPlanObservation:
    """Capture the bounded, read-only HTTP binding check for a plan."""
    from odoo_instance_sdk.execution import PlanPrecondition, SemanticPlanObservation
    from odoo_instance_sdk.internal.address import AddressState, probe_address

    try:
        state = probe_address(config.http_interface, config.http_port)
    except OSError as error:
        precondition = PlanPrecondition(
            name="http-port-free",
            status="unknown",
            detail=f"unable to inspect {config.http_interface}:{config.http_port}: {error}",
        )
    else:
        free = state is AddressState.FREE
        precondition = PlanPrecondition(
            name="http-port-free",
            status="passed" if free else "failed",
            detail=(
                f"{config.http_interface}:{config.http_port} is available"
                if free
                else f"{config.http_interface}:{config.http_port} is occupied (ownership unknown)"
            ),
        )
    return SemanticPlanObservation(
        kind="semantic",
        goal="Start Odoo in the foreground",
        targets=(f"http://{config.http_interface}:{config.http_port}",),
        mutations=("spawn the Odoo foreground process",),
        preconditions=(precondition,),
        warnings=(),
    )


def _assert_http_port_free(config: StartConfig) -> None:
    from odoo_instance_sdk.internal.address import AddressState, probe_address

    try:
        state = probe_address(config.http_interface, config.http_port)
    except OSError as error:
        raise InstanceConfigurationError(
            f"cannot verify HTTP port {config.http_interface}:{config.http_port}: {error}"
        ) from error
    if state is not AddressState.FREE:
        raise InstanceConfigurationError(
            f"port-conflict: {config.http_interface}:{config.http_port} is occupied "
            "(ownership unknown)"
        )


def _command_result(
    result: ProcessResult,
    timeout: float | None,
    step: PreparedStep | None = None,
) -> CommandResult:
    stdout = (
        result.stdout.decode(errors="replace")
        if isinstance(result.stdout, bytes)
        else result.stdout
    )
    stderr = (
        result.stderr.decode(errors="replace")
        if isinstance(result.stderr, bytes)
        else result.stderr
    )
    if step is not None:
        from odoo_instance_sdk.internal.proc.redaction import (
            captured_secret_values,
            redacted_projection,
        )

        public_step = step.public_projection()
        args = list(public_step.argv)
        environment = public_step.environment_overrides
        secrets = captured_secret_values(step)
        stdout = cast("str", redacted_projection(stdout or "", secrets=secrets, field="stdout"))
        stderr = cast("str", redacted_projection(stderr or "", secrets=secrets, field="stderr"))
    else:
        from odoo_instance_sdk.internal.proc.redaction import redacted_argv

        args = list(redacted_argv(result.argv))
        environment = ()
    return CommandResult(
        args=args,
        returncode=result.returncode,
        stdout=stdout or "",
        stderr=stderr or "",
        duration=result.duration,
        cwd=result.cwd,
        environment=environment,
        timeout=timeout,
    )


def _snapshot_start_inputs(
    config: StartConfig,
    *,
    secret_config_path: str | None = None,
) -> tuple[StartConfig, tuple[str, ...], str | None, tuple[str, ...]]:
    snapshot = copy.deepcopy(config)
    secret_path = secret_config_path
    if snapshot.config_path is None and snapshot.db_password is not None:
        secret_path = secret_path or str(
            Path(tempfile.gettempdir()) / f"odoo-sdk-{uuid.uuid4().hex}.conf"
        )
    elif snapshot.config_path is not None:
        secret_path = None
    args = tuple(
        _build_cli_args(snapshot)
        if secret_path is None
        else _build_cli_args(snapshot, secret_config_path=secret_path)
    )
    secrets = tuple(value for value in (snapshot.db_password, secret_path) if value is not None)
    return snapshot, args, secret_path, secrets


def _child_secret_values(
    project_environment: Mapping[str, str], overrides: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    values = list(project_environment_secret_values(project_environment))
    for key, value in (overrides or {}).items():
        if key == MASTER_PASSWORD_KEY and value:
            values.append(value)
    return tuple(dict.fromkeys(values))


def _build_shell_script_step(
    config: StartConfig,
    *,
    executable_prefix: Sequence[str],
    default_cwd: Path | None,
    source: str,
    argv: Sequence[str] = (),
    timeout: float | None = None,
    commit: bool = False,
    nonce: str | None = None,
    secret_config_path: str | None = None,
    project_environment: Mapping[str, str] | None = None,
) -> tuple[PreparedStep, StartConfig, str | None, tuple[str, ...]]:
    """Capture one shell script's complete private process input."""
    snapshot, cli_args, secret_path, secrets = _snapshot_start_inputs(
        config, secret_config_path=secret_config_path
    )
    secrets = (*secrets, source, *_child_secret_values(project_environment or {}))
    from odoo_instance_sdk.internal.server import _build_shell_wrapper

    wrapper_nonce = nonce or uuid.uuid4().hex
    wrapper = _build_shell_wrapper(source, list(argv), commit=commit, nonce=wrapper_nonce)
    environment_snapshot, environment_overrides = captured_child_environment(
        None, project_environment=project_environment
    )
    step = PreparedStep(
        step_id="instance.shell_script",
        argv=(*executable_prefix, "shell", *cli_args),
        cwd=None if default_cwd is None else str(default_cwd),
        environment=environment_overrides,
        environment_snapshot=environment_snapshot,
        environment_overrides=environment_overrides,
        stdin=wrapper.encode(),
        wrapper_nonce=wrapper_nonce,
        secret_config_path=secret_path,
        timeout=timeout,
        secret_values=secrets,
        read_only=not commit,
        mutating=commit,
    )
    return step, snapshot, secret_path, secrets


@dataclass(slots=True, kw_only=True)
class OdooInstance:
    config: InstanceConfig
    _client: OdooClient
    databases: DatabaseResource = field(init=False)
    _artifact_lock_path: Path | None = field(default=None, repr=False)
    _postgres_cluster: PostgresCluster | None = field(default=None, repr=False)
    _environment_id: str | None = field(default=None, repr=False)
    _runtime_binding: _RuntimeBinding | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.databases = DatabaseResource(
            base_url=self.config.base_url,
            master_password=self.config.master_password,
            _instance=self,
        )

    def __repr__(self) -> str:
        return f"OdooInstance(base_url={self.config.base_url!r}, databases=<DatabaseResource>)"

    def _dependency_manifest(
        self,
    ) -> tuple[tuple[PreparedStep | PreparedAction, ...], Path | None]:
        if self._postgres_cluster is None:
            return (), None
        from odoo_instance_sdk.resources.postgres import PostgresCluster

        if not isinstance(self._postgres_cluster, PostgresCluster):
            return (), None
        temporary_path: Path | None = None
        if self._postgres_cluster.mode == "compose":
            compose_file = self._postgres_cluster.compose_file
            temporary_path = compose_file.parent / f".compose-{uuid.uuid4().hex}.yaml.tmp"
        steps = self._postgres_cluster._ensure_running_steps(60.0, temporary_path=temporary_path)
        return tuple(steps), temporary_path

    def _ensure_dependencies_ready(
        self,
        context: RunContext[T] | None = None,
        *,
        dependency_steps: Sequence[PreparedStep | PreparedAction] = (),
        temporary_path: Path | None = None,
    ) -> None:
        """Dependency preflight: ensure project PostgresCluster is ready before spawn.

        Called exactly once per public spawn entrypoint. The exclusive shell
        mutator calls it after claiming the artifact lock, so its cluster
        recheck is serialized with other artifact operations. External-mode
        clusters are probed only; compose-mode clusters are started if stopped.
        Manual instances (``instance(base_url=...)`` / ``from_config()``) have
        ``_postgres_cluster is None`` and skip preflight.
        """
        if self._postgres_cluster is None:
            return
        from odoo_instance_sdk.resources.postgres import PostgresCluster

        if isinstance(self._postgres_cluster, PostgresCluster):
            step_ids = {
                step.step_id: step.step_id
                for step in dependency_steps
                if isinstance(step, PreparedStep)
            }
            self._postgres_cluster._ensure_running_impl(
                60.0,
                temporary_path=temporary_path,
                step_ids=step_ids,
            )
            if context is not None:
                self._postgres_cluster._account_optional_steps(context, dependency_steps)
            return
        self._postgres_cluster.ensure_running(timeout=60.0)

    def _executable_prefix(self) -> tuple[str, ...]:
        if self.config.command_prefix is not None:
            return self.config.command_prefix
        if self.config.deferred_runtime is not None:
            return self.config.deferred_runtime.command_prefix()
        return (self._client.config.executable,)

    def run(
        self,
        args: list[str],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        return self.run_command(args, cwd=cwd, env=env, timeout=timeout).run()

    def run_command(
        self,
        args: list[str],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Command[CommandResult]:

        argv = (*self._executable_prefix(), *tuple(args))
        environment_snapshot, environment_overrides = captured_child_environment(
            env, project_environment=self.config.project_environment
        )
        child_secrets = _child_secret_values(self.config.project_environment, env)
        step = PreparedStep(
            step_id="instance.run",
            argv=argv,
            cwd=None if cwd is None else str(cwd),
            environment=environment_overrides,
            environment_snapshot=environment_snapshot,
            environment_overrides=environment_overrides,
            secret_values=child_secrets,
            timeout=timeout,
            read_only=True,
        )

        def execute(context: RunContext[CommandResult]) -> CommandResult:
            result = cast("ProcessResult", context.process(step.step_id))
            return _command_result(result, timeout, step)

        from odoo_instance_sdk.execution import Command

        return Command.create(
            _command_plan((step,)),
            execute,
            (step,),
            executor=SubprocessExecutor(),
        )

    def start(
        self,
        config: StartConfig | None = None,
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> OdooProcess:
        return self.start_command(config, cwd=cwd, env=env).run()

    def start_command(
        self,
        config: StartConfig | None = None,
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> Command[OdooProcess]:
        if config is None:
            config = self.config.start_config
            if config is None:
                raise InstanceConfigurationError(
                    "No StartConfig — pass one explicitly or create instance via from_config()"
                )
        snapshot, cli_args, secret_path, secrets = _snapshot_start_inputs(config)
        argv = (*self._executable_prefix(), *cli_args)
        environment_snapshot, environment_overrides = captured_child_environment(
            env, project_environment=self.config.project_environment
        )
        secrets = (*secrets, *_child_secret_values(self.config.project_environment, env))
        step = PreparedStep(
            step_id="instance.start",
            argv=argv,
            cwd=None if cwd is None else str(cwd),
            environment=environment_overrides,
            environment_snapshot=environment_snapshot,
            environment_overrides=environment_overrides,
            mode="long-running",
            secret_values=secrets,
            long_running=True,
            start_new_session=True,
            inherit_stdio=True,
        )
        dependency_steps, dependency_temporary_path = self._dependency_manifest()
        prepared_steps: tuple[PreparedStep | PreparedAction, ...] = (
            *dependency_steps,
            step,
        )

        def execute(context: RunContext[OdooProcess]) -> OdooProcess:
            self._ensure_dependencies_ready(
                context,
                dependency_steps=dependency_steps,
                temporary_path=dependency_temporary_path,
            )
            secret_created = False
            try:
                if secret_path is not None:
                    _write_secret_config(snapshot, secret_path)
                    secret_created = True
                handle = context.spawn(step.step_id)
                proc = OdooProcess(
                    id=uuid.uuid4().hex,
                    pid=handle.pid,
                    args=list(argv),
                    started_at=time.time(),
                )
                self._client.register_process(proc, handle.process, secret_path)
            except BaseException:
                if secret_created:
                    cleanup_secret_config(secret_path)
                raise
            else:
                return proc

        from odoo_instance_sdk.execution import Command

        return Command.create(
            _command_plan(prepared_steps, secrets=secrets),
            execute,
            prepared_steps,
            executor=SubprocessExecutor(),
        )

    def run_foreground(
        self,
        config: StartConfig | None = None,
        *,
        args: Sequence[str] = (),
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        return self.run_foreground_command(config, args=args, cwd=cwd, env=env).run()

    def run_foreground_command(  # noqa: C901
        self,
        config: StartConfig | None = None,
        *,
        args: Sequence[str] = (),
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> Command[int]:
        if config is None:
            config = self.config.start_config
            if config is None:
                raise InstanceConfigurationError(
                    "No StartConfig — pass one explicitly or create instance via from_config()"
                )
        validated_args = _validate_runtime_args((*self.config.default_run_args, *args))
        resolved_cwd = cwd if cwd is not None else self.config.default_cwd
        snapshot, cli_args, secret_path, secrets = _snapshot_start_inputs(config)
        environment_snapshot, environment_overrides = captured_child_environment(
            env, project_environment=self.config.project_environment
        )
        secrets = (*secrets, *_child_secret_values(self.config.project_environment, env))
        step = PreparedStep(
            step_id="instance.foreground",
            argv=(*self._executable_prefix(), *cli_args, *validated_args),
            cwd=None if resolved_cwd is None else str(resolved_cwd),
            environment=environment_overrides,
            environment_snapshot=environment_snapshot,
            environment_overrides=environment_overrides,
            mode="foreground",
            secret_values=secrets,
            long_running=True,
            start_new_session=True,
            inherit_stdio=True,
        )
        from odoo_instance_sdk.internal.proc import PreparedStep as _PreparedStep

        dependency_steps, dependency_temporary_path = self._dependency_manifest()
        prepared_steps: tuple[PreparedStep | PreparedAction, ...] = (*dependency_steps, step)
        if (
            self._runtime_binding is not None or self._environment_id is not None
        ) and resolved_cwd is not None:
            target = str(resolved_cwd)
            prepared_steps += (
                _PreparedStep(
                    step_id="instance.foreground.git.branch",
                    argv=("git", "-C", target, "rev-parse", "--abbrev-ref", "HEAD"),
                    cwd=target,
                    timeout=10.0,
                    read_only=True,
                ),
                _PreparedStep(
                    step_id="instance.foreground.git.commit",
                    argv=("git", "-C", target, "rev-parse", "HEAD"),
                    cwd=target,
                    timeout=10.0,
                    read_only=True,
                ),
            )

        process_executor = SubprocessExecutor()

        def execute(context: RunContext[int]) -> int:
            # The planning probe is intentionally repeated at this mutation
            # boundary.  A stale preview must never turn into a spawn.
            if type(process_executor) is SubprocessExecutor:
                _assert_http_port_free(config)
            self._ensure_dependencies_ready(
                context,
                dependency_steps=dependency_steps,
                temporary_path=dependency_temporary_path,
            )
            for dependency_step in dependency_steps:
                if context.planned(dependency_step.step_id) and not context.consumed(
                    dependency_step.step_id
                ):
                    context.skip(dependency_step.step_id)
            with self._artifact_lock():
                secret_created = False
                if secret_path is not None:
                    _write_secret_config(snapshot, secret_path)
                    secret_created = True
                handle: ProcessHandle | None = None
                try:
                    handle = context.spawn(step.step_id)
                    if self._runtime_binding is not None or self._environment_id is not None:
                        self._persist_runtime_identity(
                            handle.pid,
                            snapshot,
                            resolved_cwd,
                            context=context,
                        )
                    from odoo_instance_sdk.internal.server import wait_foreground_process

                    return wait_foreground_process(
                        handle.process,
                        observer=context.observer,
                        step_id=step.step_id,
                    )
                except BaseException:
                    if handle is not None:
                        with contextlib.suppress(BaseException):
                            terminate(
                                handle,
                                process_group_id=handle.process_group_id,
                                timeout=5.0,
                            )
                    raise
                finally:
                    self._clear_runtime_identity()
                    if secret_created:
                        cleanup_secret_config(secret_path)

        from odoo_instance_sdk.execution import Command

        return Command.create(
            _command_plan(
                prepared_steps,
                secrets=secrets,
                observations=(_http_port_observation(config),),
            ),
            execute,
            prepared_steps,
            executor=process_executor,
        )

    def _clear_runtime_identity(self) -> None:
        """Best-effort cleanup of the persisted runtime identity.
        Catalog cleanup failures are diagnostic only."""
        binding = self._runtime_binding
        environment_id = self._environment_id
        if binding is None and environment_id is None:
            return
        try:
            catalog = cast("_RuntimeCatalog", self._client.get_catalog())
            if binding is not None:
                catalog._clear_runtime(binding.owner_kind, binding.owner_id)
            elif environment_id is not None:
                catalog._clear_runtime("environment", environment_id)
        except Exception as e:
            print(f"failed to clear environment runtime: {e}", file=sys.stderr)

    def _persist_runtime_identity(
        self,
        root_pid: int,
        config: StartConfig,
        cwd: str | Path | None,
        context: RunContext[T] | None = None,
    ) -> None:
        """Persist the exact runtime identity before foreground waiting begins."""
        binding = self._runtime_binding
        environment_id = self._environment_id
        if binding is None and environment_id is None:
            return
        create_time = _process_create_time(root_pid)
        checkout_branch, commit_sha = _worktree_ref(cwd, context=context)
        http_url = f"http://{config.http_interface}:{config.http_port}"
        catalog = cast("_RuntimeCatalog", self._client.get_catalog())
        if binding is not None:
            catalog._register_project(
                binding.project_id, binding.repository_root, binding.git_common_dir
            )
            catalog._upsert_runtime(
                binding.owner_kind,
                binding.owner_id,
                root_pid=root_pid,
                create_time=create_time,
                started_at=datetime.now(UTC).isoformat(),
                checkout_branch=checkout_branch,
                commit_sha=commit_sha,
                http_url=http_url,
                http_port=config.http_port,
                database_name=config.db_name or "",
            )
            return
        assert environment_id is not None
        catalog._upsert_runtime(
            "environment",
            environment_id,
            root_pid=root_pid,
            create_time=create_time,
            started_at=datetime.now(UTC).isoformat(),
            checkout_branch=checkout_branch,
            commit_sha=commit_sha,
            http_url=http_url,
            http_port=config.http_port,
            database_name=config.db_name or "",
        )

    def iter_logs(self, *, tail: int = 100, follow: bool = False) -> Iterator[str]:
        """Yield the last ``tail`` lines of the bound logfile, optionally following appends."""
        if tail < 1:
            raise InstanceConfigurationError("tail must be >= 1")
        config = self.config.start_config
        if config is None:
            raise InstanceConfigurationError(
                "No StartConfig — create instance via from_config() or from_environment()"
            )
        raw = config.logfile
        if raw is None or not raw.strip():
            raise InstanceConfigurationError(
                "logfile is absent or empty; set logfile in the bound odoo.conf"
            )
        path = (self.config.default_cwd or Path.cwd()) / raw.strip()
        yield from _iter_logfile(path, tail=tail, follow=follow)

    def shell(self, *, args: Sequence[str] = ()) -> int:
        return self.shell_command(args=args).run()

    def shell_command(self, *, args: Sequence[str] = ()) -> Command[int]:
        config = self.config.start_config
        if config is None:
            raise InstanceConfigurationError(
                "No StartConfig — create instance via from_config() or from_environment()"
            )
        validated_args = _validate_runtime_args(args)
        snapshot, cli_args, secret_path, secrets = _snapshot_start_inputs(config)
        full_args = (*self._executable_prefix(), "shell", *cli_args, *validated_args)
        resolved_cwd = self.config.default_cwd
        environment_snapshot, environment_overrides = captured_child_environment(
            None, project_environment=self.config.project_environment
        )
        secrets = (*secrets, *_child_secret_values(self.config.project_environment))
        step = PreparedStep(
            step_id="instance.shell",
            argv=full_args,
            cwd=None if resolved_cwd is None else str(resolved_cwd),
            environment=environment_overrides,
            environment_snapshot=environment_snapshot,
            environment_overrides=environment_overrides,
            mode="foreground",
            secret_values=secrets,
            interactive=True,
            long_running=True,
            start_new_session=True,
            inherit_stdio=True,
        )
        dependency_steps, dependency_temporary_path = self._dependency_manifest()
        prepared_steps: tuple[PreparedStep | PreparedAction, ...] = (
            *dependency_steps,
            step,
        )

        def execute(context: RunContext[int]) -> int:
            self._ensure_dependencies_ready(
                context,
                dependency_steps=dependency_steps,
                temporary_path=dependency_temporary_path,
            )
            for dependency_step in dependency_steps:
                if context.planned(dependency_step.step_id) and not context.consumed(
                    dependency_step.step_id
                ):
                    context.skip(dependency_step.step_id)
            with self._artifact_lock():
                secret_created = False
                if secret_path is not None:
                    _write_secret_config(snapshot, secret_path)
                    secret_created = True
                try:
                    handle = context.spawn(step.step_id)
                    return wait_foreground(handle)
                finally:
                    if secret_created:
                        cleanup_secret_config(secret_path)

        from odoo_instance_sdk.execution import Command

        return Command.create(
            _command_plan(prepared_steps, secrets=secrets),
            execute,
            prepared_steps,
            executor=SubprocessExecutor(),
        )

    def run_shell_script(
        self,
        source: str,
        *,
        argv: Sequence[str] = (),
        timeout: float | None = None,
        commit: bool = False,
    ) -> CommandResult:
        return self.run_shell_script_command(
            source, argv=argv, timeout=timeout, commit=commit
        ).run()

    def run_shell_script_command(
        self,
        source: str,
        *,
        argv: Sequence[str] = (),
        timeout: float | None = None,
        commit: bool = False,
    ) -> Command[CommandResult]:
        return self._shell_script_command(
            source, argv=argv, timeout=timeout, commit=commit, exclusive=False
        )

    def _shell_script_command(
        self,
        source: str,
        *,
        argv: Sequence[str] = (),
        timeout: float | None = None,
        commit: bool = False,
        exclusive: bool,
        result_converter: Callable[[CommandResult], T] | None = None,
        callback_override: Callable[[], T] | None = None,
        preflight: Callable[[RunContext[T]], None] | None = None,
        extra_steps: Sequence[PreparedStep | PreparedAction] = (),
        executor: ProcessExecutor | None = None,
    ) -> Command[T]:
        config = self.config.start_config
        if config is None:
            raise InstanceConfigurationError(
                "No StartConfig — create instance via from_config() or from_environment()"
            )
        step, snapshot, secret_path, secrets = _build_shell_script_step(
            config,
            executable_prefix=self._executable_prefix(),
            default_cwd=self.config.default_cwd,
            source=source,
            argv=argv,
            timeout=timeout,
            commit=commit,
            project_environment=self.config.project_environment,
        )
        dependency_steps, dependency_temporary_path = self._dependency_manifest()
        action = PreparedAction(
            step_id="instance.shell_script.transaction",
            action="commit" if commit else "rollback",
            description="Commit or roll back the Odoo shell transaction",
            details={"commit": commit},
            read_only=not commit,
            mutating=commit,
        )

        captured_steps = (*dependency_steps, *extra_steps, step, action)

        def execute(context: RunContext[T]) -> T:
            # Stale-plan validation is deliberately the first operation.  In
            # particular it must precede readiness, lock acquisition, and
            # secret-config creation so a preview cannot turn into a partial
            # execution when its provenance has changed.
            if preflight is not None:
                preflight(context)

            def run_inside_lock() -> T:
                secret_created = False
                if secret_path is not None:
                    _write_secret_config(snapshot, secret_path)
                    secret_created = True
                try:
                    if callback_override is not None:
                        converted_result = callback_override()
                        context.action(action.step_id)
                        return converted_result
                    result = cast("ProcessResult", context.process(step.step_id))
                    context.action(action.step_id)
                    converted = _command_result(result, timeout, step)
                    return (
                        result_converter(converted)
                        if result_converter is not None
                        else cast("T", converted)
                    )
                finally:
                    if secret_created:
                        cleanup_secret_config(secret_path)

            if exclusive:
                with self._artifact_operation(exclusive=True):
                    self._ensure_dependencies_ready(
                        context,
                        dependency_steps=dependency_steps,
                        temporary_path=dependency_temporary_path,
                    )
                    return run_inside_lock()
            self._ensure_dependencies_ready(
                context,
                dependency_steps=dependency_steps,
                temporary_path=dependency_temporary_path,
            )
            with self._artifact_lock():
                return run_inside_lock()

        from odoo_instance_sdk.execution import Command

        return Command.create(
            _command_plan(captured_steps, secrets=secrets),
            execute,
            captured_steps,
            executor=executor or SubprocessExecutor(),
        )

    def _run_shell_script_in_context(
        self,
        context: RunContext[PrivateJsonValue],
        source: str,
        *,
        argv: Sequence[str] = (),
        timeout: float | None = None,
        commit: bool = False,
    ) -> CommandResult:
        """Consume a shell step from an already-owned command ledger.

        Database preparation owns the lock and the command snapshot.  Calling
        ``run_shell_script_command().run()`` from that callback would silently
        create a second ledger, so this small adapter deliberately mirrors the
        captured step construction and consumes it on the active context.
        """
        config = self.config.start_config
        if config is None:
            raise InstanceConfigurationError(
                "No StartConfig — create instance via from_config() or from_environment()"
            )
        captured = context.prepared("instance.shell_script")
        runtime_step, snapshot, secret_path, _ = _build_shell_script_step(
            config,
            executable_prefix=self._executable_prefix(),
            default_cwd=self.config.default_cwd,
            source=source,
            argv=argv,
            timeout=timeout,
            commit=commit,
            nonce=captured.wrapper_nonce,
            secret_config_path=captured.secret_config_path,
            project_environment=self.config.project_environment,
        )
        # The active command owns the complete immutable process input.  Even
        # seemingly harmless late binding (argv, cwd, environment, stdin,
        # timeout, or mode) would turn an inspected child into a different
        # child, so reject it before the executor is reached.
        if runtime_step != captured:
            from odoo_instance_sdk.exceptions import UnplannedStepError

            raise UnplannedStepError(captured.step_id, reason="shell inputs changed after capture")
        secret_created = False
        if secret_path is not None:
            _write_secret_config(snapshot, secret_path)
            secret_created = True
        try:
            result = cast("ProcessResult", context.process_prepared(captured))
            return _command_result(result, timeout, captured)
        finally:
            if secret_created:
                cleanup_secret_config(secret_path)

    def _run_shell_script_exclusive(
        self,
        source: str,
        *,
        argv: Sequence[str] = (),
        timeout: float | None = None,
        commit: bool = False,
    ) -> CommandResult:
        """Internal mutator entrypoint; lock choice belongs to this instance only."""
        from odoo_instance_sdk.internal.proc import active_context

        context = active_context()
        if context is not None:
            return self._run_shell_script_in_context(
                context,
                source,
                argv=argv,
                timeout=timeout,
                commit=commit,
            )
        return self._shell_script_command(
            source, argv=argv, timeout=timeout, commit=commit, exclusive=True
        ).run()

    @contextlib.contextmanager
    def _artifact_lock(self) -> Iterator[None]:
        with self._artifact_operation(exclusive=False):
            yield

    @contextlib.contextmanager
    def _artifact_operation(self, *, exclusive: bool) -> Iterator[None]:
        if self._artifact_lock_path is None:
            yield
            return
        lock = exclusive_lock if exclusive else shared_lock
        with lock(self._artifact_lock_path):
            yield

    def stop(self, proc: OdooProcess, *, timeout: float = 10.0) -> None:
        self.stop_command(proc, timeout=timeout).run()

    def stop_command(self, proc: OdooProcess, *, timeout: float = 10.0) -> Command[None]:
        handle = self._client.get_handle(proc.id)
        steps: list[PreparedStep | PreparedAction] = []
        if handle is not None and sys.platform == "win32":
            steps.append(
                PreparedStep(
                    step_id="instance.stop.taskkill",
                    argv=("taskkill", "/T", "/PID", str(proc.pid), "/F"),
                    timeout=timeout,
                    mode="captured",
                    mutating=True,
                )
            )
        elif handle is not None:
            steps.append(
                PreparedAction(
                    step_id="instance.stop.signal",
                    action="terminate_process_group",
                    description="Terminate the owned POSIX process group",
                    details={"pid": proc.pid, "timeout": timeout},
                    mutating=True,
                )
            )
        if handle is not None:
            steps.append(
                PreparedAction(
                    step_id="instance.stop.cleanup",
                    action="cleanup_secret_config",
                    description="Remove the private process configuration",
                    mutating=True,
                )
            )
        frozen_steps = tuple(steps)

        def execute(context: RunContext[None]) -> None:
            owned, secret_config = self._client.unregister_process(proc.id)
            if owned is None:
                return
            if sys.platform == "win32":
                context.process("instance.stop.taskkill")
            else:
                context.action("instance.stop.signal")
                try:
                    terminate(
                        ProcessHandle(
                            process=owned,
                            argv=(),
                            process_group_id=owned.pid,
                            session_id=owned.pid,
                            inherited_stdio=False,
                        ),
                        process_group_id=owned.pid,
                        timeout=timeout,
                    )
                finally:
                    context.action("instance.stop.cleanup")
                    cleanup_secret_config(secret_config)
                return
            context.action("instance.stop.cleanup")
            cleanup_secret_config(secret_config)

        from odoo_instance_sdk.execution import Command

        return Command.create(
            _command_plan(frozen_steps),
            execute,
            frozen_steps,
            executor=SubprocessExecutor(),
        )

    def status(self, proc: OdooProcess) -> ProcessStatus:
        self._client.get_process(proc.id)
        return get_process_status(self._client.get_handle(proc.id))

    def wait_ready(self, proc: OdooProcess, *, timeout: float = 60.0) -> ReadinessResult:
        self._client.get_process(proc.id)
        from odoo_instance_sdk.internal.health import poll_health

        def alive_check() -> bool:
            handle = self._client.get_handle(proc.id)
            return handle is not None and handle.poll() is None

        return poll_health(
            self.config.base_url,
            timeout=timeout,
            alive_check=alive_check,
        )


def _resolve_project_python(root: Path, value: str | Path | None) -> Path:
    from odoo_instance_sdk.internal.project_runtime import resolve_project_runtime

    return resolve_project_runtime(root, value, field="python")


def _project_runtime_binding(
    root: Path, project: ProjectConfig, odoo_bin: Path
) -> tuple[Path | None, DeferredProjectRuntime | None]:
    from odoo_instance_sdk.internal.project_runtime import defer_project_runtime

    deferred = defer_project_runtime(root, project.python, field="python", odoo_bin=odoo_bin)
    if deferred is not None:
        return None, deferred
    return _resolve_project_python(root, project.python), None
