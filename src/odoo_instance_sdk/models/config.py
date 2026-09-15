from __future__ import annotations

import warnings
from pathlib import Path
from typing import Literal, cast

import msgspec


class StartConfig(msgspec.Struct, forbid_unknown_fields=True):
    http_port: int = 8069
    http_interface: str = "127.0.0.1"
    config_path: str | None = None
    addons_path: list[str] | None = None
    data_dir: str | None = None
    dbfilter: str | None = None
    workers: int | None = None
    max_cron_threads: int | None = None
    log_level: Literal["debug", "info", "warning", "error", "critical", "notset"] | None = None
    log_handler: str | None = None
    logfile: str | None = None
    dev_mode: Literal["all"] | list[str] | None = None
    db_host: str | None = None
    db_port: int | None = None
    db_user: str | None = None
    db_password: str | None = None
    db_name: str | None = None
    load_language: str | None = None

    @classmethod
    def from_odoo_config(cls, path: str | Path) -> StartConfig:
        """Build a StartConfig by reading fields from an odoo.conf file.

        Literal fields (``log_level``, ``dev_mode``) are validated by msgspec
        at construction; an invalid value raises ``msgspec.ValidationError``.
        """
        # local import: odoo_config -> urls -> exceptions; none import models,
        # but keeping it lazy avoids any import-order surprise at package init.
        from odoo_instance_sdk.internal.odoo_config import parse_odoo_config

        cfg = parse_odoo_config(path)

        def _get(name: str) -> str | None:
            v = cfg.get(name)
            return v if v else None

        def _int(name: str) -> int | None:
            v = _get(name)
            if v is None:
                return None
            try:
                return int(v)
            except ValueError:
                warnings.warn(
                    f"Invalid int for {name} in odoo.conf: {v!r}; using default",
                    stacklevel=3,
                )
                return None

        def _list(name: str) -> list[str] | None:
            v = _get(name)
            if v is None:
                return None
            return [s.strip() for s in v.split(",") if s.strip()]

        def _dev_mode() -> Literal["all"] | list[str] | None:
            v = _get("dev_mode")
            if v is None:
                return None
            if "," in v:
                return [s.strip() for s in v.split(",") if s.strip()]
            return cast("Literal['all']", v)

        return cls(
            http_port=_int("http_port") or 8069,
            http_interface=_get("http_interface") or "127.0.0.1",
            config_path=str(Path(path)),
            addons_path=_list("addons_path"),
            data_dir=_get("data_dir"),
            dbfilter=_get("dbfilter"),
            workers=_int("workers"),
            max_cron_threads=_int("max_cron_threads"),
            log_level=cast(
                "Literal['debug', 'info', 'warning', 'error', 'critical', 'notset'] | None",
                _get("log_level"),
            ),
            log_handler=_get("log_handler"),
            logfile=_get("logfile"),
            dev_mode=_dev_mode(),
            db_host=_get("db_host"),
            db_port=_int("db_port"),
            db_user=_get("db_user"),
            db_password=_get("db_password"),
            db_name=_get("db_name"),
            load_language=_get("load_language"),
        )

    def __repr__(self) -> str:
        parts: list[str] = []
        for f in msgspec.structs.fields(self):
            val = getattr(self, f.name)
            if f.name == "db_password" and val is not None:
                parts.append(f"{f.name}=<redacted>")
            else:
                parts.append(f"{f.name}={val!r}")
        return f"StartConfig({', '.join(parts)})"
