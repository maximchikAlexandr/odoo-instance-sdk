from __future__ import annotations


def human_bytes(n: int) -> str:
    """Render a byte count using the CLI's compact binary-unit convention."""
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024 or unit == "TiB":
            if unit == "B":
                return f"{n} {unit}"
            return f"{value:.1f} {unit}".rstrip("0").rstrip(".")
        value /= 1024
    return f"{value:.1f} TiB"
