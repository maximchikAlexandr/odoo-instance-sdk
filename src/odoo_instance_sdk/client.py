from __future__ import annotations

from dataclasses import dataclass, field

from odoo_instance_sdk.database import DatabaseResource
from odoo_instance_sdk.exceptions import ProcessNotFoundError
from odoo_instance_sdk.models import OdooClientConfig, OdooProcess
from odoo_instance_sdk.server import ServerResource


@dataclass
class OdooClient:
    """Main client for odoo-instance-sdk."""

    config: OdooClientConfig
    _processes: dict[str, OdooProcess] = field(default_factory=dict)
    server: ServerResource = field(init=False)
    database: DatabaseResource = field(init=False)

    def __post_init__(self) -> None:
        self.server = ServerResource(self)
        self.database = DatabaseResource(self)

    def __repr__(self) -> str:
        return f"OdooClient(base_url={self.config.base_url!r})"

    def _get_process(self, proc_id: str) -> OdooProcess:
        try:
            return self._processes[proc_id]
        except KeyError:
            raise ProcessNotFoundError(f"Process {proc_id} not found in registry") from None
