"""Build backend that materializes the distributable dashboard before uv-build."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import uv_build

from build_assets import ensure_dashboard_assets

_ROOT = Path(__file__).resolve().parent
_WEB_ROOT = _ROOT / "src" / "odoo_instance_sdk" / "web"
_DIST = _WEB_ROOT / "dist"


def _ensure_dashboard_assets() -> None:
    # An sdist already carries the dashboard generated while the source
    # archive was created. Rebuilding it while pip installs that sdist adds
    # an unnecessary npm install/build to the isolated wheel build.
    packaged_assets = _ROOT / "PKG-INFO"
    dashboard_index = _DIST / "index.html"
    dashboard_assets = _DIST / "assets"
    if (
        packaged_assets.is_file()
        and dashboard_index.is_file()
        and dashboard_assets.is_dir()
        and any(dashboard_assets.iterdir())
    ):
        return
    ensure_dashboard_assets(_WEB_ROOT, _DIST)


def build_sdist(
    sdist_directory: str,
    config_settings: Mapping[str, str | Sequence[str]] | None = None,
) -> str:
    _ensure_dashboard_assets()
    return uv_build.build_sdist(sdist_directory, config_settings=config_settings)


def build_wheel(
    wheel_directory: str,
    config_settings: Mapping[str, str | Sequence[str]] | None = None,
    metadata_directory: str | None = None,
) -> str:
    _ensure_dashboard_assets()
    return uv_build.build_wheel(
        wheel_directory,
        config_settings=config_settings,
        metadata_directory=metadata_directory,
    )


def build_editable(
    wheel_directory: str,
    config_settings: Mapping[str, str | Sequence[str]] | None = None,
    metadata_directory: str | None = None,
) -> str:
    return uv_build.build_editable(
        wheel_directory,
        config_settings=config_settings,
        metadata_directory=metadata_directory,
    )


def get_requires_for_build_sdist(
    config_settings: Mapping[str, str | Sequence[str]] | None = None,
) -> Sequence[str]:
    return uv_build.get_requires_for_build_sdist(config_settings=config_settings)


def get_requires_for_build_wheel(
    config_settings: Mapping[str, str | Sequence[str]] | None = None,
) -> Sequence[str]:
    return uv_build.get_requires_for_build_wheel(config_settings=config_settings)


def get_requires_for_build_editable(
    config_settings: Mapping[str, str | Sequence[str]] | None = None,
) -> Sequence[str]:
    return uv_build.get_requires_for_build_editable(config_settings=config_settings)


def prepare_metadata_for_build_wheel(
    metadata_directory: str,
    config_settings: Mapping[str, str | Sequence[str]] | None = None,
) -> str:
    return uv_build.prepare_metadata_for_build_wheel(
        metadata_directory,
        config_settings=config_settings,
    )


__all__ = [
    "build_editable",
    "build_sdist",
    "build_wheel",
    "get_requires_for_build_editable",
    "get_requires_for_build_sdist",
    "get_requires_for_build_wheel",
    "prepare_metadata_for_build_wheel",
]
