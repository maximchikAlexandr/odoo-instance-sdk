"""Public scaffold resource."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.execution import Command
from odoo_instance_sdk.models.scaffold import ManifestValue, ScaffoldResult
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.scaffold import create_scaffold_command

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient


class ScaffoldResource:
    """Create Odoo addons through the client's configured executable."""

    def __init__(self, _client: OdooClient) -> None:
        self._client = _client

    def create_command(
        self,
        *,
        project: ProjectConfig,
        module_name: str,
        destination: Path,
        include: tuple[str, ...] = (),
        exclude: tuple[str, ...] = (),
        manifest: Mapping[str, ManifestValue] | None = None,
    ) -> Command[ScaffoldResult]:
        """Return the immutable scaffold command for one addon."""

        return create_scaffold_command(
            project=project,
            executable=self._client.config.executable,
            module_name=module_name,
            destination=destination,
            include=include,
            exclude=exclude,
            manifest=manifest,
        )

    def create(
        self,
        *,
        project: ProjectConfig,
        module_name: str,
        destination: Path,
        include: tuple[str, ...] = (),
        exclude: tuple[str, ...] = (),
        manifest: Mapping[str, ManifestValue] | None = None,
    ) -> ScaffoldResult:
        """Create one addon by running :meth:`create_command`."""

        return self.create_command(
            project=project,
            module_name=module_name,
            destination=destination,
            include=include,
            exclude=exclude,
            manifest=manifest,
        ).run()


__all__ = ["ScaffoldResource"]
