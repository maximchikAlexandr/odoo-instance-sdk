"""Foreground Uvicorn entrypoint for one supervised monitor program.

The process never opens a browser. The user CLI does that after readiness.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from odoo_instance_sdk.http.app import ExternalProxyConfig, create_app
from odoo_instance_sdk.resources.publication import PublicationSettings, monitor_route_command


def serve(
    *,
    host: str,
    port: int,
    headless: bool,
    settings: Path | None,
) -> None:
    """Run Uvicorn in the foreground with proxy trust disabled."""
    external_proxy = None
    if settings is not None:
        publication = PublicationSettings.load(settings)
        panel_host = f"{publication.panel_host_label}.{publication.domain_suffix}"
        external_proxy = ExternalProxyConfig(
            allowed_hosts=(panel_host,),
            trusted_proxy_addresses=publication.trusted_proxy_addresses,
        )
        monitor_route_command(f"http://{host}:{port}", settings=publication).run()
    uvicorn.run(
        create_app(headless=headless, external_proxy=external_proxy),
        host=host,
        port=port,
        proxy_headers=False,
        forwarded_allow_ips="",
    )


def main(argv: list[str] | None = None) -> None:
    """Start the monitor program from a frozen command line."""
    parser = argparse.ArgumentParser(prog="odcli-monitor")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--settings", type=Path, default=None)
    args = parser.parse_args(argv)
    serve(host=args.host, port=args.port, headless=args.headless, settings=args.settings)


if __name__ == "__main__":
    main()
