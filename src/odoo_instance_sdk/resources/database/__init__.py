"""Split package; public imports preserved via re-exports."""

from __future__ import annotations  # noqa: I001 -- keep database lifecycle re-exports grouped; remove when Ruff supports grouped aliases.

from odoo_instance_sdk.models import Backup as Backup
from odoo_instance_sdk.resources.database.backup_restore_parts import *  # noqa: F403
from odoo_instance_sdk.resources.database.backup_restore_parts import (
    DatabaseResource as DatabaseResource,
)
from odoo_instance_sdk.resources.database.lifecycle import (
    _admin_password_reset_script as _admin_password_reset_script,
    _normalize_source_git_branch as _normalize_source_git_branch,
    _stream_response_to_file as _stream_response_to_file,
    _verify_database_via_psql as _verify_database_via_psql,
)
