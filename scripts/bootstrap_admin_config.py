"""Create the local administrator configuration without shell credentials."""

from __future__ import annotations

import getpass
import os
import secrets
import stat
from pathlib import Path

from pwdlib import PasswordHash

from odoo_instance_sdk.internal.paths import get_admin_config_path


def write_admin_config(password: str) -> Path:
    """Create a new owner-only config; refuse to replace existing credentials."""
    path = get_admin_config_path(ensure_exists=True)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    body = (
        "[admin]\n"
        f'password_hash = "{PasswordHash.recommended().hash(password)}"\n'
        f'session_key = "{secrets.token_urlsafe(32)}"\n'
    )
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(body)
    finally:
        if descriptor != -1:
            os.close(descriptor)
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        path.unlink(missing_ok=True)
        raise OSError("administrator configuration ownership is invalid")
    os.chmod(path, 0o600)
    return path


def main() -> int:
    password = getpass.getpass("Administrator password: ")
    if not password:
        raise SystemExit("Password must not be empty")
    if password != getpass.getpass("Confirm administrator password: "):
        raise SystemExit("Passwords do not match")
    path = write_admin_config(password)
    print(f"Created owner-only administrator configuration at {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
