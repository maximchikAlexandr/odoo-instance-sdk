from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.exceptions import (
    InstanceConfigurationError,
    MasterPasswordRequiredError,
    NonLocalInstanceError,
)
from odoo_instance_sdk.internal.odoo_config import (
    get_admin_passwd,
    infer_base_url,
    parse_db_names,
    parse_odoo_config,
)
from odoo_instance_sdk.internal.server import (
    get_process_status,
    run_command,
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
            master_password = get_admin_passwd(config)
        if master_password is None:
            raise MasterPasswordRequiredError(
                f"No master password for {normalized} — set admin_passwd in config or pass master_password"
            )
        db_names = parse_db_names(config.get("db_name"))
        try:
            assert_local(normalized)
        except NonLocalInstanceError as e:
            raise InstanceConfigurationError(
                f"from_config requires a local instance; {normalized} is remote"
            ) from e
        return OdooInstance(
            config=InstanceConfig(
                base_url=normalized,
                master_password=master_password,
                configured_database_names=db_names,
                start_config=StartConfig.from_odoo_config(path),
            ),
            _client=self._client,
        )


@dataclass(slots=True, kw_only=True)
class OdooInstance:
    config: InstanceConfig
    _client: OdooClient
    databases: DatabaseResource = field(init=False)

    def __post_init__(self) -> None:
        self.databases = DatabaseResource(
            base_url=self.config.base_url,
            master_password=self.config.master_password,
            _instance=self,
        )

    def __repr__(self) -> str:
        return f"OdooInstance(base_url={self.config.base_url!r}, databases=<DatabaseResource>)"

    def run(
        self,
        args: list[str],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        return run_command(
            self._client.config.executable,
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
            self._client.config.executable, config, cwd=cwd, env=env
        )
        self._client.register_process(proc, handle, secret_config)
        return proc

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
