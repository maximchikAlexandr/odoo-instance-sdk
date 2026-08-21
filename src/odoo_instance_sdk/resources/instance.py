from __future__ import annotations

import contextlib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import (
    InstanceConfigurationError,
    NonLocalInstanceError,
)
from odoo_instance_sdk.internal.locks import environment_lock_path, shared_lock
from odoo_instance_sdk.internal.odoo_config import (
    infer_base_url,
    parse_db_names,
    parse_odoo_config,
)
from odoo_instance_sdk.internal.server import (
    get_process_status,
    run_command,
    run_foreground_process,
    start_process,
    stop_process,
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

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.resources.environment import DevelopmentEnvironment


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
            ),
            _client=self._client,
            _artifact_lock_path=environment_lock_path(str(environment.id)),
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


_FORBIDDEN_SHELL_FLAGS = ("-c", "--config", "-d", "--database")


def _check_shell_overrides(args: Sequence[str]) -> None:
    for tok in args:
        if tok in _FORBIDDEN_SHELL_FLAGS:
            raise InstanceConfigurationError(
                f"shell() passthrough override {tok!r} is forbidden; "
                "config/DB binding cannot be changed"
            )
        for flag in _FORBIDDEN_SHELL_FLAGS:
            if tok.startswith(flag) and len(tok) > len(flag):
                raise InstanceConfigurationError(
                    f"shell() passthrough override {tok!r} is forbidden; "
                    "config/DB binding cannot be changed"
                )


@dataclass(slots=True, kw_only=True)
class OdooInstance:
    config: InstanceConfig
    _client: OdooClient
    databases: DatabaseResource = field(init=False)
    _artifact_lock_path: Path | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.databases = DatabaseResource(
            base_url=self.config.base_url,
            master_password=self.config.master_password,
            _instance=self,
        )

    def __repr__(self) -> str:
        return f"OdooInstance(base_url={self.config.base_url!r}, databases=<DatabaseResource>)"

    def _executable_prefix(self) -> tuple[str, ...]:
        if self.config.command_prefix is not None:
            return self.config.command_prefix
        return (self._client.config.executable,)

    def run(
        self,
        args: list[str],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        return run_command(
            self._executable_prefix(),
            args,
            cwd=cwd,
            env=env,
            timeout=timeout,
        )

    def start(
        self,
        config: StartConfig | None = None,
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> OdooProcess:
        if config is None:
            config = self.config.start_config
            if config is None:
                raise InstanceConfigurationError(
                    "No StartConfig — pass one explicitly or create instance via from_config()"
                )
        proc, handle, secret_config = start_process(
            self._executable_prefix(), config, cwd=cwd, env=env
        )
        self._client.register_process(proc, handle, secret_config)
        return proc

    def run_foreground(
        self,
        config: StartConfig | None = None,
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        if config is None:
            config = self.config.start_config
            if config is None:
                raise InstanceConfigurationError(
                    "No StartConfig — pass one explicitly or create instance via from_config()"
                )
        resolved_cwd = cwd if cwd is not None else self.config.default_cwd
        from odoo_instance_sdk.internal.server import _build_cli_args

        cli_args = _build_cli_args(config)
        with self._artifact_lock():
            return run_foreground_process(
                self._executable_prefix(),
                cli_args,
                cwd=resolved_cwd,
                env=env,
            )

    def shell(self, *, args: Sequence[str] = ()) -> int:
        config = self.config.start_config
        if config is None:
            raise InstanceConfigurationError(
                "No StartConfig — create instance via from_config() or from_environment()"
            )
        _check_shell_overrides(args)
        from odoo_instance_sdk.internal.server import _build_cli_args

        cli_args = _build_cli_args(config)
        full_args = [*cli_args, "shell", *args]
        resolved_cwd = self.config.default_cwd
        with self._artifact_lock():
            return run_foreground_process(
                self._executable_prefix(),
                full_args,
                cwd=resolved_cwd,
            )

    def run_shell_script(
        self,
        source: str,
        *,
        argv: Sequence[str] = (),
        timeout: float | None = None,
        commit: bool = False,
    ) -> CommandResult:
        with self._artifact_lock():
            return self._run_shell_script_unlocked(
                source, argv=argv, timeout=timeout, commit=commit
            )

    def _run_shell_script_unlocked(
        self,
        source: str,
        *,
        argv: Sequence[str] = (),
        timeout: float | None = None,
        commit: bool = False,
    ) -> CommandResult:
        """Internal shell primitive for coordinators which already own the artifact lock."""
        config = self.config.start_config
        if config is None:
            raise InstanceConfigurationError(
                "No StartConfig — create instance via from_config() or from_environment()"
            )
        from odoo_instance_sdk.internal.server import _build_cli_args, _run_captured_shell

        cli_args = _build_cli_args(config)
        full_args = [*cli_args, "shell"]
        resolved_cwd = self.config.default_cwd
        return _run_captured_shell(
            self._executable_prefix(),
            full_args,
            source=source,
            argv=list(argv),
            timeout=timeout,
            commit=commit,
            cwd=resolved_cwd,
        )

    @contextlib.contextmanager
    def _artifact_lock(self) -> Iterator[None]:
        if self._artifact_lock_path is None:
            yield
            return
        with shared_lock(self._artifact_lock_path):
            yield

    def stop(self, proc: OdooProcess, *, timeout: float = 10.0) -> None:
        handle, secret_config = self._client.unregister_process(proc.id)
        if handle is not None:
            stop_process(handle, timeout=timeout, secret_config_path=secret_config)

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
