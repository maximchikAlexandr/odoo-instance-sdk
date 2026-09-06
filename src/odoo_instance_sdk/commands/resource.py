"""Bounded read-only local resource projections."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import click
else:
    import rich_click as click

from rich.console import Console
from rich.table import Table

from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    OutputMode,
    action_command,
    emit,
    fail,
    model_to_dict,
    output_options,
    resolve_output_mode,
    success_document,
)
from odoo_instance_sdk.internal.paths import get_backups_dir, get_catalog_path, get_data_root
from odoo_instance_sdk.internal.resource_inventory import (
    ResourceInventory,
    collect_resource_inventory,
    open_catalog_read_only,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


class _CatalogPathProvider:
    provider: Callable[[], Path] | None = None


_catalog_path_provider = _CatalogPathProvider()


def configure_catalog_path_provider(provider: Callable[[], Path]) -> None:
    """Use the same imported CLI catalogue seam as backup commands."""
    _catalog_path_provider.provider = provider


def _catalog_path() -> Path:
    provider = _catalog_path_provider.provider
    return provider() if provider is not None else get_catalog_path()


def _inventory() -> ResourceInventory:
    path = _catalog_path()
    catalog: BackupCatalog | None = None
    if path.is_file():
        catalog = open_catalog_read_only(path)
    try:
        data_root = get_data_root(ensure_exists=False)
        return collect_resource_inventory(
            catalog=catalog,
            roots=(data_root,),
            backup_root=get_backups_dir(),
            owned_directories=(get_backups_dir(),),
        )
    finally:
        if catalog is not None:
            catalog.close()


def _payload(inventory: ResourceInventory) -> JsonObject:
    return model_to_dict(inventory)


def _rich_list(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    resources = result.get("resources", [])
    if not isinstance(resources, list):
        return json.dumps(result, ensure_ascii=False, default=str, indent=2)
    table = Table(
        "Identity", "Type", "Name", "Ownership", "Measured bytes", "Complete", "Reclaimable"
    )
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        table.add_row(
            str(resource.get("stable_identity", "")),
            str(resource.get("type", "")),
            str(resource.get("name", "")),
            str(resource.get("ownership_confidence", "")),
            str(resource.get("measured_bytes", "")),
            str(resource.get("completeness", "")),
            str(resource.get("reclaimable", False)).lower(),
        )
    console = Console(record=True, color_system=None, width=180)
    console.print(table)
    return console.export_text().rstrip()


def _rich_doctor(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    findings = result.get("findings", [])
    if not isinstance(findings, list):
        return json.dumps(result, ensure_ascii=False, default=str, indent=2)
    if not findings:
        return "No resource findings."
    table = Table("Severity", "Code", "Identity", "Message", "Recommendation")
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        table.add_row(
            str(finding.get("severity", "")),
            str(finding.get("code", "")),
            str(finding.get("stable_identity", "")),
            str(finding.get("message", "")),
            str(finding.get("recommendation", "") or ""),
        )
    console = Console(record=True, color_system=None, width=180)
    console.print(table)
    return console.export_text().rstrip()


def _run_resource(
    command_name: str,
    mode: OutputMode,
    rich: Callable[[OutputDocument], str],
) -> None:
    try:
        command = action_command(
            "resource.inventory",
            _inventory,
            description="Collect read-only local resource inventory",
        )
        # The operation is intentionally run once; keeping conversion here
        # gives both leaves the same frozen result graph and emitter.
        inventory = command.run()
        emit(success_document(command=command_name, result=_payload(inventory)), mode, rich=rich)
    except Exception as exc:
        fail(mode, command_name, exc)


@click.group("resource", help="Inspect retained local resources and findings.")
def resource_group() -> None:
    """Inspect retained local resources and findings."""


@resource_group.command("list", help="List read-only local resource observations.")
@output_options
def resource_list(output_format: str | None, json_output: bool) -> None:
    _run_resource("resource.list", resolve_output_mode(output_format, json_output), _rich_list)


@resource_group.command("doctor", help="Diagnose read-only local resource findings.")
@output_options
def resource_doctor(output_format: str | None, json_output: bool) -> None:
    _run_resource("resource.doctor", resolve_output_mode(output_format, json_output), _rich_doctor)


__all__ = ["configure_catalog_path_provider", "resource_group"]
