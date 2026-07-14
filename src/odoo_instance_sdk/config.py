from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


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

    def __repr__(self) -> str:
        return (
            f"InstanceConfig(base_url={self.base_url!r}, "
            f"master_pwd=<redacted>, "
            f"configured_database_names={self.configured_database_names!r})"
        )
