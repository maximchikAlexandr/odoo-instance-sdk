"""Public PostgreSQL resource entry point."""

from __future__ import annotations

from odoo_instance_sdk.resources.postgres.backup_restore_parts import (
    PostgresCluster as PostgresCluster,
)

__all__ = ["PostgresCluster"]
