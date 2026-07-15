from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path

from odoo_instance_sdk.models import StartConfig


@dataclass(frozen=True, slots=True, kw_only=True)
class OdooClientConfig:
    executable: str
    http_timeout_seconds: float = 30.0
    backups_directory: Path | None = None

    def __repr__(self) -> str:
        return f"OdooClientConfig(executable={self.executable!r})"


@dataclass(frozen=True, slots=True, kw_only=True)
class InstanceConfig:
    base_url: str
    master_password: str | None = field(default=None, repr=False)
    configured_database_names: tuple[str, ...] = ()
    start_config: StartConfig | None = field(default=None, repr=False)
    db_host: str | None = field(default=None)
    db_port: int | None = field(default=None)
    db_user: str | None = field(default=None)
    db_password: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        parts: list[str] = []
        for f in fields(self):
            val = getattr(self, f.name)
            if f.name in ("master_password", "db_password") and val is not None:
                parts.append(f"{f.name}=<redacted>")
            else:
                parts.append(f"{f.name}={val!r}")
        return f"InstanceConfig({', '.join(parts)})"
