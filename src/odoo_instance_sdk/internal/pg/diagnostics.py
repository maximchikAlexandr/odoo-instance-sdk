"""Shared bounds, asset loading, and typed JSON decoding for diagnostics."""

from __future__ import annotations

import math
from importlib import resources
from typing import TypeVar

import msgspec

from odoo_instance_sdk.exceptions import ConfigError

_T = TypeVar("_T")


def validate_top(top: int) -> int:
    if type(top) is not int or not 1 <= top <= 1000:
        raise ConfigError("diagnostic top must be an integer from 1 through 1000")
    return top


def validate_timeout(timeout: float) -> float:
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ConfigError("diagnostic timeout must be finite and greater than zero")
    return float(timeout)


def validate_exact_max_scan_mb(value: int) -> int:
    if type(value) is not int or not 0 <= value <= 1024:
        raise ConfigError("exact_max_scan_mb must be an integer from 0 through 1024")
    return value


def load_sql_asset(name: str) -> str:
    try:
        return (
            resources.files("odoo_instance_sdk.internal.pg")
            .joinpath("sql", name)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        raise ConfigError(f"PostgreSQL diagnostic SQL asset is unavailable: {name}") from exc


def decode_typed_json(stdout: str | bytes, model: type[_T], label: str) -> _T:
    payload = stdout.encode() if isinstance(stdout, str) else stdout
    if not payload.strip():
        raise ConfigError(f"{label} returned empty output")
    try:
        return msgspec.json.decode(payload, type=model)
    except (msgspec.DecodeError, TypeError, ValueError) as exc:
        raise ConfigError(f"{label} returned invalid diagnostic JSON") from exc


__all__ = [
    "decode_typed_json",
    "load_sql_asset",
    "validate_exact_max_scan_mb",
    "validate_timeout",
    "validate_top",
]
