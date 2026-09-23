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
        ("src/odoo_instance_sdk/commands/cli_parts/callbacks.py", 427),
        ("src/odoo_instance_sdk/commands/cli_parts/callbacks.py", 428),
        ("src/odoo_instance_sdk/commands/cli_parts/registration.py", 493),
        ("src/odoo_instance_sdk/commands/backup.py", 348),
        ("src/odoo_instance_sdk/commands/output.py", 247),
        ("src/odoo_instance_sdk/commands/output.py", 416),
        ("src/odoo_instance_sdk/commands/output.py", 418),
        ("src/odoo_instance_sdk/commands/output.py", 425),
        ("src/odoo_instance_sdk/commands/output.py", 427),
        ("src/odoo_instance_sdk/internal/self_update.py", 735),
        ("src/odoo_instance_sdk/internal/self_update.py", 736),
        ("src/odoo_instance_sdk/internal/self_update.py", 737),
        ("src/odoo_instance_sdk/resources/instance/identity.py", 491),
    }
)


OUTPUT_WRITE_REASONS: Final[dict[SourceLocation, str]] = {
    ("src/odoo_instance_sdk/commands/output.py", 247): "shared Rich output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 416): "shared JSON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 418): "shared TOON output boundary",
    ("src/odoo_instance_sdk/commands/output.py", 425): "shared diagnostic boundary",
    ("src/odoo_instance_sdk/commands/output.py", 427): "shared diagnostic boundary",
    (
        "src/odoo_instance_sdk/commands/cli_parts/callbacks.py",
        427,
    ): "documented logs JSONL transport",
    (
        "src/odoo_instance_sdk/commands/cli_parts/callbacks.py",
        428,
    ): "documented logs JSONL transport",
    (
        "src/odoo_instance_sdk/commands/cli_parts/registration.py",
        493,
    ): "documented --version metadata flag transport",
    ("src/odoo_instance_sdk/commands/backup.py", 348): "shared Rich output boundary",
    (
        "src/odoo_instance_sdk/internal/self_update.py",
        735,
    ): "maintenance child JSON stdout transport",
    (
        "src/odoo_instance_sdk/internal/self_update.py",
        736,
    ): "maintenance child JSON stdout transport",
    (
        "src/odoo_instance_sdk/internal/self_update.py",
        737,
    ): "maintenance child JSON stdout transport",
    (
        "src/odoo_instance_sdk/resources/instance/identity.py",
        491,
    ): "lifecycle cleanup diagnostic transport",
}


# Keep only deliberate, line-specific exceptions in this checked inventory so
# every future regression reports its exact file and line instead of being
# hidden by a broad allowlist.
EXPLICIT_IMPRECISE_ANNOTATIONS: Final[dict[str, frozenset[int]]] = {
    "src/odoo_instance_sdk/internal/transport/base.py": frozenset(
        {23, 67, 81, 85, 86, 87, 92, 97, 159, 161}
    ),
    "src/odoo_instance_sdk/internal/transport/odoo.py": frozenset(
        {28, 38, 53, 112, 116, 117, 118, 120, 133, 138, 140, 179, 185}
    ),
}


MODULE_LOCAL_SUBPROCESS_PATCHES: Final[frozenset[SourceLocation]] = frozenset(
    {
        ("tests/unit/resources/test_database_resource.py", 632),
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
        ("src/odoo_instance_sdk/internal/transport/base.py", 163),
        ("src/odoo_instance_sdk/internal/transport/base.py", 166),
        ("src/odoo_instance_sdk/internal/transport/base.py", 190),
        ("src/odoo_instance_sdk/internal/transport/base.py", 192),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 73),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 75),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 151),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 153),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 154),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 205),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 208),
        ("src/odoo_instance_sdk/internal/transport/odoo.py", 210),
    }
)
