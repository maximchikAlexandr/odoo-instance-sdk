from __future__ import annotations

import stat
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol, cast

from odoo_instance_sdk.exceptions import InstanceConfigurationError, PlanValidationError
from odoo_instance_sdk.internal.pg.builder import build_psql_specification

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult
    from odoo_instance_sdk.resources.environment.checkout_planning import _CheckoutPlan


class _AttachmentProbeContext(Protocol):
    def process(self, step_id: str) -> object: ...


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


def _verify_regular_filestore_entry(root: Path, relative: PurePosixPath, store_fname: str) -> None:
    candidate = root
    for index, part in enumerate(relative.parts):
        candidate /= part
        try:
            mode = candidate.lstat().st_mode
        except OSError as exc:
            raise InstanceConfigurationError(
                f"restored attachment file is missing: {store_fname!r}"
            ) from exc
        if stat.S_ISLNK(mode):
            raise InstanceConfigurationError("target attachment path contains a symlink")
        if index < len(relative.parts) - 1:
            if not stat.S_ISDIR(mode):
                raise InstanceConfigurationError("target attachment path is not a directory")
        elif not stat.S_ISREG(mode):
            raise InstanceConfigurationError(
                f"restored attachment file is not regular: {store_fname!r}"
            )


def verify_copy_filestore(
    context: _AttachmentProbeContext, data_dir: Path | None, target_db: str
) -> None:
    if data_dir is None:
        raise InstanceConfigurationError("COPY target filestore binding is unavailable")
    result = cast("ProcessResult", context.process("database.restore.attachments"))
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
    root = (data_dir / "filestore" / target_db).resolve()
    for line in raw.splitlines():
        try:
            store_fname = bytes.fromhex(line).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise InstanceConfigurationError(
                "target attachment query returned invalid data"
            ) from exc
        relative = PurePosixPath(store_fname)
        if (
            not store_fname
            or "\\" in store_fname
            or relative.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise InstanceConfigurationError("target attachment path is unsafe")
        _verify_regular_filestore_entry(root, relative, store_fname)
