"""Extension-owned binding files keyed by OdCLI environment id."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import msgspec

from odcli_openspec.errors import OpenSpecError
from odcli_openspec.models import OpenSpecBinding

_SCHEMA_VERSION = 1


def binding_path(environment_id: uuid.UUID | str) -> Path:
    """Return the binding file for one environment.

    Args:
        environment_id: Canonical OdCLI environment id.

    Returns:
        ``$ODCLI_USER_ROOT/openspec/<id>.json``, or the same path under
        ``~/.odcli`` when the override is absent.

    Raises:
        OpenSpecError: ``ODCLI_USER_ROOT`` is set and is not absolute.
    """
    selected = os.environ.get("ODCLI_USER_ROOT")
    if selected:
        root = Path(selected).expanduser()
        if not root.is_absolute():
            raise OpenSpecError("binding_root", "ODCLI_USER_ROOT must be an absolute path")
    else:
        root = Path.home() / ".odcli"
    return root / "openspec" / f"{environment_id}.json"


def read_binding(environment_id: uuid.UUID | str) -> OpenSpecBinding | None:
    """Read one binding, or return null when it is absent.

    Args:
        environment_id: Canonical OdCLI environment id.

    Returns:
        The binding, or null when the file is missing.

    Raises:
        OpenSpecError: The file exists but is not a version-1 binding.
    """
    path = binding_path(environment_id)
    if not path.is_file():
        return None
    try:
        binding = msgspec.json.decode(path.read_bytes(), type=OpenSpecBinding)
    except msgspec.DecodeError as exc:
        raise OpenSpecError("invalid_binding", "OpenSpec binding file is malformed") from exc
    if binding.schema_version != _SCHEMA_VERSION or binding.environment_id != str(environment_id):
        raise OpenSpecError(
            "invalid_binding", "OpenSpec binding file does not match this environment"
        )
    return binding


def write_binding(binding: OpenSpecBinding) -> Path:
    """Persist one binding with owner-only permissions.

    Args:
        binding: Binding to write. Existing content is replaced.

    Returns:
        The written path.
    """
    path = binding_path(binding.environment_id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(msgspec.json.encode(binding))
    path.chmod(0o600)
    return path


def delete_binding(environment_id: uuid.UUID | str) -> bool:
    """Delete only the extension binding file.

    Args:
        environment_id: Canonical OdCLI environment id.

    Returns:
        True when a file was removed.
    """
    path = binding_path(environment_id)
    if not path.is_file():
        return False
    path.unlink()
    return True
