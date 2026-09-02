"""Compatibility import for the PostgreSQL size collector's old module path."""

from odoo_instance_sdk.internal.pg.size import database_size_bytes

__all__ = ["database_size_bytes"]
