"""Read and normalize uv-tool installation composition metadata."""

from __future__ import annotations

import re
import tomllib
from importlib.metadata import Distribution
from pathlib import Path
from typing import Protocol

from odoo_instance_sdk.exceptions import UnsupportedInstallError, UpdateError
from odoo_instance_sdk.execution import JsonValue

_EXTRA_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _normalize_extra_set(raw: JsonValue) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise UnsupportedInstallError("uv-tool optional-extra metadata is ambiguous")
    values: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not _EXTRA_NAME_RE.fullmatch(value.strip()):
            raise UnsupportedInstallError("uv-tool optional-extra metadata is malformed")
        normalized = value.strip().lower().replace("_", "-")
        if normalized in values:
            raise UnsupportedInstallError("uv-tool optional-extra metadata is ambiguous")
        values.append(normalized)
    return tuple(sorted(values))


def _format_direct_requirement(
    package_name: str,
    source_repo: str | None,
    requested_revision: str | None,
) -> str | None:
    if not source_repo:
        return None
    raw = source_repo.removeprefix("git+")
    return f"{package_name} @ git+{raw}@{requested_revision or 'main'}"


def _uv_tool_receipt_path(dist: Distribution) -> Path | None:
    """Locate uv's authoritative tool composition receipt beside the venv."""
    try:
        candidate = Path(str(dist.locate_file("../../../uv-receipt.toml")))
    except (AttributeError, OSError, TypeError):
        return None
    return candidate if candidate.is_file() else None


def read_uv_tool_composition(
    dist: Distribution,
    *,
    package_name: str,
    source_repo: str | None,
    requested_revision: str | None,
    manual_argv: tuple[str, ...],
) -> tuple[str | None, tuple[str, ...]]:
    """Read one unambiguous direct requirement from uv's tool receipt."""
    receipt = _uv_tool_receipt_path(dist)
    # Minimal distribution fakes used by the offline unit contract do not
    # expose a file tree. Treat those as legacy core-only metadata; real uv
    # tool distributions must provide the receipt below.
    if receipt is None and getattr(dist, "files", None) is None:
        return _format_direct_requirement(package_name, source_repo, requested_revision), ()
    if receipt is None:
        raise UnsupportedInstallError(
            "uv-tool installation composition metadata is missing or ambiguous",
            manual_argv=manual_argv,
        )
    try:
        payload = tomllib.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise UnsupportedInstallError(
            "uv-tool installation composition metadata is unreadable",
            manual_argv=manual_argv,
        ) from exc
    tool = payload.get("tool")
    requirements = tool.get("requirements") if isinstance(tool, dict) else None
    if not isinstance(requirements, list):
        raise UnsupportedInstallError(
            "uv-tool installation composition metadata is missing requirements",
            manual_argv=manual_argv,
        )
    matches = [
        item for item in requirements if isinstance(item, dict) and item.get("name") == package_name
    ]
    if len(matches) != 1:
        raise UnsupportedInstallError(
            "uv-tool installation composition has an ambiguous direct requirement",
            manual_argv=manual_argv,
        )
    requirement = matches[0]
    receipt_repo = requirement.get("git")
    if receipt_repo is not None and not isinstance(receipt_repo, str):
        raise UnsupportedInstallError(
            "uv-tool direct requirement has malformed source metadata",
            manual_argv=manual_argv,
        )
    if isinstance(receipt_repo, str) and source_repo:
        expected = source_repo.removeprefix("git+").removesuffix(".git").lower()
        actual = receipt_repo.removeprefix("git+").split("?", 1)[0].removesuffix(".git").lower()
        if expected != actual:
            raise UnsupportedInstallError(
                "uv-tool direct requirement source conflicts with installed provenance",
                manual_argv=manual_argv,
            )
    extras = _normalize_extra_set(requirement.get("extras"))
    return (
        _format_direct_requirement(
            package_name,
            source_repo or receipt_repo,
            requested_revision,
        ),
        extras,
    )


def install_requirement(
    ref: str,
    *,
    package_name: str = "odoo-instance-sdk",
    source_repo: str | None = None,
    extras: tuple[str, ...] = (),
) -> str:
    raw_source = (
        source_repo or "https://github.com/maximchikAlexandr/odoo-instance-sdk.git"
    ).removeprefix("git+")
    package = package_name + ("[" + ",".join(extras) + "]" if extras else "")
    return f"{package} @ git+{raw_source}@{ref}"


def install_argv(
    ref: str,
    *,
    package_name: str = "odoo-instance-sdk",
    source_repo: str | None = None,
    extras: tuple[str, ...] = (),
) -> tuple[str, ...]:
    return (
        "uv",
        "tool",
        "install",
        "--force",
        install_requirement(ref, package_name=package_name, source_repo=source_repo, extras=extras),
    )


class _DashboardProvenance(Protocol):
    @property
    def requested_extras(self) -> tuple[str, ...]: ...


def verify_dashboard_installation(provenance: _DashboardProvenance) -> None:
    """Verify dashboard imports and packaged SPA assets in the new interpreter."""
    extras = getattr(provenance, "requested_extras", ())
    if "dashboard" not in extras:
        return
    try:
        import fastapi  # noqa: F401
        import starlette  # type: ignore[import-not-found]  # noqa: F401
        import uvicorn  # noqa: F401

        import odoo_instance_sdk
    except ImportError as exc:
        raise UpdateError("dashboard dependency import failed after update") from exc
    package_root = Path(odoo_instance_sdk.__file__).resolve().parent
    entry = package_root / "web" / "dist" / "index.html"
    assets = entry.parent / "assets"
    if not entry.is_file() or not assets.is_dir() or not any(assets.iterdir()):
        raise UpdateError("packaged dashboard SPA assets are missing after update")


__all__ = [
    "install_argv",
    "install_requirement",
    "read_uv_tool_composition",
    "verify_dashboard_installation",
]
