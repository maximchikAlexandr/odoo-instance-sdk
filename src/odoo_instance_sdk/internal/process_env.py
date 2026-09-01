"""Environment boundaries for SDK-owned child processes."""

from __future__ import annotations

import os
from collections.abc import Mapping

_REMOTE_MASTER_PASSWORD = "ODCLI_TEST_MASTER_PASSWORD"


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
    child.pop(_REMOTE_MASTER_PASSWORD, None)
    return child


def captured_child_environment(
    overrides: Mapping[str, str] | None = None,
) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    """Capture exact child inputs and keep explicit overrides separately.

    The first tuple is private execution state.  The second is the only
    environment data eligible for a public command projection.
    """
    explicit = sanitized_child_environment(overrides or {})
    child = sanitized_child_environment(None)
    child.update(explicit)
    return tuple(sorted(child.items())), tuple(sorted(explicit.items()))


__all__ = ["captured_child_environment", "sanitized_child_environment"]
