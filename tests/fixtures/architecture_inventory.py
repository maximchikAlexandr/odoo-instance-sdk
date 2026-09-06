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
        ("src/odoo_instance_sdk/cli.py", 946),
        ("src/odoo_instance_sdk/cli.py", 945),
        ("src/odoo_instance_sdk/commands/env.py", 378),
        ("src/odoo_instance_sdk/commands/output.py", 234),
        ("src/odoo_instance_sdk/commands/output.py", 358),
        ("src/odoo_instance_sdk/commands/output.py", 360),
        ("src/odoo_instance_sdk/commands/output.py", 367),
        ("src/odoo_instance_sdk/commands/output.py", 369),
        ("src/odoo_instance_sdk/resources/instance.py", 1068),
    }
)


OUTPUT_WRITE_REASONS: Final[dict[SourceLocation, str]] = {
    ("src/odoo_instance_sdk/commands/env.py", 378): "existing Rich live inventory transport",
    ("src/odoo_instance_sdk/commands/output.py", 234): "shared Rich output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 358): "shared JSON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 360): "shared TOON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 367): "shared diagnostic boundary",
    ("src/odoo_instance_sdk/commands/output.py", 369): "shared diagnostic boundary",
    ("src/odoo_instance_sdk/cli.py", 946): "documented logs JSONL transport",
    ("src/odoo_instance_sdk/cli.py", 945): "documented logs JSONL transport",
    ("src/odoo_instance_sdk/resources/instance.py", 1068): "lifecycle cleanup diagnostic transport",
}


# All production annotations are now concrete.  The empty mapping is kept as
# the checked, line-specific inventory so any future regression reports its
# exact file and line instead of being hidden by a broad allowlist.
EXPLICIT_IMPRECISE_ANNOTATIONS: Final[dict[str, frozenset[int]]] = {}


MODULE_LOCAL_SUBPROCESS_PATCHES: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("tests/unit/resources/test_database_resource.py", 636),
        ("tests/unit/test_monitor_cache_and_docker.py", 128),
        ("tests/unit/test_cluster_resources.py", 190),
    }
)


PUBLIC_PROCESS_METHODS: Final[dict[str, int]] = {}
