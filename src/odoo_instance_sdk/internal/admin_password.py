"""Shared administrator password secret resolution.

One mechanism backs ``db reset-admin-password``, restore with
``--reset-admin-password``, and COPY replacement.  The secret is resolved at
the CLI boundary (where the output mode and ``--no-input`` flag are known) and
threaded into the SDK operation; it is never placed in argv, manifest,
catalogue, plan, Rich/JSON/TOON output, logs, or exception text.
"""

from __future__ import annotations

import os
from getpass import getpass
from pathlib import Path
from typing import Literal

from odoo_instance_sdk.exceptions import AdminPasswordRequiredError
from odoo_instance_sdk.internal.project_env import load_project_environment

ADMIN_PASSWORD_KEY = "ODCLI_ADMIN_PASSWORD"

AdminPasswordProvenance = Literal["prompt", "environment"]


def resolve_admin_password_secret(
    *,
    prompt: bool,
    project_root: Path | None,
) -> tuple[str, AdminPasswordProvenance]:
    """Return the administrator password secret and its provenance.

    When ``prompt`` is true (RICH output, ``--no-input`` false, not dry-run),
    the secret is read twice with ``getpass`` and the two entries must match.
    Otherwise the process environment ``ODCLI_ADMIN_PASSWORD`` is read first,
    then the project ``.odcli/.env`` value.  A missing secret raises
    :class:`AdminPasswordRequiredError` before any restore/drop mutation.
    """
    if prompt:
        first = getpass("New administrator password: ")
        second = getpass("Confirm administrator password: ")
        if first != second:
            raise AdminPasswordRequiredError("administrator password entries did not match")
        if not first:
            raise AdminPasswordRequiredError("administrator password is required")
        return first, "prompt"

    value = os.environ.get(ADMIN_PASSWORD_KEY)
    if value and value.strip():
        return value, "environment"
    if project_root is not None:
        file_values = load_project_environment(project_root)
        file_value = file_values.get(ADMIN_PASSWORD_KEY)
        if file_value and file_value.strip():
            return file_value, "environment"
    raise AdminPasswordRequiredError(
        "administrator password is required: set ODCLI_ADMIN_PASSWORD or .odcli/.env"
    )


__all__ = [
    "ADMIN_PASSWORD_KEY",
    "AdminPasswordProvenance",
    "resolve_admin_password_secret",
]
