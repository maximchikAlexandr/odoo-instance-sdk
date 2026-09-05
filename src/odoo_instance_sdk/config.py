from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from odoo_instance_sdk.models import StartConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.project_runtime import DeferredProjectRuntime


@dataclass(frozen=True, slots=True, kw_only=True)
class OdooClientConfig:
    executable: str
    http_timeout_seconds: float = 30.0
    backup_timeout_seconds: float = 600.0
    backups_directory: Path | None = None

    def __repr__(self) -> str:
        return f"OdooClientConfig(executable={self.executable!r})"


@dataclass(frozen=True, slots=True, kw_only=True)
class InstanceConfig:
    base_url: str
    master_password: str | None = field(default=None, repr=False)
    configured_database_names: tuple[str, ...] = ()
    start_config: StartConfig | None = field(default=None, repr=False)
    command_prefix: tuple[str, ...] | None = None
    deferred_runtime: DeferredProjectRuntime | None = field(default=None, repr=False)
    default_cwd: Path | None = None
    default_run_args: tuple[str, ...] = ()
    db_host: str | None = field(default=None)
    db_port: int | None = field(default=None)
    db_user: str | None = field(default=None)
    db_password: str | None = field(default=None, repr=False)
    project_environment: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "project_environment", MappingProxyType(dict(self.project_environment))
        )

    def __repr__(self) -> str:
        parts: list[str] = []
        for f in fields(self):
            val = getattr(self, f.name)
            if f.name in ("master_password", "db_password", "project_environment") and val:
                parts.append(f"{f.name}=<redacted>")
            else:
                parts.append(f"{f.name}={val!r}")
        return f"InstanceConfig({', '.join(parts)})"
