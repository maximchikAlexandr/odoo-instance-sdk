from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import InstanceConfigurationError, PlanValidationError
from odoo_instance_sdk.internal.pg.builder import build_psql_specification

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult
    from odoo_instance_sdk.resources.environment.checkout_planning import _CheckoutPlan


def copy_attachment_probe(plan: _CheckoutPlan) -> PreparedStep:
    target_database = plan.target_database
    if target_database is None:
        raise PlanValidationError("COPY attachment verification requires a target database")
    return build_psql_specification(
        step_id="database.restore.attachments",
        host=plan.config_values.get("db_host"),
        port=int(plan.config_values.get("db_port") or "5432"),
        user=plan.config_values.get("db_user"),
        password=plan.config_values.get("db_password"),
        database=target_database,
        args=(
            "-c",
            (
                "WITH attachment_rows AS (SELECT store_fname FROM ir_attachment "
                "WHERE store_fname IS NOT NULL AND store_fname <> '' "
                "ORDER BY store_fname LIMIT 100001) "
                "SELECT CASE WHEN count(*) > 100000 THEN '__ODCLI_LIMIT_EXCEEDED__' "
                "ELSE COALESCE(string_agg(encode(convert_to(store_fname, 'UTF8'), 'hex'), "
                "E'\\n' ORDER BY store_fname), '') END FROM attachment_rows"
            ),
        ),
        _trusted_args=("-t", "-A"),
        timeout=30.0,
        _require_binary=False,
    ).prepared_step


def _open_directory(parent_fd: int, name: str) -> int:
    if "/" in name or "\\" in name or name in {"", ".", ".."}:
        raise OSError("directory entry is not a single safe path component")
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)


def _open_absolute_directory(path: Path) -> int:
    path = Path(os.path.abspath(path))
    current_fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            next_fd = _open_directory(current_fd, part)
            os.close(current_fd)
            current_fd = next_fd
    except BaseException:
        os.close(current_fd)
        raise
    else:
        return current_fd


def _verify_regular_filestore_entry(
    root_fd: int, relative: PurePosixPath, store_fname: str
) -> None:
    current_fd = os.dup(root_fd)
    try:
        for part in relative.parts[:-1]:
            next_fd = _open_directory(current_fd, part)
            os.close(current_fd)
            current_fd = next_fd
        file_fd = os.open(
            relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current_fd
        )
        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise InstanceConfigurationError(
                    f"restored attachment file is not regular: {store_fname!r}"
                )
        finally:
            os.close(file_fd)
    except OSError as exc:
        raise InstanceConfigurationError(
            f"restored attachment path is missing, symlinked, or not a directory: {store_fname!r}"
        ) from exc
    finally:
        os.close(current_fd)


def verify_copy_filestore(result: ProcessResult, data_dir: Path | None, target_db: str) -> None:
    if data_dir is None:
        raise InstanceConfigurationError("COPY target filestore binding is unavailable")
    if result.returncode != 0:
        stderr = (
            result.stderr.decode(errors="replace")
            if isinstance(result.stderr, bytes)
            else str(result.stderr or "")
        )
        suffix = f": {stderr.strip()}" if stderr.strip() else ""
        raise InstanceConfigurationError(
            f"target attachment query failed (returncode={result.returncode}){suffix}"
        )
    raw = (
        result.stdout.decode(errors="replace")
        if isinstance(result.stdout, bytes)
        else str(result.stdout or "")
    )
    if raw.strip() == "__ODCLI_LIMIT_EXCEEDED__":
        raise InstanceConfigurationError("target attachment query exceeded its safety limit")
    if not raw.strip():
        return
    if (
        not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or os.open not in os.supports_dir_fd
    ):
        raise InstanceConfigurationError("secure target filestore verification is unavailable")
    try:
        data_fd = _open_absolute_directory(data_dir)
        try:
            filestore_fd = _open_directory(data_fd, "filestore")
        finally:
            os.close(data_fd)
        try:
            root_fd = _open_directory(filestore_fd, target_db)
        finally:
            os.close(filestore_fd)
    except (OSError, RuntimeError) as exc:
        raise InstanceConfigurationError("COPY target filestore root is missing or unsafe") from exc
    try:
        for line in raw.splitlines():
            try:
                store_fname = bytes.fromhex(line).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                raise InstanceConfigurationError(
                    "target attachment query returned invalid data"
                ) from exc
            path_parts = store_fname.split("/")
            relative = PurePosixPath(store_fname)
            if (
                not store_fname
                or "\\" in store_fname
                or relative.is_absolute()
                or any(part in {"", ".", ".."} for part in path_parts)
            ):
                raise InstanceConfigurationError("target attachment path is unsafe")
            _verify_regular_filestore_entry(root_fd, relative, store_fname)
    finally:
        os.close(root_fd)
