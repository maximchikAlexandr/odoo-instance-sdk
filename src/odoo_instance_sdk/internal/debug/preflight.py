"""Reject debug runtimes that cannot stay on one process."""

from __future__ import annotations

from odoo_instance_sdk.exceptions import DebugError, InstanceConfigurationError
from odoo_instance_sdk.models.config import StartConfig
from odoo_instance_sdk.resources.instance.auxiliary_restore import _assert_http_port_free


def assert_debug_runtime(config: StartConfig, argv: tuple[str, ...]) -> None:
    """Fail before spawn when workers, reload, or the HTTP port are unsafe."""
    workers = _effective_workers(config, argv)
    if workers is not None and workers > 0:
        raise DebugError(
            "workers-unsupported",
            "headless debug requires workers = 0",
            hint="Set workers = 0 in the environment configuration",
        )
    if _reload_enabled(config, argv):
        raise DebugError(
            "reload-unsupported",
            "headless debug does not support Odoo reload",
            hint="Remove reload from dev_mode and --dev",
        )
    try:
        _assert_http_port_free(config)
    except InstanceConfigurationError as exc:
        raise DebugError(
            "port-conflict",
            str(exc),
            hint="Stop the process listening on the environment HTTP port",
        ) from exc


def _effective_workers(config: StartConfig, argv: tuple[str, ...]) -> int | None:
    override = _option_value(argv, "--workers")
    if override is None:
        return config.workers
    if not override.isdigit():
        raise DebugError("workers-unsupported", f"invalid workers value {override!r}")
    return int(override)


def _reload_enabled(config: StartConfig, argv: tuple[str, ...]) -> bool:
    mode = config.dev_mode
    configured = (mode,) if isinstance(mode, str) else tuple(mode or ())
    if any(_is_reload(item) for item in configured):
        return True
    override = _option_value(argv, "--dev")
    return override is not None and any(_is_reload(item) for item in override.split(","))


def _is_reload(value: str) -> bool:
    return value.strip() in {"reload", "all"}


def _option_value(argv: tuple[str, ...], flag: str) -> str | None:
    prefix = f"{flag}="
    for index, token in enumerate(argv):
        if token.startswith(prefix):
            return token[len(prefix) :]
        if token == flag and index + 1 < len(argv):
            return argv[index + 1]
    return None
