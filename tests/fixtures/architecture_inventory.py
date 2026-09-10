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
        ("src/odoo_instance_sdk/cli.py", 1178),
        ("src/odoo_instance_sdk/cli.py", 1179),
        ("src/odoo_instance_sdk/commands/env.py", 668),
        ("src/odoo_instance_sdk/commands/output.py", 246),
        ("src/odoo_instance_sdk/commands/output.py", 370),
        ("src/odoo_instance_sdk/commands/output.py", 372),
        ("src/odoo_instance_sdk/commands/output.py", 379),
        ("src/odoo_instance_sdk/commands/output.py", 381),
        ("src/odoo_instance_sdk/resources/instance.py", 1186),
    }
)


OUTPUT_WRITE_REASONS: Final[dict[SourceLocation, str]] = {
    ("src/odoo_instance_sdk/commands/env.py", 668): "existing Rich live inventory transport",
    ("src/odoo_instance_sdk/commands/output.py", 246): "shared Rich output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 370): "shared JSON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 372): "shared TOON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 379): "shared diagnostic boundary",
    ("src/odoo_instance_sdk/commands/output.py", 381): "shared diagnostic boundary",
    ("src/odoo_instance_sdk/cli.py", 1178): "documented logs JSONL transport",
    ("src/odoo_instance_sdk/cli.py", 1179): "documented logs JSONL transport",
    ("src/odoo_instance_sdk/resources/instance.py", 1186): "lifecycle cleanup diagnostic transport",
}


# All production annotations are now concrete.  The empty mapping is kept as
# the checked, line-specific inventory so any future regression reports its
# exact file and line instead of being hidden by a broad allowlist.
EXPLICIT_IMPRECISE_ANNOTATIONS: Final[dict[str, frozenset[int]]] = {}


MODULE_LOCAL_SUBPROCESS_PATCHES: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("tests/unit/resources/test_database_resource.py", 637),
        ("tests/unit/test_monitor_cache_and_docker.py", 129),
        ("tests/unit/test_cluster_resources.py", 190),
    }
)


PUBLIC_PROCESS_METHODS: Final[dict[str, int]] = {}
