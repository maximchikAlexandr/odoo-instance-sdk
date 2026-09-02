"""Compatibility import for the PostgreSQL transport's old module path."""

import shutil
import subprocess

from odoo_instance_sdk.internal.pg.transport import run_psql

# Keep legacy test and extension patch points working while callers migrate to
# ``internal.pg.transport``. These are the same module objects used by the
# canonical implementation.
__all__ = ["run_psql", "shutil", "subprocess"]
