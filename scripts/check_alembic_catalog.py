#!/usr/bin/env python3
"""Reject multiple Alembic heads and schema-metadata divergence."""

from __future__ import annotations

import sys

from odoo_instance_sdk.storage.catalog_migrate import assert_schema_metadata_matches_revision


def main() -> int:
    """Run the catalogue Alembic gate."""
    assert_schema_metadata_matches_revision()
    return 0


if __name__ == "__main__":
    sys.exit(main())
