"""Plain stdout for a description that is piped into ``odcli git commit -``."""

from __future__ import annotations

import click


def emit_plain_line(line: str) -> None:
    """Write one description line and a newline, with no envelope."""
    click.echo(line)
