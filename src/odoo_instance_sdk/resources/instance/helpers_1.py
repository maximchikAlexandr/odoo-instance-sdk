from __future__ import annotations

# ruff: noqa: F821
import os
import time
from collections import deque
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, TextIO, TypeVar, cast

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import (
    InstanceConfigurationError,
    LogfileAccessError,
    NonLocalInstanceError,
)
from odoo_instance_sdk.internal.generated_config import project_generated_config_path
from odoo_instance_sdk.internal.locks import environment_lock_path
from odoo_instance_sdk.internal.odoo_config import (
    infer_base_url,
    parse_db_names,
    parse_odoo_config,
)
from odoo_instance_sdk.internal.paths import resolve_environment_artifact_paths
from odoo_instance_sdk.internal.proc import (
    is_process_alive,
)
from odoo_instance_sdk.internal.project_env import (
    load_project_environment,
)
from odoo_instance_sdk.internal.project_runtime import resolve_project_http_port
from odoo_instance_sdk.internal.repo_key import git_common_dir, repo_key
from odoo_instance_sdk.internal.urls import assert_local, normalize_base_url
from odoo_instance_sdk.models import (
    StartConfig,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import (
        JsonValue,
    )
    from odoo_instance_sdk.project import ProjectConfig
    from odoo_instance_sdk.resources.environment import DevelopmentEnvironment
T = TypeVar("T")


def _build_cli_args(config: StartConfig, *, secret_config_path: str | None = None) -> list[str]:
    """Resolve the shared non-launching CLI argument builder at the boundary."""
    from odoo_instance_sdk.internal.server import _build_cli_args as build_args

    if secret_config_path is None:
        return build_args(config)
    return build_args(config, secret_config_path=secret_config_path)


@dataclass(frozen=True, slots=True)
class _RuntimeBinding:
    """Private owner-neutral identity shared by environment and project instances."""

    owner_kind: Literal["environment", "project"]
    owner_id: str
    project_id: str
    repository_root: Path
    git_common_dir: Path


@dataclass(frozen=True, slots=True)
class _RuntimeIdentity:
    """One execution-time projection of persisted and live runtime identity."""

    environment_id: str
    root_pid: int
    create_time: float
    expected_executable: str
    expected_argv: tuple[str, ...]
    expected_cwd: str
    expected_config_path: str
    live_create_time: float | None
    live_executable: str | None
    live_argv: tuple[str, ...] | None
    live_cwd: str | None
    live_config_path: str | None
    process_group_id: int | None

    @property
    def vanished(self) -> bool:
        return self.live_create_time is None


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

    def get_environment(self, environment_id: str) -> Mapping[str, JsonValue] | None: ...

    def get_environment_runtime(self, environment_id: str) -> Mapping[str, JsonValue] | None: ...

    def _clear_environment_runtime_if_matches(
        self, environment_id: str, *, root_pid: int, create_time: float
    ) -> bool: ...


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
        artifacts = resolve_environment_artifact_paths(
            environment_id=str(environment.id),
            repository_root=environment.repository_root,
            git_common_dir=environment.git_common_dir,
            python_environment_owned=environment.python_environment_owned,
            python_environment_path=environment.python_environment_path,
        )
        config_path = artifacts.generated_config_path
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

        default_cwd = artifacts.worktree_path

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
        generated_config_path = project_generated_config_path(root)
        if (
            project.postgres is not None
            and project.postgres.mode == "compose"
            and generated_config_path.is_file()
        ):
            config_path = generated_config_path
        else:
            config_path = _project_path(root, project.source_config, field="source_config")
        odoo_bin = _project_path(root, project.odoo_bin, field="odoo_bin")
        from odoo_instance_sdk.resources.instance.helpers_2 import _project_runtime_binding

        python_bin, deferred_runtime = _project_runtime_binding(root, project, odoo_bin)
        default_cwd = (
            _project_path(root, project.runtime_cwd, field="runtime_cwd", directory=True)
            if project.runtime_cwd is not None
            else root
        )

        start_cfg = StartConfig.from_odoo_config(config_path)
        start_cfg.http_port = resolve_project_http_port(
            project.preferred_http_port, start_cfg.http_port
        )
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


def _canonical_runtime_path(value: str) -> str:
    return str(Path(value).expanduser().resolve(strict=False))


def _runtime_config_arg(argv: Sequence[str]) -> str | None:
    for index, value in enumerate(argv[:-1]):
        if value in {"--config", "-c"}:
            return argv[index + 1]
    return None


def _canonical_runtime_argv(argv: Sequence[str]) -> tuple[str, ...]:
    values = list(argv)
    for index, value in enumerate(values):
        if index in {0, 1} or (index > 0 and values[index - 1] in {"--config", "-c"}):
            values[index] = _canonical_runtime_path(value)
    return tuple(values)


def _runtime_expectations(
    env_row: Mapping[str, JsonValue],
) -> tuple[str, tuple[str, ...], str, str]:
    from odoo_instance_sdk.resources.environment import _decode_runtime_json

    try:
        runtime_json = _decode_runtime_json(cast("str | None", env_row["runtime_json"]))
        odoo_bin = runtime_json["odoo_bin"]
        artifacts = resolve_environment_artifact_paths(
            environment_id=str(env_row["id"]),
            repository_root=str(env_row["repository_root"]),
            git_common_dir=str(env_row["git_common_dir"]),
            python_environment_owned=bool(int(env_row["python_environment_owned"])),
            python_environment_path=str(env_row["python_environment_path"]),
        )
        config_path = _canonical_runtime_path(str(artifacts.generated_config_path))
        python_path = artifacts.python_environment_path
        if python_path.is_dir():
            python_path /= "bin/python"
        expected_executable = _canonical_runtime_path(str(python_path))
        expected_cwd = _canonical_runtime_path(str(artifacts.worktree_path))
        expected_odoo_bin = _canonical_runtime_path(odoo_bin)
        start_config = StartConfig.from_odoo_config(config_path)
        expected_argv = _canonical_runtime_argv(
            (expected_executable, expected_odoo_bin, *_build_cli_args(start_config))
        )
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise RuntimeError("runtime identity configuration is unreadable") from exc
    return expected_executable, expected_argv, expected_cwd, config_path


def _runtime_protected_bindings(argv: Sequence[str]) -> dict[str, str | None]:
    bindings: dict[str, str | None] = {}
    protected = set(_PROTECTED_RUNTIME_OPTIONS)
    for index, token in enumerate(argv):
        option, separator, inline_value = token.partition("=")
        if option not in protected:
            continue
        if separator:
            bindings[option] = inline_value
        elif index + 1 < len(argv):
            bindings[option] = argv[index + 1]
        else:
            bindings[option] = None
    return bindings


def _runtime_argv_matches(identity: _RuntimeIdentity) -> bool:
    live_argv = identity.live_argv
    return (
        live_argv is not None
        and live_argv[:2] == identity.expected_argv[:2]
        and _runtime_protected_bindings(live_argv)
        == _runtime_protected_bindings(identity.expected_argv)
    )


def _verify_process_exit(pid: int) -> None:
    if is_process_alive(pid):
        raise RuntimeError("runtime process did not exit")


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
