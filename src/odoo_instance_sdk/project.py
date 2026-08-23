from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

import msgspec

from odoo_instance_sdk.exceptions import ProjectManifestNotFoundError


class PostgresProjectConfig(msgspec.Struct, frozen=True, kw_only=True):
    """Non-secret project-level PostgreSQL cluster intent stored under ``[postgres]``.

    Secrets (password) never live here — they live in a ``0600`` file under
    platformdirs user data directory, created lazily on first ``up``.
    """

    mode: Literal["external", "compose"] = "external"
    image: str | None = None
    port: int | None = None
    user: str | None = None

    def is_default(self) -> bool:
        """True when the section carries no non-default fields (may be omitted from manifest)."""
        return (
            self.mode == "external"
            and self.image is None
            and self.port is None
            and self.user is None
        )


class ProjectConfig(msgspec.Struct, frozen=True, kw_only=True):
    """Declarative project manifest plus required repository identity.

    Manifest serialization intentionally writes only declarative fields.
    """

    repository_root: Path
    odoo_bin: Path | None = None
    python: str | Path | None = None
    source_config: Path | None = None
    default_source_database: str | None = None
    preferred_http_port: int | None = None
    requirements: tuple[str, ...] = ()
    default_run_args: tuple[str, ...] = ()
    runtime_cwd: Path | None = None
    postgres: PostgresProjectConfig | None = None

    @classmethod
    def load(cls, project_path: str | Path) -> ProjectConfig:
        root = Path(project_path)
        manifest = root / ".odcli" / "project.toml"
        if not manifest.is_file():
            raise ProjectManifestNotFoundError(str(root))
        with open(manifest, "rb") as f:
            data = tomllib.load(f)
        section = data.get("project", data) if isinstance(data, dict) else data
        if not isinstance(section, dict):
            raise ProjectManifestNotFoundError(str(root))
        # ``[postgres]`` is a top-level table, not under ``[project]``.
        postgres_data: object = None
        if isinstance(data, dict) and "postgres" in data:
            postgres_data = data["postgres"]
        return cls._from_mapping(
            section,
            repository_root=root.resolve(),
            postgres_data=postgres_data,
        )

    @classmethod
    def _from_mapping(
        cls,
        data: dict[str, object],
        *,
        repository_root: Path,
        postgres_data: object = None,
    ) -> ProjectConfig:
        postgres = _postgres_from_mapping(postgres_data)
        return cls(
            repository_root=repository_root,
            odoo_bin=_path_or_none(data.get("odoo_bin")),
            python=_python_field(data.get("python")),
            source_config=_path_or_none(data.get("source_config")),
            default_source_database=_str_or_none(data.get("default_source_database")),
            preferred_http_port=_int_or_none(data.get("preferred_http_port")),
            requirements=tuple(_str_list(data.get("requirements"))),
            default_run_args=tuple(_str_list(data.get("default_run_args"))),
            runtime_cwd=_path_or_none(data.get("runtime_cwd")),
            postgres=postgres,
        )

    def to_manifest(self) -> str:
        lines: list[str] = ["[project]"]
        if self.odoo_bin is not None:
            lines.append(f'odoo_bin = "{_toml_path(self.odoo_bin)}"')
        if self.python is not None:
            lines.append(f'python = "{_toml_str(self.python)}"')
        if self.source_config is not None:
            lines.append(f'source_config = "{_toml_path(self.source_config)}"')
        if self.default_source_database is not None:
            lines.append(f'default_source_database = "{self.default_source_database}"')
        if self.preferred_http_port is not None:
            lines.append(f"preferred_http_port = {self.preferred_http_port}")
        if self.requirements:
            reqs = ", ".join(f'"{r}"' for r in self.requirements)
            lines.append(f"requirements = [{reqs}]")
        if self.default_run_args:
            args = ", ".join(f'"{a}"' for a in self.default_run_args)
            lines.append(f"default_run_args = [{args}]")
        if self.runtime_cwd is not None:
            lines.append(f'runtime_cwd = "{_toml_path(self.runtime_cwd)}"')
        postgres_block = _postgres_to_manifest(self.postgres)
        if postgres_block is not None:
            lines.append(postgres_block)
        return "\n".join(lines) + "\n"


def _postgres_from_mapping(value: object) -> PostgresProjectConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    mode_raw = value.get("mode", "external")
    mode = "compose" if mode_raw == "compose" else "external"
    image = _str_or_none(value.get("image"))
    port = _int_or_none(value.get("port"))
    user = _str_or_none(value.get("user"))
    cfg = PostgresProjectConfig(mode=mode, image=image, port=port, user=user)  # type: ignore[arg-type]
    return None if cfg.is_default() else cfg


def _postgres_to_manifest(postgres: PostgresProjectConfig | None) -> str | None:
    if postgres is None or postgres.is_default():
        return None
    lines: list[str] = ["[postgres]"]
    lines.append(f'mode = "{postgres.mode}"')
    if postgres.image is not None:
        lines.append(f'image = "{_toml_str(postgres.image)}"')
    if postgres.port is not None:
        lines.append(f"port = {postgres.port}")
    if postgres.user is not None:
        lines.append(f'user = "{_toml_str(postgres.user)}"')
    return "\n".join(lines)


def _path_or_none(value: object) -> Path | None:
    if value is None:
        return None
    return Path(str(value))


def _python_field(value: object) -> str | Path | None:
    if value is None:
        return None
    s = str(value)
    if s.startswith(("/", "./")) or (len(s) > 1 and s[1] == ":"):
        return Path(s)
    return s


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return int(str(value))


def _str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def _toml_path(p: Path) -> str:
    return str(p).replace("\\", "\\\\").replace('"', '\\"')


def _toml_str(v: str | Path) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"')
