from __future__ import annotations

import contextlib
import hashlib
import os
import unicodedata
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import (
    BackupDownloadError,
    ConfigError,
)
from odoo_instance_sdk.models import (
    BackupDownloadFailureContext,
    BackupState,
)

if TYPE_CHECKING:
    import httpx


_MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024 * 1024  # 10 GiB
_RESET_ADMIN_PASSWORD_SCRIPT = """
user = env.ref('base.user_admin', raise_if_not_found=True)
user.ensure_one()
user.write({'password': 'admin'})
result = {'xml_id': 'base.user_admin', 'updated': True}
"""


def _normalize_source_git_branch(value: str | None) -> str | None:
    """Validate declarative branch metadata before any backup side effect."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError("source_git_branch must be text")
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise ConfigError(
            "source_git_branch must be non-empty after trimming and contain no control characters"
        )
    branch = value.strip()
    if not branch:
        raise ConfigError(
            "source_git_branch must be non-empty after trimming and contain no control characters"
        )
    return branch


def _stream_response_to_file(
    resp: httpx.Response,
    dest: Path,
    *,
    max_bytes: int = _MAX_DOWNLOAD_BYTES,
    expected_bytes: int | None = None,
    progress: Callable[[int], None] | None = None,
) -> tuple[int, str]:
    if expected_bytes is not None and expected_bytes > max_bytes:
        raise BackupDownloadError(f"Download exceeded {max_bytes} bytes")
    sha = hashlib.sha256()
    written = 0
    fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        for chunk in resp.iter_bytes(chunk_size=8192):
            next_written = written + len(chunk)
            if next_written > max_bytes:
                raise BackupDownloadError(f"Download exceeded {max_bytes} bytes")
            f.write(chunk)
            written = next_written
            sha.update(chunk)
            if progress is not None:
                progress(written)
        if expected_bytes is not None and written != expected_bytes:
            raise BackupDownloadError(
                f"Download size {written} does not match Content-Length {expected_bytes}"
            )
        f.flush()
        os.fsync(f.fileno())
    return written, sha.hexdigest()


def _trustworthy_content_length(headers: Mapping[str, str]) -> int | None:
    """Return a byte total only when transfer headers make it trustworthy."""
    raw = next(
        (value for name, value in headers.items() if name.lower() == "content-length"),
        None,
    )
    if raw is None or not raw.strip().isdigit():
        return None
    if any(
        value.strip().lower() not in {"", "identity"}
        for name, value in headers.items()
        if name.lower() in {"content-encoding", "transfer-encoding"}
    ):
        return None
    return int(raw.strip())


def _annotate_backup_failure(
    error: BaseException,
    backup_id: str,
    *,
    published: bool,
) -> None:
    """Attach only the known backup identity and catalogue state to failures."""
    with contextlib.suppress(ValueError):
        context = BackupDownloadFailureContext(
            backup_id=uuid.UUID(backup_id),
            state=BackupState.AVAILABLE if published else BackupState.FAILED,
            published=published,
        )
        setattr(error, "failure_context", context)
        error.add_note(
            f"backup {context.backup_id} state={context.state.value} published={context.published}"
        )


def _verify_database_via_psql(
    db_host: str | None,
    db_port: int,
    db_user: str | None,
    db_password: str | None,
    database_name: str,
    step_id: str | None = None,
) -> bool | None:
    """Probe whether a PostgreSQL database exists via the ``psql`` CLI.

    Return values:
      * ``True``  — psql ran successfully and stdout indicates the database
                    exists (e.g. a row from ``pg_database``).
      * ``False`` — psql ran successfully and stdout is empty: the database
                    is confirmed absent. Callers SHOULD record the drop.
      * ``None``  — inconclusive: psql not in PATH, returned non-zero, or
                    timed out. Callers MUST NOT treat this as a drop.
    """
    if "\\" in database_name:
        return None
    from odoo_instance_sdk.internal.pg.transport import run_psql

    escaped = database_name.replace("'", "''")
    proc = run_psql(
        host=db_host,
        port=db_port,
        user=db_user,
        password=db_password,
        query=f"SELECT 1 FROM pg_database WHERE datname='{escaped}'",
        timeout=30,
        step_id=step_id,
    )
    if proc is None or proc.returncode != 0:
        return None
    return bool(proc.stdout.strip())
