"""Metadata-only contract export commands."""

from __future__ import annotations

import rich_click as click

from odoo_instance_sdk.operations import contract_bytes


@click.group("contract", help="Inspect local machine-operation contracts.")
def contract_group() -> None:
    """Inspect operation metadata without opening a project."""


@contract_group.command("export")
@click.option("--format", "output_format", type=click.Choice(["json"]), default="json")
def export_contract(output_format: str) -> None:
    """Export the deterministic versioned operation contract bundle."""
    del output_format
    click.echo(contract_bytes().decode("utf-8"))


__all__ = ["contract_group", "export_contract"]
