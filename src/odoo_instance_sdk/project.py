from __future__ import annotations

import math
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import msgspec

from odoo_instance_sdk.exceptions import ConfigError, ProjectManifestNotFoundError
from odoo_instance_sdk.internal.sanitize import sanitize_terminal_text
from odoo_instance_sdk.internal.urls import normalize_base_url

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue


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


class TestInstanceProjectConfig(msgspec.Struct, frozen=True, kw_only=True):
    """Non-secret remote test instance settings used by database preparation."""

    base_url: str
    database: str
    git_branch: str | None = None

    def __post_init__(self) -> None:
        if not self.base_url.strip() or not self.database.strip():
            raise ConfigError("test_instance.base_url and database must not be empty")
        if self.git_branch is not None and not self.git_branch.strip():
            raise ConfigError("test_instance.git_branch must not be empty")
        try:
            normalize_base_url(self.base_url)
        except Exception as exc:
            raise ConfigError("invalid test_instance.base_url") from exc


class TicketLinkSettings(msgspec.Struct, frozen=True, kw_only=True):
    """Effective tracker-neutral commit-link policy."""

    enabled: bool
    base_url: str | None


def effective_ticket_settings(
    project: ProjectConfig,
    *,
    global_enabled: bool | None = None,
    global_base_url: str | None = None,
) -> TicketLinkSettings:
    """Resolve project values first, with optional pre-existing client values."""
    enabled = (
        project.ticket_link_enabled if project.ticket_link_enabled is not None else global_enabled
    )
    base_url = project.ticket_base_url or global_base_url
    return TicketLinkSettings(enabled=enabled is True, base_url=base_url)


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
    test_instance: TestInstanceProjectConfig | None = None
    default_base_ref: str | None = None
    refresh_after_hours: float | None = None
    ticket_link_enabled: bool | None = None
    ticket_base_url: str | None = None

    def __post_init__(self) -> None:
        if self.default_base_ref is not None and not self.default_base_ref.strip():
            raise ConfigError("project.default_base_ref must not be empty")
        if self.refresh_after_hours is not None and (
            isinstance(self.refresh_after_hours, bool)
            or not math.isfinite(self.refresh_after_hours)
            or self.refresh_after_hours <= 0
        ):
            raise ConfigError("project.refresh_after_hours must be finite and greater than zero")
        if self.ticket_link_enabled is not None and type(self.ticket_link_enabled) is not bool:
            raise ConfigError("project.ticket_link_enabled must be a boolean")
        if self.ticket_base_url is not None and self.ticket_base_url.strip():
            try:
                normalize_base_url(self.ticket_base_url)
            except Exception as exc:
                raise ConfigError("invalid project.ticket_base_url") from exc
        if self.ticket_link_enabled is True and self.ticket_base_url is None:
            raise ConfigError("project.ticket_link_enabled requires project.ticket_base_url")

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
        postgres_data: JsonValue = None
        if isinstance(data, dict) and "postgres" in data:
            postgres_data = data["postgres"]
        return cls._from_mapping(
            section,
            repository_root=root.resolve(),
            postgres_data=postgres_data,
            test_instance_data=data.get("test_instance") if isinstance(data, dict) else None,
        )

    @classmethod
    def _from_mapping(
        cls,
        data: dict[str, JsonValue],
        *,
        repository_root: Path,
        postgres_data: JsonValue | Mapping[str, JsonValue] = None,
        test_instance_data: JsonValue | Mapping[str, JsonValue] = None,
    ) -> ProjectConfig:
        legacy_enabled = data.get("jira_enabled")
        legacy_url = data.get("jira_base_url")
        if legacy_enabled is not None or legacy_url is not None:
            if "ticket_link_enabled" in data or "ticket_base_url" in data:
                raise ConfigError(
                    "historical ticket-link settings conflict with tracker-neutral settings"
                )
            if legacy_enabled is not None and type(legacy_enabled) is not bool:
                raise ConfigError(
                    "historical ticket-link setting is invalid; use ticket_link_enabled"
                )
            if legacy_enabled is True and (
                not isinstance(legacy_url, str | Path) or not str(legacy_url).strip()
            ):
                raise ConfigError(
                    "historical ticket-link setting is incomplete; set ticket_base_url and retry"
                )
            ticket_enabled: bool | None = legacy_enabled
            ticket_url: str | None = str(legacy_url) if legacy_url is not None else None
        else:
            ticket_enabled = _bool_or_none(data.get("ticket_link_enabled"))
            ticket_url = _str_or_none(data.get("ticket_base_url"))
        postgres = _postgres_from_mapping(cast("JsonValue", postgres_data))
        test_instance = _test_instance_from_mapping(
            cast(
                "JsonValue",
                test_instance_data if test_instance_data is not None else data.get("test_instance"),
            )
        )
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
            test_instance=test_instance,
            default_base_ref=_str_or_none(data.get("default_base_ref")),
            refresh_after_hours=_float_or_none(data.get("refresh_after_hours")),
            ticket_link_enabled=ticket_enabled,
            ticket_base_url=_ticket_url_or_none(ticket_url),
        )

    def to_manifest(self) -> str:
        lines: list[str] = ["[project]"]
        _append_project_manifest_fields(lines, self)
        postgres_block = _postgres_to_manifest(self.postgres)
        if postgres_block is not None:
            lines.append("")
            lines.append(postgres_block)
        test_instance_block = _test_instance_to_manifest(self.test_instance)
        if test_instance_block is not None:
            lines.append("")
            lines.append(test_instance_block)
        return "\n".join(lines) + "\n"


def _append_project_manifest_fields(lines: list[str], config: ProjectConfig) -> None:
    if config.odoo_bin is not None:
        lines.append(f'odoo_bin = "{_toml_path(config.odoo_bin)}"')
    if config.python is not None:
        lines.append(f'python = "{_toml_str(config.python)}"')
    if config.source_config is not None:
        lines.append(f'source_config = "{_toml_path(config.source_config)}"')
    if config.default_source_database is not None:
        lines.append(f'default_source_database = "{_toml_str(config.default_source_database)}"')
    if config.default_base_ref is not None:
        lines.append(f'default_base_ref = "{_toml_str(config.default_base_ref)}"')
    if config.refresh_after_hours is not None:
        lines.append(f"refresh_after_hours = {config.refresh_after_hours!r}")
    if config.preferred_http_port is not None:
        lines.append(f"preferred_http_port = {config.preferred_http_port}")
    if config.ticket_link_enabled is not None:
        lines.append(f"ticket_link_enabled = {str(config.ticket_link_enabled).lower()}")
        lines.append(f'ticket_base_url = "{_toml_str(config.ticket_base_url or "")}"')
    _append_runtime_manifest_fields(lines, config)


def _append_runtime_manifest_fields(lines: list[str], config: ProjectConfig) -> None:
    if config.requirements:
        reqs = ", ".join(f'"{_toml_str(r)}"' for r in config.requirements)
        lines.append(f"requirements = [{reqs}]")
    if config.default_run_args:
        args = ", ".join(f'"{_toml_str(a)}"' for a in config.default_run_args)
        lines.append(f"default_run_args = [{args}]")
    if config.runtime_cwd is not None:
        lines.append(f'runtime_cwd = "{_toml_path(config.runtime_cwd)}"')


def _test_instance_to_manifest(config: TestInstanceProjectConfig | None) -> str | None:
    if config is None:
        return None
    lines = [
        "[test_instance]",
        f'base_url = "{_toml_str(config.base_url)}"',
        f'database = "{_toml_str(config.database)}"',
    ]
    if config.git_branch is not None:
        lines.append(f'git_branch = "{_toml_str(config.git_branch)}"')
    return "\n".join(lines)


def _test_instance_from_mapping(value: JsonValue) -> TestInstanceProjectConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"base_url", "database", "git_branch"}:
        raise ConfigError("invalid [test_instance] manifest section")
    base_url = value.get("base_url")
    database = value.get("database")
    git_branch = value.get("git_branch")
    if not isinstance(base_url, str) or not isinstance(database, str):
        raise ConfigError("test_instance.base_url and database must be strings")
    if git_branch is not None and not isinstance(git_branch, str):
        raise ConfigError("test_instance.git_branch must be a string")
    try:
        normalized_url = normalize_base_url(base_url)
    except Exception as exc:
        raise ConfigError("invalid test_instance.base_url") from exc
    return TestInstanceProjectConfig(
        base_url=normalized_url,
        database=database,
        git_branch=git_branch,
    )


def _postgres_from_mapping(value: JsonValue) -> PostgresProjectConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"mode", "image", "port", "user"}:
        raise ConfigError("invalid [postgres] manifest section")
    mode = value.get("mode", "external")
    image = value.get("image")
    port = value.get("port")
    user = value.get("user")
    if mode not in {"external", "compose"} or not isinstance(mode, str):
        raise ConfigError("postgres.mode must be external or compose")
    if image is not None and not isinstance(image, str):
        raise ConfigError("postgres.image must be a string")
    if user is not None and not isinstance(user, str):
        raise ConfigError("postgres.user must be a string")
    if port is not None and (not isinstance(port, int) or isinstance(port, bool)):
        raise ConfigError("postgres.port must be an integer")
    cfg = PostgresProjectConfig(
        mode=cast("Literal['external', 'compose']", mode), image=image, port=port, user=user
    )
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


def _path_or_none(value: JsonValue) -> Path | None:
    if value is None:
        return None
    return Path(str(value))


def _python_field(value: JsonValue) -> str | Path | None:
    if value is None:
        return None
    s = str(value)
    if s.startswith(("/", "./")) or (len(s) > 1 and s[1] == ":"):
        return Path(s)
    return s


def _str_or_none(value: JsonValue) -> str | None:
    if value is None:
        return None
    return str(value)


def _bool_or_none(value: JsonValue) -> bool | None:
    if value is None:
        return None
    if type(value) is not bool:
        raise ConfigError("project.ticket_link_enabled must be a boolean")
    return value


def _ticket_url_or_none(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    try:
        return normalize_base_url(value)
    except Exception as exc:
        raise ConfigError("invalid project.ticket_base_url") from exc


def _int_or_none(value: JsonValue) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return int(str(value))


def _float_or_none(value: JsonValue) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ConfigError("project.refresh_after_hours must be a number")
    try:
        return float(cast("str | float | int", value))
    except (TypeError, ValueError) as exc:
        raise ConfigError("project.refresh_after_hours must be a number") from exc


def _str_list(value: JsonValue) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def _toml_path(p: Path) -> str:
    return _toml_str(p)


def _toml_str(v: str | Path) -> str:
    sanitized = sanitize_terminal_text(str(v))
    return sanitized.replace("\\", "\\\\").replace('"', '\\"')
