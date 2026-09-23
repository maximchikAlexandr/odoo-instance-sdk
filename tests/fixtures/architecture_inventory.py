"""Checked baseline for the execution-boundary migration.

The entries in this module are deliberately line-specific.  The architecture
tests compare the repository's current AST/source findings with this snapshot;
each migration phase must remove its entries instead of silently growing a
second, undocumented exception list.
"""

from __future__ import annotations

from typing import Final

SourceLocation = tuple[str, int]


DIRECT_SUBPROCESS_LAUNCHES: Final[frozenset[SourceLocation]] = frozenset({})


DIRECT_OUTPUT_WRITES: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("src/odoo_instance_sdk/commands/cli_parts/callbacks.py", 450),
        ("src/odoo_instance_sdk/commands/cli_parts/callbacks.py", 451),
        ("src/odoo_instance_sdk/commands/backup.py", 344),
        ("src/odoo_instance_sdk/commands/output.py", 112),
        ("src/odoo_instance_sdk/commands/output.py", 277),
        ("src/odoo_instance_sdk/commands/output.py", 422),
        ("src/odoo_instance_sdk/commands/output.py", 424),
        ("src/odoo_instance_sdk/commands/output.py", 431),
        ("src/odoo_instance_sdk/commands/output.py", 433),
        ("src/odoo_instance_sdk/resources/instance/identity.py", 457),
    }
)


OUTPUT_WRITE_REASONS: Final[dict[SourceLocation, str]] = {
    ("src/odoo_instance_sdk/commands/output.py", 112): "in-memory Rich serialization boundary",
    ("src/odoo_instance_sdk/commands/output.py", 277): "shared Rich output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 422): "shared JSON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 424): "shared TOON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 431): "shared diagnostic boundary",
    ("src/odoo_instance_sdk/commands/output.py", 433): "shared diagnostic boundary",
    (
        "src/odoo_instance_sdk/commands/cli_parts/callbacks.py",
        450,
    ): "documented logs JSONL transport",
    (
        "src/odoo_instance_sdk/commands/cli_parts/callbacks.py",
        451,
    ): "documented logs JSONL transport",
    ("src/odoo_instance_sdk/commands/backup.py", 344): "shared Rich output boundary",
    (
        "src/odoo_instance_sdk/resources/instance/identity.py",
        457,
    ): "lifecycle cleanup diagnostic transport",
}


# Keep only deliberate, line-specific exceptions in this checked inventory so
# every future regression reports its exact file and line instead of being
# hidden by a broad allowlist.
EXPLICIT_IMPRECISE_ANNOTATIONS: Final[dict[str, frozenset[int]]] = {
    "src/odoo_instance_sdk/commands/env/checkout.py": frozenset({646}),
    "src/odoo_instance_sdk/commands/ps.py": frozenset({318}),
}


MODULE_LOCAL_SUBPROCESS_PATCHES: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("tests/unit/resources/test_database_resource.py", 643),
        ("tests/unit/test_monitor_cache_and_docker.py", 119),
        ("tests/unit/test_cluster_resources.py", 188),
        ("tests/unit/test_real_odoo_ci_components.py", 40),
        ("tests/unit/test_real_odoo_ci_components.py", 109),
        ("tests/unit/test_real_odoo_ci_components.py", 151),
        ("tests/unit/test_real_odoo_foundation.py", 325),
        ("tests/unit/test_real_odoo_foundation.py", 348),
        ("tests/unit/test_real_odoo_foundation.py", 367),
    }
)


PUBLIC_PROCESS_METHODS: Final[dict[str, int]] = {}
