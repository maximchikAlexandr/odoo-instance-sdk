from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from odoo_instance_sdk.exceptions import (
    PostgresClusterError,
    PostgresClusterNotOwnedError,
    PostgresClusterTimeoutError,
    PostgresClusterUnhealthyError,
    PostgresClusterUnreachableError,
)
from odoo_instance_sdk.internal.address import AddressState, probe_address
from odoo_instance_sdk.internal.git_worktree import (
    rev_parse_git_common_dir,
    rev_parse_toplevel,
)
from odoo_instance_sdk.internal.paths import get_project_postgres_dir
from odoo_instance_sdk.internal.postgres_compose import (
    ComposeRunner,
    SubprocessComposeRunner,
    compose_config,
    compose_project_name,
    compose_stop,
    compose_up,
    derive_state,
    docker_available,
    ensure_docker_or_raise,
    ensure_password_file,
    render_compose_yaml,
    write_compose_file_atomic,
)
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.models import PostgresClusterState
from odoo_instance_sdk.project import ProjectConfig

_DEFAULT_TIMEOUT = 60.0
_DEFAULT_STOP_TIMEOUT = 30.0
_PROBE_TIMEOUT = 2.0


def _resolve_project_id(repository_root: Path) -> str:
    """Return the deterministic project id for ``repository_root``.

    Falls back to a name-based key if Git is unavailable (e.g. non-git project);
    in that case artifacts remain usable but are not shared across worktrees.
    """
    try:
        toplevel = rev_parse_toplevel(repository_root)
        common = rev_parse_git_common_dir(toplevel)
        return repo_key(toplevel, common)
    except Exception:
        # ponytail: non-git fallback; deterministic per-directory, no sharing.
        import hashlib

        digest = hashlib.sha256(str(repository_root.resolve()).encode("utf-8")).hexdigest()[:8]
        return f"{repository_root.resolve().name or 'repo'}_{digest}"


def _resolve_endpoint_external(source_config: Path | None) -> tuple[str, int]:
    if source_config is None:
        raise PostgresClusterError(
            "external postgres mode requires source_config in manifest; rerun init --config"
        )
    from odoo_instance_sdk.models import StartConfig

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

    def _ensure_artifacts(self) -> None:
        """Lazily create compose artifacts (idempotent)."""
        if not self.owned:
            return
        compose_dir = self._compose_dir()
        compose_dir.mkdir(parents=True, exist_ok=True)
        password_path = self._password_file()
        ensure_password_file(password_path)
        assert self._image is not None
        assert self._user is not None
        content = render_compose_yaml(
            image=self._image,
            port=self._endpoint_port,
            user=self._user,
            project_id=self._project_id,
            password_file=str(password_path),
        )
        write_compose_file_atomic(self._compose_file(), content)
        project_name = compose_project_name(self._project_id)
        compose_config(self._compose_runner, self._compose_file(), project_name)

    def status(self) -> PostgresClusterState:
        if self._mode == "external":
            return self._status_external()
        return self._status_compose()

    def _status_external(self) -> PostgresClusterState:
        state = probe_address(self._endpoint_host, self._endpoint_port)
        if state is AddressState.FREE:
            return PostgresClusterState.UNREACHABLE
        if state is AddressState.OCCUPIED:
            return PostgresClusterState.HEALTHY
        return PostgresClusterState.UNKNOWN

    def _status_compose(self) -> PostgresClusterState:
        if not docker_available():
            return PostgresClusterState.UNKNOWN
        compose_file = self._compose_file()
        if not compose_file.is_file():
            # No artifacts yet == never started; treated as STOPPED so
            # ensure_running() will issue up. This conflates "never initialized"
            # with "stopped", which is acceptable since both require the same
            # recovery action (start the cluster).
            return PostgresClusterState.STOPPED
        assert self._user is not None
        return derive_state(
            self._compose_runner,
            compose_file,
            compose_project_name(self._project_id),
            user=self._user,
        )

    def ensure_running(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        if self._mode == "external":
            self._ensure_running_external()
            return
        self._ensure_running_compose(timeout)

    def _ensure_running_external(self) -> None:
        state = self.status()
        if state is PostgresClusterState.HEALTHY:
            return
        raise PostgresClusterUnreachableError(
            f"external postgres cluster not reachable at {self.endpoint} "
            f"(mode={self._mode}, state={state.value})"
        )

    def _ensure_running_compose(self, timeout: float) -> None:
        ensure_docker_or_raise()
        self._ensure_artifacts()
        state = self.status()
        if state is PostgresClusterState.HEALTHY:
            return
        if state is PostgresClusterState.UNHEALTHY:
            raise PostgresClusterUnhealthyError(
                f"compose postgres cluster unhealthy at {self.endpoint} "
                f"(mode={self._mode}, state={state.value})"
            )
        # STOPPED / STARTING / UNKNOWN — issue up.
        # UNKNOWN here can only mean a transient compose ps/exec hiccup
        # (Docker availability already checked above); re-issuing up is
        # the recovery path.
        compose_up(
            self._compose_runner,
            self._compose_file(),
            compose_project_name(self._project_id),
            timeout=timeout,
        )
        # Poll status until HEALTHY or timeout.
        import time

        deadline = time.monotonic() + max(0.1, timeout)
        while True:
            current = self.status()
            if current is PostgresClusterState.HEALTHY:
                return
            if current is PostgresClusterState.UNHEALTHY:
                raise PostgresClusterUnhealthyError(
                    f"compose postgres cluster unhealthy at {self.endpoint} "
                    f"(mode={self._mode}, state={current.value})"
                )
            if time.monotonic() >= deadline:
                raise PostgresClusterTimeoutError(timeout)
            time.sleep(0.5)

    def stop(self, timeout: float = _DEFAULT_STOP_TIMEOUT) -> None:
        if self._mode == "external":
            raise PostgresClusterNotOwnedError(
                f"cannot stop externally owned postgres cluster at {self.endpoint}"
            )
        ensure_docker_or_raise()
        compose_file = self._compose_file()
        if not compose_file.is_file():
            # Idempotent: never started → no-op.
            return
        compose_stop(
            self._compose_runner,
            compose_file,
            compose_project_name(self._project_id),
            timeout=timeout,
        )

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


__all__ = ["PostgresCluster"]
