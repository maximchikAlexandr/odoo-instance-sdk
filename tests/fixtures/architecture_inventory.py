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
        ("src/odoo_instance_sdk/cli.py", 688),
        ("src/odoo_instance_sdk/cli.py", 689),
        ("src/odoo_instance_sdk/commands/env.py", 383),
        ("src/odoo_instance_sdk/commands/output.py", 195),
        ("src/odoo_instance_sdk/commands/output.py", 305),
        ("src/odoo_instance_sdk/commands/output.py", 307),
        ("src/odoo_instance_sdk/commands/output.py", 314),
        ("src/odoo_instance_sdk/commands/output.py", 316),
        ("src/odoo_instance_sdk/resources/instance.py", 676),
    }
)


OUTPUT_WRITE_REASONS: Final[dict[SourceLocation, str]] = {
    ("src/odoo_instance_sdk/commands/env.py", 383): "existing Rich live inventory transport",
    ("src/odoo_instance_sdk/commands/output.py", 195): "shared Rich output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 305): "shared JSON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 307): "shared TOON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 314): "shared diagnostic boundary",
    ("src/odoo_instance_sdk/commands/output.py", 316): "shared diagnostic boundary",
    ("src/odoo_instance_sdk/cli.py", 688): "documented logs JSONL transport",
    ("src/odoo_instance_sdk/cli.py", 689): "documented logs JSONL transport",
    ("src/odoo_instance_sdk/resources/instance.py", 676): "lifecycle cleanup diagnostic transport",
}


# All production annotations are now concrete.  The empty mapping is kept as
# the checked, line-specific inventory so any future regression reports its
# exact file and line instead of being hidden by a broad allowlist.
EXPLICIT_IMPRECISE_ANNOTATIONS: Final[dict[str, frozenset[int]]] = {}


MODULE_LOCAL_SUBPROCESS_PATCHES: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("tests/unit/internal/test_pgadmin_files.py", 333),
        ("tests/unit/internal/test_pgadmin_files.py", 392),
        ("tests/unit/internal/test_postgres_size.py", 28),
        ("tests/unit/internal/test_postgres_size.py", 55),
        ("tests/unit/internal/test_postgres_size.py", 78),
        ("tests/unit/internal/test_postgres_size.py", 109),
        ("tests/unit/internal/test_postgres_transport.py", 25),
        ("tests/unit/internal/test_postgres_transport.py", 72),
        ("tests/unit/internal/test_postgres_transport.py", 89),
        ("tests/unit/internal/test_postgres_transport.py", 110),
        ("tests/unit/internal/test_postgres_transport.py", 132),
        ("tests/unit/internal/test_postgres_transport.py", 175),
        ("tests/unit/resources/test_cli_automation.py", 607),
        ("tests/unit/resources/test_database_resource.py", 395),
        ("tests/unit/resources/test_database_resource.py", 421),
        ("tests/unit/resources/test_database_resource.py", 448),
        ("tests/unit/resources/test_database_resource.py", 473),
        ("tests/unit/resources/test_database_resource.py", 518),
        ("tests/unit/resources/test_database_resource.py", 539),
        ("tests/unit/resources/test_database_resource.py", 558),
        ("tests/unit/resources/test_database_resource.py", 573),
        ("tests/unit/resources/test_database_resource.py", 585),
        ("tests/unit/resources/test_database_resource.py", 599),
        ("tests/unit/resources/test_database_resource.py", 617),
        ("tests/unit/resources/test_environment_python.py", 42),
        ("tests/unit/resources/test_environment_python.py", 233),
        ("tests/unit/resources/test_environment_python.py", 271),
        ("tests/unit/test_monitor_cache_and_docker.py", 128),
    }
)


PUBLIC_PROCESS_METHODS: Final[dict[str, int]] = {}
