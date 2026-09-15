from __future__ import annotations

import math
from datetime import datetime
from typing import TypeVar

_TupleItem = TypeVar("_TupleItem")


def _require_tuple(value: tuple[_TupleItem, ...], field: str) -> tuple[_TupleItem, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple")
    return value


def _require_non_negative_int(value: int, field: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")


def _require_bool(value: bool, field: str) -> None:
    if type(value) is not bool:
        raise TypeError(f"{field} must be a boolean")


def _require_datetime(value: datetime, field: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")


def _require_ratio(value: float | None, field: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a finite ratio")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{field} must be a finite ratio in [0, 1]")


def _require_non_negative_float(value: float | None, field: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a finite non-negative number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
