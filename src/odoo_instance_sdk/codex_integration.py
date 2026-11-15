"""Load the optional Codex extension only when a caller asks for it."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from odoo_instance_sdk.exceptions import ConfigError

if TYPE_CHECKING:
    import msgspec

    from odoo_instance_sdk.resources.git import GitResource


class CatalogFiller(Protocol):
    """Extension callback that fills catalogs core has already exported."""

    def __call__(
        self,
        paths: tuple[Path, ...],
        *,
        contexts: tuple[str, ...],
        model: str | None,
        project_root: Path,
        language: str | None = None,
    ) -> tuple[msgspec.Struct, ...]:
        """Fill one exported catalog path per call."""


class CommitDescriptionLoader(Protocol):
    """Extension callback that returns one English commit description."""

    def __call__(
        self,
        resource: GitResource,
        *,
        contexts: tuple[str, ...],
        model: str | None,
        project_root: Path,
    ) -> str:
        """Return one description line."""


INSTALL_HINT = (
    "odcli-codex is not installed. "
    "uv tool install --with-executables-from odcli-codex odoo-instance-sdk"
)


def load_catalog_fill() -> CatalogFiller:
    """Import catalog filling from the extension, or raise an install hint."""
    try:
        from odcli_codex.integration import fill_exported_catalogs
    except ImportError as exc:
        raise ConfigError(INSTALL_HINT) from exc
    return fill_exported_catalogs


def load_commit_description() -> CommitDescriptionLoader:
    """Import description generation from the extension, or raise an install hint."""
    try:
        from odcli_codex.integration import generate_commit_description
    except ImportError as exc:
        raise ConfigError(INSTALL_HINT) from exc
    return generate_commit_description


def generated_description(
    resource: GitResource,
    *,
    contexts: tuple[str, ...],
    model: str | None,
    project_root: Path,
) -> str:
    """Return one extension-generated description for an existing Git resource."""
    return load_commit_description()(
        resource,
        contexts=contexts,
        model=model,
        project_root=project_root,
    )
