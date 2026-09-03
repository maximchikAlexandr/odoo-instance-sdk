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
        ("src/odoo_instance_sdk/cli.py", 741),
        ("src/odoo_instance_sdk/cli.py", 740),
        ("src/odoo_instance_sdk/commands/env.py", 373),
        ("src/odoo_instance_sdk/commands/output.py", 190),
        ("src/odoo_instance_sdk/commands/output.py", 300),
        ("src/odoo_instance_sdk/commands/output.py", 302),
        ("src/odoo_instance_sdk/commands/output.py", 309),
        ("src/odoo_instance_sdk/commands/output.py", 311),
        ("src/odoo_instance_sdk/resources/instance.py", 918),
    }
)


OUTPUT_WRITE_REASONS: Final[dict[SourceLocation, str]] = {
    ("src/odoo_instance_sdk/commands/env.py", 373): "existing Rich live inventory transport",
    ("src/odoo_instance_sdk/commands/output.py", 190): "shared Rich output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 300): "shared JSON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 302): "shared TOON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 309): "shared diagnostic boundary",
    ("src/odoo_instance_sdk/commands/output.py", 311): "shared diagnostic boundary",
    ("src/odoo_instance_sdk/cli.py", 741): "documented logs JSONL transport",
    ("src/odoo_instance_sdk/cli.py", 740): "documented logs JSONL transport",
    ("src/odoo_instance_sdk/resources/instance.py", 918): "lifecycle cleanup diagnostic transport",
}


# All production annotations are now concrete.  The empty mapping is kept as
# the checked, line-specific inventory so any future regression reports its
# exact file and line instead of being hidden by a broad allowlist.
EXPLICIT_IMPRECISE_ANNOTATIONS: Final[dict[str, frozenset[int]]] = {}


MODULE_LOCAL_SUBPROCESS_PATCHES: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("tests/unit/internal/test_pgadmin_files.py", 442),
        ("tests/unit/internal/test_pgadmin_files.py", 501),
        ("tests/unit/internal/test_pgadmin.py", 533),
        ("tests/unit/resources/test_database_resource.py", 459),
        ("tests/unit/resources/test_database_resource.py", 485),
        ("tests/unit/resources/test_database_resource.py", 512),
        ("tests/unit/resources/test_database_resource.py", 537),
        ("tests/unit/resources/test_database_resource.py", 582),
        ("tests/unit/resources/test_database_resource.py", 603),
        ("tests/unit/resources/test_database_resource.py", 622),
        ("tests/unit/resources/test_database_resource.py", 637),
        ("tests/unit/resources/test_database_resource.py", 649),
        ("tests/unit/resources/test_database_resource.py", 663),
        ("tests/unit/resources/test_database_resource.py", 681),
        ("tests/unit/resources/test_environment_python.py", 42),
        ("tests/unit/resources/test_environment_python.py", 233),
        ("tests/unit/resources/test_environment_python.py", 271),
        ("tests/unit/test_monitor_cache_and_docker.py", 128),
        ("tests/unit/test_cluster_resources.py", 190),
        ("tests/unit/internal/test_proc_boundary.py", 255),
    }
)


PUBLIC_PROCESS_METHODS: Final[dict[str, int]] = {}
