"""Environment boundaries for SDK-owned child processes."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

_REMOTE_MASTER_PASSWORD = "ODCLI_TEST_MASTER_PASSWORD"
_ADMIN_PASSWORD = "ODCLI_ADMIN_PASSWORD"
_NAMED_REMOTE_PASSWORD = re.compile(r"ODCLI_REMOTE_[A-Z][A-Z0-9_]*_MASTER_PASSWORD\Z")


def is_secret_environment_key(key: str) -> bool:
    return key in {_REMOTE_MASTER_PASSWORD, _ADMIN_PASSWORD} or bool(
        _NAMED_REMOTE_PASSWORD.fullmatch(key)
    )


def sanitized_child_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a child environment that cannot inherit the remote test secret.

    Callers may provide explicit variables (for example ``PGPASSWORD``); the
    remote Odoo master password is removed regardless of its source.  Keeping
    this at the subprocess boundary makes it difficult for a new project,
    runtime, or cluster invocation to accidentally inherit the credential.
    """
    child = dict(os.environ if environment is None else environment)
    for key in tuple(child):
        if is_secret_environment_key(key):
            child.pop(key, None)
    return child


def captured_child_environment(
    overrides: Mapping[str, str] | None = None,
    *,
    project_environment: Mapping[str, str] | None = None,
) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    """Capture exact child inputs and keep explicit overrides separately.

    The first tuple is private execution state.  The second is the only
    environment data eligible for a public command projection.
    """
    explicit = sanitized_child_environment(overrides or {})
    child = sanitized_child_environment(None)
    if project_environment:
        for key, value in project_environment.items():
            child.setdefault(key, value)
    public_overrides = dict(project_environment or {})
    public_overrides.update(explicit)
    public_overrides = sanitized_child_environment(public_overrides)
    child.update(explicit)
    for key in tuple(child):
        if is_secret_environment_key(key):
            child.pop(key, None)
    return tuple(sorted(child.items())), tuple(sorted(public_overrides.items()))


__all__ = ["captured_child_environment", "is_secret_environment_key", "sanitized_child_environment"]
