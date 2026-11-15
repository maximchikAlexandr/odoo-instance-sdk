"""Typed scaffold configuration and results."""

from __future__ import annotations

import msgspec

type ManifestValue = bool | int | float | str | list[ManifestValue] | dict[str, ManifestValue]

BASELINE_COMPONENTS: tuple[str, ...] = ("models", "security", "views")
OPTIONAL_COMPONENTS: tuple[str, ...] = (
    "wizard",
    "controllers",
    "data",
    "demo",
    "report",
    "tests",
    "static/description",
)
TEMPLATE_SOURCE = "odoo.cli.scaffold"


class ScaffoldConfig(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One scaffold configuration layer.

    Manifest values are shallow overrides. ``include`` and ``exclude`` name
    optional component bundles. ``omit_manifest`` names optional manifest keys
    to drop at this layer.
    """

    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    omit_manifest: tuple[str, ...] = ()
    manifest: dict[str, ManifestValue] = msgspec.field(default_factory=dict)


class UserConfig(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """User-level OdCLI settings.

    A missing file is represented by ``scaffold=None``, which means built-in
    defaults rather than an error.
    """

    scaffold: ScaffoldConfig | None = None


class ScaffoldResult(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Files and manifest produced for one addon."""

    module_name: str
    destination: str
    components: tuple[str, ...]
    created_files: tuple[str, ...]
    manifest: dict[str, ManifestValue]
    template_source: str


__all__ = [
    "BASELINE_COMPONENTS",
    "OPTIONAL_COMPONENTS",
    "TEMPLATE_SOURCE",
    "ManifestValue",
    "ScaffoldConfig",
    "ScaffoldResult",
    "UserConfig",
]
