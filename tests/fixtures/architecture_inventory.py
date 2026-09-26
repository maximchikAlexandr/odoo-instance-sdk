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
        ("src/odoo_instance_sdk/commands/cli_parts/callbacks.py", 503),
        ("src/odoo_instance_sdk/commands/cli_parts/callbacks.py", 504),
        ("src/odoo_instance_sdk/commands/cli_parts/registration.py", 473),
        ("src/odoo_instance_sdk/commands/backup.py", 347),
        ("src/odoo_instance_sdk/commands/output.py", 116),
        ("src/odoo_instance_sdk/commands/output.py", 288),
        ("src/odoo_instance_sdk/commands/output.py", 457),
        ("src/odoo_instance_sdk/commands/output.py", 459),
        ("src/odoo_instance_sdk/commands/output.py", 466),
        ("src/odoo_instance_sdk/commands/output.py", 468),
        ("src/odoo_instance_sdk/internal/self_update.py", 798),
        ("src/odoo_instance_sdk/internal/self_update.py", 799),
        ("src/odoo_instance_sdk/internal/self_update.py", 800),
        ("src/odoo_instance_sdk/resources/instance/identity.py", 503),
    }
)


OUTPUT_WRITE_REASONS: Final[dict[SourceLocation, str]] = {
    ("src/odoo_instance_sdk/commands/output.py", 116): "in-memory Rich serialization boundary",
    ("src/odoo_instance_sdk/commands/output.py", 288): "shared Rich output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 457): "shared JSON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 459): "shared TOON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 466): "shared diagnostic boundary",
    ("src/odoo_instance_sdk/commands/output.py", 468): "shared diagnostic boundary",
    (
        "src/odoo_instance_sdk/commands/cli_parts/callbacks.py",
        503,
    ): "documented logs JSONL transport",
    (
        "src/odoo_instance_sdk/commands/cli_parts/callbacks.py",
        504,
    ): "documented logs JSONL transport",
    (
        "src/odoo_instance_sdk/commands/cli_parts/registration.py",
        473,
    ): "documented --version metadata flag transport",
    ("src/odoo_instance_sdk/commands/backup.py", 347): "shared Rich output boundary",
    (
        "src/odoo_instance_sdk/internal/self_update.py",
        798,
    ): "maintenance child JSON stdout transport",
    (
        "src/odoo_instance_sdk/internal/self_update.py",
        799,
    ): "maintenance child JSON stdout transport",
    (
        "src/odoo_instance_sdk/internal/self_update.py",
        800,
    ): "maintenance child JSON stdout transport",
    (
        "src/odoo_instance_sdk/resources/instance/identity.py",
        503,
    ): "lifecycle cleanup diagnostic transport",
}


# Keep only deliberate, line-specific exceptions in this checked inventory so
# every future regression reports its exact file and line instead of being
# hidden by a broad allowlist.
EXPLICIT_IMPRECISE_ANNOTATIONS: Final[dict[str, frozenset[int]]] = {
    "src/odoo_instance_sdk/resources/database/backup_restore_parts/backup.py": frozenset(
        {363, 365, 421, 433}
    ),
}


MODULE_LOCAL_SUBPROCESS_PATCHES: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("tests/unit/resources/test_database_resource.py", 634),
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


DIRECT_HTTPX_USAGE: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("src/odoo_instance_sdk/internal/transport/base.py", 203),
        ("src/odoo_instance_sdk/internal/transport/base.py", 208),
        ("src/odoo_instance_sdk/internal/transport/base.py", 233),
        ("src/odoo_instance_sdk/internal/transport/base.py", 235),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 77),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 79),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 156),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 158),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 159),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 210),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 213),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 215),
    }
)
