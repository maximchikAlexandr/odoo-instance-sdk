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
        ("src/odoo_instance_sdk/commands/cli_parts/callbacks_a.py", 484),
        ("src/odoo_instance_sdk/commands/cli_parts/callbacks_a.py", 485),
        ("src/odoo_instance_sdk/commands/backup.py", 342),
        ("src/odoo_instance_sdk/commands/output.py", 236),
        ("src/odoo_instance_sdk/commands/output.py", 381),
        ("src/odoo_instance_sdk/commands/output.py", 383),
        ("src/odoo_instance_sdk/commands/output.py", 390),
        ("src/odoo_instance_sdk/commands/output.py", 392),
        ("src/odoo_instance_sdk/resources/instance/identity.py", 413),
    }
)


OUTPUT_WRITE_REASONS: Final[dict[SourceLocation, str]] = {
    ("src/odoo_instance_sdk/commands/output.py", 236): "shared Rich output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 381): "shared JSON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 383): "shared TOON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 390): "shared diagnostic boundary",
    ("src/odoo_instance_sdk/commands/output.py", 392): "shared diagnostic boundary",
    (
        "src/odoo_instance_sdk/commands/cli_parts/callbacks_a.py",
        484,
    ): "documented logs JSONL transport",
    (
        "src/odoo_instance_sdk/commands/cli_parts/callbacks_a.py",
        485,
    ): "documented logs JSONL transport",
    ("src/odoo_instance_sdk/commands/backup.py", 342): "shared Rich output boundary",
    (
        "src/odoo_instance_sdk/resources/instance/identity.py",
        413,
    ): "lifecycle cleanup diagnostic transport",
}


# Keep only deliberate, line-specific exceptions in this checked inventory so
# every future regression reports its exact file and line instead of being
# hidden by a broad allowlist.
EXPLICIT_IMPRECISE_ANNOTATIONS: Final[dict[str, frozenset[int]]] = {
    "src/odoo_instance_sdk/cli.py": frozenset({58}),
    "src/odoo_instance_sdk/commands/cli_parts/__init__.py": frozenset({9}),
    "src/odoo_instance_sdk/commands/env/__init__.py": frozenset({88}),
    "src/odoo_instance_sdk/commands/test.py": frozenset({78}),
    "src/odoo_instance_sdk/internal/doctor/__init__.py": frozenset({37}),
    "src/odoo_instance_sdk/internal/pg/__init__.py": frozenset({12}),
    "src/odoo_instance_sdk/resources/monitor/__init__.py": frozenset({60}),
}


MODULE_LOCAL_SUBPROCESS_PATCHES: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("tests/unit/resources/test_database_resource.py", 638),
        ("tests/unit/test_monitor_cache_and_docker.py", 129),
        ("tests/unit/test_cluster_resources.py", 190),
        ("tests/unit/test_real_odoo_ci_components.py", 38),
        ("tests/unit/test_real_odoo_ci_components.py", 107),
        ("tests/unit/test_real_odoo_ci_components.py", 149),
        ("tests/unit/test_real_odoo_foundation.py", 325),
        ("tests/unit/test_real_odoo_foundation.py", 348),
        ("tests/unit/test_real_odoo_foundation.py", 367),
    }
)


PUBLIC_PROCESS_METHODS: Final[dict[str, int]] = {}
