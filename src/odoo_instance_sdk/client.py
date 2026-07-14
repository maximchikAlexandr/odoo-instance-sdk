from __future__ import annotations

import atexit
import contextlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from odoo_instance_sdk.config import OdooClientConfig
from odoo_instance_sdk.exceptions import ProcessNotFoundError
from odoo_instance_sdk.models import OdooProcess
from odoo_instance_sdk.resources.backup import BackupResource
from odoo_instance_sdk.resources.instance import InstanceFactory
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


@dataclass(slots=True, kw_only=True)
class OdooClient:
    """Main client for odoo-instance-sdk."""

    config: OdooClientConfig
    _processes: dict[str, OdooProcess] = field(default_factory=dict)
    _handles: dict[str, subprocess.Popen[bytes]] = field(default_factory=dict, repr=False)
    _secret_config_paths: dict[str, str] = field(default_factory=dict, repr=False)
    _catalog: BackupCatalog | None = field(default=None, repr=False)
    instance: InstanceFactory = field(init=False)
    backups: BackupResource = field(init=False)

    def __post_init__(self) -> None:
        self.instance = InstanceFactory(_client=self)
        self.backups = BackupResource(_client=self)
        atexit.register(self._cleanup_secret_configs)

    def __repr__(self) -> str:
        return f"OdooClient(executable={self.config.executable!r})"

    def _cleanup_secret_configs(self) -> None:
        for path in self._secret_config_paths.values():
            with contextlib.suppress(OSError):
                Path(path).unlink(missing_ok=True)

    def register_process(
        self, proc: OdooProcess, handle: subprocess.Popen[bytes], secret_config_path: str | None
    ) -> None:
        self._processes[proc.id] = proc
        self._handles[proc.id] = handle
        if secret_config_path is not None:
            self._secret_config_paths[proc.id] = secret_config_path

    def unregister_process(self, proc_id: str) -> tuple[subprocess.Popen[bytes] | None, str | None]:
        handle = self._handles.pop(proc_id, None)
        self._processes.pop(proc_id, None)
        secret_config = self._secret_config_paths.pop(proc_id, None)
        return handle, secret_config

    def get_process(self, proc_id: str) -> OdooProcess:
        try:
            return self._processes[proc_id]
        except KeyError:
            raise ProcessNotFoundError(f"Process {proc_id} not found in registry") from None

    def get_handle(self, proc_id: str) -> subprocess.Popen[bytes] | None:
        return self._handles.get(proc_id)

    def get_catalog(self) -> BackupCatalog:
        from odoo_instance_sdk.internal.paths import get_cache_root

        if self._catalog is None:
            self._catalog = BackupCatalog(db_path=get_cache_root() / "backups.sqlite3")
        return self._catalog
