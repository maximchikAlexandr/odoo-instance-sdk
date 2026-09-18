"""Compatibility shim for legacy ``postgres._paths`` import paths."""

from __future__ import annotations

from odoo_instance_sdk.internal.paths import get_project_postgres_dir

__all__ = ["get_project_postgres_dir"]
