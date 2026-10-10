"""Metadata-only contract export commands."""

from __future__ import annotations

import json
from typing import cast

import rich_click as click

from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputMode,
    emit,
    fail,
    success_document,
)
from odoo_instance_sdk.operations import (
    build_registry,
    builtin_bindings,
    contract_bytes,
    discovered_bindings,
)


@click.group("contract", help="Inspect local machine-operation contracts.")
def contract_group() -> None:
    """Inspect operation metadata without opening a project."""


@contract_group.command("export")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["rich", "json", "toon"]),
    default="json",
)
def export_contract(output_format: str) -> None:
    """Export the deterministic versioned operation contract bundle."""
    # Provider DTOs are loaded only for this explicit metadata export.  The
    # selected interpreter remains the sole source of installed operations.
    try:
        builtins = builtin_bindings()
        from odoo_instance_sdk.cli import cli

        build_registry(builtins).validate_click_tree(cli)
        bindings = (*builtins, *discovered_bindings())
        raw_bundle = contract_bytes(build_registry(bindings).bindings)
        bundle = cast("JsonObject", json.loads(raw_bundle))
    except Exception as error:
        fail(OutputMode(output_format), "contract.export", error, dry_run=False)
    emit(
        success_document(command="contract.export", result=bundle),
        OutputMode(output_format),
        raw_json=raw_bundle if output_format == "json" else None,
    )


__all__ = ["contract_group", "export_contract"]
