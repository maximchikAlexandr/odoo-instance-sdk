from __future__ import annotations

import configparser
import warnings
from collections.abc import Mapping
from pathlib import Path

from odoo_instance_sdk.exceptions import InstanceConfigurationError
from odoo_instance_sdk.internal.urls import is_loopback_host


def parse_odoo_config(path: str | Path) -> dict[str, str]:
    cfg = configparser.RawConfigParser(interpolation=None)
    cfg.read(str(path))
    if not cfg.has_section("options"):
        return {}
    return dict(cfg.items("options"))


def infer_base_url(config: dict[str, str], *, base_url: str | None = None) -> str:
    if base_url is not None:
        return base_url
    http_interface = config.get("http_interface", "")
    http_port_raw = config.get("http_port", "8069")
    try:
        http_port = int(http_port_raw)
    except ValueError:
        http_port = 8069
    if not http_interface or not is_loopback_host(http_interface):
        raise InstanceConfigurationError(
            "http_interface is absent, empty, wildcard or non-loopback; explicit base_url required"
        )
    host = f"[{http_interface}]" if ":" in http_interface else http_interface
    return f"http://{host}:{http_port}"


def parse_db_names(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(name.strip() for name in raw.split(",") if name.strip())


def get_admin_passwd(config: Mapping[str, str]) -> str | None:
    if "admin_passwd" not in config:
        warnings.warn(
            "admin_passwd not set in config; using Odoo default 'admin'. "
            "Set an explicit master password for production.",
            stacklevel=2,
        )
        return "admin"
    value = config["admin_passwd"]
    return value if value else None
