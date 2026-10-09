"""Composition-boundary registration for the observability monitor leaf."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import click
else:
    import rich_click as click


def register_monitor_command(group: click.Group) -> None:
    """Register the monitor leaf without coupling it to other callbacks."""

    @group.command("monitor")
    @click.option("--headless", is_flag=True, default=False, help="Serve API only, no UI/browser.")
    @click.option(
        "--host", default="127.0.0.1", help="Loopback bind address (127.0.0.1, localhost, or ::1)."
    )
    @click.option(
        "--port", type=int, default=None, help="Exact port (else auto-select 8069 or 8100-8120)."
    )
    @click.option("--no-open", is_flag=True, default=False, help="Do not open a browser.")
    @click.option(
        "--external",
        "external_mode",
        is_flag=True,
        default=False,
        help="Publish the monitor through the configured Caddy panel route.",
    )
    @click.option(
        "--settings",
        type=click.Path(path_type=Path),
        default=None,
        help="Publication settings file (requires --external).",
    )
    @click.pass_context
    def monitor_cmd(
        ctx: click.Context,
        headless: bool,
        host: str,
        port: int | None,
        no_open: bool,
        external_mode: bool,
        settings: Path | None,
    ) -> None:
        """Start the observability monitor (FastAPI + React UI)."""
        from odoo_instance_sdk.http.app import ExternalProxyConfig
        from odoo_instance_sdk.internal.serve import run_server
        from odoo_instance_sdk.resources.publication import PublicationSettings

        if settings is not None and not external_mode:
            raise click.UsageError("--settings requires --external")
        loaded_settings = PublicationSettings.load(settings) if external_mode else None
        external_proxy = None
        if loaded_settings is not None:
            panel_host = f"{loaded_settings.panel_host_label}.{loaded_settings.domain_suffix}"
            external_proxy = ExternalProxyConfig(
                allowed_hosts=(panel_host,),
                trusted_proxy_addresses=loaded_settings.trusted_proxy_addresses,
            )

        # run_server raises SystemExit with an actionable hint if the dashboard
        # extra (fastapi/uvicorn) is missing; that propagates as exit 1.
        run_server(
            host=host,
            port=port,
            headless=headless,
            no_open=no_open,
            external_proxy=external_proxy,
            publication_settings=loaded_settings,
        )


__all__ = ["register_monitor_command"]
